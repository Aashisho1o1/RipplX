"""Golden-set eval harness + model bake-off (CLAUDE.md §16).

Per case, run P0 → P1 → verify and score: critical-flag recall (must be 100% on
critical cases — missing a going-concern is disqualifying), JSON validity, verifier
pass, false alarms (a boring filing must not scream), and cost/tokens. The winner is
the CHEAPEST model that clears every threshold. Model choice is config, not
architecture — re-run the bake-off whenever the model landscape shifts.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from finwatch.db import Company, Filing, Repo, init_db
from finwatch.evals.golden import GoldenCase, load_case_html, load_manifest, load_recorded_p1
from finwatch.llm.router import FakeLLMClient, LLMClient
from finwatch.llm.stages import P1Extractor, StageError
from finwatch.metrics.envelope import MetricsBundle
from finwatch.pipeline.orchestrator import assemble_verify_bundle
from finwatch.verify.checks import run_all

_SCREAM = frozenset({"critical", "high"})


@dataclass
class CaseScore:
    case_id: str
    category: str
    critical_recall: float       # fraction of expected critical flags found (1.0 = all)
    json_valid: bool
    verifier_pass: bool
    false_alarm: bool            # a boring filing that wrongly screamed
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class Thresholds:
    min_verifier_pass: float = 1.0
    min_json_valid: float = 1.0
    max_false_alarms: int = 0
    min_critical_recall: float = 1.0  # missing a critical item is disqualifying


@dataclass
class ModelReport:
    model: str
    scores: list[CaseScore] = field(default_factory=list)

    def _critical(self) -> list[CaseScore]:
        return [s for s in self.scores if s.category == "critical"]

    @property
    def critical_recall(self) -> float:
        c = self._critical()
        return 1.0 if not c else sum(s.critical_recall for s in c) / len(c)

    @property
    def verifier_pass_rate(self) -> float:
        if not self.scores:
            return 1.0
        return sum(s.verifier_pass for s in self.scores) / len(self.scores)

    @property
    def json_valid_rate(self) -> float:
        return 1.0 if not self.scores else sum(s.json_valid for s in self.scores) / len(self.scores)

    @property
    def false_alarms(self) -> int:
        return sum(1 for s in self.scores if s.false_alarm)

    @property
    def total_cost(self) -> float:
        return sum(s.cost_usd for s in self.scores)

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens_in + s.tokens_out for s in self.scores)

    def passes(self, t: Thresholds) -> bool:
        return (
            self.critical_recall >= t.min_critical_recall
            and self.verifier_pass_rate >= t.min_verifier_pass
            and self.json_valid_rate >= t.min_json_valid
            and self.false_alarms <= t.max_false_alarms
        )


@dataclass
class BakeoffReport:
    reports: list[ModelReport]
    thresholds: Thresholds
    winner: str | None = None


def score_case(case: GoldenCase, llm: LLMClient, html: str, *, now: str = "eval") -> CaseScore:
    """Run one golden case through P0 → P1 → verify and score it. Never raises."""
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(cik=case.cik, ticker=case.ticker, added_at=now))
    repo.upsert_filing(Filing(accession_number=case.accession, cik=case.cik,
                              form_type=case.form_type, filed_at="2024-01-01"))

    from finwatch.preprocess.preprocessor import Preprocessor
    Preprocessor(repo, now_fn=lambda: now).preprocess_html(
        accession_number=case.accession, cik=case.cik, form_type=case.form_type,
        filed_at="2024-01-01", period_of_report=None, html=html,
    )
    sections = {
        s.section_key: {"text": s.text, "char_start": s.char_start, "char_end": s.char_end,
                        "html_element_id": s.html_element_id, "is_furnished": bool(s.is_furnished)}
        for s in repo.list_filing_sections(case.accession)
    }
    filing_meta = {
        "cik": case.cik, "ticker": case.ticker, "company_name": None,
        "form_type": case.form_type, "filed_at": "2024-01-01", "period_of_report": None,
        "accession_number": case.accession, "is_amendment": False, "amends_accession": None,
    }

    try:
        p1, _aid, resp = P1Extractor(llm, repo, now_fn=lambda: now).run(
            filing_meta=filing_meta, sections=sections)
    except StageError:
        # unparseable output: JSON invalid; a critical case with no output is a miss
        false_alarm = False
        recall = 0.0 if case.expected_critical_flags else 1.0
        return CaseScore(case.id, case.category, recall, json_valid=False,
                         verifier_pass=False, false_alarm=false_alarm)

    found = {rf.flag for rf in p1.red_flags}
    expected = set(case.expected_critical_flags)
    recall = 1.0 if not expected else len(expected & found) / len(expected)
    false_alarm = case.category == "boring" and (
        p1.classification.overall_severity in _SCREAM
        or any(rf.severity in _SCREAM for rf in p1.red_flags)
    )

    section_texts = {
        f"{case.accession}:{s.section_key}": s.text
        for s in repo.list_filing_sections(case.accession)
    }
    bundle = assemble_verify_bundle(p1, None, MetricsBundle(), section_texts, [])
    verifier_pass = run_all(bundle).verdict != "FAIL"

    return CaseScore(
        case.id, case.category, recall, json_valid=True, verifier_pass=verifier_pass,
        false_alarm=false_alarm, tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
        cost_usd=resp.cost_usd or 0.0,
    )


def run_model(
    model_label: str,
    client_for_case: Callable[[GoldenCase], LLMClient],
    html_for_case: Callable[[GoldenCase], str],
    cases: list[GoldenCase],
) -> ModelReport:
    scores: list[CaseScore] = []
    for c in cases:
        try:
            html = html_for_case(c)
        except Exception:  # noqa: BLE001 — a fetch failure must not abort the whole bake-off
            # unfetchable case: unevaluable → miss (a critical case then fails recall)
            scores.append(CaseScore(
                c.id, c.category, 1.0 if not c.expected_critical_flags else 0.0,
                json_valid=False, verifier_pass=False, false_alarm=False))
            continue
        scores.append(score_case(c, client_for_case(c), html))
    return ModelReport(model=model_label, scores=scores)


def run_recorded(cases: list[GoldenCase] | None = None) -> ModelReport:
    """Deterministic run over the bundled recorded responses (no network / no keys)."""
    cases = cases or load_manifest()
    return run_model(
        "recorded",
        lambda c: FakeLLMClient(responder=lambda _s, _u, cid=c.id: load_recorded_p1(cid)),
        lambda c: load_case_html(c.id),
        cases,
    )


def run_live(model: str, edgar, cases: list[GoldenCase] | None = None) -> ModelReport:
    """Live run: fetch each case's real primary doc via EDGAR and call a real model."""
    from finwatch.llm.router import LiteLLMClient

    cases = cases or load_manifest()
    client = LiteLLMClient(model)

    def html_for(case: GoldenCase) -> str:
        return edgar.fetch_primary_doc(case.primary_doc).decode("utf-8", "replace")

    return run_model(model, lambda _c: client, html_for, cases)


def bakeoff(reports: list[ModelReport], thresholds: Thresholds | None = None) -> BakeoffReport:
    """Pick the cheapest model that clears every threshold."""
    t = thresholds or Thresholds()
    passing = [r for r in reports if r.passes(t)]
    winner = min(passing, key=lambda r: r.total_cost).model if passing else None
    return BakeoffReport(reports=reports, thresholds=t, winner=winner)


def render_report(bo: BakeoffReport) -> str:
    lines = ["# Golden-set bake-off", ""]
    lines.append(
        "| model | critical recall | verifier pass | json valid | false alarms "
        "| tokens | cost | passes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in bo.reports:
        lines.append(
            f"| {r.model} | {r.critical_recall:.0%} | {r.verifier_pass_rate:.0%} | "
            f"{r.json_valid_rate:.0%} | {r.false_alarms} | {r.total_tokens} | "
            f"${r.total_cost:.4f} | {'✓' if r.passes(bo.thresholds) else '✗'} |"
        )
    lines.append("")
    if bo.winner:
        lines.append(f"**Winner (cheapest passing): `{bo.winner}`**")
    else:
        lines.append("**No model cleared every threshold.**")
    return "\n".join(lines) + "\n"
