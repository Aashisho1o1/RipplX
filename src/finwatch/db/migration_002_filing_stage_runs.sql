CREATE TABLE filing_stage_runs (
  accession_number TEXT NOT NULL REFERENCES filings(accession_number),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  diagnostics_json TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (accession_number, stage)
);

CREATE INDEX idx_filing_stage_runs_status
  ON filing_stage_runs(status);
