"""Thin repository layer with typed (pydantic) row mappers.

Every method is a small, explicit SQL call. Row models mirror the schema columns
one-to-one so ``Model(**dict(row))`` round-trips. Booleans-as-INTEGER (owned,
is_financial, is_amendment) are kept as ``int`` to match the DB exactly.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable

from pydantic import BaseModel, FiniteFloat

LOCAL_USER_ID = "local"


# --------------------------------------------------------------- row models --
class User(BaseModel):
    id: str
    email: str
    created_at: str
    last_login_at: str


class Company(BaseModel):
    cik: str
    ticker: str
    name: str | None = None
    sic_code: str | None = None
    is_financial: int = 0
    added_at: str


class UserCompany(BaseModel):
    user_id: str
    cik: str
    tracked_at: str


class UserPreference(BaseModel):
    user_id: str
    period: str


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


class FilingStageRun(BaseModel):
    accession_number: str
    stage: str
    status: str
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    diagnostics_json: str = "{}"


class XbrlFact(BaseModel):
    id: int | None = None
    cik: str
    taxonomy: str
    tag: str
    value: FiniteFloat | None = None
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
    tool: str  # metric name
    args_json: str
    result_json: str  # the MetricResult, model_dump_json()
    status: str  # computed | unavailable | not_applicable
    formula_version: str
    as_of: str
    created_at: str


class VerificationResult(BaseModel):
    id: int | None = None
    analysis_id: int
    check_id: str  # V1..V6 sub-checks, e.g. 'V2b'
    verdict: str  # pass | fail | warn | skipped_not_applicable
    severity: str  # blocking | warning | info
    detail: str | None = None
    created_at: str


class Analysis(BaseModel):
    id: int | None = None
    accession_number: str
    ticker: str
    stage: str  # 'P1' | 'P2' | 'P3'
    model: str
    prompt_version: str
    output_json: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    created_at: str


class Digest(BaseModel):
    id: int | None = None
    run_at: str
    since: str | None = None
    until: str | None = None
    markdown_path: str
    filings_json: str  # JSON array of accession numbers covered


# ------------------------------------------------------------------- repo --
class Repo:
    """A thin typed wrapper over an open SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- users -----------------------------------------------------------
    def create_user(self, user: User) -> bool:
        """Create a user; return False when that normalized email already exists."""
        cur = self.conn.execute(
            """INSERT INTO users (id, email, created_at, last_login_at)
               VALUES (:id, :email, :created_at, :last_login_at)
               ON CONFLICT(email) DO NOTHING""",
            user.model_dump(),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_user(self, user_id: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return None if row is None else User(**dict(row))

    def get_user_by_email(self, email: str) -> User | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        return None if row is None else User(**dict(row))

    def update_user_last_login(self, user_id: str, *, at: str) -> bool:
        cur = self.conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?", (at, user_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- companies -------------------------------------------------------
    def upsert_company(self, c: Company) -> None:
        """Insert-or-update issuer identity. Never clobbers name/sic with NULLs, updates
        ``is_financial`` only when a fresh ``sic_code`` is supplied. Tracking is stored
        separately per user, so a profile refresh cannot change it."""
        self.conn.execute(
            """
            INSERT INTO companies
                (cik, ticker, name, sic_code, is_financial, added_at)
            VALUES
                (:cik, :ticker, :name, :sic_code, :is_financial, :added_at)
            ON CONFLICT(cik) DO UPDATE SET
                ticker = excluded.ticker,
                name = COALESCE(excluded.name, companies.name),
                sic_code = COALESCE(excluded.sic_code, companies.sic_code),
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

    def list_tracked_companies(self, user_id: str = LOCAL_USER_ID) -> list[Company]:
        rows = self.conn.execute(
            """SELECT c.* FROM companies c
                 JOIN user_companies uc ON uc.cik = c.cik
                WHERE uc.user_id = ? ORDER BY c.ticker""",
            (user_id,),
        ).fetchall()
        return [Company(**dict(r)) for r in rows]

    def list_tracked_ciks(self, user_id: str = LOCAL_USER_ID) -> list[str]:
        rows = self.conn.execute(
            "SELECT cik FROM user_companies WHERE user_id = ? ORDER BY cik",
            (user_id,),
        ).fetchall()
        return [r["cik"] for r in rows]

    def count_tracked_companies(self, user_id: str = LOCAL_USER_ID) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM user_companies WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["n"])

    def get_user_company(self, user_id: str, cik: str) -> UserCompany | None:
        row = self.conn.execute(
            "SELECT * FROM user_companies WHERE user_id = ? AND cik = ?", (user_id, cik)
        ).fetchone()
        return None if row is None else UserCompany(**dict(row))

    def track_company(
        self, cik: str, *, at: str, user_id: str = LOCAL_USER_ID
    ) -> bool:
        """Track an issuer for one user, preserving the first tracking timestamp."""
        cur = self.conn.execute(
            """INSERT INTO user_companies (user_id, cik, tracked_at) VALUES (?, ?, ?)
               ON CONFLICT(user_id, cik) DO NOTHING""",
            (user_id, cik, at),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def untrack_company(self, cik: str, *, user_id: str = LOCAL_USER_ID) -> bool:
        """Stop one user tracking an issuer while retaining shared public history."""
        cur = self.conn.execute(
            "DELETE FROM user_companies WHERE user_id = ? AND cik = ?",
            (user_id, cik),
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ---- user preferences -----------------------------------------------
    def set_user_period(self, user_id: str, period: str) -> None:
        self.conn.execute(
            """INSERT INTO user_preferences (user_id, period) VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET period = excluded.period""",
            (user_id, period),
        )
        self.conn.commit()

    def get_user_period(self, user_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT period FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
        return None if row is None else str(row["period"])

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
            rows = self.conn.execute("SELECT * FROM filings ORDER BY filed_at DESC").fetchall()
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
            (
                f.cik,
                f.taxonomy,
                f.tag,
                f.value,
                f.unit_ref,
                f.decimals,
                f.period_start,
                f.period_end,
                f.instant,
                f.fy,
                f.fp,
                f.form,
                f.accession_number,
                f.dimensions_json,
            )
            for f in facts
        ]
        with self.conn:
            self.conn.execute("DELETE FROM xbrl_facts WHERE cik = ?", (cik,))
            self.conn.executemany(
                """INSERT INTO xbrl_facts
                       (cik, taxonomy, tag, value, unit_ref, decimals, period_start,
                        period_end, instant, fy, fp, form, accession_number, dimensions_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
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
        rows = self.conn.execute("SELECT * FROM xbrl_facts WHERE cik = ?", (cik,)).fetchall()
        return [XbrlFact(**dict(r)) for r in rows]

    # ---- settings --------------------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    # ---- filing sections (P0 output) -------------------------------------
    def replace_filing_sections(
        self, accession_number: str, sections: Iterable[FilingSection]
    ) -> int:
        """Replace a filing's sections, keeping the external-content FTS index in sync.

        External-content FTS5 needs an explicit 'delete' for each old row before the
        content row goes away, then a matching insert for each new row.
        """
        rows = list(sections)
        with self.conn:
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
            for s in rows:
                cur = self.conn.execute(
                    """INSERT INTO filing_sections
                           (accession_number, section_key, title, char_start, char_end,
                            html_element_id, is_furnished, text, text_sha256)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        accession_number,
                        s.section_key,
                        s.title,
                        s.char_start,
                        s.char_end,
                        s.html_element_id,
                        int(s.is_furnished),
                        s.text,
                        s.text_sha256,
                    ),
                )
                self.conn.execute(
                    "INSERT INTO section_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, s.text)
                )
        return len(rows)

    def list_filing_sections(self, accession_number: str) -> list[FilingSection]:
        rows = self.conn.execute(
            "SELECT * FROM filing_sections WHERE accession_number = ? ORDER BY char_start",
            (accession_number,),
        ).fetchall()
        return [FilingSection(**dict(r)) for r in rows]

    def get_filing_section(self, accession_number: str, section_key: str) -> FilingSection | None:
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

    def has_prior_comparable_filing(
        self, cik: str, base_form: str, filed_before: str
    ) -> bool:
        """Whether an earlier non-amendment filing of the same form exists.

        This is intentionally filing-level rather than section-level: a comparable
        prior filing may exist even when a section is new in the current document.
        """
        row = self.conn.execute(
            """SELECT 1
                 FROM filings
                WHERE cik = ? AND form_type = ? AND is_amendment = 0
                  AND filed_at < ?
                LIMIT 1""",
            (cik, base_form, filed_before),
        ).fetchone()
        return row is not None

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

    # ---- per-filing pipeline progress -----------------------------------
    def set_filing_stage(
        self,
        accession_number: str,
        stage: str,
        status: str,
        *,
        at: str,
        error: str | None = None,
        diagnostics: dict | None = None,
    ) -> None:
        """Persist the current state of one resumable pipeline stage.

        Starting a stage increments its attempt counter. Completion/failure keeps the
        original start time and records when that attempt finished.
        """
        payload = json.dumps(diagnostics or {}, ensure_ascii=False, sort_keys=True)
        self.conn.execute(
            """INSERT INTO filing_stage_runs
                   (accession_number, stage, status, attempts, started_at, finished_at,
                    error, diagnostics_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(accession_number, stage) DO UPDATE SET
                 status = excluded.status,
                 attempts = filing_stage_runs.attempts
                            + CASE WHEN excluded.status = 'running' THEN 1 ELSE 0 END,
                 started_at = CASE WHEN excluded.status = 'running'
                                   THEN excluded.started_at
                                   ELSE filing_stage_runs.started_at END,
                 finished_at = excluded.finished_at,
                 error = excluded.error,
                 diagnostics_json = excluded.diagnostics_json""",
            (
                accession_number,
                stage,
                status,
                1 if status == "running" else 0,
                at if status == "running" else None,
                None if status == "running" else at,
                error,
                payload,
            ),
        )
        self.conn.commit()

    def get_filing_stage(self, accession_number: str, stage: str) -> FilingStageRun | None:
        row = self.conn.execute(
            "SELECT * FROM filing_stage_runs WHERE accession_number = ? AND stage = ?",
            (accession_number, stage),
        ).fetchone()
        return None if row is None else FilingStageRun(**dict(row))

    def list_filing_stages(self, accession_number: str) -> list[FilingStageRun]:
        rows = self.conn.execute(
            "SELECT * FROM filing_stage_runs WHERE accession_number = ?",
            (accession_number,),
        ).fetchall()
        return [FilingStageRun(**dict(row)) for row in rows]

    # ---- computations (metric results) -----------------------------------
    def insert_computations(self, computations: Iterable[Computation]) -> int:
        rows = [
            (
                c.ticker,
                c.tool,
                c.args_json,
                c.result_json,
                c.status,
                c.formula_version,
                c.as_of,
                c.created_at,
            )
            for c in computations
        ]
        with self.conn:
            self.conn.executemany(
                """INSERT INTO computations
                       (ticker, tool, args_json, result_json, status, formula_version,
                        as_of, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        return len(rows)

    def list_computations(self, ticker: str | None = None) -> list[Computation]:
        if ticker is None:
            rows = self.conn.execute("SELECT * FROM computations ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM computations WHERE ticker = ? ORDER BY id", (ticker,)
            ).fetchall()
        return [Computation(**dict(r)) for r in rows]

    def latest_computations(self, ticker: str) -> list[Computation]:
        """Greatest ``as_of`` computation per metric, breaking date ties by id."""
        rows = self.conn.execute(
            """SELECT c.*
                 FROM computations c
                WHERE c.ticker = ?
                  AND NOT EXISTS (
                      SELECT 1
                        FROM computations newer
                       WHERE newer.ticker = c.ticker
                         AND newer.tool = c.tool
                         AND (newer.as_of > c.as_of
                              OR (newer.as_of = c.as_of AND newer.id > c.id))
                  )
                ORDER BY c.tool""",
            (ticker,),
        ).fetchall()
        return [Computation(**dict(r)) for r in rows]

    def computations_as_of(self, ticker: str, as_of: str) -> list[Computation]:
        """Greatest date at/before ``as_of`` per metric, breaking ties by id."""
        rows = self.conn.execute(
            """SELECT c.*
                 FROM computations c
                WHERE c.ticker = ? AND c.as_of <= ?
                  AND NOT EXISTS (
                      SELECT 1
                        FROM computations newer
                       WHERE newer.ticker = c.ticker
                         AND newer.tool = c.tool
                         AND newer.as_of <= ?
                         AND (newer.as_of > c.as_of
                              OR (newer.as_of = c.as_of AND newer.id > c.id))
                  )
                ORDER BY c.tool""",
            (ticker, as_of, as_of),
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
            row = self.conn.execute("SELECT COUNT(*) AS n FROM verification_results").fetchone()
        else:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM verification_results WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        return int(row["n"])

    # ---- analyses --------------------------------------------------------
    def _insert_analysis_row(self, analysis: Analysis) -> int:
        cur = self.conn.execute(
            """INSERT INTO analyses
                   (accession_number, ticker, stage, model, prompt_version, output_json,
                    tokens_in, tokens_out, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                analysis.accession_number,
                analysis.ticker,
                analysis.stage,
                analysis.model,
                analysis.prompt_version,
                analysis.output_json,
                analysis.tokens_in,
                analysis.tokens_out,
                analysis.cost_usd,
                analysis.created_at,
            ),
        )
        return int(cur.lastrowid)

    def insert_analysis(self, a: Analysis) -> int:
        cur_id = self._insert_analysis_row(a)
        self.conn.commit()
        return cur_id

    def insert_p1_with_trace(
        self,
        p1: Analysis,
        trace_factory: Callable[[int, str], Analysis],
    ) -> tuple[int, int, str]:
        """Atomically persist one P1 output and its exact linked harness trace.

        ``trace_factory`` is intentionally pure and receives the inserted P1 ID plus
        the SHA-256 of the exact UTF-8 bytes stored in ``P1.output_json``.  A factory
        or second-insert failure rolls the entire pair back, so no successful research
        attempt can expose an unlinked P1 row.
        """
        if p1.stage != "P1" or p1.id is not None:
            raise ValueError("paired analysis insert requires a new P1 row")
        p1_sha256 = hashlib.sha256(p1.output_json.encode("utf-8")).hexdigest()
        with self.conn:
            p1_id = self._insert_analysis_row(p1)
            trace = trace_factory(p1_id, p1_sha256)
            if trace.id is not None or trace.stage != "P1_TRACE":
                raise ValueError("trace factory must return a new P1_TRACE row")
            if (
                trace.accession_number != p1.accession_number
                or trace.ticker != p1.ticker
            ):
                raise ValueError("P1 and P1_TRACE identities must match")
            try:
                trace_payload = json.loads(trace.output_json)
            except (TypeError, ValueError) as exc:
                raise ValueError("P1_TRACE must contain valid JSON") from exc
            if not isinstance(trace_payload, dict):
                raise ValueError("P1_TRACE must contain a JSON object")
            if (
                trace_payload.get("schema_version") != "harness.v2"
                or trace_payload.get("p1_analysis_id") != p1_id
                or trace_payload.get("p1_output_sha256") != p1_sha256
            ):
                raise ValueError("P1_TRACE does not link the exact stored P1 bytes")
            trace_id = self._insert_analysis_row(trace)
        return p1_id, trace_id, p1_sha256

    def finalize_p1_attempt(
        self,
        p1_analysis_id: int,
        trace_analysis_id: int,
        *,
        verification_results: Iterable[VerificationResult],
        finalized_trace_json: str,
        filing_status: str,
        processed_at: str,
    ) -> int:
        """Atomically finalize one linked attempt and its publication gate.

        Verification rows, the immutable final trace snapshot, and the filing's
        terminal state move together.  Retained earlier P1/trace pairs remain linked
        historical audit records and are never deleted by finalization.
        """
        if filing_status not in {"verified", "analyzed", "failed"}:
            raise ValueError("invalid finalized filing status")
        results = list(verification_results)
        if any(result.analysis_id != p1_analysis_id for result in results):
            raise ValueError("verification results must belong to the linked P1")
        try:
            final_payload = json.loads(finalized_trace_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("finalized P1_TRACE must contain valid JSON") from exc
        with self.conn:
            p1_row = self.conn.execute(
                "SELECT * FROM analyses WHERE id = ?", (p1_analysis_id,)
            ).fetchone()
            trace_row = self.conn.execute(
                "SELECT * FROM analyses WHERE id = ?", (trace_analysis_id,)
            ).fetchone()
            if p1_row is None or p1_row["stage"] != "P1":
                raise ValueError("linked P1 analysis is missing or invalid")
            if trace_row is None or trace_row["stage"] != "P1_TRACE":
                raise ValueError("linked P1_TRACE analysis is missing or invalid")
            if (
                p1_row["accession_number"] != trace_row["accession_number"]
                or p1_row["ticker"] != trace_row["ticker"]
            ):
                raise ValueError("linked P1/P1_TRACE identities do not match")
            expected_sha256 = hashlib.sha256(
                str(p1_row["output_json"]).encode("utf-8")
            ).hexdigest()
            try:
                initial_payload = json.loads(trace_row["output_json"])
            except (TypeError, ValueError) as exc:
                raise ValueError("stored P1_TRACE is malformed") from exc
            for payload in (initial_payload, final_payload):
                if not isinstance(payload, dict):
                    raise ValueError("stored P1_TRACE must contain a JSON object")
                if (
                    payload.get("schema_version") != "harness.v2"
                    or payload.get("p1_analysis_id") != p1_analysis_id
                    or payload.get("p1_output_sha256") != expected_sha256
                ):
                    raise ValueError("P1_TRACE link or output hash is invalid")
            if final_payload.get("trace_analysis_id") != trace_analysis_id:
                raise ValueError("finalized P1_TRACE does not identify its own row")
            filing_snapshot = final_payload.get("filing_snapshot") or {}
            if (
                final_payload.get("publication_outcome") is None
                or final_payload.get("terminal_reason") is None
                or filing_snapshot.get("accession") != p1_row["accession_number"]
                or filing_snapshot.get("ticker") != p1_row["ticker"]
            ):
                raise ValueError("finalized P1_TRACE snapshot is incomplete")
            if filing_status == "verified" and final_payload["verification_verdict"] not in {
                "PASS",
                "PASS_WITH_WARNINGS",
            }:
                raise ValueError("verified status requires a passing verifier verdict")
            if filing_status == "analyzed" and final_payload["verification_verdict"] != "FAIL":
                raise ValueError("analyzed status requires a failed verifier verdict")
            if filing_status == "failed" and final_payload["terminal_reason"] != (
                "verification_incomplete"
            ):
                raise ValueError("failed finalization requires verification_incomplete")
            if filing_status == "failed" and final_payload.get("verification_verdict") is not None:
                raise ValueError("incomplete verification cannot carry a verifier verdict")
            if filing_status in {"analyzed", "failed"}:
                publication = final_payload.get("publication_snapshot") or {}
                if (
                    final_payload.get("publication_outcome") != "withheld"
                    or final_payload.get("published_finding_ids")
                    or publication.get("classification") is not None
                    or publication.get("evidence")
                    or any(
                        call.get("arguments")
                        for call in final_payload.get("tool_calls", [])
                        if isinstance(call, dict)
                    )
                ):
                    raise ValueError("withheld P1_TRACE must be redacted before persistence")
            elif final_payload.get("publication_outcome") == "withheld":
                raise ValueError("verified status cannot carry a withheld publication outcome")

            self.conn.execute(
                "DELETE FROM verification_results WHERE analysis_id = ?",
                (p1_analysis_id,),
            )
            rows = [
                (
                    result.analysis_id,
                    result.check_id,
                    result.verdict,
                    result.severity,
                    result.detail,
                    result.created_at,
                )
                for result in results
            ]
            self.conn.executemany(
                """INSERT INTO verification_results
                       (analysis_id, check_id, verdict, severity, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            updated = self.conn.execute(
                "UPDATE analyses SET output_json = ? WHERE id = ? AND stage = 'P1_TRACE'",
                (finalized_trace_json, trace_analysis_id),
            )
            if updated.rowcount != 1:
                raise ValueError("linked P1_TRACE could not be finalized")
            status = self.conn.execute(
                """UPDATE filings SET status = ?, processed_at = ?
                     WHERE accession_number = ?""",
                (filing_status, processed_at, p1_row["accession_number"]),
            )
            if status.rowcount != 1:
                raise ValueError("linked filing is missing")
        return len(results)

    def get_analysis(self, analysis_id: int) -> Analysis | None:
        row = self.conn.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        return None if row is None else Analysis(**dict(row))

    def latest_analysis(self, accession_number: str, stage: str) -> Analysis | None:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE accession_number = ? AND stage = ? "
            "ORDER BY id DESC LIMIT 1",
            (accession_number, stage),
        ).fetchone()
        return None if row is None else Analysis(**dict(row))

    def latest_linked_p1_attempt(
        self, accession_number: str
    ) -> tuple[Analysis, Analysis] | None:
        """Return the P1 linked by the latest strict v2 trace, or fail closed.

        Selection deliberately starts from the latest trace.  It never falls back to
        an earlier trace or independently selects the latest P1, preventing artifacts
        from different attempts from being combined after a retry or partial write.
        """
        trace = self.latest_analysis(accession_number, "P1_TRACE")
        if trace is None or trace.id is None:
            return None
        try:
            payload = json.loads(trace.output_json)
        except (TypeError, ValueError):
            return None
        # A payload that parses to a JSON scalar or array is still malformed state.
        # Dereferencing it raised AttributeError out of this selector, which 500s the
        # filing, certificate AND brief endpoints — one corrupt row denying a whole
        # workspace instead of failing this filing closed.
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != "harness.v2":
            return None
        p1_id = payload.get("p1_analysis_id")
        p1_sha256 = payload.get("p1_output_sha256")
        if not isinstance(p1_id, int) or not isinstance(p1_sha256, str):
            return None
        trace_id = payload.get("trace_analysis_id")
        if trace_id is not None and trace_id != trace.id:
            return None
        p1 = self.get_analysis(p1_id)
        if (
            p1 is None
            or p1.stage != "P1"
            or p1.accession_number != trace.accession_number
            or p1.ticker != trace.ticker
            or p1.accession_number != accession_number
            or hashlib.sha256(p1.output_json.encode("utf-8")).hexdigest() != p1_sha256
        ):
            return None
        return p1, trace

    def list_analyses(self, accession_number: str | None = None) -> list[Analysis]:
        if accession_number is None:
            rows = self.conn.execute("SELECT * FROM analyses ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM analyses WHERE accession_number = ? ORDER BY id",
                (accession_number,),
            ).fetchall()
        return [Analysis(**dict(r)) for r in rows]

    # ---- digests ---------------------------------------------------------
    def insert_digest(self, d: Digest) -> int:
        cur = self.conn.execute(
            """INSERT INTO digests (run_at, since, until, markdown_path, filings_json)
               VALUES (?, ?, ?, ?, ?)""",
            (d.run_at, d.since, d.until, d.markdown_path, d.filings_json),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_digests(self) -> list[Digest]:
        rows = self.conn.execute("SELECT * FROM digests ORDER BY id DESC").fetchall()
        return [Digest(**dict(r)) for r in rows]
