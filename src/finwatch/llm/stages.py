"""LLM stage runners: bound inputs/cost → parse strict schemas → persist.

Parsing the response into the pydantic schema is the schema-validity gate (a
ValidationError = malformed output → the pipeline regenerates). The trusted
accession/ticker (from the filing being analyzed) are used for the DB keys, never
the model's echoed values. P1 stores evidence inside each finding.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from finwatch.db.repositories import Analysis, Repo
from finwatch.llm.prompts import STAGE_P1, STAGE_P2, load_prompt
from finwatch.llm.router import (
    LAUNCH_MAX_OUTPUT_TOKENS,
    LLMClient,
    LLMResponse,
    extract_json,
)
from finwatch.llm.schemas import P1Output, P2Output

P1_MAX_INPUT_CHARS = 240_000
_CRITICAL_8K_SECTION_FLAGS = {
    "item_1_03": "item_1_03_bankruptcy",
    "item_2_04": "item_2_04_acceleration",
    "item_3_01": "item_3_01_delisting",
    "item_4_02": "item_4_02_non_reliance",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StageError(RuntimeError):
    """Raised when a stage output cannot be parsed/validated (triggers regeneration)."""


def _anchor_p1_evidence(output: Any, sections: dict) -> None:
    """Derive each evidence span server-side by locating the exact snippet in its
    declared canonical section. Offsets returned by the model are ignored — LLMs cannot
    reliably count characters, so a verbatim quote with a miscounted offset would
    otherwise be withheld. Matching is literal (code-point equality against the exact
    stored section text): no regex, fuzzy, whitespace/case folding, or normalization.
    A snippet that is absent, or that occurs more than once (ambiguous), raises — routing
    to the one bounded schema-repair attempt and then failing closed. The trust guarantee
    is preserved: the quote must still be an exact, unique substring of the named SEC
    section; only the pointer is computed, never the text."""
    for finding in output.findings:
        for evidence in finding.evidence:
            section = sections.get(evidence.section_key)
            text = section.get("text") if isinstance(section, dict) else None
            if not text:
                raise ValueError(
                    f"evidence cites section {evidence.section_key!r} not in the filing"
                )
            first = text.find(evidence.snippet)
            if first == -1:
                raise ValueError(
                    f"evidence snippet is not a verbatim substring of section "
                    f"{evidence.section_key!r}"
                )
            if text.find(evidence.snippet, first + 1) != -1:
                raise ValueError(
                    f"evidence snippet is ambiguous (multiple matches) in section "
                    f"{evidence.section_key!r}"
                )
            evidence.char_start = first
            evidence.char_end = first + len(evidence.snippet)


def _run_stage(
    *,
    llm: LLMClient,
    repo: Repo,
    stage: str,
    prompt_stage: str,
    schema_cls: type,
    inputs: dict,
    accession_number: str,
    ticker: str,
    temperature: float,
    model_label: str | None,
    now_fn: Callable[[], str],
) -> tuple[Any, int, LLMResponse]:
    system, version = load_prompt(prompt_stage)
    active_inputs = inputs
    last_error: Exception | None = None
    resp: LLMResponse | None = None
    for attempt in range(2):
        user = json.dumps(active_inputs, ensure_ascii=False, default=str)
        if stage == "P1" and len(user) > P1_MAX_INPUT_CHARS:
            raise StageError(
                f"P1 input exceeds the {P1_MAX_INPUT_CHARS:,}-character launch limit"
            )
        resp = llm.complete(
            system=system,
            user=user,
            temperature=temperature,
            json_mode=True,
            max_tokens=LAUNCH_MAX_OUTPUT_TOKENS,
        )
        try:
            data = extract_json(resp.text)
            output = schema_cls.model_validate(data)
            if getattr(output, "accession_number", accession_number) != accession_number:
                raise ValueError("output accession_number does not match trusted filing metadata")
            if stage == "P1":
                if getattr(output, "ticker", ticker) != ticker:
                    raise ValueError("P1 ticker does not match trusted filing metadata")
                trusted_form = (inputs.get("filing_meta") or {}).get("form_type")
                if trusted_form and getattr(output, "form_type", None) != trusted_form:
                    raise ValueError("P1 form_type does not match trusted filing metadata")
                if str(trusted_form or "").upper().startswith("8-K"):
                    section_keys = set((inputs.get("sections") or {}).keys())
                    for section_key, required_flag in _CRITICAL_8K_SECTION_FLAGS.items():
                        if section_key not in section_keys:
                            continue
                        matching = [
                            finding
                            for finding in output.findings
                            if finding.critical_flag == required_flag
                            and finding.severity == "critical"
                            and any(
                                evidence.section_key == section_key
                                for evidence in finding.evidence
                            )
                        ]
                        if not matching:
                            raise ValueError(
                                f"{section_key} requires a critical evidence-backed "
                                f"{required_flag} finding"
                            )
                # Derive evidence offsets server-side. A snippet that is absent or
                # ambiguous in its declared section raises here and is treated exactly
                # like a schema failure: one bounded repair, then fail closed.
                _anchor_p1_evidence(output, inputs.get("sections") or {})
            break
        except Exception as exc:  # noqa: BLE001 — one bounded schema-repair attempt
            last_error = exc
            if attempt == 1:
                raise StageError(f"{stage} output invalid after schema repair: {exc}") from exc
            # Do NOT echo str(exc) back to the model: pydantic's message names the
            # exact failed constraint, which is an unnecessary hint that adversarial
            # filing content could use to steer a more precise second attempt. The
            # schema itself is sufficient for a good-faith repair; the real error is
            # preserved on the raised StageError (server-side only) via `from exc`.
            active_inputs = {
                **inputs,
                "_schema_repair": {
                    "instruction": (
                        "Your previous response failed validation. Recreate the complete "
                        "JSON output using the exact field names and constraints in this schema."
                    ),
                    "json_schema": schema_cls.model_json_schema(),
                },
            }
    else:  # pragma: no cover - the loop either breaks or raises
        assert last_error is not None
        raise StageError(f"{stage} output invalid: {last_error}") from last_error

    assert resp is not None

    # Persist the CANONICAL validated output, not the raw model JSON. For P1 this is
    # the anchored form (server-derived offsets); downstream V4/canonical read these.
    persisted_json = (
        output.model_dump_json() if stage == "P1" else json.dumps(data, ensure_ascii=False)
    )
    analysis_id = repo.insert_analysis(Analysis(
        accession_number=accession_number, ticker=ticker, stage=stage,
        model=model_label or resp.model, prompt_version=version,
        output_json=persisted_json,
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
        created_at=now_fn(),
    ))
    return output, analysis_id, resp


class P1Extractor:
    """Filing Event Extractor (prompts/P1_extractor.md). Temperature 0.1."""

    def __init__(self, llm: LLMClient, repo: Repo, *, model_label: str | None = None,
                 now_fn: Callable[[], str] = _now_iso) -> None:
        self.llm = llm
        self.repo = repo
        self.model_label = model_label
        self._now_fn = now_fn

    def run(
        self, *, filing_meta: dict, sections: dict,
        risk_factor_diff: dict | None = None,
    ) -> tuple[P1Output, int, LLMResponse]:
        inputs = {
            "filing_meta": filing_meta, "sections": sections,
            "risk_factor_diff": risk_factor_diff,
        }
        return _run_stage(
            llm=self.llm, repo=self.repo, stage="P1", prompt_stage=STAGE_P1,
            schema_cls=P1Output, inputs=inputs,
            accession_number=filing_meta["accession_number"], ticker=filing_meta["ticker"],
            temperature=0.1, model_label=self.model_label, now_fn=self._now_fn,
        )


class P2Explainer:
    """Portfolio Impact Explainer (prompts/P2_impact.md). Temperature 0.2."""

    def __init__(self, llm: LLMClient, repo: Repo, *, model_label: str | None = None,
                 now_fn: Callable[[], str] = _now_iso) -> None:
        self.llm = llm
        self.repo = repo
        self.model_label = model_label
        self._now_fn = now_fn

    def run(
        self, *, extraction: dict, records: list, accession_number: str, ticker: str,
        cross_holding_map: dict | None = None,
    ) -> tuple[P2Output, int, LLMResponse]:
        inputs = {
            "extraction": extraction, "records": records,
            "cross_holding_map": cross_holding_map,
        }
        return _run_stage(
            llm=self.llm, repo=self.repo, stage="P2", prompt_stage=STAGE_P2,
            schema_cls=P2Output, inputs=inputs,
            accession_number=accession_number, ticker=ticker,
            temperature=0.2, model_label=self.model_label, now_fn=self._now_fn,
        )
