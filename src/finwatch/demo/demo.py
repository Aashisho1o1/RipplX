"""`finwatch demo` — run the full pipeline on bundled fixtures with ZERO API keys.

A new user must see real output in under 60 seconds without any key (CLAUDE.md §5).
This builds an in-memory DB, seeds a small demo portfolio, and runs the REAL
orchestrator (P0 → P1 → starter metrics → verify) over five bundled filings — the
LLM is a ``DemoLLM`` that replays recorded stage outputs, so nothing hits the network.
The rendered digest is therefore produced by exactly the same code path as a live run.

Portfolio (deliberately covers every digest section):
  MSFT (owned)  material 10-Q     → one evidence-backed change + verified numbers
  DPLS (owned)  going-concern 10-K→ evidence-backed critical findings
  TWKS (watch)  non-reliance 8-K  → critical red flag
  AAPL (watch)  clean 10-Q + 8-K  → routine, silence
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from finwatch.db import Company, Filing, Repo, User, init_db
from finwatch.llm.router import LAUNCH_MAX_OUTPUT_TOKENS, LLMResponse
from finwatch.llm.stages import P1Extractor
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import Orchestrator
from finwatch.preprocess.preprocessor import Preprocessor

_HERE = Path(__file__).resolve().parent
_DATA = _HERE / "data"
_GOLDEN = _HERE.parent / "evals" / "golden_set"
_MSFT_CIK = "0000789019"
_MSFT_ACCN = "0000950170-24-048288"
_MODEL = "demo/recorded"
_NOW = "2024-08-05T00:00:00+00:00"
PUBLIC_SHOWCASE_USER_ID = "public-showcase"
PUBLIC_SHOWCASE_SETTING = "web.public_showcase_refreshed_at"
DEFAULT_SHOWCASE_TICKERS = ("AAPL", "MSFT", "NVDA")
# Production computes the starter metrics as of *today*, never as of the filing date
# (`_compute_synced_metrics(..., as_of=date.today())`). The demo previously used each
# case's `filed` date, which excluded the bundled fixtures' own newer facts under the
# point-in-time rule and left almost every metric row "unavailable" — the opposite of
# the "six verified financial deltas" the sample brief exists to show. This constant is
# the demo's frozen "today", aligned with the bundled companyfacts vintage so the sample
# stays deterministic instead of decaying as real time passes.
DEMO_METRICS_AS_OF = "2026-04-29"


class DemoLLM:
    """Replays one recorded P1 output for the current filing."""

    def __init__(self) -> None:
        self.model = _MODEL
        self.stage_outputs: dict[str, str] = {}

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        json_mode: bool = True,
        max_tokens: int = LAUNCH_MAX_OUTPUT_TOKENS,
    ) -> LLMResponse:
        if "finance Skeptic" in system:
            return LLMResponse(
                text=json.dumps({"action": "done", "obligations": []}), model=self.model
            )
        text = self.stage_outputs.get("P1")
        if text is None:
            raise RuntimeError("demo: no recorded P1 output for the current filing")
        draft = json.loads(text)
        for index, finding in enumerate(draft["findings"], start=1):
            finding.setdefault("finding_id", f"f{index}")
        return LLMResponse(
            text=json.dumps({"action": "submit", "draft": draft}), model=self.model
        )


@dataclass(frozen=True)
class _Case:
    accn: str
    cik: str
    ticker: str
    form: str
    filed: str
    primary_doc: str
    html_path: Path
    p1_path: Path

    def stage_outputs(self) -> dict[str, str]:
        return {"P1": self.p1_path.read_text()}


_CASES: list[_Case] = [
    _Case(accn=_MSFT_ACCN, cik=_MSFT_CIK, ticker="MSFT", form="10-Q",
          filed="2024-04-25",
          primary_doc=("https://www.sec.gov/Archives/edgar/data/789019/"
                       "000095017024048288/msft-20240331.htm"),
          html_path=_DATA / "msft_10q.html", p1_path=_DATA / "msft_10q.p1.json"),
    _Case(accn="0001683168-24-004848", cik="0000866439", ticker="DPLS", form="10-K",
          filed="2024-07-15",
          primary_doc="https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm",
          html_path=_GOLDEN / "going_concern_10k" / "filing.html",
          p1_path=_GOLDEN / "going_concern_10k" / "recorded_p1.json"),
    _Case(accn="0001866550-24-000006", cik="0001866550", ticker="TWKS", form="8-K",
          filed="2024-02-12",
          primary_doc="https://www.sec.gov/Archives/edgar/data/1866550/000186655024000006/twks-20240206.htm",
          html_path=_GOLDEN / "non_reliance_8k" / "filing.html",
          p1_path=_GOLDEN / "non_reliance_8k" / "recorded_p1.json"),
    _Case(accn="0000320193-24-000081", cik="0000320193", ticker="AAPL", form="10-Q",
          filed="2024-08-01",
          primary_doc="https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm",
          html_path=_GOLDEN / "clean_10q" / "filing.html",
          p1_path=_GOLDEN / "clean_10q" / "recorded_p1.json"),
    _Case(accn="0000320193-26-000011", cik="0000320193", ticker="AAPL", form="8-K",
          filed="2026-04-30",
          primary_doc="https://www.sec.gov/Archives/edgar/data/320193/000032019326000011/aapl-20260430.htm",
          html_path=_GOLDEN / "furnished_earnings_8k" / "filing.html",
          p1_path=_GOLDEN / "furnished_earnings_8k" / "recorded_p1.json"),
]

_COMPANIES = [
    Company(cik=_MSFT_CIK, ticker="MSFT", name="Microsoft Corp.", sic_code="7372",
            is_financial=0, added_at=_NOW),
    Company(cik="0000866439", ticker="DPLS", name="DarkPulse, Inc.", sic_code="7372",
            is_financial=0, added_at=_NOW),
    Company(cik="0001866550", ticker="TWKS", name="Thoughtworks Holding, Inc.",
            sic_code="7372", is_financial=0, added_at=_NOW),
    Company(cik="0000320193", ticker="AAPL", name="Apple Inc.", sic_code="3571",
            is_financial=0, added_at=_NOW),
]

_BUNDLED_FACTS = {_MSFT_CIK: "companyfacts_MSFT.json", "0000320193": "companyfacts_AAPL.json"}


def ensure_public_showcase_user(repo: Repo, *, at: str) -> None:
    """Create the reserved non-login owner used by anonymous showcase projections."""
    if repo.get_user(PUBLIC_SHOWCASE_USER_ID) is None:
        repo.create_user(
            User(
                id=PUBLIC_SHOWCASE_USER_ID,
                email="public-showcase@finwatch.invalid",
                created_at=at,
                last_login_at=at,
            )
        )


def publish_public_showcase(repo: Repo, ciks: list[str], *, refreshed_at: str) -> None:
    """Atomically publish one complete curated SEC watchlist after a successful refresh."""
    selected = list(dict.fromkeys(ciks))
    if not selected:
        raise ValueError("the public showcase requires at least one company")
    missing = [cik for cik in selected if repo.get_company(cik) is None]
    if missing:
        raise ValueError(f"unknown showcase CIK: {missing[0]}")
    ensure_public_showcase_user(repo, at=refreshed_at)
    with repo.conn:
        repo.conn.execute(
            "DELETE FROM user_companies WHERE user_id = ?", (PUBLIC_SHOWCASE_USER_ID,)
        )
        repo.conn.executemany(
            "INSERT INTO user_companies (user_id, cik, tracked_at) VALUES (?, ?, ?)",
            [(PUBLIC_SHOWCASE_USER_ID, cik, refreshed_at) for cik in selected],
        )
        repo.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (PUBLIC_SHOWCASE_SETTING, refreshed_at),
        )


def public_showcase_refreshed_at(repo: Repo) -> str | None:
    refreshed_at = repo.get_setting(PUBLIC_SHOWCASE_SETTING)
    if not refreshed_at or not repo.list_tracked_ciks(PUBLIC_SHOWCASE_USER_ID):
        return None
    return refreshed_at


def _companyfacts(cik: str) -> dict:
    """Bundled real SEC facts for the large-cap cases; empty-but-valid otherwise.

    DPLS and TWKS ship no facts on purpose: they are a micro-cap and a company taken
    private, and their genuinely sparse XBRL is what an `unavailable` metric row looks
    like. Showing that beside two issuers with a full six-metric table is the honest
    demonstration — the sample must not imply every issuer computes cleanly.
    """
    name = _BUNDLED_FACTS.get(cik)
    if name:
        return json.loads((_DATA / name).read_text())
    return {"cik": cik, "entityName": cik, "facts": {"us-gaap": {}, "dei": {}}}


def build_demo_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Build a DB with the launch demo run persisted. Defaults to in-memory."""
    conn = init_db(db_path)
    repo = Repo(conn)
    ensure_public_showcase_user(repo, at=_NOW)
    for c in _COMPANIES:
        repo.upsert_company(c)
        repo.track_company(c.cik, at=_NOW)
        repo.track_company(c.cik, at=_NOW, user_id=PUBLIC_SHOWCASE_USER_ID)

    def now_fn() -> str:
        return _NOW

    llm = DemoLLM()
    metrics = MetricsService(repo, companyfacts_provider=_companyfacts, now_fn=now_fn)
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(llm, repo, model_label=_MODEL, now_fn=now_fn),
        metrics, now_fn=now_fn)
    for case in _CASES:
        filing = Filing(accession_number=case.accn, cik=case.cik, form_type=case.form,
                        filed_at=case.filed, primary_doc_url=case.primary_doc)
        repo.upsert_filing(filing)
        llm.stage_outputs = case.stage_outputs()
        analysis = orch.process_html(
            filing=filing,
            html=case.html_path.read_text(),
            as_of=DEMO_METRICS_AS_OF,
        )
        repo.set_filing_status(
            case.accn,
            "analyzed" if analysis.withheld else "verified",
            processed_at=_NOW,
        )
    return conn


DEMO_SINCE = "2024-01-01"
