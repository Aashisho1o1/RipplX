from __future__ import annotations

import json
import uuid

import pytest

from finwatch.db import LOCAL_USER_ID, Repo, User
from finwatch.demo import build_demo_db
from finwatch.llm.router import FakeLLMClient
from finwatch.product import ProductService
from finwatch.product.models import ResearchTrace
from finwatch.product.research import (
    ChangesAction,
    CompanyResearchHarness,
    DraftInsight,
    EmptyArgs,
    FinancialAction,
    FinancialArgs,
    ResearchDraft,
    ResearchHarnessError,
)


def _service() -> ProductService:
    return ProductService(Repo(build_demo_db()), user_id=LOCAL_USER_ID)


def _insight(insight_id: str, category: str, observation_id: str, **updates) -> DraftInsight:
    payload = {
        "insight_id": insight_id,
        "category": category,
        "headline": "A verified development affects the financial picture.",
        "evidence_summary": "The cited evidence establishes the underlying development.",
        "driver": "The observed development affects operating performance.",
        "mechanism": "uncertain",
        "implication": "If the cited condition persists, future cash flows or risk may change.",
        "scenario": "mixed",
        "assumptions": ["The cited condition persists."],
        "limitations": ["Only the available SEC evidence was used."],
        "observation_ids": [observation_id],
    }
    payload.update(updates)
    return DraftInsight.model_validate(payload)


def test_tool_order_does_not_change_observations_or_compiler_decisions():
    service = _service()
    company = service._company("MSFT")
    assert company is not None
    filing = service._latest_supported(company)
    assert filing is not None
    harness = CompanyResearchHarness(service, FakeLLMClient(responses=[]))
    changes = ChangesAction(action="tool", tool="get_verified_changes", arguments=EmptyArgs())
    financial = FinancialAction(
        action="tool",
        tool="get_financial_context",
        arguments=FinancialArgs(metric_ids=[], risk_lenses=["liquidity"]),
    )

    first = harness._call_tool(company, filing, changes) + harness._call_tool(
        company, filing, financial
    )
    second = harness._call_tool(company, filing, financial) + harness._call_tool(
        company, filing, changes
    )
    first_ledger = {row.observation_id: row for row in first}
    second_ledger = {row.observation_id: row for row in second}
    assert set(first_ledger) == set(second_ledger)

    change_id = next(row.observation_id for row in first if row.tool == "get_verified_changes")
    financial_id = next(
        row.observation_id
        for row in first
        if row.tool == "get_financial_context" and row.evidence_label == "calculation"
    )
    draft = ResearchDraft(
        summary="The evidence connects filing changes and financial quality.",
        insights=[
            _insight("i1", "change", change_id),
            _insight("i2", "financial_quality", financial_id),
        ],
    )
    first_report, first_errors = harness.compile(company, filing, draft, first_ledger)
    second_report, second_errors = harness.compile(company, filing, draft, second_ledger)
    assert first_errors == second_errors == {}
    assert first_report == second_report


def test_observation_hashes_are_replayable_and_open_obligations_transition():
    service = _service()
    company = service._company("MSFT")
    assert company is not None
    filing = service._latest_supported(company)
    assert filing is not None
    harness = CompanyResearchHarness(service, FakeLLMClient(responses=[]))
    changes = harness._changes(filing)
    ledger = {row.observation_id: row for row in changes}
    called = [{"tool": "get_verified_changes"}]

    open_after_change = harness._open_obligations(ledger, called)
    assert "IMPORTANT_CHANGES" not in open_after_change
    assert "SOURCE_COVERAGE" in open_after_change

    financial = harness._financial(
        company, FinancialArgs(metric_ids=[], risk_lenses=["liquidity"])
    )
    ledger.update({row.observation_id: row for row in financial})
    called.append({"tool": "get_financial_context"})
    assert "SOURCE_COVERAGE" not in harness._open_obligations(ledger, called)

    tampered = changes[0].model_dump(mode="json")
    tampered["text"] = "Tampered after compilation."
    with pytest.raises(ValueError, match="content, ID, and stable hash"):
        type(changes[0]).model_validate(tampered)


def test_compiler_prunes_authored_numbers_and_fabricated_citations_per_insight():
    service = _service()
    company = service._company("MSFT")
    assert company is not None
    filing = service._latest_supported(company)
    assert filing is not None
    harness = CompanyResearchHarness(service, FakeLLMClient(responses=[]))
    rows = harness._changes(filing)
    assert rows
    ledger = {row.observation_id: row for row in rows}
    valid = _insight("i1", "change", rows[0].observation_id)
    authored_number = _insight(
        "i2",
        "change",
        rows[0].observation_id,
        headline="Revenue changed by 42 percent.",
    )
    fabricated = _insight("i3", "change", "o_0000000000000000")
    recommendation = _insight(
        "i4",
        "change",
        rows[0].observation_id,
        implication="Investors should buy while this condition persists.",
    )
    report, errors = harness.compile(
        company,
        filing,
        ResearchDraft(
            summary="The available filing evidence supports one insight.",
            insights=[valid, authored_number, fabricated, recommendation],
        ),
        ledger,
    )

    assert [row.insight_id for row in report.insights] == ["i1"]
    assert "UNSAFE_AUTHORED_TEXT" in errors["i2"]
    assert "UNKNOWN_OBSERVATION" in errors["i3"]
    assert errors["i4"] == ["UNSAFE_AUTHORED_TEXT"]


def test_missing_valuation_is_unavailable_and_never_estimated():
    service = _service()
    company = service._company("MSFT")
    assert company is not None
    filing = service._latest_supported(company)
    assert filing is not None
    harness = CompanyResearchHarness(service, FakeLLMClient(responses=[]))
    observation = harness._valuation(company)[0]
    report, errors = harness.compile(
        company,
        filing,
        ResearchDraft(
            summary="Valuation evidence is incomplete.",
            insights=[_insight("i1", "valuation", observation.observation_id)],
        ),
        {observation.observation_id: observation},
    )

    assert report.insights == []
    assert errors["i1"] == [
        "CATEGORY_EVIDENCE_MISSING",
        "INSUFFICIENT_EVIDENCE",
        "VALUATION_UNAVAILABLE",
    ]
    valuation = next(row for row in report.obligations if row.obligation == "VALUATION_CONTEXT")
    assert valuation.state == "unavailable"
    assert report.valuation_context == observation
    assert any("current price" in gap for gap in report.evidence_gaps)


def test_explicit_unavailability_is_a_complete_report_not_protocol_degradation():
    def responder(system: str, user: str) -> str:
        if "one-directional financial Skeptic" in system:
            return json.dumps({"action": "review", "objections": []})
        payload = json.loads(user)
        if not payload["observations"]:
            return json.dumps(
                {"action": "tool", "tool": "get_verified_changes", "arguments": {}}
            )
        observation_id = payload["observations"][0]["observation_id"]
        return json.dumps(
            {
                "action": "submit",
                "draft": {
                    "summary": "One filing change is supported; other contexts are unavailable.",
                    "insights": [
                        _insight("i1", "change", observation_id).model_dump(mode="json")
                    ],
                },
            }
        )

    result = CompanyResearchHarness(_service(), FakeLLMClient(responder=responder)).run("MSFT")
    assert result.status == "completed"
    assert result.trace.terminal_reason == "submitted"
    assert any(row.state == "unavailable" for row in result.report.obligations)


def test_shared_repair_cannot_erase_an_already_compiler_passing_insight():
    def responder(system: str, user: str) -> str:
        if "one-directional financial Skeptic" in system:
            return json.dumps({"action": "review", "objections": []})
        payload = json.loads(user)
        if not payload["observations"]:
            return json.dumps(
                {"action": "tool", "tool": "get_verified_changes", "arguments": {}}
            )
        observation_id = payload["observations"][0]["observation_id"]
        if not payload["compiler_errors"]:
            insights = [
                _insight("i1", "change", observation_id).model_dump(mode="json"),
                _insight(
                    "i2",
                    "change",
                    observation_id,
                    headline="Revenue changed by 42 percent.",
                ).model_dump(mode="json"),
            ]
        else:
            insights = [_insight("i2", "change", observation_id).model_dump(mode="json")]
        return json.dumps(
            {
                "action": "submit",
                "draft": {"summary": "The filing supports two changes.", "insights": insights},
            }
        )

    result = CompanyResearchHarness(_service(), FakeLLMClient(responder=responder)).run("MSFT")
    assert [row.insight_id for row in result.report.insights] == ["i1", "i2"]
    assert result.trace.repair_used is True
    assert result.trace.dropped_insights == {}
    assert result.status == "completed"


def test_skeptic_objection_drops_only_targeted_insight_and_run_stays_bounded():
    def responder(system: str, user: str) -> str:
        if "one-directional financial Skeptic" in system:
            return json.dumps(
                {
                    "action": "review",
                    "objections": [{"insight_id": "i2", "code": "MATERIALITY_OVERREACH"}],
                }
            )
        payload = json.loads(user)
        tool_calls = 4 - payload["remaining"]["tool_calls"]
        if tool_calls == 0:
            return json.dumps({"action": "tool", "tool": "get_verified_changes", "arguments": {}})
        if tool_calls == 1:
            return json.dumps(
                {
                    "action": "tool",
                    "tool": "get_financial_context",
                    "arguments": {"metric_ids": [], "risk_lenses": ["liquidity"]},
                }
            )
        change = next(
            row for row in payload["observations"] if row["tool"] == "get_verified_changes"
        )
        financial = next(
            row
            for row in payload["observations"]
            if row["tool"] == "get_financial_context" and row["evidence_label"] == "calculation"
        )
        return json.dumps(
            {
                "action": "submit",
                "draft": {
                    "summary": "The evidence connects a filing change and financial quality.",
                    "insights": [
                        _insight("i1", "change", change["observation_id"]).model_dump(),
                        _insight(
                            "i2", "financial_quality", financial["observation_id"]
                        ).model_dump(),
                    ],
                },
            }
        )

    result = CompanyResearchHarness(_service(), FakeLLMClient(responder=responder)).run("MSFT")
    assert [row.insight_id for row in result.report.insights] == ["i1"]
    assert result.trace.dropped_insights == {"i2": ["MATERIALITY_OVERREACH"]}
    assert result.trace.tool_budget_used == 2
    assert result.trace.turn_budget_used <= 6
    assert result.status == "partial"


def test_duplicate_tool_calls_spend_budget_and_publish_deterministic_partial_report():
    client = FakeLLMClient(
        responder=lambda _system, _user: json.dumps(
            {"action": "tool", "tool": "get_verified_changes", "arguments": {}}
        )
    )
    result = CompanyResearchHarness(_service(), client).run("MSFT")

    assert result.status == "partial"
    assert result.report.insights == []
    assert result.trace.tool_budget_used == 4
    assert result.trace.turn_budget_used == 5
    assert result.trace.terminal_reason == "tool_budget_exhausted"
    assert [row.cached for row in result.trace.tool_calls] == [False, True, True, True]


def test_primary_provider_failure_is_a_closed_terminal_error():
    def fail(_system: str, _user: str) -> str:
        raise RuntimeError("secret provider detail")

    with pytest.raises(ResearchHarnessError, match="^provider_failed$"):
        CompanyResearchHarness(_service(), FakeLLMClient(responder=fail)).run("MSFT")


def test_repeated_malformed_actions_publish_a_deterministic_partial_report():
    client = FakeLLMClient(responses=["not JSON", '{"action":"unknown"}'])
    result = CompanyResearchHarness(_service(), client).run("MSFT")

    assert result.status == "partial"
    assert result.report.insights == []
    assert result.report.summary == "No additional research insight met the evidence standard."
    assert result.trace.terminal_reason == "malformed_action_breakdown"
    assert result.trace.turn_budget_used == 2


def test_optional_skeptic_failure_preserves_the_compiler_passing_baseline():
    def generator(_system: str, user: str) -> str:
        payload = json.loads(user)
        if not payload["observations"]:
            return json.dumps(
                {"action": "tool", "tool": "get_verified_changes", "arguments": {}}
            )
        observation_id = payload["observations"][0]["observation_id"]
        return json.dumps(
            {
                "action": "submit",
                "draft": {
                    "summary": "A verified filing change is available.",
                    "insights": [
                        _insight("i1", "change", observation_id).model_dump(mode="json")
                    ],
                },
            }
        )

    def fail_skeptic(_system: str, _user: str) -> str:
        raise RuntimeError("private skeptic failure")

    result = CompanyResearchHarness(
        _service(),
        FakeLLMClient(responder=generator),
        FakeLLMClient(responder=fail_skeptic),
    ).run("MSFT")
    assert [row.insight_id for row in result.report.insights] == ["i1"]
    assert result.trace.terminal_reason == "skeptic_unavailable"
    assert result.status == "partial"


def test_research_run_artifacts_are_owner_scoped_and_reusable():
    service = _service()
    repo = service.repo
    company = service._company("MSFT")
    assert company is not None
    repo.create_user(
        User(
            id="other",
            email="other-research@example.com",
            created_at="2026-08-03T00:00:00+00:00",
            last_login_at="2026-08-03T00:00:00+00:00",
        )
    )
    run_id = uuid.uuid4().hex
    input_hash = "a" * 64
    created = service.store.begin_research_run(
        company,
        run_id=run_id,
        input_hash=input_hash,
        now="2026-08-03T00:00:00+00:00",
    )

    assert created.status == "queued"
    filing = service._latest_supported(company)
    assert filing is not None
    harness = CompanyResearchHarness(service, FakeLLMClient(responses=[]))
    report, _ = harness.compile(
        company,
        filing,
        ResearchDraft(summary="Verified evidence was insufficient for qualitative insights."),
        {},
    )
    trace = ResearchTrace(
        obligation_transitions=report.obligations,
        tool_budget_used=0,
        turn_budget_used=1,
        repair_used=False,
        model="fake/model",
        prompt_version="Company_research.v1",
        compiler_version="company_research_compiler.v1",
        terminal_reason="turn_budget_exhausted",
    )
    assert service.store.set_research_running(run_id)
    assert service.store.finish_research_run(
        run_id,
        status="partial",
        report_json=report.model_dump_json(),
        trace_json=trace.model_dump_json(),
        now="2026-08-03T00:00:01+00:00",
    )
    loaded = service.store.matching_research_run(company, input_hash=input_hash)
    assert loaded is not None
    assert loaded.report == report
    assert loaded.trace == trace
    assert ProductService(repo, user_id="other").store.research_run(run_id) is None
