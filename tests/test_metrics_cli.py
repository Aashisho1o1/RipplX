"""`finwatch metrics` — the zero-key terminal view of the XBRL trust layer.

Exercises the shared `metric_view_rows` seam over a real `MetricsService.compute_and_store`
run, fed from recorded companyfacts fixtures — no network, no LLM. This is the deterministic
core the CLI command wires live EDGAR clients into.
"""
from __future__ import annotations

import json
from pathlib import Path

from finwatch.db import Company, Repo, init_db
from finwatch.digest.render import metric_view_rows
from finwatch.metrics.service import MetricsService

FX = Path(__file__).parent / "fixtures" / "companyfacts"


def _cf(ticker: str) -> dict:
    return json.loads((FX / f"{ticker}.json").read_text())


def _bundle_for(cik: str, ticker: str, sic: str, *, financial: int):
    repo = Repo(init_db(":memory:"))
    repo.upsert_company(Company(
        cik=cik, ticker=ticker, sic_code=sic,
        sector_class="financial" if financial else "general",
        is_financial=financial, added_at="t"))
    svc = MetricsService(repo, lambda c: _cf(ticker), now_fn=lambda: "t")
    bundle, _ = svc.compute_and_store(cik, as_of="2025-05-01")
    return bundle


def test_starter_view_separates_fresh_numbers_from_stale_annual_sources():
    bundle = _bundle_for("0000789019", "MSFT", "7372", financial=0)
    rows = metric_view_rows(bundle)
    by_label = {label: (value, mark) for label, value, _f, mark in rows}

    # The starter surface, and nothing but it (never the full ambitious core by default).
    assert set(by_label) <= {"Revenue growth", "Net income trend", "Operating cash flow",
                             "Liquidity", "Share count Δ",
                             "Net debt / (operating income + D&A) proxy"}
    # The annual fixture available at this point-in-time is stale and must not be
    # dressed up as current; recent instant/share facts remain usable.
    assert by_label["Revenue growth"][1] == "—"
    assert "current source is stale" in by_label["Revenue growth"][0]
    assert by_label["Liquidity"][1] == "✓"
    assert by_label["Share count Δ"][1] == "✓"


def test_bank_marks_not_applicable():
    bundle = _bundle_for("0000019617", "JPM", "6021", financial=1)
    rows = {label: (value, mark) for label, value, _f, mark in
            metric_view_rows(bundle)}
    value, mark = rows["Net debt / (operating income + D&A) proxy"]
    assert mark == "—"
    assert value.startswith("n/a")
