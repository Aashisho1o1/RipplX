-- finwatch schema v1 (migration 1). Transcribed verbatim from CLAUDE.md §6.
-- Applied by db/database.py::apply_migrations; version tracked via PRAGMA user_version.

CREATE TABLE companies (
  cik TEXT PRIMARY KEY, ticker TEXT NOT NULL, name TEXT,
  sic_code TEXT, sector_class TEXT,        -- 'general'|'financial'|'insurance'|'reit'|'utility'
  is_financial INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL
);
CREATE TABLE holdings (
  id INTEGER PRIMARY KEY, cik TEXT NOT NULL REFERENCES companies(cik),
  ticker TEXT NOT NULL,
  owned INTEGER NOT NULL,                  -- 1 owned, 0 watch
  shares REAL, cost_basis REAL, target_weight_pct REAL,
  horizon TEXT, thesis TEXT,               -- thesis NULLABLE by design
  added_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);   -- e.g. risk_tolerance

CREATE TABLE filings (
  accession_number TEXT PRIMARY KEY, cik TEXT NOT NULL,
  form_type TEXT NOT NULL, filed_at TEXT NOT NULL, period_of_report TEXT,
  is_amendment INTEGER NOT NULL DEFAULT 0, amends_accession TEXT,
  primary_doc_url TEXT, raw_sha256 TEXT,
  fetched_at TEXT, processed_at TEXT,
  status TEXT NOT NULL DEFAULT 'fetched'   -- fetched|sectioned|analyzed|verified|failed
);
CREATE TABLE filing_sections (
  id INTEGER PRIMARY KEY, accession_number TEXT NOT NULL REFERENCES filings,
  section_key TEXT NOT NULL, title TEXT,
  char_start INTEGER, char_end INTEGER, html_element_id TEXT,
  is_furnished INTEGER NOT NULL DEFAULT 0,      -- Item 2.02 / 7.01 handling
  text TEXT NOT NULL, text_sha256 TEXT NOT NULL
);
CREATE VIRTUAL TABLE section_fts USING fts5(text, content='filing_sections', content_rowid='id');

CREATE TABLE xbrl_facts (
  id INTEGER PRIMARY KEY, cik TEXT NOT NULL,
  taxonomy TEXT NOT NULL, tag TEXT NOT NULL,
  value REAL, unit_ref TEXT, decimals TEXT,
  period_start TEXT, period_end TEXT, instant TEXT,
  fy TEXT, fp TEXT, form TEXT, accession_number TEXT,
  dimensions_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_xbrl ON xbrl_facts(cik, tag, period_end, instant);

CREATE TABLE prices (                       -- EOD only, from Stooq
  ticker TEXT NOT NULL, date TEXT NOT NULL, close REAL NOT NULL,
  PRIMARY KEY (ticker, date)
);

CREATE TABLE analyses (
  id INTEGER PRIMARY KEY, accession_number TEXT NOT NULL, ticker TEXT NOT NULL,
  stage TEXT NOT NULL,                      -- 'P1'|'P2'|'P3'
  model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  output_json TEXT NOT NULL,
  tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE analysis_claims (
  claim_id TEXT PRIMARY KEY,                -- e.g. 'c_000123'
  analysis_id INTEGER NOT NULL REFERENCES analyses(id),
  claim_type TEXT NOT NULL,                 -- 'evidence'|'judgment'
  text TEXT NOT NULL,
  provenance_json TEXT,                     -- required for evidence claims
  basis_claim_ids_json TEXT,                -- required for judgment claims
  confidence TEXT
);
CREATE TABLE computations (
  id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, tool TEXT NOT NULL,
  args_json TEXT NOT NULL, result_json TEXT NOT NULL,
  status TEXT NOT NULL,                     -- computed|unavailable|not_applicable
  formula_version TEXT NOT NULL, as_of TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE verification_results (
  id INTEGER PRIMARY KEY, analysis_id INTEGER NOT NULL,
  check_id TEXT NOT NULL,                   -- V1..V6 sub-checks e.g. 'V2b'
  verdict TEXT NOT NULL,                    -- pass|fail|warn|skipped_not_applicable
  severity TEXT NOT NULL,                   -- blocking|warning|info
  detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE signal_shadow_log (
  id INTEGER PRIMARY KEY, accession_number TEXT NOT NULL, ticker TEXT NOT NULL,
  review_posture TEXT NOT NULL,
  hypothetical_signal TEXT NOT NULL,
  rules_fired_json TEXT NOT NULL, rules_skipped_json TEXT NOT NULL,
  computed_inputs_json TEXT NOT NULL,
  price_at_eval REAL, created_at TEXT NOT NULL,
  outcome_30d REAL, outcome_90d REAL, outcome_reviewed_at TEXT
);
CREATE TABLE digests (
  id INTEGER PRIMARY KEY, run_at TEXT NOT NULL, since TEXT, until TEXT,
  markdown_path TEXT NOT NULL, filings_json TEXT NOT NULL
);
