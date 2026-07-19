"""Bounded, provider-neutral JSON tool loop for filing research."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from finwatch.db.repositories import Analysis, Repo
from finwatch.llm.prompts import STAGE_P1, STAGE_SKEPTIC, load_prompt
from finwatch.llm.router import LAUNCH_MAX_OUTPUT_TOKENS, LLMClient, LLMResponse, extract_json
from finwatch.llm.schemas import Classification, P1Output
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
    queries: list[str] = Field(min_length=1, max_length=3)
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
    from_status: Literal["open", "discharged", "failed", "not_applicable"] = "open"
    status: Literal["open", "discharged", "failed", "not_applicable"]
    finding_id: str | None = None


class HarnessTrace(BaseModel):
    model_config = _STRICT
    schema_version: Literal["harness.v1"] = "harness.v1"
    outcome: Literal["published", "partial", "metrics_only", "withheld"]
    terminal_reason: Literal[
        "verified", "budget_exhausted", "compile_failed",
        "skeptic_blocked", "provider_failed", "malformed_action_breakdown",
    ]
    generator_model: str
    skeptic_model: str
    generator_prompt_version: str
    skeptic_prompt_version: str
    generator_turns: int
    skeptic_turns: int
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
class ToolContext:
    filing_meta: dict
    sections: dict[str, dict]
    prior_sections: dict[str, dict]
    metrics: MetricsBundle
    data_quality: list[CheckResult]
    change_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    tool_cache: dict[str, dict] = field(default_factory=dict)

    @property
    def has_prior_comparable(self) -> bool:
        return bool(self.prior_sections)

    def _section_rows(self, scope: str) -> dict[str, dict]:
        return self.sections if scope == "current" else self.prior_sections

    def search_sections(self, args: SearchSectionsArgs) -> dict:
        source = self._section_rows(args.scope)
        keys = args.section_keys or list(source)
        results: list[dict] = []
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
        changes: list[dict] = []
        for key in args.section_keys:
            if key not in _CHANGE_SECTIONS or key not in self.sections:
                continue
            current = self.sections[key].get("text", "")
            prior_row = self.prior_sections.get(key)
            prior = prior_row.get("text", "") if prior_row else ""
            if prior_row:
                diff = diff_risk_factors(prior, current)
                current_rows = [
                    ("added", row.char_start, row.char_end, row.text, None)
                    for row in diff.added
                ] + [
                    ("modified", row.current.char_start, row.current.char_end,
                     row.current.text, row.similarity)
                    for row in diff.modified
                ]
                removed = [
                    {"kind": "removed", "section_key": key,
                     "char_start": row.char_start, "char_end": row.char_end,
                     "snippet": row.text}
                    for row in diff.removed[:args.max_results]
                ]
            elif self.has_prior_comparable:
                current_rows = [
                    ("added", row.char_start, row.char_end, row.text, None)
                    for row in split_paragraphs(current)
                ]
                removed = []
            else:
                current_rows = []
                removed = []
            ranges = self.change_ranges.setdefault(key, [])
            for kind, start, end, snippet, similarity in current_rows:
                ranges.append((start, end))
                if len(changes) < args.max_results * len(args.section_keys):
                    changes.append({
                        "kind": kind, "section_key": key,
                        "char_start": start, "char_end": end,
                        "snippet": snippet, "similarity": similarity,
                    })
            changes.extend(removed)
        return {"changes": changes}

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
        counters: dict[str, int],
        repair_state: dict[str, bool],
    ) -> tuple[P1Output, list[DroppedFinding], str]:
        invalid_actions = 0
        check_draft_used = state.get("check_draft_used", False)
        while counters["generator_turns"] < self.MAX_GENERATOR_TURNS:
            counters["generator_turns"] += 1
            state["budget"] = {
                "generator_turns_remaining": self.MAX_GENERATOR_TURNS
                - counters["generator_turns"],
                "tool_calls_remaining": self.MAX_TOOL_CALLS - counters["tool_calls"],
                "repair_available": not repair_state["used"],
            }
            try:
                raw = self._call(
                    self.generator, usage, system=system, state=state, temperature=0.1
                )
                action = _GENERATOR_ADAPTER.validate_python(raw)
            except HarnessError:
                raise
            except Exception as exc:
                invalid_actions += 1
                state["last_error"] = "INVALID_ACTION"
                if invalid_actions >= 2:
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
                return final.output, dropped, "verified"

            if counters["tool_calls"] >= self.MAX_TOOL_CALLS:
                break
            counters["tool_calls"] += 1
            result, check_draft_used = self._execute_tool(
                action, context, check_draft_used=check_draft_used
            )
            state["check_draft_used"] = check_draft_used
            call_id = f"t{counters['tool_calls']}"
            observation = self._tool_observation(call_id, action.tool, result)
            state.setdefault("observations", []).append(observation)
            result_json = json.dumps(observation["result"], sort_keys=True)
            trace_calls.append(ToolTrace(
                call_id=call_id,
                tool=action.tool,
                arguments=self._trace_arguments(action),
                result_sha256=hashlib.sha256(result_json.encode()).hexdigest(),
            ))

        empty = self._empty_output(context.filing_meta)
        final = compile_draft(
            empty,
            trusted_meta=context.filing_meta,
            sections=context.sections,
            metrics=context.metrics,
            change_ranges=context.change_ranges,
            has_prior_comparable=context.has_prior_comparable,
            prune=True,
        )
        if final.run_errors:
            raise HarnessError(final.run_errors[0].lower())
        return final.output, final.dropped, "budget_exhausted"

    def _skeptic_pass(
        self,
        *,
        system: str,
        context: ToolContext,
        output: P1Output,
        trace_calls: list[ToolTrace],
        usage: _Usage,
        counters: dict[str, int],
        observations: list[dict],
    ) -> list[SkepticObligation]:
        if not output.findings:
            return []
        invalid_actions = 0
        local_tools = 0
        state = {
            "filing_meta": context.filing_meta,
            "draft": output.model_dump(mode="json"),
            "validated_observations": observations,
            "rules": {"may_only_add_obligations_or_use_read_tools": True},
        }
        while True:
            counters["skeptic_turns"] += 1
            state["budget"] = {"tool_calls_remaining": self.MAX_SKEPTIC_TOOL_CALLS-local_tools}
            try:
                raw = self._call(
                    self.skeptic, usage, system=system, state=state, temperature=0.0
                )
                action = _SKEPTIC_ADAPTER.validate_python(raw)
            except HarnessError:
                raise
            except Exception as exc:
                invalid_actions += 1
                state["last_error"] = "INVALID_ACTION"
                if invalid_actions >= 2:
                    raise HarnessError("malformed_action_breakdown") from exc
                continue
            if isinstance(action, SkepticDoneAction):
                valid_ids = {finding.finding_id for finding in output.findings}
                if any(row.finding_id not in valid_ids for row in action.obligations):
                    invalid_actions += 1
                    state["last_error"] = "UNKNOWN_FINDING_ID"
                    if invalid_actions >= 2:
                        raise HarnessError("malformed_action_breakdown")
                    continue
                return action.obligations
            if local_tools >= self.MAX_SKEPTIC_TOOL_CALLS:
                invalid_actions += 1
                state["last_error"] = "SKEPTIC_TOOL_BUDGET_EXHAUSTED_RETURN_DONE"
                if invalid_actions >= 2:
                    raise HarnessError("malformed_action_breakdown")
                continue
            local_tools += 1
            counters["tool_calls"] += 1
            result, _ = self._execute_tool(action, context, check_draft_used=True)
            call_id = f"t{counters['tool_calls']}"
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
        counters = {"generator_turns": 0, "skeptic_turns": 0, "tool_calls": 0}
        repair_state = {"used": False}
        dropped: list[DroppedFinding] = []
        terminal = "verified"
        state = {
            "filing_meta": filing_meta,
            "section_catalog": self._catalog(sections),
            "prior_section_catalog": self._catalog(prior_sections),
            "metric_catalog": [metric.value for metric in MetricId],
            "agenda": [
                {"name": name, "status": "open"}
                for name in (
                    "FORM_SCOPE", "CRITICAL_COVERAGE", "CHANGE_BASIS", "EXACT_EVIDENCE",
                    "NUMERIC_PROVENANCE", "DIRECTION_CONSISTENCY", "SAFE_LANGUAGE",
                    "DATA_QUALITY_REVIEW", "SKEPTIC_REVIEW",
                )
            ],
            "observations": [],
        }
        try:
            output, generator_dropped, terminal = self._generator_loop(
                system=generator_system, context=context, usage=usage, state=state,
                trace_calls=trace_calls, counters=counters, repair_state=repair_state,
            )
            dropped.extend(generator_dropped)
            objections = self._skeptic_pass(
                system=skeptic_system, context=context, output=output,
                trace_calls=trace_calls, usage=usage, counters=counters,
                observations=state.get("observations", []),
            )
            if objections and not repair_state["used"]:
                repair_state["used"] = True
                state["current_draft"] = output.model_dump(mode="json")
                state["compiler_errors"] = [row.model_dump(mode="json") for row in objections]
                repair_targets: dict[str, list[str]] = {}
                for row in objections:
                    codes = repair_targets.setdefault(row.finding_id, [])
                    if row.code not in codes:
                        codes.append(row.code)
                state["repair_targets"] = repair_targets
                state["instruction"] = (
                    "The Skeptic added finding-local obligations. Submit one complete repair; "
                    "drop any finding you cannot support."
                )
                output, repair_dropped, _ = self._generator_loop(
                    system=generator_system, context=context, usage=usage, state=state,
                    trace_calls=trace_calls, counters=counters, repair_state=repair_state,
                )
                dropped.extend(repair_dropped)
                objections = self._skeptic_pass(
                    system=skeptic_system, context=context, output=output,
                    trace_calls=trace_calls, usage=usage, counters=counters,
                    observations=state.get("observations", []),
                )
            if objections:
                extra = [
                    CompilerIssue(code=row.code, finding_id=row.finding_id)
                    for row in objections
                ]
                final = compile_draft(
                    output, trusted_meta=filing_meta, sections=sections, metrics=metrics,
                    change_ranges=context.change_ranges,
                    has_prior_comparable=context.has_prior_comparable,
                    prune=True, extra_issues=extra,
                )
                if final.run_errors:
                    raise HarnessError(final.run_errors[0].lower())
                output = final.output
                dropped.extend(final.dropped)
                terminal = "skeptic_blocked"
        except HarnessError as exc:
            reason = exc.reason
            mapped = (
                "provider_failed" if reason == "provider_failed"
                else "malformed_action_breakdown" if reason == "malformed_action_breakdown"
                else "compile_failed"
            )
            trace = HarnessTrace(
                outcome="withheld", terminal_reason=mapped,
                generator_model=self.generator_model or usage.model or "unknown",
                skeptic_model=self.skeptic_model or usage.model or "unknown",
                generator_prompt_version=generator_version,
                skeptic_prompt_version=skeptic_version,
                generator_turns=counters["generator_turns"],
                skeptic_turns=counters["skeptic_turns"],
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
                ],
                dropped_findings=dropped,
            )
            self._persist_trace(
                meta=filing_meta, trace=trace, usage=usage,
                prompt_version=f"{generator_version}+{skeptic_version}",
            )
            raise

        merged_drops: dict[str, list[str]] = {}
        for row in dropped:
            target = merged_drops.setdefault(row.finding_id, [])
            target.extend(code for code in row.error_codes if code not in target)
        dropped = [
            DroppedFinding(finding_id=finding_id, error_codes=codes)
            for finding_id, codes in merged_drops.items()
        ]
        published_ids = [finding.finding_id for finding in output.findings]
        outcome = (
            "metrics_only" if not published_ids else "partial" if dropped else "published"
        )
        trace = HarnessTrace(
            outcome=outcome,
            terminal_reason=terminal,
            generator_model=self.generator_model or usage.model or "unknown",
            skeptic_model=self.skeptic_model or usage.model or "unknown",
            generator_prompt_version=generator_version,
            skeptic_prompt_version=skeptic_version,
            generator_turns=counters["generator_turns"],
            skeptic_turns=counters["skeptic_turns"],
            tool_budget=self.MAX_TOOL_CALLS + self.MAX_SKEPTIC_TOOL_CALLS,
            tool_calls=trace_calls, repair_used=repair_state["used"],
            metric_results=list(metrics.results.values()),
            agenda=[
                AgendaItem(name="FORM_SCOPE", status="discharged"),
                AgendaItem(name="CRITICAL_COVERAGE", status="discharged"),
                AgendaItem(name="DATA_QUALITY_REVIEW", status="discharged"),
                AgendaItem(
                    name="SKEPTIC_REVIEW",
                    status="not_applicable" if not published_ids else "discharged",
                ),
            ] + [
                AgendaItem(name=name, status="discharged", finding_id=finding_id)
                for finding_id in published_ids
                for name in (
                    "CHANGE_BASIS", "EXACT_EVIDENCE", "NUMERIC_PROVENANCE",
                    "DIRECTION_CONSISTENCY", "SAFE_LANGUAGE",
                )
            ] + [
                AgendaItem(name="SKEPTIC_REVIEW", status="failed", finding_id=row.finding_id)
                for row in dropped
            ],
            published_finding_ids=published_ids,
            dropped_findings=dropped,
        )
        analysis_id = self.repo.insert_analysis(Analysis(
            accession_number=filing_meta["accession_number"], ticker=filing_meta["ticker"],
            stage="P1", model=self.generator_model or usage.model or "unknown",
            prompt_version=generator_version, output_json=output.model_dump_json(),
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=usage.cost_usd if usage.saw_cost else None,
            created_at=self._now_fn(),
        ))
        self._persist_trace(
            meta=filing_meta, trace=trace, usage=usage,
            prompt_version=f"{generator_version}+{skeptic_version}",
        )
        response = LLMResponse(
            text="", model=self.generator_model or usage.model or "unknown",
            tokens_in=usage.tokens_in, tokens_out=usage.tokens_out,
            cost_usd=usage.cost_usd if usage.saw_cost else None,
        )
        return HarnessResult(output, analysis_id, response, trace)
