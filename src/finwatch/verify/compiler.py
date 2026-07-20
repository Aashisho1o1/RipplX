"""Finding-local deterministic compiler used by the research harness.

The compiler never repairs prose. It anchors evidence, checks the small publication
grammar, validates structured metric direction, and returns typed issues so the
caller can spend one repair or prune only the affected finding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from finwatch.core.text_policy import authored_text_violations
from finwatch.llm.schemas import Classification, Finding, P1Output
from finwatch.metrics.envelope import MetricsBundle
from finwatch.preprocess.forms import base_form, is_amendment

_STRICT = ConfigDict(extra="forbid")
_CRITICAL_8K_SECTION_FLAGS = {
    "item_1_03": "item_1_03_bankruptcy",
    "item_2_04": "item_2_04_acceleration",
    "item_3_01": "item_3_01_delisting",
    "item_4_02": "item_4_02_non_reliance",
}


class CompilerIssue(BaseModel):
    model_config = _STRICT
    code: str
    finding_id: str | None = None


class DroppedFinding(BaseModel):
    model_config = _STRICT
    finding_id: str
    error_codes: list[str]


@dataclass
class CompileResult:
    output: P1Output
    issues: list[CompilerIssue] = field(default_factory=list)
    dropped: list[DroppedFinding] = field(default_factory=list)
    run_errors: list[str] = field(default_factory=list)


def _anchor_finding(finding: Finding, sections: dict[str, dict]) -> list[CompilerIssue]:
    issues: list[CompilerIssue] = []
    for evidence in finding.evidence:
        section = sections.get(evidence.section_key)
        text = section.get("text") if isinstance(section, dict) else None
        if not text:
            issues.append(CompilerIssue(
                code="QUOTE_NOT_EXACT", finding_id=finding.finding_id
            ))
            continue
        first = text.find(evidence.snippet)
        if first < 0:
            issues.append(CompilerIssue(
                code="QUOTE_NOT_EXACT", finding_id=finding.finding_id
            ))
            continue
        if text.find(evidence.snippet, first + 1) >= 0:
            issues.append(CompilerIssue(
                code="AMBIGUOUS_QUOTE", finding_id=finding.finding_id
            ))
            continue
        evidence.char_start = first
        evidence.char_end = first + len(evidence.snippet)
    return issues


def _overlaps_change(
    finding: Finding,
    change_ranges: dict[str, list[tuple[int, int]]],
) -> bool:
    for evidence in finding.evidence:
        if evidence.char_start is None or evidence.char_end is None:
            continue
        for start, end in change_ranges.get(evidence.section_key, []):
            if evidence.char_start < end and start < evidence.char_end:
                return True
    return False


def _finding_issues(
    finding: Finding,
    *,
    metrics: MetricsBundle,
    require_change_basis: bool,
    change_ranges: dict[str, list[tuple[int, int]]],
) -> list[CompilerIssue]:
    issues: list[CompilerIssue] = []
    fid = finding.finding_id
    headline = finding.headline
    violations = authored_text_violations(headline)
    if "quantity" in violations:
        issues.append(CompilerIssue(code="AUTHORED_NUMBER", finding_id=fid))
    if any(violation != "quantity" for violation in violations):
        issues.append(CompilerIssue(code="UNSAFE_LANGUAGE", finding_id=fid))
    if require_change_basis and not finding.critical_flag and not _overlaps_change(
        finding, change_ranges
    ):
        issues.append(CompilerIssue(code="NOT_A_CHANGED_SPAN", finding_id=fid))
    if finding.metric_id is not None:
        metric = metrics.get(finding.metric_id.value)
        actual = metric.deterministic_direction if metric and metric.computed else None
        if actual is None:
            issues.append(CompilerIssue(
                code="METRIC_DIRECTION_UNAVAILABLE", finding_id=fid
            ))
        elif actual != finding.direction:
            issues.append(CompilerIssue(code="METRIC_CONTRADICTION", finding_id=fid))
    return issues


def _critical_coverage(output: P1Output, section_keys: set[str]) -> bool:
    if base_form(output.form_type) != "8-K":
        return True
    for section_key, flag in _CRITICAL_8K_SECTION_FLAGS.items():
        if section_key not in section_keys:
            continue
        if not any(
            finding.critical_flag == flag
            and finding.severity == "critical"
            and any(evidence.section_key == section_key for evidence in finding.evidence)
            for finding in output.findings
        ):
            return False
    return True


def compile_draft(
    output: P1Output,
    *,
    trusted_meta: dict,
    sections: dict[str, dict],
    metrics: MetricsBundle,
    change_ranges: dict[str, list[tuple[int, int]]] | None = None,
    has_prior_comparable: bool = False,
    prune: bool = False,
    extra_issues: list[CompilerIssue] | None = None,
) -> CompileResult:
    """Compile a draft and optionally remove only findings carrying local errors."""
    anchored = output.model_copy(deep=True)
    run_errors: list[str] = []
    if (
        anchored.accession_number != trusted_meta.get("accession_number")
        or anchored.ticker != trusted_meta.get("ticker")
        or anchored.form_type != trusted_meta.get("form_type")
    ):
        run_errors.append("FORM_SCOPE")

    changes = change_ranges or {}
    require_change = (
        has_prior_comparable
        and base_form(anchored.form_type) in {"10-K", "10-Q"}
        and not is_amendment(anchored.form_type)
    )
    issues: list[CompilerIssue] = list(extra_issues or [])
    for finding in anchored.findings:
        issues.extend(_anchor_finding(finding, sections))
        issues.extend(_finding_issues(
            finding,
            metrics=metrics,
            require_change_basis=require_change,
            change_ranges=changes,
        ))

    # Keep one code per finding while preserving deterministic order.
    unique: list[CompilerIssue] = []
    seen: set[tuple[str | None, str]] = set()
    for issue in issues:
        key = (issue.finding_id, issue.code)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    issues = unique

    dropped: list[DroppedFinding] = []
    if prune:
        codes_by_finding: dict[str, list[str]] = {}
        for issue in issues:
            if issue.finding_id is not None:
                codes_by_finding.setdefault(issue.finding_id, []).append(issue.code)
        survivors = [
            finding for finding in anchored.findings
            if finding.finding_id not in codes_by_finding
        ]
        dropped = [
            DroppedFinding(finding_id=finding.finding_id,
                           error_codes=codes_by_finding[finding.finding_id])
            for finding in anchored.findings
            if finding.finding_id in codes_by_finding
        ]
        severity = "routine"
        if survivors:
            rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            severity = min((finding.severity for finding in survivors), key=rank.__getitem__)
        payload = anchored.model_dump(mode="json")
        payload["findings"] = [finding.model_dump(mode="json") for finding in survivors]
        payload["classification"] = Classification(overall_severity=severity).model_dump()
        anchored = P1Output.model_validate(payload)

    if not _critical_coverage(anchored, set(sections)):
        run_errors.append("CRITICAL_COVERAGE")
    return CompileResult(
        output=anchored,
        issues=issues,
        dropped=dropped,
        run_errors=list(dict.fromkeys(run_errors)),
    )
