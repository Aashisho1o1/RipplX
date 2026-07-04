"""Live golden-set bake-off — excluded by default (`-m live`).

Requires network + real model/provider keys. Fetches the pinned real filings and runs
them through an actual model, then asserts the DoD (critical recall 100%, verifier pass).
Set FINWATCH_EVAL_MODEL to a litellm model string (plus its provider key).
"""
from __future__ import annotations

import os

import pytest

from finwatch.evals.harness import Thresholds, run_live
from finwatch.ingest import EdgarClient


@pytest.mark.live
def test_live_bakeoff_meets_dod():
    model = os.environ.get("FINWATCH_EVAL_MODEL")
    ua = os.environ.get("SEC_USER_AGENT")
    if not model or not ua:
        pytest.skip("FINWATCH_EVAL_MODEL and SEC_USER_AGENT required")
    report = run_live(model, EdgarClient(ua))
    assert report.critical_recall == 1.0, report.scores
    assert report.passes(Thresholds())
