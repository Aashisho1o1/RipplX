"""P1/P2 stage runners: build inputs → call the LLM → parse to schema → persist.

Parsing the response into the pydantic schema is the schema-validity gate (a
ValidationError = malformed output → the pipeline regenerates). The trusted
accession/ticker (from the filing being analyzed) are used for the DB keys, never
the model's echoed values.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from finwatch.claims.persist import to_analysis_claims
from finwatch.db.repositories import Analysis, Repo
from finwatch.llm.prompts import STAGE_P1, STAGE_P2, load_prompt
from finwatch.llm.router import LLMClient, LLMResponse, extract_json
from finwatch.llm.schemas import P1Output, P2Output


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class StageError(RuntimeError):
    """Raised when a stage output cannot be parsed/validated (triggers regeneration)."""


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
    user = json.dumps(inputs, ensure_ascii=False, default=str)
    resp = llm.complete(system=system, user=user, temperature=temperature, json_mode=True)
    try:
        data = extract_json(resp.text)
        output = schema_cls.model_validate(data)
    except Exception as exc:  # noqa: BLE001 — any parse/validation failure is a stage failure
        raise StageError(f"{stage} output invalid: {exc}") from exc

    analysis_id = repo.insert_analysis(Analysis(
        accession_number=accession_number, ticker=ticker, stage=stage,
        model=model_label or resp.model, prompt_version=version,
        output_json=json.dumps(data, ensure_ascii=False),
        tokens_in=resp.tokens_in, tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
        created_at=now_fn(),
    ))
    claim_rows = to_analysis_claims(analysis_id, list(getattr(output, "claims", []) or []))
    if claim_rows:
        repo.insert_analysis_claims(claim_rows)
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
        risk_factor_diff: dict | None = None, xbrl_facts: list | None = None,
    ) -> tuple[P1Output, int, LLMResponse]:
        inputs = {
            "filing_meta": filing_meta, "sections": sections,
            "risk_factor_diff": risk_factor_diff, "xbrl_facts": xbrl_facts,
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
