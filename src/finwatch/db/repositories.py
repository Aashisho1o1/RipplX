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
