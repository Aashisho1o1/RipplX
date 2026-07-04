"""Live routing checks against real SEC filings — excluded by default (`-m live`).

Validates that the deterministic router lands the canonical sections on genuine
AAPL filings, including the load-bearing 10-Q MD&A = Part I Item 2 rule.
"""
from __future__ import annotations

import os

import pytest

from finwatch.ingest import EdgarClient
from finwatch.preprocess import route_sections

_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/320193/{sub}/{doc}"
# (form, accession-nodash sub-path, primary doc, required section keys)
_CASES = [
    ("10-Q", "000032019324000081", "aapl-20240629.htm",
     {"mdna", "market_risk", "controls", "legal", "risk_factor_changes"}),
    ("10-K", "000032019324000123", "aapl-20240928.htm",
     {"business", "risk_factors", "legal", "mdna", "market_risk", "financials", "controls"}),
    ("8-K", "000032019326000011", "aapl-20260430.htm", {"item_2_02", "item_9_01"}),
]


@pytest.mark.live
@pytest.mark.parametrize("form,sub,doc,required", _CASES)
def test_live_routing(form, sub, doc, required):
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua:
        pytest.skip("SEC_USER_AGENT not set")
    client = EdgarClient(ua)
    html = client.fetch_primary_doc(_ARCHIVE.format(sub=sub, doc=doc)).decode("utf-8", "replace")
    keys = {s.section_key for s in route_sections(form, html)}
    assert required <= keys, f"{form}: missing {required - keys}"
