"""Explicit SQLite reads/writes for private product state."""

from __future__ import annotations

import json

from finwatch.db.repositories import Company, Repo
from finwatch.product.models import (
    AttentionEvent,
    CompanyProfile,
    ManagementPromise,
    RiskRadarResult,
    Thesis,
    ValuationRun,
)


class ProductStore:
    def __init__(self, repo: Repo, user_id: str) -> None:
        self.repo = repo
        self.user_id = user_id

    def profile(self, company: Company, *, now: str) -> CompanyProfile:
        row = self.repo.conn.execute(
            "SELECT * FROM company_profiles WHERE user_id = ? AND cik = ?",
            (self.user_id, company.cik),
        ).fetchone()
        if row is None:
            return CompanyProfile(ticker=company.ticker, cik=company.cik, updated_at=now)
        return CompanyProfile(
            ticker=company.ticker,
            cik=company.cik,
            monitoring_enabled=bool(row["monitoring_enabled"]),
            notification_level=row["notification_level"],
            thesis=Thesis.model_validate_json(row["thesis_json"]),
            peer_ciks=json.loads(row["peer_ciks_json"]),
            updated_at=row["updated_at"],
        )

    def save_profile(self, profile: CompanyProfile) -> None:
        self.repo.conn.execute(
            """INSERT INTO company_profiles
                 (user_id, cik, monitoring_enabled, notification_level, thesis_json,
                  peer_ciks_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, cik) DO UPDATE SET
                 monitoring_enabled=excluded.monitoring_enabled,
                 notification_level=excluded.notification_level,
                 thesis_json=excluded.thesis_json,
                 peer_ciks_json=excluded.peer_ciks_json,
                 updated_at=excluded.updated_at""",
            (
                self.user_id,
                profile.cik,
                int(profile.monitoring_enabled),
                profile.notification_level,
                profile.thesis.model_dump_json(),
                json.dumps(profile.peer_ciks),
                profile.updated_at,
            ),
        )
        self.repo.conn.commit()

    def save_risks(self, cik: str, accession: str | None, key: str, payload: str, now: str) -> None:
        self.repo.conn.execute(
            """INSERT INTO risk_snapshots
                 (user_id, cik, accession_number, snapshot_key, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, cik, snapshot_key) DO UPDATE SET
                 result_json=excluded.result_json, created_at=excluded.created_at""",
            (self.user_id, cik, accession, key, payload, now),
        )
        self.repo.conn.commit()

    def latest_risks(self, cik: str) -> list[RiskRadarResult]:
        row = self.repo.conn.execute(
            """SELECT result_json FROM risk_snapshots
                WHERE user_id = ? AND cik = ? ORDER BY created_at DESC, id DESC LIMIT 1""",
            (self.user_id, cik),
        ).fetchone()
        if row is None:
            return []
        try:
            payload = json.loads(row["result_json"])
            return [RiskRadarResult.model_validate(item) for item in payload]
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    def insert_event(self, event: AttentionEvent) -> bool:
        cursor = self.repo.conn.execute(
            """INSERT INTO attention_events
                 (event_key, user_id, cik, accession_number, priority, reason_codes_json,
                  risk_changes_json, thesis_impacts_json, created_at, read_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(event_key) DO NOTHING""",
            (
                event.event_key,
                self.user_id,
                event.cik,
                event.accession,
                event.priority,
                json.dumps(event.reason_codes),
                json.dumps(event.risk_changes),
                json.dumps(event.thesis_impacts),
                event.created_at,
                event.read_at,
            ),
        )
        self.repo.conn.commit()
        return cursor.rowcount > 0

    def list_events(self, *, cik: str | None = None, limit: int = 100) -> list[AttentionEvent]:
        sql = """SELECT e.*, c.ticker FROM attention_events e
                   JOIN companies c ON c.cik = e.cik
                  WHERE e.user_id = ?"""
        params: list[object] = [self.user_id]
        if cik is not None:
            sql += " AND e.cik = ?"
            params.append(cik)
        sql += " ORDER BY e.created_at DESC, e.id DESC LIMIT ?"
        params.append(limit)
        return [
            AttentionEvent(
                event_id=row["id"],
                event_key=row["event_key"],
                ticker=row["ticker"],
                cik=row["cik"],
                accession=row["accession_number"],
                priority=row["priority"],
                reason_codes=json.loads(row["reason_codes_json"]),
                risk_changes=json.loads(row["risk_changes_json"]),
                thesis_impacts=json.loads(row["thesis_impacts_json"]),
                created_at=row["created_at"],
                read_at=row["read_at"],
            )
            for row in self.repo.conn.execute(sql, params).fetchall()
        ]

    def mark_event_read(self, event_id: int, now: str) -> bool:
        cursor = self.repo.conn.execute(
            "UPDATE attention_events SET read_at = ? WHERE id = ? AND user_id = ?",
            (now, event_id, self.user_id),
        )
        self.repo.conn.commit()
        return cursor.rowcount > 0

    def promises(self, company: Company) -> list[ManagementPromise]:
        rows = self.repo.conn.execute(
            """SELECT * FROM management_promises
                WHERE user_id = ? AND cik = ? ORDER BY created_at DESC""",
            (self.user_id, company.cik),
        ).fetchall()
        return [
            ManagementPromise(
                promise_id=row["id"],
                ticker=company.ticker,
                accession=row["accession_number"],
                section_key=row["section_key"],
                char_start=row["char_start"],
                char_end=row["char_end"],
                section_sha256=row["section_sha256"],
                quote=row["quote"],
                target_period=row["target_period"],
                target_metric=row["target_metric"],
                status=row["status"],
            )
            for row in rows
        ]

    def save_promise(self, company: Company, promise: ManagementPromise, now: str) -> bool:
        cursor = self.repo.conn.execute(
            """INSERT INTO management_promises
                 (id, user_id, cik, accession_number, section_key, char_start, char_end,
                  section_sha256, quote, target_period, target_metric, status,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO NOTHING""",
            (
                promise.promise_id,
                self.user_id,
                company.cik,
                promise.accession,
                promise.section_key,
                promise.char_start,
                promise.char_end,
                promise.section_sha256,
                promise.quote,
                promise.target_period,
                promise.target_metric,
                promise.status,
                now,
                now,
            ),
        )
        self.repo.conn.commit()
        return cursor.rowcount > 0

    def save_valuation(self, company: Company, run: ValuationRun) -> None:
        self.repo.conn.execute(
            """INSERT INTO valuation_runs
                 (id, user_id, cik, price, price_as_of, assumptions_json, output_json,
                  formula_version, certificate_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.run_id,
                self.user_id,
                company.cik,
                run.price,
                run.price_as_of,
                run.assumptions.model_dump_json(),
                run.model_dump_json(),
                run.formula_version,
                run.certificate_hash,
                run.created_at,
            ),
        )
        self.repo.conn.commit()

    def latest_valuation(self, company: Company) -> ValuationRun | None:
        row = self.repo.conn.execute(
            """SELECT output_json FROM valuation_runs
                WHERE user_id = ? AND cik = ? ORDER BY created_at DESC LIMIT 1""",
            (self.user_id, company.cik),
        ).fetchone()
        return None if row is None else ValuationRun.model_validate_json(row["output_json"])

    def peers(self, company: Company, profile: CompanyProfile) -> list[Company]:
        known = {
            row.cik: row
            for row in self.repo.list_tracked_companies(self.user_id)
            if row.cik != company.cik and row.sic_code and row.sic_code == company.sic_code
        }
        for cik in profile.peer_ciks:
            row = self.repo.get_company(cik)
            if row is not None and row.cik != company.cik:
                known[row.cik] = row
        return sorted(known.values(), key=lambda row: row.ticker)[:6]

    def begin_delivery(self, key: str, notification_type: str, period_key: str) -> bool:
        """Claim an idempotent delivery key; failed sends may be retried."""
        row = self.repo.conn.execute(
            "SELECT status FROM notification_deliveries WHERE delivery_key = ? AND user_id = ?",
            (key, self.user_id),
        ).fetchone()
        if row is not None and row["status"] == "sent":
            return False
        self.repo.conn.execute(
            """INSERT INTO notification_deliveries
                 (delivery_key, user_id, notification_type, status, attempts, period_key)
               VALUES (?, ?, ?, 'sending', 1, ?)
               ON CONFLICT(delivery_key) DO UPDATE SET
                 status='sending', attempts=notification_deliveries.attempts + 1,
                 error_code=NULL""",
            (key, self.user_id, notification_type, period_key),
        )
        self.repo.conn.commit()
        return True

    def finish_delivery(
        self, key: str, *, sent_at: str | None = None, error_code: str | None = None
    ) -> None:
        status = "sent" if sent_at else "failed"
        self.repo.conn.execute(
            """UPDATE notification_deliveries
                  SET status = ?, sent_at = ?, error_code = ?
                WHERE delivery_key = ? AND user_id = ?""",
            (status, sent_at, error_code, key, self.user_id),
        )
        self.repo.conn.commit()

    def billing_account(self) -> dict | None:
        row = self.repo.conn.execute(
            "SELECT * FROM billing_accounts WHERE user_id = ?", (self.user_id,)
        ).fetchone()
        return None if row is None else dict(row)

    def save_billing(
        self,
        *,
        customer_id: str | None,
        subscription_id: str | None,
        status: str,
        price_id: str | None,
        updated_at: str,
    ) -> None:
        self.repo.conn.execute(
            """INSERT INTO billing_accounts
                 (user_id, stripe_customer_id, subscription_id, status, price_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 stripe_customer_id=COALESCE(excluded.stripe_customer_id,
                                             billing_accounts.stripe_customer_id),
                 subscription_id=excluded.subscription_id,
                 status=excluded.status, price_id=excluded.price_id,
                 updated_at=excluded.updated_at""",
            (self.user_id, customer_id, subscription_id, status, price_id, updated_at),
        )
        self.repo.conn.commit()
