"""Idempotent attention-event and email delivery helpers for the scheduled command."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from finwatch.db.repositories import Repo
from finwatch.product.models import AttentionEvent
from finwatch.product.service import ProductService

SendEmail = Callable[[str, str, str], None]


def build_attention_events(repo: Repo) -> list[AttentionEvent]:
    """Build at most one event per user/accession; repeated cycles are harmless."""
    rows = repo.conn.execute(
        """SELECT uc.user_id, c.ticker FROM user_companies uc
             JOIN companies c ON c.cik = uc.cik
        LEFT JOIN company_profiles p ON p.user_id = uc.user_id AND p.cik = uc.cik
            WHERE COALESCE(p.monitoring_enabled, 1) = 1
            ORDER BY uc.user_id, c.ticker"""
    ).fetchall()
    events = []
    for row in rows:
        event = ProductService(repo, user_id=row["user_id"]).create_attention_event(row["ticker"])
        if event is not None:
            events.append(event)
    return events


def deliver_attention_event(
    repo: Repo, event: AttentionEvent, send: SendEmail, *, period_key: str | None = None
) -> bool:
    """Deliver urgent/this-week events once, according to the user's profile."""
    user = repo.conn.execute(
        """SELECT u.id, u.email FROM attention_events e
             JOIN users u ON u.id = e.user_id
            WHERE e.event_key = ?""",
        (event.event_key,),
    ).fetchone()
    if user is None or str(user["email"]).endswith(".invalid"):
        return False
    service = ProductService(repo, user_id=user["id"])
    profile = service.profile(event.ticker)
    if profile is None or profile.notification_level == "off":
        return False
    if event.priority == "routine" or (
        event.priority == "this_week" and profile.notification_level == "urgent"
    ) or profile.notification_level == "weekly":
        return False
    period = period_key or date.today().isoformat()
    key = f"{service.user_id}:{event.event_key}:attention:{period}"
    if not service.store.begin_delivery(key, "attention", period):
        return False
    try:
        send(
            user["email"],
            f"RipplX: {event.ticker} needs {event.priority.replace('_', ' ')} review",
            (
                f"{event.ticker} · {event.priority.replace('_', ' ')}\n\n"
                f"Reasons: {', '.join(event.reason_codes)}\n"
                "Open RipplX to inspect exact SEC evidence and verified metrics.\n\n"
                "Educational research support only; not investment advice."
            ),
        )
    except Exception:  # noqa: BLE001 - provider details never enter persisted/public errors
        service.store.finish_delivery(key, error_code="provider_failed")
        return False
    service.store.finish_delivery(key, sent_at=service.now_fn())
    return True


def deliver_weekly_briefs(repo: Repo, send: SendEmail, *, week_key: str) -> int:
    """Send one compact, deduplicated weekly watchlist brief per eligible user."""
    users = repo.conn.execute(
        """SELECT DISTINCT u.id, u.email FROM users u
             JOIN user_companies uc ON uc.user_id = u.id
        LEFT JOIN company_profiles p ON p.user_id = uc.user_id AND p.cik = uc.cik
            WHERE u.email NOT LIKE '%.invalid'
              AND COALESCE(p.notification_level, 'urgent') != 'off'"""
    ).fetchall()
    sent = 0
    for user in users:
        service = ProductService(repo, user_id=user["id"])
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        events = [
            row
            for row in service.list_events()
            if row.created_at[:10] >= cutoff
            and (profile := service.profile(row.ticker)) is not None
            and profile.notification_level != "off"
        ]
        key = f"{user['id']}:weekly:{week_key}"
        if not service.store.begin_delivery(key, "weekly", week_key):
            continue
        important = [row for row in events if row.priority != "routine"][:3]
        routine = [row.ticker for row in events if row.priority == "routine"]
        lines = ["Your weekly RipplX brief", ""]
        lines.extend(
            f"- {row.ticker}: {row.priority.replace('_', ' ')} — {', '.join(row.reason_codes)}"
            for row in important
        )
        elevated = sorted(
            {f"{row.ticker} ({', '.join(row.risk_changes)})" for row in events if row.risk_changes}
        )
        thesis = sorted(
            {
                f"{row.ticker} ({', '.join(row.thesis_impacts)})"
                for row in events
                if row.thesis_impacts
            }
        )
        if elevated:
            lines.append(f"- Elevated or worsening downside: {'; '.join(elevated)}")
        if thesis:
            lines.append(f"- Saved watch conditions to review: {'; '.join(thesis)}")
        promises = []
        suggestion = None
        for ticker in sorted({row.ticker for row in events}):
            profile = service.profile(ticker)
            company = service._company(ticker)
            if profile is None or company is None:
                continue
            promises.extend(
                f"{ticker}: {row.status}" for row in service.store.promises(company)
            )
            if suggestion is None:
                peers = service.peers(ticker) or []
                peer = next(
                    (
                        row
                        for row in peers
                        if (peer_profile := service.profile(row.ticker)) is None
                        or peer_profile.notification_level != "off"
                    ),
                    None,
                )
                if peer is not None:
                    suggestion = f"Compare {ticker} with {peer.ticker} (research prompt only)."
        if promises:
            lines.append(f"- Additional filing commitments to watch: {'; '.join(promises[:3])}")
        if routine:
            lines.append(f"- No important verified change: {', '.join(sorted(set(routine)))}")
        if not events:
            lines.append("- No monitored filing activity was recorded this week.")
        if suggestion:
            lines.append(f"- Suggested comparison: {suggestion}")
        lines.extend(
            [
                "",
                "Open RipplX for evidence and methodology.",
                "Educational research support only; not investment advice.",
            ]
        )
        try:
            send(user["email"], "Your weekly RipplX portfolio brief", "\n".join(lines))
        except Exception:  # noqa: BLE001
            service.store.finish_delivery(key, error_code="provider_failed")
            continue
        service.store.finish_delivery(key, sent_at=service.now_fn())
        sent += 1
    return sent
