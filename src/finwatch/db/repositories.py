"""Thin repository layer with typed (pydantic) row mappers.

Every method is a small, explicit SQL call. Row models mirror the schema columns
one-to-one so ``Model(**dict(row))`` round-trips. Booleans-as-INTEGER (owned,
is_financial, is_amendment) are kept as ``int`` to match the DB exactly.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from pydantic import BaseModel


# --------------------------------------------------------------- row models --
class Company(BaseModel):
    cik: str
    ticker: str
    name: str | None = None
    sic_code: str | None = None
    sector_class: str | None = None
    is_financial: int = 0
    added_at: str


class Holding(BaseModel):
    id: int | None = None
    cik: str
    ticker: str
    owned: int
    shares: float | None = None
    cost_basis: float | None = None
    target_weight_pct: float | None = None
    horizon: str | None = None
    thesis: str | None = None
    added_at: str


class Filing(BaseModel):
    accession_number: str
    cik: str
    form_type: str
    filed_at: str
    period_of_report: str | None = None
    is_amendment: int = 0
    amends_accession: str | None = None
    primary_doc_url: str | None = None
    raw_sha256: str | None = None
    fetched_at: str | None = None
    processed_at: str | None = None
    status: str = "fetched"


class XbrlFact(BaseModel):
    id: int | None = None
    cik: str
    taxonomy: str
    tag: str
    value: float | None = None
    unit_ref: str | None = None
    decimals: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    instant: str | None = None
    fy: str | None = None
    fp: str | None = None
    form: str | None = None
    accession_number: str | None = None
    dimensions_json: str = "{}"


class Price(BaseModel):
    ticker: str
    date: str
    close: float


class FilingSection(BaseModel):
    id: int | None = None
    accession_number: str
    section_key: str
    title: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    html_element_id: str | None = None
    is_furnished: int = 0
    text: str
    text_sha256: str


class Computation(BaseModel):
    id: int | None = None
    ticker: str
    tool: str                 # metric name
    args_json: str
    result_json: str          # the MetricResult, model_dump_json()
    status: str               # computed | unavailable | not_applicable
    formula_version: str
    as_of: str
    created_at: str


class VerificationResult(BaseModel):
    id: int | None = None
    analysis_id: int
    check_id: str             # V1..V6 sub-checks, e.g. 'V2b'
    verdict: str              # pass | fail | warn | skipped_not_applicable
    severity: str             # blocking | warning | info
    detail: str | None = None
    created_at: str


class Analysis(BaseModel):
    id: int | None = None
    accession_number: str
    ticker: str
    stage: str                # 'P1' | 'P2' | 'P3'
    model: str
    prompt_version: str
    output_json: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    created_at: str


class AnalysisClaim(BaseModel):
    claim_id: str             # globally unique (namespaced by analysis id on persist)
    analysis_id: int
    claim_type: str           # 'evidence' | 'judgment'
    text: str
    provenance_json: str | None = None
    basis_claim_ids_json: str | None = None
    confidence: str | None = None


class SignalShadowLog(BaseModel):
    id: int | None = None
    accession_number: str
    ticker: str
    review_posture: str
    hypothetical_signal: str
    rules_fired_json: str
    rules_skipped_json: str
    computed_inputs_json: str
    price_at_eval: float | None = None
    created_at: str
    outcome_30d: float | None = None
    outcome_90d: float | None = None
    outcome_reviewed_at: str | None = None


# ------------------------------------------------------------------- repo --
class Repo:
    """A thin typed wrapper over an open SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- companies -------------------------------------------------------
    def upsert_company(self, c: Company) -> None:
        """Insert-or-update identity. Never clobbers sector fields with NULLs, and
        only updates ``is_financial`` when a fresh ``sic_code`` is supplied (so an
        ``add`` with no SIC does not reset an ingest-derived classification)."""
        self.conn.execute(
            """
            INSERT INTO companies
                (cik, ticker, name, sic_code, sector_class, is_financial, added_at)
            VALUES
                (:cik, :ticker, :name, :sic_code, :sector_class, :is_financial, :added_at)
            ON CONFLICT(cik) DO UPDATE SET
                ticker = excluded.ticker,
                name = COALESCE(excluded.name, companies.name),
                sic_code = COALESCE(excluded.sic_code, companies.sic_code),
                -- sector_class and is_financial are BOTH derived from the SIC, so they
                -- update or preserve together, gated on a fresh sic_code. Guarding only
                -- one would leave a contradictory (sector_class, is_financial) pair.
                sector_class = CASE WHEN excluded.sic_code IS NOT NULL
                                    THEN excluded.sector_class ELSE companies.sector_class END,
                is_financial = CASE WHEN excluded.sic_code IS NOT NULL
                                    THEN excluded.is_financial ELSE companies.is_financial END
            """,
            c.model_dump(),
        )
        self.conn.commit()

    def get_company(self, cik: str) -> Company | None:
        row = self.conn.execute("SELECT * FROM companies WHERE cik = ?", (cik,)).fetchone()
        return None if row is None else Company(**dict(row))

    def get_company_by_ticker(self, ticker: str) -> Company | None:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE ticker = ? COLLATE NOCASE LIMIT 1", (ticker,)
        ).fetchone()
        return None if row is None else Company(**dict(row))

    def list_companies(self) -> list[Company]:
        rows = self.conn.execute("SELECT * FROM companies ORDER BY ticker").fetchall()
        return [Company(**dict(r)) for r in rows]

    def list_tracked_ciks(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT cik FROM holdings ORDER BY cik"
        ).fetchall()
        return [r["cik"] for r in rows]

    # ---- holdings --------------------------------------------------------
    def upsert_holding(self, h: Holding) -> int:
        """One holding per CIK at the app level: update if present, else insert."""
        existing = self.conn.execute(
            "SELECT id FROM holdings WHERE cik = ?", (h.cik,)
        ).fetchone()
        if existing is not None:
            self.conn.execute(
                """UPDATE holdings SET ticker=?, owned=?, shares=?, cost_basis=?,
                       target_weight_pct=?, horizon=?, thesis=? WHERE id=?""",
                (h.ticker, h.owned, h.shares, h.cost_basis, h.target_weight_pct,
                 h.horizon, h.thesis, existing["id"]),
            )
            holding_id = int(existing["id"])
        else:
            cur = self.conn.execute(
                """INSERT INTO holdings
                       (cik, ticker, owned, shares, cost_basis, target_weight_pct,
                        horizon, thesis, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (h.cik, h.ticker, h.owned, h.shares, h.cost_basis, h.target_weight_pct,
                 h.horizon, h.thesis, h.added_at),
            )
            holding_id = int(cur.lastrowid)
        self.conn.commit()
        return holding_id

    def get_holding_by_cik(self, cik: str) -> Holding | None:
        row = self.conn.execute("SELECT * FROM holdings WHERE cik = ?", (cik,)).fetchone()
        return None if row is None else Holding(**dict(row))

    def list_holdings(self, owned: bool | None = None) -> list[Holding]:
        if owned is None:
            rows = self.conn.execute("SELECT * FROM holdings ORDER BY ticker").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM holdings WHERE owned = ? ORDER BY ticker", (int(owned),)
            ).fetchall()
        return [Holding(**dict(r)) for r in rows]

    # ---- filings ---------------------------------------------------------
    def upsert_filing(self, f: Filing) -> bool:
        """Idempotent index insert. Returns True if a new row was created; existing
        rows are left untouched (filings are immutable index entries)."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO filings
                   (accession_number, cik, form_type, filed_at, period_of_report,
                    is_amendment, amends_accession, primary_doc_url, raw_sha256,
                    fetched_at, processed_at, status)
               VALUES
                   (:accession_number, :cik, :form_type, :filed_at, :period_of_report,
                    :is_amendment, :amends_accession, :primary_doc_url, :raw_sha256,
                    :fetched_at, :processed_at, :status)""",
            f.model_dump(),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_filing(self, accession_number: str) -> Filing | None:
        row = self.conn.execute(
            "SELECT * FROM filings WHERE accession_number = ?", (accession_number,)
        ).fetchone()
        return None if row is None else Filing(**dict(row))

    def list_filings(self, cik: str | None = None) -> list[Filing]:
        if cik is None:
            rows = self.conn.execute(
                "SELECT * FROM filings ORDER BY filed_at DESC"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM filings WHERE cik = ? ORDER BY filed_at DESC", (cik,)
            ).fetchall()
        return [Filing(**dict(r)) for r in rows]

    def known_accessions(self, cik: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT accession_number FROM filings WHERE cik = ?", (cik,)
        ).fetchall()
        return {r["accession_number"] for r in rows}

    # ---- xbrl facts ------------------------------------------------------
    def replace_xbrl_facts(self, cik: str, facts: Iterable[XbrlFact]) -> int:
        """Delete this CIK's facts then bulk-insert the supplied set. companyfacts
        already carries full, amendment-superseded history, so a clean replace keeps
        the table faithful to the latest fetch."""
        rows = [
            (f.cik, f.taxonomy, f.tag, f.value, f.unit_ref, f.decimals,
             f.period_start, f.period_end, f.instant, f.fy, f.fp, f.form,
             f.accession_number, f.dimensions_json)
            for f in facts
        ]
        self.conn.execute("DELETE FROM xbrl_facts WHERE cik = ?", (cik,))
        self.conn.executemany(
            """INSERT INTO xbrl_facts
                   (cik, taxonomy, tag, value, unit_ref, decimals, period_start,
                    period_end, instant, fy, fp, form, accession_number, dimensions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count_xbrl_facts(self, cik: str | None = None) -> int:
        if cik is None:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM xbrl_facts").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM xbrl_facts WHERE cik = ?", (cik,)
            ).fetchone()
        return int(row["n"])

    def list_xbrl_facts(self, cik: str) -> list[XbrlFact]:
        rows = self.conn.execute(
            "SELECT * FROM xbrl_facts WHERE cik = ?", (cik,)
        ).fetchall()
        return [XbrlFact(**dict(r)) for r in rows]

    # ---- prices ----------------------------------------------------------
    def upsert_prices(self, prices: Iterable[Price]) -> int:
        rows = [(p.ticker.upper(), p.date, p.close) for p in prices]
        self.conn.executemany(
            "INSERT OR REPLACE INTO prices (ticker, date, close) VALUES (?, ?, ?)", rows
        )
        self.conn.commit()
        return len(rows)

    def close_on_or_before(self, ticker: str, date_iso: str) -> float | None:
        row = self.conn.execute(
            "SELECT close FROM prices WHERE ticker = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (ticker.upper(), date_iso),
        ).fetchone()
        return None if row is None else float(row["close"])

    def count_prices(self, ticker: str | None = None) -> int:
        if ticker is None:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM prices").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM prices WHERE ticker = ?", (ticker.upper(),)
            ).fetchone()
        return int(row["n"])

    # ---- settings --------------------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else row["value"]

    # ---- filing sections (P0 output) -------------------------------------
    def replace_filing_sections(
        self, accession_number: str, sections: Iterable[FilingSection]
    ) -> int:
        """Replace a filing's sections, keeping the external-content FTS index in sync.

        External-content FTS5 needs an explicit 'delete' for each old row before the
        content row goes away, then a matching insert for each new row.
        """
        old = self.conn.execute(
            "SELECT id, text FROM filing_sections WHERE accession_number = ?",
            (accession_number,),
        ).fetchall()
        for r in old:
            self.conn.execute(
                "INSERT INTO section_fts(section_fts, rowid, text) VALUES ('delete', ?, ?)",
                (r["id"], r["text"]),
            )
        self.conn.execute(
            "DELETE FROM filing_sections WHERE accession_number = ?", (accession_number,)
        )
        count = 0
        for s in sections:
            cur = self.conn.execute(
                """INSERT INTO filing_sections
                       (accession_number, section_key, title, char_start, char_end,
                        html_element_id, is_furnished, text, text_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (accession_number, s.section_key, s.title, s.char_start, s.char_end,
                 s.html_element_id, int(s.is_furnished), s.text, s.text_sha256),
            )
            self.conn.execute(
                "INSERT INTO section_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, s.text)
            )
            count += 1
        self.conn.commit()
        return count

    def list_filing_sections(self, accession_number: str) -> list[FilingSection]:
        rows = self.conn.execute(
            "SELECT * FROM filing_sections WHERE accession_number = ? ORDER BY char_start",
            (accession_number,),
        ).fetchall()
        return [FilingSection(**dict(r)) for r in rows]

    def get_filing_section(
        self, accession_number: str, section_key: str
    ) -> FilingSection | None:
        row = self.conn.execute(
            "SELECT * FROM filing_sections WHERE accession_number = ? AND section_key = ? "
            "ORDER BY char_start LIMIT 1",
            (accession_number, section_key),
        ).fetchone()
        return None if row is None else FilingSection(**dict(row))

    def prior_comparable_section(
        self, cik: str, base_form: str, section_key: str, filed_before: str
    ) -> tuple[str, str] | None:
        """(accession, section text) of the most recent non-amendment filing of the
        same base form with this section, filed before ``filed_before``. For the
        risk-factor diff's prior comparable."""
        row = self.conn.execute(
            """SELECT f.accession_number AS accn, s.text AS text
                 FROM filings f JOIN filing_sections s
                   ON s.accession_number = f.accession_number
                WHERE f.cik = ? AND f.form_type = ? AND f.is_amendment = 0
                  AND s.section_key = ? AND f.filed_at < ?
                ORDER BY f.filed_at DESC LIMIT 1""",
            (cik, base_form, section_key, filed_before),
        ).fetchone()
        return (row["accn"], row["text"]) if row else None

    # ---- filing lifecycle ------------------------------------------------
    def set_amends_accession(self, accession_number: str, amends: str | None) -> None:
        self.conn.execute(
            "UPDATE filings SET amends_accession = ? WHERE accession_number = ?",
            (amends, accession_number),
        )
        self.conn.commit()

    def find_amended_accession(
        self, cik: str, base_form: str, period_of_report: str | None, filed_before: str
    ) -> str | None:
        """Best-effort: the original filing an amendment corrects — the most recent
        non-amendment of the same base form and period, filed earlier."""
        if not period_of_report:
            return None
        row = self.conn.execute(
            """SELECT accession_number FROM filings
                WHERE cik = ? AND form_type = ? AND is_amendment = 0
                  AND period_of_report = ? AND filed_at < ?
                ORDER BY filed_at DESC LIMIT 1""",
            (cik, base_form, period_of_report, filed_before),
        ).fetchone()
        return row["accession_number"] if row else None

    def set_filing_status(
        self, accession_number: str, status: str, processed_at: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE filings SET status = ?, "
            "processed_at = COALESCE(?, processed_at) WHERE accession_number = ?",
            (status, processed_at, accession_number),
        )
        self.conn.commit()

    # ---- computations (metric results) -----------------------------------
    def insert_computations(self, computations: Iterable[Computation]) -> int:
        rows = [
            (c.ticker, c.tool, c.args_json, c.result_json, c.status,
             c.formula_version, c.as_of, c.created_at)
            for c in computations
        ]
        self.conn.executemany(
            """INSERT INTO computations
                   (ticker, tool, args_json, result_json, status, formula_version,
                    as_of, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def list_computations(self, ticker: str | None = None) -> list[Computation]:
        if ticker is None:
            rows = self.conn.execute(
                "SELECT * FROM computations ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM computations WHERE ticker = ? ORDER BY id", (ticker,)
            ).fetchall()
        return [Computation(**dict(r)) for r in rows]

    def count_computations(self, ticker: str | None = None) -> int:
        if ticker is None:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM computations").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM computations WHERE ticker = ?", (ticker,)
            ).fetchone()
        return int(row["n"])

    # ---- verification results --------------------------------------------
    def insert_verification_results(self, results: Iterable[VerificationResult]) -> int:
        rows = [
            (r.analysis_id, r.check_id, r.verdict, r.severity, r.detail, r.created_at)
            for r in results
        ]
        self.conn.executemany(
            """INSERT INTO verification_results
                   (analysis_id, check_id, verdict, severity, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def list_verification_results(self, analysis_id: int) -> list[VerificationResult]:
        rows = self.conn.execute(
            "SELECT * FROM verification_results WHERE analysis_id = ? ORDER BY id",
            (analysis_id,),
        ).fetchall()
        return [VerificationResult(**dict(r)) for r in rows]

    def count_verification_results(self, analysis_id: int | None = None) -> int:
        if analysis_id is None:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM verification_results"
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM verification_results WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        return int(row["n"])

    # ---- analyses + claim graph ------------------------------------------
    def insert_analysis(self, a: Analysis) -> int:
        cur = self.conn.execute(
            """INSERT INTO analyses
                   (accession_number, ticker, stage, model, prompt_version, output_json,
                    tokens_in, tokens_out, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (a.accession_number, a.ticker, a.stage, a.model, a.prompt_version,
             a.output_json, a.tokens_in, a.tokens_out, a.cost_usd, a.created_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def get_analysis(self, analysis_id: int) -> Analysis | None:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
        return None if row is None else Analysis(**dict(row))

    def latest_analysis(self, accession_number: str, stage: str) -> Analysis | None:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE accession_number = ? AND stage = ? "
            "ORDER BY id DESC LIMIT 1",
            (accession_number, stage),
        ).fetchone()
        return None if row is None else Analysis(**dict(row))

    def list_analyses(self, accession_number: str | None = None) -> list[Analysis]:
        if accession_number is None:
            rows = self.conn.execute("SELECT * FROM analyses ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM analyses WHERE accession_number = ? ORDER BY id",
                (accession_number,),
            ).fetchall()
        return [Analysis(**dict(r)) for r in rows]

    def insert_analysis_claims(self, claims: Iterable[AnalysisClaim]) -> int:
        rows = [
            (c.claim_id, c.analysis_id, c.claim_type, c.text, c.provenance_json,
             c.basis_claim_ids_json, c.confidence)
            for c in claims
        ]
        self.conn.executemany(
            """INSERT INTO analysis_claims
                   (claim_id, analysis_id, claim_type, text, provenance_json,
                    basis_claim_ids_json, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def list_analysis_claims(self, analysis_id: int) -> list[AnalysisClaim]:
        rows = self.conn.execute(
            "SELECT * FROM analysis_claims WHERE analysis_id = ? ORDER BY claim_id",
            (analysis_id,),
        ).fetchall()
        return [AnalysisClaim(**dict(r)) for r in rows]

    # ---- signal shadow log -----------------------------------------------
    def insert_shadow_log(self, row: SignalShadowLog) -> int:
        cur = self.conn.execute(
            """INSERT INTO signal_shadow_log
                   (accession_number, ticker, review_posture, hypothetical_signal,
                    rules_fired_json, rules_skipped_json, computed_inputs_json,
                    price_at_eval, created_at, outcome_30d, outcome_90d, outcome_reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row.accession_number, row.ticker, row.review_posture, row.hypothetical_signal,
             row.rules_fired_json, row.rules_skipped_json, row.computed_inputs_json,
             row.price_at_eval, row.created_at, row.outcome_30d, row.outcome_90d,
             row.outcome_reviewed_at),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_shadow_log(self, ticker: str | None = None) -> list[SignalShadowLog]:
        if ticker is None:
            rows = self.conn.execute(
                "SELECT * FROM signal_shadow_log ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM signal_shadow_log WHERE ticker = ? ORDER BY id", (ticker,)
            ).fetchall()
        return [SignalShadowLog(**dict(r)) for r in rows]

    def count_shadow_log(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM signal_shadow_log").fetchone()["n"])
