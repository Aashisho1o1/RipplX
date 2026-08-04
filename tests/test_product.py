from __future__ import annotations

from types import SimpleNamespace

from finwatch.core.types import MetricStatus
from finwatch.db import Company, Computation, Filing, Repo, User
from finwatch.metrics.envelope import InputUsed, MetricResult
from finwatch.product.models import (
    AttentionEvent,
    CompanyProfile,
    RiskRadarResult,
    Thesis,
    ThesisItem,
)
from finwatch.product.monitoring import (
    build_attention_events,
    deliver_attention_event,
    deliver_weekly_briefs,
)
from finwatch.product.service import ProductService

NOW = "2026-08-02T12:00:00+00:00"
AS_OF = "2026-07-31"


def _company(repo: Repo, *, user_id: str = "local") -> Company:
    company = Company(cik="1", ticker="TEST", name="Test Co", sic_code="7372", added_at=NOW)
    repo.upsert_company(company)
    repo.track_company(company.cik, at=NOW, user_id=user_id)
    return company


def _metric(
    name: str,
    *,
    value: float | None = None,
    components: dict | None = None,
    direction: float | None = None,
    inputs: list[InputUsed] | None = None,
) -> MetricResult:
    return MetricResult(
        metric=name,
        status=MetricStatus.COMPUTED,
        value=value,
        components=components or {},
        inputs_used=inputs
        or [
            InputUsed(
                concept=name,
                tag=name,
                value=1,
                unit_ref="USD",
                period_end=AS_OF,
                accession_number="0000000001-26-000001",
            )
        ],
        formula_version=f"{name}.test",
        as_of=AS_OF,
        direction_delta=direction,
        direction_slack=0,
        direction_basis="current_minus_prior" if direction is not None else None,
    )


def _persist(repo: Repo, *metrics: MetricResult) -> None:
    repo.insert_computations(
        [
            Computation(
                ticker="TEST",
                tool=row.metric,
                args_json="{}",
                result_json=row.model_dump_json(),
                status=row.status.value,
                formula_version=row.formula_version,
                as_of=row.as_of,
                created_at=NOW,
            )
            for row in metrics
        ]
    )


def test_risk_radar_is_deterministic_and_missing_data_stays_unavailable(repo: Repo):
    _company(repo)
    _persist(
        repo,
        _metric(
            "liquidity_basics",
            components={"net_debt": 80, "current_ratio": 0.8},
        ),
        _metric(
            "simple_leverage",
            value=5.2,
            components={"net_debt_to_ebitda": 5.2, "interest_coverage": 1.5},
        ),
        _metric("revenue_growth", value=-0.1, direction=-10),
        _metric(
            "net_income_trend",
            value=0.2,
            components={"current": 100, "prior": 80},
            direction=20,
        ),
        _metric(
            "cfo_trend",
            value=-0.8,
            components={"current": 30, "prior": 150},
            direction=-120,
        ),
    )

    results = ProductService(repo, user_id="local", now_fn=lambda: NOW).risk_radar("TEST")
    assert results is not None
    by_lens = {row.lens: row for row in results}
    assert by_lens["liquidity"].status == "elevated"
    assert by_lens["leverage"].status == "elevated"
    assert by_lens["cash_conversion"].status == "elevated"
    assert by_lens["operating_deterioration"].status == "elevated"
    assert by_lens["share_count"].status == "unavailable"
    assert by_lens["concentration"].status == "unavailable"


def test_liquidity_does_not_flag_covered_current_liabilities_as_watch(repo: Repo):
    """Positive working-capital coverage is not a liquidity warning by itself.

    This mirrors Microsoft's 2026 shape: current assets cover current liabilities and
    net debt is small relative to earnings capacity. Leverage is assessed separately.
    """
    _company(repo)
    metric = _metric(
        "liquidity_basics",
        components={"cash": 21, "net_debt": 19, "current_ratio": 1.23},
    )

    result = ProductService(repo, user_id="local", now_fn=lambda: NOW)._liquidity(
        (None, metric)
    )

    assert result.status == "stable"
    assert result.reason_codes == ["CURRENT_LIABILITIES_COVERED"]


def test_low_severity_filing_change_does_not_become_a_risk_warning(repo: Repo):
    _company(repo)
    service = ProductService(repo, user_id="local", now_fn=lambda: NOW)
    finding = SimpleNamespace(severity="LOW", finding_id="f1", evidence=[])
    service.presentation = SimpleNamespace(
        filing=lambda _accession: SimpleNamespace(
            filing=SimpleNamespace(outcome="published", findings=[finding])
        )
    )
    filing = Filing(
        accession_number="0000000001-26-000001",
        cik="1",
        form_type="10-K",
        filed_at=AS_OF,
    )

    result = service._filing_events(filing)

    assert result.status == "stable"
    assert result.reason_codes == ["LOW_SEVERITY_VERIFIED_CHANGE"]
    assert "low-severity" in result.explanation


def test_profile_and_thesis_are_owner_isolated(repo: Repo):
    company = _company(repo)
    repo.create_user(User(id="other", email="other@example.com", created_at=NOW, last_login_at=NOW))
    repo.track_company(company.cik, at=NOW, user_id="other")
    local = ProductService(repo, user_id="local", now_fn=lambda: NOW)
    profile = local.profile("TEST")
    assert profile is not None
    saved = local.save_profile(
        "TEST",
        CompanyProfile(
            **profile.model_dump(exclude={"thesis"}),
            thesis=Thesis(
                items=[
                    ThesisItem(
                        item_id="t1", kind="assumption", text="Cash conversion stays positive"
                    )
                ]
            ),
        ),
    )
    assert saved is not None
    assert saved.thesis.items[0].text == "Cash conversion stays positive"
    other = ProductService(repo, user_id="other", now_fn=lambda: NOW).profile("TEST")
    assert other is not None
    assert other.thesis.items == []


def test_stock_impact_is_explainable_and_uses_verified_change_structure(repo: Repo):
    _company(repo)
    service = ProductService(repo, user_id="local", now_fn=lambda: NOW)
    impact = service._stock_impact(
        risks=[
            RiskRadarResult(
                lens="operating_deterioration",
                status="elevated",
                reason_codes=["MULTIPLE_TRENDS_DOWN"],
                explanation="Multiple verified operating measures declined.",
            )
        ],
        recent_filings=[
            {
                "accession": "0000000001-26-000001",
                "form": "10-Q",
                "findings": [
                    {
                        "finding_id": "f1",
                        "headline": "Revenue declined from the comparable period.",
                        "severity": "HIGH",
                        "metric_id": "revenue_growth",
                        "direction": "down",
                        "evidence": [
                            {
                                "claim_id": "f1-e1",
                                "accession": "0000000001-26-000001",
                                "section_key": "mdna",
                                "char_start": 0,
                                "char_end": 7,
                                "quote": "Revenue",
                                "section_sha256": "a" * 64,
                                "edgar_url": "https://www.sec.gov/Archives/example",
                            }
                        ],
                    }
                ],
            }
        ],
    )
    assert impact.directional_pressure == "downside"
    assert impact.reason_codes == ["VERIFIED_REVENUE_DOWN", "ELEVATED_DOWNSIDE_RISK"]
    assert impact.changes[0].driver == "revenue"
    assert impact.changes[0].effect == "downside"
    assert "future cash flow" in impact.changes[0].implication.lower()
    assert impact.changes[0].evidence[0].accession == "0000000001-26-000001"


def test_change_without_exact_evidence_is_not_projected():
    impact = ProductService._stock_impact(
        risks=[],
        recent_filings=[
            {
                "findings": [
                    {
                        "finding_id": "f1",
                        "headline": "The company updated its operating approach",
                        "evidence": [],
                    }
                ]
            }
        ],
    )

    assert impact.directional_pressure == "uncertain"
    assert impact.changes == []


def test_attention_event_is_idempotent(repo: Repo):
    from finwatch.db import Filing

    _company(repo)
    repo.upsert_filing(
        Filing(
            accession_number="0000000001-26-000001",
            cik="1",
            form_type="10-Q",
            filed_at=AS_OF,
            status="verified",
        )
    )
    _persist(
        repo,
        _metric(
            "share_count_change",
            value=0.10,
            components={"current": 110, "prior": 100},
            direction=10,
        ),
    )
    service = ProductService(repo, user_id="local", now_fn=lambda: NOW)
    first = service.create_attention_event("TEST")
    second = service.create_attention_event("TEST")
    assert first is not None and second is not None
    assert first.event_key == second.event_key
    assert len(service.list_events()) == 1


def test_attention_event_requires_a_verified_analysis(repo: Repo):
    from finwatch.db import Filing

    _company(repo)
    repo.upsert_filing(
        Filing(
            accession_number="0000000001-26-000001",
            cik="1",
            form_type="10-Q",
            filed_at=AS_OF,
            status="failed",
        )
    )
    assert ProductService(repo, user_id="local", now_fn=lambda: NOW).create_attention_event(
        "TEST"
    ) is None


def test_monitoring_and_delivery_are_idempotent(repo: Repo):
    from finwatch.db import Filing

    company = _company(repo)
    repo.create_user(User(id="alice", email="alice@example.com", created_at=NOW, last_login_at=NOW))
    repo.track_company(company.cik, at=NOW, user_id="alice")
    repo.upsert_filing(
        Filing(
            accession_number="0000000001-26-000001",
            cik=company.cik,
            form_type="10-Q",
            filed_at=AS_OF,
            status="verified",
        )
    )
    _persist(
        repo,
        _metric(
            "share_count_change",
            value=0.10,
            components={"current": 110, "prior": 100},
            direction=10,
        ),
    )
    alice = ProductService(repo, user_id="alice", now_fn=lambda: NOW)
    profile = alice.profile("TEST")
    assert profile is not None
    alice.save_profile(
        "TEST",
        profile.model_copy(
            update={
                "notification_level": "this_week",
                "thesis": Thesis(
                    items=[
                        ThesisItem(
                            item_id="t1",
                            kind="assumption",
                            text="Share count does not expand materially",
                            status="confirmed",
                            lens="share_count",
                        )
                    ]
                ),
            }
        ),
    )

    first = build_attention_events(repo)
    second = build_attention_events(repo)
    event_key = repo.conn.execute(
        "SELECT event_key FROM attention_events WHERE user_id = 'alice'"
    ).fetchone()["event_key"]
    alice_event = next(row for row in first if row.event_key == event_key)
    assert len([row for row in second if row.event_key == event_key]) == 1
    assert alice.profile("TEST").thesis.items[0].status == "broken"
    assert "share_count:unavailable->elevated" in alice_event.risk_changes
    assert alice_event.thesis_impacts == ["t1:broken"]

    sent = []

    def sender(recipient, subject, text):
        sent.append((recipient, subject, text))

    assert deliver_attention_event(repo, alice_event, sender, period_key="2026-08-02")
    assert not deliver_attention_event(repo, alice_event, sender, period_key="2026-08-02")
    assert len(sent) == 1
    assert "buy" not in sent[0][2].lower()

    muted = Company(cik="2", ticker="MUTE", name="Muted Co", sic_code="7372", added_at=NOW)
    repo.upsert_company(muted)
    repo.track_company(muted.cik, at=NOW, user_id="alice")
    muted_profile = alice.profile("MUTE")
    assert muted_profile is not None
    alice.save_profile(
        "MUTE", muted_profile.model_copy(update={"notification_level": "off"})
    )
    alice.store.insert_event(
        AttentionEvent(
            event_key="muted-event",
            ticker="MUTE",
            cik="2",
            priority="urgent",
            reason_codes=["MUTED_REASON"],
            created_at=NOW,
        )
    )
    assert deliver_weekly_briefs(repo, sender, week_key="2026-W31") == 1
    assert deliver_weekly_briefs(repo, sender, week_key="2026-W31") == 0
    assert "Saved watch conditions to review" in sent[-1][2]
    assert "MUTE" not in sent[-1][2]
