"""Ingestion orchestration.

`add`/`watch` register a company + holding (keyed on CIK). `ingest` runs, for every
tracked CIK: refresh the issuer profile + filing index (bounded by a backfill window,
idempotent for incremental polling), flatten companyfacts into ``xbrl_facts``, and
pull Stooq EOD prices. Each CIK's steps fail independently so one bad ticker never
aborts the batch; partial progress is recorded.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from finwatch.config import Config
from finwatch.core.types import sector_from_sic
from finwatch.db.repositories import Company, Filing, Holding, Price, Repo, XbrlFact
from finwatch.ingest.edgar import EdgarClient, EdgarHTTPError, normalize_cik
from finwatch.ingest.stooq import StooqClient
from finwatch.ingest.tickers import resolve_ticker

DEFAULT_BACKFILL_QUARTERS = 8
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{doc}"


class TickerNotFoundError(Exception):
    def __init__(self, ticker: str) -> None:
        super().__init__(f"ticker not found in SEC company index: {ticker}")
        self.ticker = ticker


class CikIngestResult(BaseModel):
    cik: str
    ticker: str
    filings_indexed: int = 0
    filings_new: int = 0
    xbrl_facts: int = 0
    prices: int = 0
    error: str | None = None


class IngestSummary(BaseModel):
    results: list[CikIngestResult] = []

    @property
    def companies(self) -> int:
        return len(self.results)

    @property
    def filings(self) -> int:
        return sum(r.filings_indexed for r in self.results)

    @property
    def filings_new(self) -> int:
        return sum(r.filings_new for r in self.results)

    @property
    def xbrl_facts(self) -> int:
        return sum(r.xbrl_facts for r in self.results)

    @property
    def prices(self) -> int:
        return sum(r.prices for r in self.results)


def companyfacts_to_rows(cf_json: dict, cik: str) -> list[XbrlFact]:
    """Flatten SEC companyfacts JSON into ``XbrlFact`` rows.

    Mirrors the FactStore.from_companyfacts split: durations carry period_start +
    period_end; instants (no ``start``) carry ``instant`` only. companyfacts is
    consolidated/undimensioned, so ``dimensions_json`` stays '{}'.
    """
    rows: list[XbrlFact] = []
    for taxonomy, tags in (cf_json.get("facts") or {}).items():
        for tag, body in (tags or {}).items():
            for unit, entries in ((body or {}).get("units") or {}).items():
                for e in entries or []:
                    if e.get("val") is None:
                        continue
                    start, end = e.get("start"), e.get("end")
                    is_instant = start is None
                    dec = e.get("decimals")
                    fy = e.get("fy")
                    rows.append(XbrlFact(
                        cik=cik, taxonomy=taxonomy, tag=tag, value=float(e["val"]),
                        unit_ref=unit, decimals=None if dec is None else str(dec),
                        period_start=start,
                        period_end=None if is_instant else end,
                        instant=end if is_instant else None,
                        fy=None if fy is None else str(fy),
                        fp=e.get("fp"), form=e.get("form"),
                        accession_number=e.get("accn"), dimensions_json="{}",
                    ))
    return rows


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class IngestService:
    def __init__(
        self,
        repo: Repo,
        edgar: EdgarClient,
        stooq: StooqClient,
        *,
        as_of: date | None = None,
        now_fn: Callable[[], str] | None = None,
    ) -> None:
        self.repo = repo
        self.edgar = edgar
        self.stooq = stooq
        self._as_of = as_of
        self._now_fn = now_fn or _now_iso

    # ---- registration ----------------------------------------------------
    def add_holding(
        self,
        ticker: str,
        *,
        owned: bool,
        shares: float | None = None,
        cost_basis: float | None = None,
        target_weight_pct: float | None = None,
        horizon: str | None = None,
        thesis: str | None = None,
    ) -> Company:
        """Resolve CIK, register the company (identity only) and the holding."""
        ticker = ticker.strip().upper()
        rec = resolve_ticker(self.edgar.company_tickers(), ticker)
        if rec is None:
            # The ticker file is cached; a newly-listed ticker may be missing from a
            # stale copy. Force one refresh before declaring the ticker unknown.
            rec = resolve_ticker(self.edgar.company_tickers(force_refresh=True), ticker)
        if rec is None:
            raise TickerNotFoundError(ticker)
        now = self._now_fn()
        self.repo.upsert_company(Company(cik=rec.cik, ticker=ticker, name=rec.title, added_at=now))
        self.repo.upsert_holding(Holding(
            cik=rec.cik, ticker=ticker, owned=int(owned), shares=shares,
            cost_basis=cost_basis, target_weight_pct=target_weight_pct,
            horizon=horizon, thesis=thesis, added_at=now,
        ))
        company = self.repo.get_company(rec.cik)
        assert company is not None  # just upserted
        return company

    # ---- ingest ----------------------------------------------------------
    def ingest_all(
        self, *, backfill_quarters: int | None = DEFAULT_BACKFILL_QUARTERS
    ) -> IngestSummary:
        summary = IngestSummary()
        for cik in self.repo.list_tracked_ciks():
            summary.results.append(self.ingest_one(cik, backfill_quarters=backfill_quarters))
        return summary

    def ingest_one(
        self, cik: str, *, backfill_quarters: int | None = DEFAULT_BACKFILL_QUARTERS
    ) -> CikIngestResult:
        """Ingest a single CIK (profile+SIC, filings, companyfacts, prices) — the per-ticker
        path behind ``ingest_all``, exposed so ``finwatch metrics`` can scope to one company
        without pulling the whole tracked portfolio."""
        return self._ingest_cik(cik, backfill_quarters)

    def _as_of_date(self) -> date:
        return self._as_of or date.today()

    def _cutoff(self, backfill_quarters: int | None) -> str | None:
        if backfill_quarters is None:
            return None
        days = int(round(backfill_quarters * 91.3125))  # ~quarter length
        return (self._as_of_date() - timedelta(days=days)).isoformat()

    def _ingest_cik(self, cik: str, backfill_quarters: int | None) -> CikIngestResult:
        company = self.repo.get_company(cik)
        holding = self.repo.get_holding_by_cik(cik)
        ticker = (holding.ticker if holding else None) or (company.ticker if company else cik)
        result = CikIngestResult(cik=cik, ticker=ticker)
        errors: list[str] = []

        try:
            subs = self.edgar.submissions(cik)
            self._update_company_profile(cik, ticker, subs)
            result.filings_indexed, result.filings_new = self._index_filings(
                cik, subs, backfill_quarters
            )
        except Exception as exc:  # noqa: BLE001 — one CIK's failure must not abort the batch
            errors.append(f"submissions/filings: {exc}")

        try:
            result.xbrl_facts = self._ingest_companyfacts(cik)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"companyfacts: {exc}")

        try:
            result.prices = self._ingest_prices(ticker)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"prices: {exc}")

        if errors:
            result.error = "; ".join(errors)
        return result

    def _update_company_profile(self, cik: str, ticker: str, subs: dict) -> None:
        sic_raw = subs.get("sic")
        sic = str(sic_raw) if sic_raw not in (None, "") else None
        info = sector_from_sic(sic)
        self.repo.upsert_company(Company(
            cik=cik, ticker=ticker, name=subs.get("name"), sic_code=sic,
            sector_class=info.sector_class.value, is_financial=int(info.is_financial),
            added_at=self._now_fn(),
        ))

    def _index_filings(
        self, cik: str, subs: dict, backfill_quarters: int | None
    ) -> tuple[int, int]:
        cutoff = self._cutoff(backfill_quarters)
        cik_int = str(int(cik))
        filings = subs.get("filings") or {}
        indexed, new = self._index_filing_arrays(
            cik, cik_int, filings.get("recent") or {}, cutoff)
        # 'recent' caps at ~1000 most-recent filings; older ones (which for a prolific
        # filer can still fall inside the backfill window) live in paginated 'files'
        # pages. Fetch every page whose date range overlaps the window so an in-window
        # 10-Q/8-K is never silently dropped. Pages are effectively immutable (cached).
        for page in (filings.get("files") or []):
            name = page.get("name")
            if not name:
                continue
            filed_to = page.get("filingTo") or ""
            if cutoff and filed_to and filed_to < cutoff:
                continue                       # whole page predates the backfill window
            try:
                page_data = self.edgar.submissions_page(name)
            except Exception:  # noqa: BLE001 — one bad page must not abort the rest
                continue
            pi, pn = self._index_filing_arrays(cik, cik_int, page_data, cutoff)
            indexed += pi
            new += pn
        return indexed, new

    def _index_filing_arrays(
        self, cik: str, cik_int: str, arrays: dict, cutoff: str | None
    ) -> tuple[int, int]:
        """Index one block of the SEC parallel-array filing index (the 'recent' block
        or one paginated 'files' page — both share the same array shape)."""
        accns = arrays.get("accessionNumber") or []
        forms = arrays.get("form") or []
        fdates = arrays.get("filingDate") or []
        rdates = arrays.get("reportDate") or []
        pdocs = arrays.get("primaryDocument") or []
        indexed = new = 0
        for i, accn in enumerate(accns):
            form = forms[i] if i < len(forms) else ""
            filed = fdates[i] if i < len(fdates) else ""
            if cutoff and filed and filed < cutoff:
                continue
            report = rdates[i] if i < len(rdates) and rdates[i] else None
            pdoc = pdocs[i] if i < len(pdocs) and pdocs[i] else None
            url = (
                _ARCHIVE_URL.format(cik_int=cik_int, accn_nodash=accn.replace("-", ""), doc=pdoc)
                if pdoc else None
            )
            filing = Filing(
                accession_number=accn, cik=cik, form_type=form, filed_at=filed,
                period_of_report=report, is_amendment=1 if form.endswith("/A") else 0,
                primary_doc_url=url,
            )
            indexed += 1
            if self.repo.upsert_filing(filing):
                new += 1
        return indexed, new

    def _ingest_companyfacts(self, cik: str) -> int:
        try:
            cf = self.edgar.companyfacts(cik)
        except EdgarHTTPError as exc:
            if exc.status_code == 404:
                return 0  # issuer has no structured XBRL (e.g. some foreign filers)
            raise
        rows = companyfacts_to_rows(cf, cik)
        if not rows:
            # Anomalous but valid (HTTP 200) payload with no usable facts — do NOT
            # replace, or we would silently wipe previously-ingested history.
            return self.repo.count_xbrl_facts(cik)
        return self.repo.replace_xbrl_facts(cik, rows)

    def _ingest_prices(self, ticker: str) -> int:
        history = self.stooq.fetch_history(ticker)
        rows = [Price(ticker=ticker, date=d, close=c) for d, c in history]
        return self.repo.upsert_prices(rows)


def build_service(config: Config, *, conn=None) -> tuple:
    """Build an IngestService (and open connection) from resolved config."""
    from finwatch.db import Repo, init_db

    if conn is None:
        conn = init_db(config.db_path)
    cache_dir = (
        Path(config.db_path).parent / "cache" if config.db_path != ":memory:" else None
    )
    edgar = EdgarClient(config.sec_user_agent, cache_dir=cache_dir)
    return conn, IngestService(Repo(conn), edgar, StooqClient())


__all__ = [
    "IngestService",
    "IngestSummary",
    "CikIngestResult",
    "TickerNotFoundError",
    "build_service",
    "companyfacts_to_rows",
    "normalize_cik",
    "DEFAULT_BACKFILL_QUARTERS",
]
