-- finwatch lean schema (v6). One product path: track tickers → analyze the newest
-- filing → six deterministic metrics → verified, canonical presentation. Installed
-- once on a fresh database by db/database.py::init_db, which stamps application_id +
-- user_version and refuses to open a database created by an older schema.

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL COLLATE NOCASE UNIQUE,
  created_at TEXT NOT NULL,
  last_login_at TEXT NOT NULL
);
-- CLI/local mode keeps its existing single-workspace behavior without public login.
INSERT INTO users (id, email, created_at, last_login_at) VALUES
  ('local', 'local@finwatch.invalid', '1970-01-01T00:00:00+00:00', '1970-01-01T00:00:00+00:00');

-- A company row exists once its ticker is resolved on EDGAR. User tracking is private
-- state in user_companies; issuer identity and public filing history remain shared.
CREATE TABLE companies (
  cik TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  name TEXT,
  sic_code TEXT,
  is_financial INTEGER NOT NULL DEFAULT 0,
  added_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_companies_ticker ON companies(ticker COLLATE NOCASE);

CREATE TABLE user_companies (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  tracked_at TEXT NOT NULL,
  PRIMARY KEY (user_id, cik)
);

CREATE TABLE user_preferences (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  period TEXT NOT NULL
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);   -- operator settings

CREATE TABLE filings (
  accession_number TEXT PRIMARY KEY,
  cik TEXT NOT NULL,
  form_type TEXT NOT NULL, filed_at TEXT NOT NULL, period_of_report TEXT,
  is_amendment INTEGER NOT NULL DEFAULT 0, amends_accession TEXT,
  primary_doc_url TEXT, raw_sha256 TEXT,
  fetched_at TEXT, processed_at TEXT,
  status TEXT NOT NULL DEFAULT 'fetched'   -- fetched|sectioned|analyzed|verified|failed
);
CREATE INDEX ix_filings_cik_filed ON filings(cik, filed_at DESC);

CREATE TABLE filing_sections (
  id INTEGER PRIMARY KEY, accession_number TEXT NOT NULL REFERENCES filings,
  section_key TEXT NOT NULL, title TEXT,
  char_start INTEGER, char_end INTEGER, html_element_id TEXT,
  is_furnished INTEGER NOT NULL DEFAULT 0,      -- Item 2.02 / 7.01 handling
  text TEXT NOT NULL, text_sha256 TEXT NOT NULL
);
CREATE VIRTUAL TABLE section_fts USING fts5(text, content='filing_sections', content_rowid='id');

CREATE TABLE filing_stage_runs (
  accession_number TEXT NOT NULL, stage TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  started_at TEXT, finished_at TEXT, error TEXT,
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (accession_number, stage)
);

CREATE TABLE xbrl_facts (
  id INTEGER PRIMARY KEY, cik TEXT NOT NULL,
  taxonomy TEXT NOT NULL, tag TEXT NOT NULL,
  value REAL, unit_ref TEXT, decimals TEXT,
  period_start TEXT, period_end TEXT, instant TEXT,
  fy TEXT, fp TEXT, form TEXT, accession_number TEXT,
  dimensions_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX ix_xbrl ON xbrl_facts(cik, tag, period_end, instant);

CREATE TABLE analyses (
  id INTEGER PRIMARY KEY, accession_number TEXT NOT NULL, ticker TEXT NOT NULL,
  stage TEXT NOT NULL,
  model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  output_json TEXT NOT NULL,
  tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE computations (
  id INTEGER PRIMARY KEY, ticker TEXT NOT NULL, tool TEXT NOT NULL,
  args_json TEXT NOT NULL, result_json TEXT NOT NULL,
  status TEXT NOT NULL,                     -- computed|unavailable|not_applicable
  formula_version TEXT NOT NULL, as_of TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE verification_results (
  id INTEGER PRIMARY KEY, analysis_id INTEGER NOT NULL,
  check_id TEXT NOT NULL,                   -- V1..V5 sub-checks e.g. 'V2b'
  verdict TEXT NOT NULL,                    -- pass|fail|warn|skipped_not_applicable
  severity TEXT NOT NULL,                   -- blocking|warning|info
  detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE digests (
  id INTEGER PRIMARY KEY, run_at TEXT NOT NULL, since TEXT, until TEXT,
  markdown_path TEXT NOT NULL, filings_json TEXT NOT NULL
);
