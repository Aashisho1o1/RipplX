"""Bounded, provider-neutral JSON tool loop for filing research."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from finwatch.db.repositories import Analysis, Repo
from finwatch.llm.prompts import STAGE_P1, STAGE_SKEPTIC, load_prompt
from finwatch.llm.router import LAUNCH_MAX_OUTPUT_TOKENS, LLMClient, LLMResponse, extract_json
from finwatch.llm.schemas import Classification, Finding, P1Output
from finwatch.metrics.catalog import MetricId
from finwatch.metrics.envelope import MetricResult, MetricsBundle
from finwatch.preprocess.diff import diff_risk_factors, split_paragraphs
from finwatch.verify.checks import CheckResult
from finwatch.verify.compiler import CompilerIssue, DroppedFinding, compile_draft

_STRICT = ConfigDict(extra="forbid")
_CHANGE_SECTIONS = frozenset({
    "auditor_report", "controls", "legal", "risk_factors", "notes", "mdna",
    "business", "market_risk", "risk_factor_changes", "financials",
})
_SKEPTIC_CODES = Literal[
    "HYPOTHETICAL_AS_ACTUAL", "TEMPORAL_MISMATCH", "ENTITY_MISMATCH",
    "MATERIALITY_OVERREACH", "METRIC_CONTRADICTION", "MISSING_CHANGE_BASIS",
    "LOW_CONFIDENCE",
]


class SearchSectionsArgs(BaseModel):
    model_config = _STRICT
    scope: Literal["current", "prior"] = "current"
    # Optional: with no queries the tool returns the head of each named section, so a
    # model that just wants to read a section by key is served rather than rejected.
    queries: list[str] = Field(default_factory=list, max_length=3)
    section_keys: list[str] = Field(default_factory=list, max_length=8)
    max_results: int = Field(default=5, ge=1, le=5)


class GetChangesArgs(BaseModel):
    model_config = _STRICT
    section_keys: list[str] = Field(min_length=1, max_length=3)
    max_results: int = Field(default=5, ge=1, le=5)


class GetMetricArgs(BaseModel):
    model_config = _STRICT
    metric_ids: list[MetricId] = Field(min_length=1, max_length=3)


class GetAccountingChecksArgs(BaseModel):
    model_config = _STRICT


class CheckDraftArgs(BaseModel):
    model_config = _STRICT
    draft: P1Output


class SearchSectionsAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["search_sections"]
    arguments: SearchSectionsArgs


class GetChangesAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_changes"]
    arguments: GetChangesArgs


class GetMetricAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_metric"]
    arguments: GetMetricArgs


class GetAccountingChecksAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["get_accounting_checks"]
    arguments: GetAccountingChecksArgs


class CheckDraftAction(BaseModel):
    model_config = _STRICT
    action: Literal["tool"]
    tool: Literal["check_draft"]
    arguments: CheckDraftArgs


class SubmitAction(BaseModel):
    model_config = _STRICT
    action: Literal["submit"]
    draft: P1Output


GeneratorAction = Annotated[
    SearchSectionsAction | GetChangesAction | GetMetricAction |
    GetAccountingChecksAction | CheckDraftAction | SubmitAction,
    Field(union_mode="left_to_right"),
]
_GENERATOR_ADAPTER = TypeAdapter(GeneratorAction)

_ARG_STRAY_FIELDS = ("accession_number", "ticker", "form_type", "cik")
_ARG_SINGULAR_ALIASES = (
    ("query", "queries"),
    ("section_key", "section_keys"),
    ("metric_id", "metric_ids"),
)
# Per-tool list caps, matching the *Args schemas. A model that over-supplies (e.g. six
# search queries) is truncated to the cap rather than failing the whole run.
_ARG_LIST_CAPS = {
    "search_sections": {"queries": 3, "section_keys": 8},
    "get_changes": {"section_keys": 3},
    "get_metric": {"metric_ids": 3},
}


def _safe_validation_hint(exc: Exception) -> str:
    """Turn a schema failure into a controlled hint the model can act on.

    Feeds back ONLY our own schema rule text (field path + message), never the model's
    rejected input: ``include_input=False`` drops every echoed value, so untrusted model
    output can never round-trip through the prompt. Literal-union discriminator noise
    ("Input should be 'tool'") is dropped so the real violation — e.g. a 50-word snippet
    cap — is what the model sees. This is what lets a capable model self-correct a
    fixable mistake instead of dying with a blind ``INVALID_ACTION``.
    """
    if not isinstance(exc, ValidationError):
        return "INVALID_ACTION"
    parts: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = ".".join(
            str(piece) for piece in err.get("loc", ()) if not str(piece).endswith("Action")
        )
        message = str(err.get("msg", "")).strip()
        if not message:
            continue
        item = f"{loc}: {message}" if loc else message
        if item not in parts:
            parts.append(item)
    useful = [item for item in parts if "Input should be" not in item] or parts
    return "INVALID_ACTION: " + "; ".join(useful[:4]) if useful else "INVALID_ACTION"


def _normalize_tool_arguments(raw: object) -> object:
    """Map benign tool-argument variants onto the canonical schema before validation.

    Tool arguments are navigation — which section, change, or metric to inspect — never
    verified output, so accepting the shapes real models actually emit costs no trust
    guarantee. Three variants are common across providers: echoing identifiers the
    harness already knows (accession/ticker/form), using a singular key where the schema
    wants a list, and over-supplying list items past the cap. We drop the first, alias
    the second, and truncate the third; everything else still fails validation exactly as
    before. Mutates and returns the same dict.
    """
    if not isinstance(raw, dict):
        return raw
    args = raw.get("arguments")
    if not isinstance(args, dict):
        return raw
    for stray in _ARG_STRAY_FIELDS:
        args.pop(stray, None)
    for singular, plural in _ARG_SINGULAR_ALIASES:
        if singular in args and plural not in args:
            value = args.pop(singular)
            args[plural] = value if isinstance(value, list) else [value]
    for field_name, cap in _ARG_LIST_CAPS.get(raw.get("tool"), {}).items():
        value = args.get(field_name)
        if isinstance(value, list) and len(value) > cap:
            args[field_name] = value[:cap]
    return raw


class SkepticObligation(BaseModel):
    model_config = _STRICT
    finding_id: Literal["f1", "f2", "f3"]
    code: _SKEPTIC_CODES


class SkepticDoneAction(BaseModel):
    model_config = _STRICT
    action: Literal["done"]
    obligations: list[SkepticObligation] = Field(default_factory=list, max_length=3)


SkepticAction = Annotated[
    SearchSectionsAction | GetChangesAction | GetMetricAction |
    GetAccountingChecksAction | SkepticDoneAction,
    Field(union_mode="left_to_right"),
]
_SKEPTIC_ADAPTER = TypeAdapter(SkepticAction)


class ToolTrace(BaseModel):
    model_config = _STRICT
    call_id: str
    tool: str
    arguments: dict
    result_sha256: str


class AgendaItem(BaseModel):
    model_config = _STRICT
    name: str
    status: Literal["open", "discharged", "failed", "not_applicable"]


class HarnessTrace(BaseModel):
    model_config = _STRICT
    schema_version: Literal["harness.v2"] = "harness.v2"
    p1_analysis_id: int | None = None
    trace_analysis_id: int | None = None
    p1_output_sha256: str | None = None
    research_outcome: Literal["published", "partial", "metrics_only", "withheld"]
    publication_outcome: Literal["published", "partial", "metrics_only", "withheld"] | None = None
    research_terminal_reason: Literal[
        "verified", "budget_exhausted", "compile_failed",
        "skeptic_blocked", "skeptic_incomplete", "provider_failed",
        "malformed_action_breakdown",
    ]
    terminal_reason: Literal[
        "verified", "budget_exhausted", "compile_failed", "skeptic_blocked",
        "skeptic_incomplete", "provider_failed", "malformed_action_breakdown",
        "verification_failed", "verification_incomplete",
    ] | None = None
    verification_verdict: Literal["PASS", "PASS_WITH_WARNINGS", "FAIL"] | None = None
    filing_snapshot: dict
    verification_snapshot: list[dict] = Field(default_factory=list)
    publication_snapshot: dict = Field(default_factory=dict)
    generator_model: str
    skeptic_model: str
    generator_prompt_version: str
    skeptic_prompt_version: str
    generator_turns: int
    generator_tool_calls: int
    skeptic_turns: int
    skeptic_tool_calls: int
    tool_budget: int
    tool_calls: list[ToolTrace] = Field(default_factory=list)
    repair_used: bool
    agenda: list[AgendaItem]
    metric_results: list[MetricResult] = Field(default_factory=list)
    published_finding_ids: list[str] = Field(default_factory=list)
    dropped_findings: list[DroppedFinding] = Field(default_factory=list)


@dataclass
class HarnessResult:
    output: P1Output
    analysis_id: int
    trace_analysis_id: int
    response: LLMResponse
    trace: HarnessTrace


class HarnessError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class _Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    saw_cost: bool = False
    model: str = ""

    def add(self, response: LLMResponse) -> None:
        self.tokens_in += response.tokens_in
        self.tokens_out += response.tokens_out
        self.model = response.model
        if response.cost_usd is not None:
            self.cost_usd += response.cost_usd
            self.saw_cost = True


@dataclass
class _Counters:
    generator_turns: int = 0
    generator_tool_calls: int = 0
    skeptic_turns: int = 0
    skeptic_tool_calls: int = 0


@dataclass
class _GeneratorLoopResult:
    output: P1Output | None
    dropped: list[DroppedFinding] = field(default_factory=list)
    terminal_reason: str = "verified"
    submitted: bool = False


@dataclass
class _SkepticPassResult:
    obligations: list[SkepticObligation] = field(default_factory=list)
    completed: bool = False


@dataclass
class ToolContext:
    filing_meta: dict
    sections: dict[str, dict]
    prior_sections: dict[str, dict]
    metrics: MetricsBundle
    data_quality: list[CheckResult]
    change_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    change_catalog: dict[str, list[dict]] = field(default_factory=dict)
    tool_cache: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Freeze the complete deterministic diff before the model can act."""
        for key in sorted(_CHANGE_SECTIONS.intersection(self.sections)):
            current = self.sections[key].get("text", "")
            prior_row = self.prior_sections.get(key)
            rows: list[dict] = []
            current_ranges: list[tuple[int, int]] = []
            if prior_row:
                diff = diff_risk_factors(prior_row.get("text", ""), current)
                for row in diff.added:
                    rows.append({
                        "kind": "added", "section_key": key,
                        "char_start": row.char_start, "char_end": row.char_end,
                        "snippet": row.text, "similarity": None,
                    })
                    current_ranges.append((row.char_start, row.char_end))
                for row in diff.modified:
                    rows.append({
                        "kind": "modified", "section_key": key,
                        "char_start": row.current.char_start,
                        "char_end": row.current.char_end,
                        "snippet": row.current.text, "similarity": row.similarity,
                    })
                    current_ranges.append((row.current.char_start, row.current.char_end))
                rows.extend({
                    "kind": "removed", "section_key": key,
                    "char_start": row.char_start, "char_end": row.char_end,
                    "snippet": row.text,
                } for row in diff.removed)
            elif self.has_prior_comparable:
                for row in split_paragraphs(current):
                    rows.append({
                        "kind": "added", "section_key": key,
                        "char_start": row.char_start, "char_end": row.char_end,
                        "snippet": row.text, "similarity": None,
                    })
                    current_ranges.append((row.char_start, row.char_end))
            self.change_catalog[key] = rows
            self.change_ranges[key] = current_ranges

    @property
    def has_prior_comparable(self) -> bool:
        explicit = self.filing_meta.get("has_prior_comparable")
        return bool(self.prior_sections) if explicit is None else bool(explicit)

    def _section_rows(self, scope: str) -> dict[str, dict]:
        return self.sections if scope == "current" else self.prior_sections

    def search_sections(self, args: SearchSectionsArgs) -> dict:
        source = self._section_rows(args.scope)
        keys = args.section_keys or list(source)
        results: list[dict] = []
        if not any(query.strip() for query in args.queries):
            # No query terms: return the head of each named section (a plain read).
            for key in keys:
                row = source.get(key)
                text = row.get("text", "") if isinstance(row, dict) else ""
                if not text:
                    continue
                right = min(len(text), 460)
                accession = (
                    self.filing_meta["accession_number"]
                    if args.scope == "current"
                    else row.get("accession_number")
                )
                results.append({
                    "scope": args.scope,
                    "accession_number": accession,
                    "section_key": key,
                    "char_start": 0,
                    "char_end": right,
                    "snippet": text[:right],
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                })
                if len(results) >= args.max_results:
                    return {"results": results}
            return {"results": results}
        for query in args.queries:
            needle = query.strip().lower()
            if not needle:
                continue
            for key in keys:
                row = source.get(key)
                text = row.get("text", "") if isinstance(row, dict) else ""
                start = text.lower().find(needle)
                if start < 0:
                    continue
                left = max(0, start - 180)
                right = min(len(text), start + len(needle) + 280)
                accession = (
                    self.filing_meta["accession_number"]
                    if args.scope == "current"
                    else row.get("accession_number")
                )
                excerpt = text[left:right]
                results.append({
                    "scope": args.scope,
                    "accession_number": accession,
                    "section_key": key,
                    "char_start": left,
                    "char_end": right,
                    "snippet": excerpt,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                })
                if len(results) >= args.max_results:
                    return {"results": results}
        return {"results": results}

    def get_changes(self, args: GetChangesArgs) -> dict:
        changes = [
            row
            for key in args.section_keys
            for row in self.change_catalog.get(key, [])
        ]
        return {"changes": changes[:args.max_results]}

    def get_metric(self, args: GetMetricArgs) -> dict:
        return {"metrics": [
            self.metrics.get(metric.value).model_dump(mode="json")
            for metric in args.metric_ids
            if self.metrics.get(metric.value) is not None
        ]}

    def get_accounting_checks(self) -> dict:
        return {"checks": [row.model_dump(mode="json") for row in self.data_quality]}


class FilingResearchHarness:
    MAX_GENERATOR_TURNS = 8
    MAX_TOOL_CALLS = 6
    MAX_SKEPTIC_TOOL_CALLS = 2

    def __init__(
        self,
        generator: LLMClient,
        repo: Repo,
        *,
        skeptic: LLMClient | None = None,
        generator_model: str | None = None,
        skeptic_model: str | None = None,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.generator = generator
        self.skeptic = skeptic or generator
        self.repo = repo
        self.generator_model = generator_model
        self.skeptic_model = skeptic_model or generator_model
        self._now_fn = now_fn or (lambda: datetime.now(UTC).isoformat())

    @staticmethod
    def _empty_output(meta: dict) -> P1Output:
        return P1Output(
            accession_number=meta["accession_number"],
            ticker=meta["ticker"],
            form_type=meta["form_type"],
            classification=Classification(overall_severity="routine"),
            findings=[], extraction_confidence="medium", gaps=[],
        )

    @staticmethod
    def _catalog(sections: dict[str, dict]) -> list[dict]:
        return [
            {"section_key": key, "characters": len(row.get("text", "")),
             "text_sha256": hashlib.sha256(row.get("text", "").encode()).hexdigest()}
            for key, row in sections.items()
        ]

    def _call(
        self, client: LLMClient, usage: _Usage, *, system: str, state: dict,
        temperature: float,
    ) -> dict:
        try:
            response = client.complete(
                system=system,
                user=json.dumps(state, ensure_ascii=False, default=str),
                temperature=temperature,
                json_mode=True,
                max_tokens=LAUNCH_MAX_OUTPUT_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - provider text never leaves this boundary
            raise HarnessError("provider_failed") from exc
        usage.add(response)
        try:
            return extract_json(response.text)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("invalid JSON action") from exc

    def _execute_tool(
        self,
        action,
        context: ToolContext,
        *,
        check_draft_used: bool,
    ) -> tuple[dict, bool]:
        cache_key = json.dumps(
            {"tool": action.tool, "arguments": action.arguments.model_dump(mode="json")},
            sort_keys=True,
        )
        cached = context.tool_cache.get(cache_key)
        if cached is not None:
            return cached, check_draft_used or action.tool == "check_draft"
        if action.tool == "search_sections":
            result = context.search_sections(action.arguments)
        elif action.tool == "get_changes":
            result = context.get_changes(action.arguments)
        elif action.tool == "get_metric":
            result = context.get_metric(action.arguments)
        elif action.tool == "get_accounting_checks":
            result = context.get_accounting_checks()
        elif check_draft_used:
            return {"error": "CHECK_DRAFT_ALREADY_USED"}, check_draft_used
        else:
            compiled = compile_draft(
                action.arguments.draft,
                trusted_meta=context.filing_meta,
                sections=context.sections,
                metrics=context.metrics,
                change_ranges=context.change_ranges,
                has_prior_comparable=context.has_prior_comparable,
            )
            result = {
                "issues": [issue.model_dump(mode="json") for issue in compiled.issues],
                "run_errors": compiled.run_errors,
            }
        context.tool_cache[cache_key] = result
        return result, check_draft_used or action.tool == "check_draft"

    @staticmethod
    def _tool_observation(call_id: str, tool: str, result: dict) -> dict:
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if len(payload) > 12_000:
            result = {"error": "TOOL_RESULT_TOO_LARGE",
                      "result_sha256": hashlib.sha256(payload.encode()).hexdigest()}
        return {"call_id": call_id, "tool": tool, "result": result}

    @staticmethod
    def _trace_arguments(action) -> dict:
        arguments = action.arguments.model_dump(mode="json")
        if action.tool != "check_draft":
            return arguments
        draft = arguments["draft"]
        encoded = json.dumps(draft, sort_keys=True, separators=(",", ":")).encode()
        return {
            "draft_sha256": hashlib.sha256(encoded).hexdigest(),
            "finding_ids": [row["finding_id"] for row in draft["findings"]],
        }

    def _generator_loop(
        self,
        *,
        system: str,
        context: ToolContext,
        usage: _Usage,
        state: dict,
        trace_calls: list[ToolTrace],
        counters: _Counters,
        repair_state: dict[str, bool],
        optional_repair: bool = False,
    ) -> _GeneratorLoopResult:
        invalid_actions = 0
        check_draft_used = state.get("check_draft_used", False)
        submit_only = False
        fallback_candidate: P1Output | None = None
        while counters.generator_turns < self.MAX_GENERATOR_TURNS:
            counters.generator_turns += 1
            state["budget"] = {
                "generator_turns_remaining": max(
                    0, self.MAX_GENERATOR_TURNS - counters.generator_turns
                ),
                "tool_calls_remaining": max(
                    0, self.MAX_TOOL_CALLS - counters.generator_tool_calls
                ),
                "repair_available": not repair_state["used"],
            }
            try:
                raw = self._call(
                    self.generator, usage, system=system, state=state, temperature=0.1
                )
                action = _GENERATOR_ADAPTER.validate_python(_normalize_tool_arguments(raw))
            except HarnessError:
                raise
            except Exception as exc:
                if submit_only:
                    break
                invalid_actions += 1
                state["last_error"] = _safe_validation_hint(exc)
                if invalid_actions >= 2:
                    if optional_repair:
                        return _GeneratorLoopResult(
                            output=None,
                            terminal_reason="malformed_action_breakdown",
                            submitted=False,
                        )
                    raise HarnessError("malformed_action_breakdown") from exc
                continue

            if isinstance(action, SubmitAction):
                compiled = compile_draft(
                    action.draft,
                    trusted_meta=context.filing_meta,
                    sections=context.sections,
                    metrics=context.metrics,
                    change_ranges=context.change_ranges,
                    has_prior_comparable=context.has_prior_comparable,
                )
                if (compiled.issues or compiled.run_errors) and not repair_state["used"]:
                    repair_state["used"] = True
                    fallback_candidate = compiled.output
                    state["repair_targets"] = {
                        finding_id: list(dict.fromkeys(
                            issue.code for issue in compiled.issues
                            if issue.finding_id == finding_id
                        ))
                        for finding_id in {
                            issue.finding_id for issue in compiled.issues
                            if issue.finding_id is not None
                        }
                    }
                    state["current_draft"] = compiled.output.model_dump(mode="json")
                    state["compiler_errors"] = [
                        issue.model_dump(mode="json") for issue in compiled.issues
                    ] + [{"code": code} for code in compiled.run_errors]
                    state["instruction"] = (
                        "Use the single repair now. Return a complete corrected submit action; "
                        "drop a finding when evidence cannot satisfy the compiler."
                    )
                    continue
                final = compile_draft(
                    compiled.output,
                    trusted_meta=context.filing_meta,
                    sections=context.sections,
                    metrics=context.metrics,
                    change_ranges=context.change_ranges,
                    has_prior_comparable=context.has_prior_comparable,
                    prune=True,
                )
                if final.run_errors:
                    # A run error raised by the OPTIONAL repair draft describes that
                    # discarded draft, not the filing: the baseline already satisfied
                    # FORM_SCOPE/CRITICAL_COVERAGE before the repair was offered.
                    # Withholding here would discard a compiler-passing baseline for a
                    # cause the published output does not carry, so mirror the sibling
                    # optional-repair guards and let run() fall back to the baseline.
                    if optional_repair:
                        return _GeneratorLoopResult(
                            output=None,
                            terminal_reason="repair_compile_failed",
                            submitted=False,
                        )
                    raise HarnessError(final.run_errors[0].lower())
                dropped = list(final.dropped)
                surviving = {finding.finding_id for finding in final.output.findings}
                for finding_id, codes in state.get("repair_targets", {}).items():
                    if finding_id not in surviving and not any(
                        row.finding_id == finding_id for row in dropped
                    ):
                        dropped.append(DroppedFinding(
                            finding_id=finding_id, error_codes=codes
                        ))
                state.pop("repair_targets", None)
                return _GeneratorLoopResult(
                    output=final.output,
                    dropped=dropped,
                    terminal_reason="verified",
                    submitted=True,
                )

            if submit_only:
                break
            if counters.generator_tool_calls >= self.MAX_TOOL_CALLS:
                state["last_error"] = "TOOL_BUDGET_EXHAUSTED_SUBMIT_NOW"
                state["instruction"] = (
                    "Tool budget exhausted. The next and only accepted action is submit."
                )
                submit_only = True
                if counters.generator_turns < self.MAX_GENERATOR_TURNS:
                    continue
                break
            counters.generator_tool_calls += 1
            result, check_draft_used = self._execute_tool(
                action, context, check_draft_used=check_draft_used
            )
            state["check_draft_used"] = check_draft_used
            call_id = f"t{len(trace_calls) + 1}"
            observation = self._tool_observation(call_id, action.tool, result)
            state.setdefault("observations", []).append(observation)
            result_json = json.dumps(observation["result"], sort_keys=True)
            trace_calls.append(ToolTrace(
                call_id=call_id,
                tool=action.tool,
                arguments=self._trace_arguments(action),
                result_sha256=hashlib.sha256(result_json.encode()).hexdigest(),
            ))

        if optional_repair:
            return _GeneratorLoopResult(
                output=None,
                terminal_reason="budget_exhausted",
                submitted=False,
            )
        candidate = fallback_candidate or self._empty_output(context.filing_meta)
        final = compile_draft(
            candidate,
            trusted_meta=context.filing_meta,
            sections=context.sections,
            metrics=context.metrics,
            change_ranges=context.change_ranges,
            has_prior_comparable=context.has_prior_comparable,
            prune=True,
        )
        if final.run_errors:
            raise HarnessError(final.run_errors[0].lower())
        return _GeneratorLoopResult(
            output=final.output,
            dropped=final.dropped,
            terminal_reason="budget_exhausted",
            submitted=fallback_candidate is not None,
        )

    def _skeptic_pass(
        self,
        *,
        system: str,
        context: ToolContext,
        output: P1Output,
        trace_calls: list[ToolTrace],
        usage: _Usage,
        counters: _Counters,
        observations: list[dict],
    ) -> _SkepticPassResult:
        if not output.findings:
            return _SkepticPassResult()
        invalid_actions = 0
        state = {
            "filing_meta": context.filing_meta,
            "draft": output.model_dump(mode="json"),
            "validated_observations": observations,
            "rules": {"may_only_add_obligations_or_use_read_tools": True},
        }
        while True:
            counters.skeptic_turns += 1
            state["budget"] = {
                "tool_calls_remaining": max(
                    0, self.MAX_SKEPTIC_TOOL_CALLS - counters.skeptic_tool_calls
                )
            }
            try:
                raw = self._call(
                    self.skeptic, usage, system=system, state=state, temperature=0.0
                )
                action = _SKEPTIC_ADAPTER.validate_python(_normalize_tool_arguments(raw))
            except HarnessError:
                raise
            except Exception:
                invalid_actions += 1
                state["last_error"] = "INVALID_ACTION"
                if invalid_actions >= 2:
                    return _SkepticPassResult(completed=False)
                continue
            if isinstance(action, SkepticDoneAction):
                valid_ids = {finding.finding_id for finding in output.findings}
                if any(row.finding_id not in valid_ids for row in action.obligations):
                    invalid_actions += 1
                    state["last_error"] = "UNKNOWN_FINDING_ID"
                    if invalid_actions >= 2:
                        return _SkepticPassResult(completed=False)
                    continue
                return _SkepticPassResult(
                    obligations=action.obligations,
                    completed=True,
                )
            if counters.skeptic_tool_calls >= self.MAX_SKEPTIC_TOOL_CALLS:
                state["last_error"] = "SKEPTIC_TOOL_BUDGET_EXHAUSTED_RETURN_DONE"
                return _SkepticPassResult(completed=False)
            counters.skeptic_tool_calls += 1
            result, _ = self._execute_tool(action, context, check_draft_used=True)
            call_id = f"t{len(trace_calls) + 1}"
            observation = self._tool_observation(call_id, action.tool, result)
            state.setdefault("observations", []).append(observation)
            trace_calls.append(ToolTrace(
                call_id=call_id, tool=action.tool,
                arguments=self._trace_arguments(action),
                result_sha256=hashlib.sha256(
                    json.dumps(observation["result"], sort_keys=True).encode()
                ).hexdigest(),
            ))

    def _persist_trace(
        self,
        *,
        meta: dict,
        trace: HarnessTrace,
        usage: _Usage,
        prompt_version: str,
    ) -> None:
        self.repo.insert_analysis(Analysis(
            accession_number=meta["accession_number"], ticker=meta["ticker"],
            stage="P1_TRACE", model=self.generator_model or usage.model or "unknown",
            prompt_version=prompt_version, output_json=trace.model_dump_json(),
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=usage.cost_usd if usage.saw_cost else None,
            created_at=self._now_fn(),
        ))

    @staticmethod
    def _merge_drops(rows: list[DroppedFinding]) -> list[DroppedFinding]:
        merged: dict[str, list[str]] = {}
        for row in rows:
            codes = merged.setdefault(row.finding_id, [])
            codes.extend(code for code in row.error_codes if code not in codes)
        return [
            DroppedFinding(finding_id=finding_id, error_codes=codes)
            for finding_id, codes in merged.items()
        ]

    @staticmethod
    def _objection_issues(
        obligations: list[SkepticObligation],
    ) -> list[CompilerIssue]:
        return [
            CompilerIssue(code=row.code, finding_id=row.finding_id)
            for row in obligations
        ]

    @staticmethod
    def _finding_signature(finding: Finding) -> tuple[str, frozenset[tuple[str, str]]]:
        """Identify a finding by its authored claim AND the content it cites.

        Keying on the label alone lets a repair launder an objection by renumbering:
        the objected text publishes while the trace records a drop that never happened.
        Keying on the evidence alone is the opposite error — a Skeptic objection such as
        MATERIALITY_OVERREACH is about the authored claim, so rewriting the headline
        over the same quote IS the repair, and treating it as an un-repaired
        resubmission pruned the corrected finding (withholding the whole filing when it
        was a required 8-K critical finding).

        The signature is therefore the pair. An unchanged resubmission still carries its
        objection; a genuinely rewritten claim discharges it and is re-reviewed by the
        mandatory second Skeptic pass.
        """
        return (
            finding.headline.strip(),
            frozenset(
                (evidence.section_key, evidence.snippet) for evidence in finding.evidence
            ),
        )

    @staticmethod
    def _filing_snapshot(meta: dict) -> dict:
        return {
            "accession": meta["accession_number"],
            "ticker": meta["ticker"],
            "form": meta.get("form_type"),
            "filed_at": meta.get("filed_at"),
            "source_sha256": meta.get("source_sha256") or meta.get("raw_sha256"),
        }

    def _prune_objections(
        self,
        output: P1Output,
        obligations: list[SkepticObligation],
        context: ToolContext,
    ) -> tuple[P1Output, list[DroppedFinding]]:
        final = compile_draft(
            output,
            trusted_meta=context.filing_meta,
            sections=context.sections,
            metrics=context.metrics,
            change_ranges=context.change_ranges,
            has_prior_comparable=context.has_prior_comparable,
            prune=True,
            extra_issues=self._objection_issues(obligations),
        )
        if final.run_errors:
            raise HarnessError(final.run_errors[0].lower())
        return final.output, final.dropped

    def run(
        self,
        *,
        filing_meta: dict,
        sections: dict[str, dict],
        prior_sections: dict[str, dict],
        metrics: MetricsBundle,
        data_quality: list[CheckResult],
    ) -> HarnessResult:
        generator_system, generator_version = load_prompt(STAGE_P1)
        skeptic_system, skeptic_version = load_prompt(STAGE_SKEPTIC)
        context = ToolContext(
            filing_meta=filing_meta, sections=sections, prior_sections=prior_sections,
            metrics=metrics, data_quality=data_quality,
        )
        trace_calls: list[ToolTrace] = []
        usage = _Usage()
        counters = _Counters()
        repair_state = {"used": False}
        dropped: list[DroppedFinding] = []
        terminal = "verified"
        skeptic_status: Literal["discharged", "failed", "not_applicable"] = (
            "not_applicable"
        )
        state = {
            "filing_meta": filing_meta,
            "section_catalog": self._catalog(sections),
            "prior_section_catalog": self._catalog(prior_sections),
            "metric_catalog": [metric.value for metric in MetricId],
            "agenda": [
                {"name": name, "status": "open"}
                for name in ("FORM_SCOPE", "CRITICAL_COVERAGE", "SKEPTIC_REVIEW")
            ],
            "observations": [],
        }
        try:
            generated = self._generator_loop(
                system=generator_system, context=context, usage=usage, state=state,
                trace_calls=trace_calls, counters=counters, repair_state=repair_state,
            )
            if generated.output is None:
                raise HarnessError("malformed_action_breakdown")
            output = generated.output
            dropped.extend(generated.dropped)
            terminal = generated.terminal_reason
            baseline = output.model_copy(deep=True)

            if baseline.findings:
                first_review = self._skeptic_pass(
                    system=skeptic_system,
                    context=context,
                    output=baseline,
                    trace_calls=trace_calls,
                    usage=usage,
                    counters=counters,
                    observations=state.get("observations", []),
                )
                if not first_review.completed:
                    skeptic_status = "failed"
                    terminal = "skeptic_incomplete"
                else:
                    skeptic_status = "discharged"
                    original_objections = first_review.obligations
                    if original_objections and not repair_state["used"]:
                        repair_state["used"] = True
                        state["current_draft"] = baseline.model_dump(mode="json")
                        state["compiler_errors"] = [
                            row.model_dump(mode="json") for row in original_objections
                        ]
                        repair_targets: dict[str, list[str]] = {}
                        for row in original_objections:
                            codes = repair_targets.setdefault(row.finding_id, [])
                            if row.code not in codes:
                                codes.append(row.code)
                        state["repair_targets"] = repair_targets
                        state["instruction"] = (
                            "The Skeptic added finding-local obligations. Submit one complete "
                            "repair; drop any finding you cannot support."
                        )
                        repaired = self._generator_loop(
                            system=generator_system,
                            context=context,
                            usage=usage,
                            state=state,
                            trace_calls=trace_calls,
                            counters=counters,
                            repair_state=repair_state,
                            optional_repair=True,
                        )
                        if repaired.output is None:
                            output, objection_drops = self._prune_objections(
                                baseline, original_objections, context
                            )
                            dropped.extend(objection_drops)
                            terminal = "skeptic_blocked"
                        else:
                            output = repaired.output
                            dropped.extend(repaired.dropped)
                            # Reconcile the repair against the baseline it replaced.
                            # Nothing else binds the two: without this, a repair that
                            # omits a clean finding loses it silently, and one that
                            # renumbers an objected finding publishes the objected
                            # content while recording a drop that did not happen.
                            baseline_by_id = {
                                finding.finding_id: finding
                                for finding in baseline.findings
                            }
                            objected_codes: dict[
                                tuple[str, frozenset[tuple[str, str]]], list[str]
                            ] = {}
                            for row in original_objections:
                                target = baseline_by_id.get(row.finding_id)
                                if target is None:
                                    continue
                                codes = objected_codes.setdefault(
                                    self._finding_signature(target), []
                                )
                                if row.code not in codes:
                                    codes.append(row.code)
                            # Objected content that survived under ANY label still
                            # carries its objection.
                            carried = [
                                SkepticObligation(
                                    finding_id=finding.finding_id, code=code
                                )
                                for finding in output.findings
                                for code in objected_codes.get(
                                    self._finding_signature(finding), []
                                )
                            ]
                            objection_caused_drop = False
                            if carried:
                                output, carried_drops = self._prune_objections(
                                    output, carried, context
                                )
                                dropped.extend(carried_drops)
                                objection_caused_drop = bool(carried_drops)
                            surviving_ids = {
                                finding.finding_id for finding in output.findings
                            }
                            surviving_sigs = {
                                self._finding_signature(finding)
                                for finding in output.findings
                            }
                            recorded = {row.finding_id for row in dropped}
                            # An objected finding the repair actually removed is a
                            # discharged objection; record why it is gone. A finding
                            # still published under its own id was repaired with new
                            # evidence and is not a drop.
                            for row in original_objections:
                                if row.finding_id in surviving_ids:
                                    continue
                                # The objected finding is gone, so the objection took
                                # effect regardless of which layer recorded the drop —
                                # the generator loop already records repair_targets.
                                objection_caused_drop = True
                                if row.finding_id in recorded:
                                    continue
                                dropped.append(DroppedFinding(
                                    finding_id=row.finding_id,
                                    error_codes=[row.code],
                                ))
                                recorded.add(row.finding_id)
                            # A clean, unobjected baseline finding the repair simply
                            # dropped must be recorded rather than silently lost.
                            for finding in baseline.findings:
                                if finding.finding_id in surviving_ids:
                                    continue
                                if finding.finding_id in recorded:
                                    continue
                                if self._finding_signature(finding) in surviving_sigs:
                                    continue
                                dropped.append(DroppedFinding(
                                    finding_id=finding.finding_id,
                                    error_codes=["REPAIR_OMITTED"],
                                ))
                                recorded.add(finding.finding_id)
                            # Only claim the Skeptic blocked something when it actually
                            # did. A repair that addressed every objection leaves the
                            # run verified; reporting skeptic_blocked there would put a
                            # drop that never happened into the signed certificate.
                            if objection_caused_drop:
                                terminal = "skeptic_blocked"
                            if output.findings:
                                second_review = self._skeptic_pass(
                                    system=skeptic_system,
                                    context=context,
                                    output=output,
                                    trace_calls=trace_calls,
                                    usage=usage,
                                    counters=counters,
                                    observations=state.get("observations", []),
                                )
                                if not second_review.completed:
                                    skeptic_status = "failed"
                                    output, unresolved_drops = self._prune_objections(
                                        output, original_objections, context
                                    )
                                    dropped.extend(unresolved_drops)
                                    terminal = "skeptic_incomplete"
                                elif second_review.obligations:
                                    output, objection_drops = self._prune_objections(
                                        output, second_review.obligations, context
                                    )
                                    dropped.extend(objection_drops)
                                    terminal = "skeptic_blocked"
                    elif original_objections:
                        output, objection_drops = self._prune_objections(
                            baseline, original_objections, context
                        )
                        dropped.extend(objection_drops)
                        terminal = "skeptic_blocked"
        except HarnessError as exc:
            reason = exc.reason
            mapped = (
                "provider_failed" if reason == "provider_failed"
                else "malformed_action_breakdown" if reason == "malformed_action_breakdown"
                else "compile_failed"
            )
            trace = HarnessTrace(
                research_outcome="withheld",
                research_terminal_reason=mapped,
                filing_snapshot=self._filing_snapshot(filing_meta),
                generator_model=self.generator_model or usage.model or "unknown",
                skeptic_model=self.skeptic_model or usage.model or "unknown",
                generator_prompt_version=generator_version,
                skeptic_prompt_version=skeptic_version,
                generator_turns=counters.generator_turns,
                generator_tool_calls=counters.generator_tool_calls,
                skeptic_turns=counters.skeptic_turns,
                skeptic_tool_calls=counters.skeptic_tool_calls,
                tool_budget=self.MAX_TOOL_CALLS + self.MAX_SKEPTIC_TOOL_CALLS,
                tool_calls=trace_calls, repair_used=repair_state["used"],
                metric_results=list(metrics.results.values()),
                agenda=[
                    AgendaItem(
                        name="FORM_SCOPE",
                        status="failed" if reason == "form_scope" else "open",
                    ),
                    AgendaItem(
                        name="CRITICAL_COVERAGE",
                        status="failed" if reason == "critical_coverage" else "open",
                    ),
                    AgendaItem(
                        name="SKEPTIC_REVIEW",
                        status="failed" if skeptic_status == "failed" else "not_applicable",
                    ),
                ],
                dropped_findings=dropped,
            )
            self._persist_trace(
                meta=filing_meta, trace=trace, usage=usage,
                prompt_version=f"{generator_version}+{skeptic_version}",
            )
            raise

        dropped = self._merge_drops(dropped)
        published_ids = [finding.finding_id for finding in output.findings]
        research_outcome = (
            "metrics_only" if not published_ids else "partial" if dropped else "published"
        )
        trace = HarnessTrace(
            research_outcome=research_outcome,
            research_terminal_reason=terminal,
            filing_snapshot=self._filing_snapshot(filing_meta),
            generator_model=self.generator_model or usage.model or "unknown",
            skeptic_model=self.skeptic_model or usage.model or "unknown",
            generator_prompt_version=generator_version,
            skeptic_prompt_version=skeptic_version,
            generator_turns=counters.generator_turns,
            generator_tool_calls=counters.generator_tool_calls,
            skeptic_turns=counters.skeptic_turns,
            skeptic_tool_calls=counters.skeptic_tool_calls,
            tool_budget=self.MAX_TOOL_CALLS + self.MAX_SKEPTIC_TOOL_CALLS,
            tool_calls=trace_calls, repair_used=repair_state["used"],
            metric_results=list(metrics.results.values()),
            agenda=[
                AgendaItem(name="FORM_SCOPE", status="discharged"),
                AgendaItem(name="CRITICAL_COVERAGE", status="discharged"),
                AgendaItem(name="SKEPTIC_REVIEW", status=skeptic_status),
            ],
            dropped_findings=dropped,
        )
        p1_analysis = Analysis(
            accession_number=filing_meta["accession_number"], ticker=filing_meta["ticker"],
            stage="P1", model=self.generator_model or usage.model or "unknown",
            prompt_version=generator_version, output_json=output.model_dump_json(),
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=usage.cost_usd if usage.saw_cost else None,
            created_at=self._now_fn(),
        )

        def trace_factory(p1_id: int, p1_sha256: str) -> Analysis:
            linked = trace.model_copy(update={
                "p1_analysis_id": p1_id,
                "p1_output_sha256": p1_sha256,
            })
            return Analysis(
                accession_number=filing_meta["accession_number"],
                ticker=filing_meta["ticker"],
                stage="P1_TRACE",
                model=self.generator_model or usage.model or "unknown",
                prompt_version=f"{generator_version}+{skeptic_version}",
                output_json=linked.model_dump_json(),
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_usd=usage.cost_usd if usage.saw_cost else None,
                created_at=self._now_fn(),
            )

        analysis_id, trace_analysis_id, p1_sha256 = self.repo.insert_p1_with_trace(
            p1_analysis, trace_factory
        )
        trace = trace.model_copy(update={
            "p1_analysis_id": analysis_id,
            "trace_analysis_id": trace_analysis_id,
            "p1_output_sha256": p1_sha256,
        })
        response = LLMResponse(
            text="", model=self.generator_model or usage.model or "unknown",
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=usage.cost_usd if usage.saw_cost else None,
        )
        return HarnessResult(output, analysis_id, trace_analysis_id, response, trace)
