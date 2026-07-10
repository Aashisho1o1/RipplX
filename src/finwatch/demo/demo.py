"""`finwatch demo` — run the full pipeline on bundled fixtures with ZERO API keys.

A new user must see real output in under 60 seconds without any key (CLAUDE.md §5).
This builds an in-memory DB, seeds a small demo portfolio, and runs the REAL
orchestrator (P0 → P1 → metrics → P2 → verify) over five bundled filings — the
LLM is a ``DemoLLM`` that replays recorded stage outputs, so nothing hits the network.
The rendered digest is therefore produced by exactly the same code path as a live run.

Portfolio (deliberately covers every digest section):
  MSFT (owned)  routine 10-Q      → verified numbers + boring line
  DPLS (owned)  going-concern 10-K→ critical red flag + thesis impact
  TWKS (watch)  non-reliance 8-K  → critical red flag
  AAPL (watch)  clean 10-Q + 8-K  → routine, silence
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from finwatch.db import Company, Filing, Holding, Repo, init_db
from finwatch.llm.router import LLMResponse
from finwatch.llm.stages import P1Extractor, P2Explainer
from finwatch.metrics.service import MetricsService
from finwatch.pipeline.orchestrator import Orchestrator
from finwatch.preprocess.preprocessor import Preprocessor

_HERE = Path(__file__).resolve().parent
_DATA = _HERE / "data"
_GOLDEN = _HERE.parent / "evals" / "golden_set"
_MSFT_CIK = "0000789019"
_MODEL = "demo/recorded"
_NOW = "2024-08-05T00:00:00+00:00"


class DemoLLM:
    """Replays recorded P1/P2 outputs for the current filing."""

    def __init__(self) -> None:
        self.model = _MODEL
        self.stage_outputs: dict[str, str] = {}

    def complete(self, *, system: str, user: str, temperature: float = 0.0,
                 json_mode: bool = True) -> LLMResponse:
        if "portfolio manager and risk officer" in system:
            stage = "P2"
        else:
            stage = "P1"
        text = self.stage_outputs.get(stage)
        if text is None:
            raise RuntimeError(f"demo: no recorded {stage} output for the current filing")
        return LLMResponse(text=text, model=self.model)


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
    p2_path: Path | None = None

    def stage_outputs(self) -> dict[str, str]:
        out = {"P1": self.p1_path.read_text()}
        if self.p2_path is not None:
            out["P2"] = self.p2_path.read_text()
        return out


def _sec_index(cik: str, accn: str) -> str:
    return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{accn.replace('-', '')}/{accn}-index.htm")


_CASES: list[_Case] = [
    _Case(accn="0000789019-24-000070", cik=_MSFT_CIK, ticker="MSFT", form="10-Q",
          filed="2024-08-01", primary_doc=_sec_index(_MSFT_CIK, "0000789019-24-000070"),
          html_path=_DATA / "msft_10q.html", p1_path=_DATA / "msft_10q.p1.json"),
    _Case(accn="0001683168-24-004848", cik="0000866439", ticker="DPLS", form="10-K",
          filed="2024-08-02",
          primary_doc="https://www.sec.gov/Archives/edgar/data/866439/000168316824004848/darkpulse_i10k-123123.htm",
          html_path=_GOLDEN / "going_concern_10k" / "filing.html",
          p1_path=_GOLDEN / "going_concern_10k" / "recorded_p1.json",
          p2_path=_DATA / "going_concern.p2.json"),
    _Case(accn="0001866550-24-000006", cik="0001866550", ticker="TWKS", form="8-K",
          filed="2024-08-02",
          primary_doc="https://www.sec.gov/Archives/edgar/data/1866550/000186655024000006/twks-20240206.htm",
          html_path=_GOLDEN / "non_reliance_8k" / "filing.html",
          p1_path=_GOLDEN / "non_reliance_8k" / "recorded_p1.json",
          p2_path=_DATA / "non_reliance.p2.json"),
    _Case(accn="0000320193-24-000081", cik="0000320193", ticker="AAPL", form="10-Q",
          filed="2024-08-01",
          primary_doc="https://www.sec.gov/Archives/edgar/data/320193/000032019324000081/aapl-20240629.htm",
          html_path=_GOLDEN / "clean_10q" / "filing.html",
          p1_path=_GOLDEN / "clean_10q" / "recorded_p1.json"),
    _Case(accn="0000320193-26-000011", cik="0000320193", ticker="AAPL", form="8-K",
          filed="2024-08-03",
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

_HOLDINGS = [
    Holding(cik=_MSFT_CIK, ticker="MSFT", owned=1, shares=40, cost_basis=300.0,
            target_weight_pct=25.0, horizon="5y+",
            thesis=("Cloud and AI drive durable double-digit growth; margins and buybacks "
                    "compound it."),
            added_at=_NOW),
    Holding(cik="0000866439", ticker="DPLS", owned=1, shares=5000, cost_basis=1.20,
            target_weight_pct=5.0, horizon="1-3y",
            thesis="Deep-value turnaround: new contract wins restore positive operating cash flow.",
            added_at=_NOW),
    Holding(cik="0001866550", ticker="TWKS", owned=0, added_at=_NOW),
    Holding(cik="0000320193", ticker="AAPL", owned=0, added_at=_NOW),
]


def _companyfacts(cik: str) -> dict:
    """Bundled MSFT facts (full metric set); an empty-but-valid store otherwise."""
    if cik == _MSFT_CIK:
        return json.loads((_DATA / "companyfacts_MSFT.json").read_text())
    return {"cik": cik, "entityName": cik, "facts": {"us-gaap": {}, "dei": {}}}


def build_demo_db(db_path: str = ":memory:") -> sqlite3.Connection:
    """Build a DB with the launch demo run persisted. Defaults to in-memory."""
    conn = init_db(db_path)
    repo = Repo(conn)
    for c in _COMPANIES:
        repo.upsert_company(c)
    for h in _HOLDINGS:
        repo.upsert_holding(h)

    def now_fn() -> str:
        return _NOW

    llm = DemoLLM()
    metrics = MetricsService(repo, price_provider=repo, companyfacts_provider=_companyfacts,
                             now_fn=now_fn)
    orch = Orchestrator(
        repo, Preprocessor(repo, now_fn=now_fn),
        P1Extractor(llm, repo, model_label=_MODEL, now_fn=now_fn),
        P2Explainer(llm, repo, model_label=_MODEL, now_fn=now_fn),
        metrics, companyfacts_provider=_companyfacts, now_fn=now_fn)

    records = [
        {"ticker": h.ticker, "owned": bool(h.owned), "shares": h.shares,
         "cost_basis": h.cost_basis, "target_weight_pct": h.target_weight_pct,
         "thesis": h.thesis}
        for h in repo.list_holdings()
    ]
    for case in _CASES:
        filing = Filing(accession_number=case.accn, cik=case.cik, form_type=case.form,
                        filed_at=case.filed, primary_doc_url=case.primary_doc)
        repo.upsert_filing(filing)
        llm.stage_outputs = case.stage_outputs()
        orch.process_html(filing=filing, html=case.html_path.read_text(), as_of=case.filed,
                          records=records)
    return conn


DEMO_SINCE = "2024-08-01"
