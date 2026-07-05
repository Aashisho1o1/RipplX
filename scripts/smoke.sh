#!/usr/bin/env bash
# Zero-key backend smoke test — no LLM key required.
#   1) `finwatch demo`    proves the full pipeline WIRING on bundled filings (no network).
#   2) `finwatch metrics` proves the TRUST LAYER on live SEC data (real XBRL -> verified numbers).
#
# Usage:  scripts/smoke.sh [TICKER]      (default: AAPL)
# The metrics step hits EDGAR, so it needs SEC_USER_AGENT (in .env or exported); no model/key.
set -euo pipefail
cd "$(dirname "$0")/.."
TICKER="${1:-AAPL}"

echo "== 1/2  finwatch demo — full pipeline on bundled filings (no keys, no network) =="
uv run finwatch demo | tail -n 20

echo
echo "== 2/2  finwatch metrics ${TICKER} --all — real SEC XBRL -> deterministic verified numbers =="
uv run finwatch init >/dev/null
uv run finwatch metrics "${TICKER}" --all
