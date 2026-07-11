#!/usr/bin/env bash
#
# reanalyze.sh — re-run the launch analysis pipeline on the newest filing per ticker
# with a chosen DeepSeek v4 model, so you can compare how "flash" vs "pro" perform.
#
# It resets each ticker's newest supported filing (drops the prior analysis, its
# verification rows, and stage attempts, then moves it back to 'fetched') and runs
# `finwatch analyze <ticker>` with the model you pick.
#
# Usage:
#   OPENROUTER_API_KEY=sk-or-... scripts/reanalyze.sh flash            # every tracked ticker
#   OPENROUTER_API_KEY=sk-or-... scripts/reanalyze.sh pro JPM MSFT     # only these tickers
#   OPENROUTER_API_KEY=sk-or-... scripts/reanalyze.sh openrouter/google/gemini-2.5-pro JPM
#
# Verify the exact OpenRouter slugs on https://openrouter.ai/models — adjust below if they differ.
set -euo pipefail

TIER="${1:-flash}"; shift || true
case "$TIER" in
  flash) MODEL="openrouter/deepseek/deepseek-v4-flash" ;;
  pro)   MODEL="openrouter/deepseek/deepseek-v4-pro" ;;
  openrouter/*|openai/*) MODEL="$TIER" ;;   # pass a full litellm model string straight through
  *) echo "usage: reanalyze.sh {flash|pro|<full-model-string>} [TICKER ...]" >&2; exit 2 ;;
esac

DB="${FINWATCH_DB:-./data/finwatch.db}"
FINWATCH_CMD="${FINWATCH_CMD:-uv run finwatch}"

: "${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY (your OpenRouter key) in the environment}"
# SEC_USER_AGENT is required by EDGAR; fall back to the contact already stored in the DB.
export SEC_USER_AGENT="${SEC_USER_AGENT:-$(sqlite3 "$DB" "SELECT value FROM settings WHERE key='web.sec_user_agent';")}"
: "${SEC_USER_AGENT:?set SEC_USER_AGENT (your contact email for EDGAR)}"
export FINWATCH_MODEL="$MODEL"

cp -n "$DB" "$DB.orig" 2>/dev/null && echo "Backed up original DB → $DB.orig" || true
echo "Model: $FINWATCH_MODEL"
echo "DB:    $DB"

# Tickers: the ones passed as args, or every tracked ticker.
if [ "$#" -gt 0 ]; then TICKERS="$*"; else
  TICKERS="$(sqlite3 "$DB" "SELECT c.ticker FROM holdings h JOIN companies c ON c.cik=h.cik ORDER BY c.ticker;")"
fi

for T in $TICKERS; do
  ACC="$(sqlite3 "$DB" "
    SELECT f.accession_number FROM filings f JOIN companies c ON c.cik=f.cik
    WHERE c.ticker='$T' AND f.form_type IN ('8-K','10-Q','10-K')
    ORDER BY f.filed_at DESC, f.accession_number DESC LIMIT 1;")"
  if [ -z "$ACC" ]; then echo "· $T: no 8-K/10-Q/10-K on file — skipping"; continue; fi
  sqlite3 "$DB" "
    DELETE FROM verification_results WHERE analysis_id IN (SELECT id FROM analyses WHERE accession_number='$ACC');
    DELETE FROM analyses WHERE accession_number='$ACC';
    DELETE FROM filing_stage_runs WHERE accession_number='$ACC';
    UPDATE filings SET status='fetched', processed_at=NULL WHERE accession_number='$ACC';"
  echo "· $T: reset $ACC → analyzing with $MODEL …"
  $FINWATCH_CMD analyze "$T" || echo "  (analyze failed for $T; continuing)"
done

echo
echo "Done. See results:  $FINWATCH_CMD digest   (or  $FINWATCH_CMD serve  and open the brief)"
