-- finwatch lean schema (v9). One product path: research a ticker → analyze the newest
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

-- Private product state. Public filing/fact history remains in the canonical tables
-- above; these rows are always scoped by user_id at the repository boundary.
CREATE TABLE company_profiles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  monitoring_enabled INTEGER NOT NULL DEFAULT 1,
  notification_level TEXT NOT NULL DEFAULT 'urgent',
  thesis_json TEXT NOT NULL DEFAULT '{"items":[]}',
  peer_ciks_json TEXT NOT NULL DEFAULT '[]',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, cik)
);

CREATE TABLE risk_snapshots (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  accession_number TEXT,
  snapshot_key TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (user_id, cik, snapshot_key)
);

CREATE TABLE attention_events (
  id INTEGER PRIMARY KEY,
  event_key TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  accession_number TEXT,
  priority TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL,
  risk_changes_json TEXT NOT NULL DEFAULT '[]',
  thesis_impacts_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  read_at TEXT
);
CREATE INDEX ix_attention_user_created ON attention_events(user_id, created_at DESC);

CREATE TABLE management_promises (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  accession_number TEXT NOT NULL,
  section_key TEXT NOT NULL,
  char_start INTEGER NOT NULL,
  char_end INTEGER NOT NULL,
  section_sha256 TEXT NOT NULL,
  quote TEXT NOT NULL,
  target_period TEXT,
  target_metric TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX ix_promises_user_cik ON management_promises(user_id, cik, created_at DESC);

CREATE TABLE research_runs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  cik TEXT NOT NULL REFERENCES companies(cik) ON DELETE CASCADE,
  status TEXT NOT NULL,                    -- queued|running|completed|partial|failed
  input_hash TEXT NOT NULL,
  artifact_json TEXT,
  trace_json TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX ix_research_user_cik_created
  ON research_runs(user_id, cik, created_at DESC);
CREATE INDEX ix_research_user_input
  ON research_runs(user_id, cik, input_hash, created_at DESC);

CREATE TABLE notification_deliveries (
  delivery_key TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  notification_type TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  period_key TEXT NOT NULL,
  sent_at TEXT,
  error_code TEXT
);

CREATE TABLE billing_accounts (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  stripe_customer_id TEXT UNIQUE,
  subscription_id TEXT,
  status TEXT NOT NULL DEFAULT 'free',
  price_id TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE broker_connections (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  external_user_id TEXT NOT NULL,
  encrypted_user_secret TEXT NOT NULL,
  status TEXT NOT NULL,
  refreshed_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE position_snapshots (
  id INTEGER PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider TEXT NOT NULL,
  as_of TEXT NOT NULL,
  positions_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (user_id, provider, as_of)
);
