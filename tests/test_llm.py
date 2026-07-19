"""LLM layer: prompt loader, router/JSON extraction, stage schemas, stage runners."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from finwatch.db import Repo, init_db
from finwatch.llm.prompts import STAGE_P1, load_prompt
from finwatch.llm.router import (
    LAUNCH_MAX_OUTPUT_TOKENS,
    FakeLLMClient,
    LiteLLMClient,
    extract_json,
)
from finwatch.llm.schemas import P1Output
from finwatch.llm.stages import P1Extractor, StageError
from finwatch.metrics.envelope import MetricsBundle
from finwatch.verify.compiler import compile_draft

VALID_P1 = {
    "accession_number": "a-1", "ticker": "T", "form_type": "8-K",
    "classification": {"overall_severity": "low"},
    "findings": [], "extraction_confidence": "high", "gaps": [],
}


def _evidence(**over):
    value = {
        "accession_number": "a-1", "form_type": "8-K", "section_key": "item_2_02",
        "char_start": 0, "char_end": 5, "snippet": "hello",
    }
    value.update(over)
    return value


def _finding(**over):
    value = {
        "finding_id": "f1", "headline": "Results changed", "severity": "medium",
        "critical_flag": None,
        "evidence": [_evidence()],
    }
    value.update(over)
    return value


# ---- prompt loader ---------------------------------------------------------
def test_prompt_loader_splices_foundation_and_versions():
    text, version = load_prompt(STAGE_P1)
    assert "[FOUNDATION BLOCK]" not in text
    assert "R1. NUMBERS" in text            # foundation content spliced in
    assert "filing-research Generator" in text
    assert version == "P1_extractor.v5+foundation.v2"
    assert '"findings"' in text and '"critical_flag"' in text
    assert "the server derives them" in text  # offsets are server-anchored, not model-supplied


def test_foundation_prompt_has_its_own_version():
    _text, version = load_prompt("foundation")
    assert version == "foundation.v2"


def test_stage_prompt_without_foundation_placeholder_hard_fails(monkeypatch):
    # If a stage prompt loses its [FOUNDATION BLOCK] marker, the injection-defense
    # foundation must NOT be silently omitted — load_prompt fails closed. `foundation`
    # itself carries no placeholder and stays loadable.
    from finwatch.llm import prompts as prompts_mod

    monkeypatch.setattr(prompts_mod, "_read", lambda _name: "stage body with no placeholder")
    with pytest.raises(ValueError, match="FOUNDATION BLOCK"):
        prompts_mod.load_prompt(prompts_mod.STAGE_P1)

    body, version = prompts_mod.load_prompt("foundation")
    assert body == "stage body with no placeholder"
    assert version == "foundation.v2"


# ---- JSON extraction -------------------------------------------------------
def test_extract_json_plain_and_fenced_and_prose():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Here is the output:\n{"a": 3}\nDone.') == {"a": 3}


# ---- fake router -----------------------------------------------------------
def test_fake_llm_responder_and_queue_and_records_calls():
    responder = FakeLLMClient(responder=lambda _s, u: "R:" + u)
    r = responder.complete(system="s", user="hi")
    assert r.text == "R:hi" and responder.calls == [("s", "hi")]

    queue = FakeLLMClient(responses=["a", "b"])
    assert queue.complete(system="s", user="u").text == "a"
    assert queue.complete(system="s", user="u").text == "b"


def test_litellm_client_construction_is_lazy():
    # Construction must not import/call litellm (it happens only in complete()).
    client = LiteLLMClient("provider/model")
    assert client.model == "provider/model"
    with pytest.raises(ValueError):
        LiteLLMClient("")


def test_litellm_call_has_fixed_launch_output_cap(monkeypatch):
    captured = {}
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )
    fake_module = SimpleNamespace(
        completion=lambda **kwargs: captured.update(kwargs) or response,
        completion_cost=lambda **_kwargs: 0.0,
    )
    monkeypatch.setitem(sys.modules, "litellm", fake_module)

    LiteLLMClient("openai/test").complete(system="s", user="u")

    assert captured["max_tokens"] == LAUNCH_MAX_OUTPUT_TOKENS == 2_000


# ---- schemas ---------------------------------------------------------------
def test_p1_schema_accepts_valid_and_rejects_missing_required():
    from pydantic import ValidationError

    P1Output.model_validate(VALID_P1)
    bad = {k: v for k, v in VALID_P1.items() if k != "findings"}
    with pytest.raises(ValidationError):
        P1Output.model_validate(bad)


# ---- strict evidence-backed finding contract -------------------------------
def test_out_of_vocabulary_enums_are_rejected():
    from pydantic import ValidationError

    for bad in [{"classification": {"overall_severity": "banana"}},
                {"extraction_confidence": "LOUD"}]:
        with pytest.raises(ValidationError):
            P1Output.model_validate({**VALID_P1, **bad})
    # well-formed but differently-cased value is normalised, not rejected
    assert P1Output.model_validate(
        {**VALID_P1, "extraction_confidence": "HIGH"}).extraction_confidence == "high"


def test_finding_requires_one_to_three_exact_evidence_spans():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "medium"},
            "findings": [_finding(evidence=[])],
        })
    with pytest.raises(ValidationError):
        P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "medium"},
            "findings": [_finding(evidence=[_evidence()] * 4)],
        })
    with pytest.raises(ValidationError):
        P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "medium"},
            "findings": [_finding(evidence=[_evidence(char_start=5, char_end=5)])],
        })


def test_p1_rejects_general_claim_graph_and_legacy_parallel_lists():
    from pydantic import ValidationError

    for field, value in (
        ("claims", []), ("material_items", []), ("red_flags", []),
        ("guidance_direction", {"value": "none_stated"}),
        ("risk_factor_findings", None),
    ):
        with pytest.raises(ValidationError):
            P1Output.model_validate({**VALID_P1, field: value})


def test_critical_flag_is_strictly_controlled_and_severity_gated():
    from pydantic import ValidationError

    for finding in (
        _finding(severity="high", critical_flag="invented_flag"),
        _finding(severity="medium", critical_flag="going_concern"),
        _finding(severity="high", critical_flag="cyber_1_05_critical_tier"),
    ):
        with pytest.raises(ValidationError):
            P1Output.model_validate({
                **VALID_P1,
                "classification": {"overall_severity": finding["severity"]},
                "findings": [finding],
            })
    valid = P1Output.model_validate({
        **VALID_P1,
        "classification": {"overall_severity": "critical"},
        "findings": [_finding(severity="critical", critical_flag="going_concern")],
    })
    assert valid.findings[0].critical_flag == "going_concern"


def test_p1_compiler_localizes_numbers_and_schema_keeps_severity_consistent():
    from pydantic import ValidationError

    for headline in ("Revenue rose 12 percent", "Revenue rose fifty percent"):
        draft = P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "medium"},
            "findings": [_finding(headline=headline)],
        })
        result = compile_draft(
            draft,
            trusted_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
            sections={"item_2_02": {"text": "hello"}},
            metrics=MetricsBundle(),
        )
        assert "AUTHORED_NUMBER" in {issue.code for issue in result.issues}
    with pytest.raises(ValidationError):
        P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "high"},
            "findings": [_finding(severity="medium")],
        })
    with pytest.raises(ValidationError):
        P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "critical"},
            "findings": [],
        })


def test_finding_headline_policy_is_a_finding_local_compiler_error():
    for headline in (
        "We recommend buying the shares",
        "Investors should consider adding shares",
        "Revenue quadrupled this year",
    ):
        draft = P1Output.model_validate({
            **VALID_P1,
            "classification": {"overall_severity": "medium"},
            "findings": [_finding(headline=headline)],
        })
        result = compile_draft(
            draft,
            trusted_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
            sections={"item_2_02": {"text": "hello"}},
            metrics=MetricsBundle(),
        )
        codes = {issue.code for issue in result.issues}
        assert codes & {"UNSAFE_LANGUAGE", "AUTHORED_NUMBER"}


def _p2_record(thesis_jid, net_jid):
    return {"ticker": "T", "owned": True, "impact_class": "direct", "channels": {},
            "guidance_direction": "maintained", "liquidity_read": "stable",
            "net_direction": "neutral",
            "thesis_check": {"verdict": "weakened", "judgment_claim_id": thesis_jid},
            "net_read": {"text": "Mild pressure on the thesis.", "judgment_claim_id": net_jid},
            "confidence": "medium"}


def test_unknown_fields_are_forbidden():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "surprise_field": 1})


# ---- stage runners ---------------------------------------------------------
def test_p1_extractor_persists_embedded_findings_without_claim_rows():
    repo = Repo(init_db(":memory:"))
    p1_json = {
        **VALID_P1,
        "classification": {"overall_severity": "medium"},
        "findings": [_finding()],
    }
    llm = FakeLLMClient(responder=lambda system, _u: json.dumps(
        {"action": "done", "obligations": []}
        if "finance Skeptic" in system else {"action": "submit", "draft": p1_json}
    ))
    out, aid, _ = P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t").run(
        filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
        sections={"item_2_02": {"text": "hello"}})
    assert isinstance(out, P1Output)
    stored = repo.get_analysis(aid)
    assert stored.stage == "P1" and stored.model == "fake/m"
    assert out.findings[0].evidence[0].snippet == "hello"
    # offsets are server-anchored from the section text, not the model's echoed values
    assert (out.findings[0].evidence[0].char_start, out.findings[0].evidence[0].char_end) == (0, 5)


def test_stage_error_on_unparseable_output():
    repo = Repo(init_db(":memory:"))
    llm = FakeLLMClient(responder=lambda _s, _u: "not json at all")
    with pytest.raises(StageError):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={})


def test_p1_trusted_identity_mismatch_never_persists():
    repo = Repo(init_db(":memory:"))
    wrong = {
        **VALID_P1,
        "accession_number": "attacker-accession",
        "ticker": "EVIL",
    }
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(
        {"action": "submit", "draft": wrong}
    ))

    with pytest.raises(StageError, match="form_scope"):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
            sections={},
        )

    assert len(llm.calls) == 2
    assert repo.latest_analysis("a-1", "P1") is None
    assert repo.latest_analysis("a-1", "P1_TRACE") is not None


def test_canonical_critical_8k_item_cannot_be_omitted_or_downgraded():
    repo = Repo(init_db(":memory:"))
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(
        {"action": "submit", "draft": VALID_P1}
    ))

    with pytest.raises(StageError, match="critical_coverage"):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
            sections={"item_4_02": {"text": "Statements should no longer be relied upon."}},
        )

    assert len(llm.calls) == 2
    assert repo.latest_analysis("a-1", "P1") is None
    assert repo.latest_analysis("a-1", "P1_TRACE") is not None


def test_large_section_is_progressively_disclosed_not_put_in_initial_prompt():
    repo = Repo(init_db(":memory:"))
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(
        {"action": "submit", "draft": VALID_P1}
    ))

    P1Extractor(llm, repo).run(
        filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
        sections={"mdna": {"text": "x" * 240_000}},
    )

    assert len(llm.calls) == 1
    assert "x" * 1_000 not in llm.calls[0][1]


def test_p1_extractor_repairs_one_schema_invalid_response():
    repo = Repo(init_db(":memory:"))

    def respond(_system, user):
        if '"last_error": "INVALID_ACTION"' in user:
            return json.dumps({"action": "submit", "draft": VALID_P1})
        return json.dumps({"action": "submit", "surprise": "invalid"})

    llm = FakeLLMClient(responder=respond)
    out, _, _ = P1Extractor(llm, repo).run(
        filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
        sections={},
    )
    assert out.findings == []
    assert len(llm.calls) == 2


def test_schema_repair_prompt_does_not_leak_validation_error_text_to_model():
    # Invalid model output is never echoed back; only a controlled error code is.
    repo = Repo(init_db(":memory:"))
    sentinel = "ZZZSENTINEL"

    def respond(_system, user):
        if '"last_error": "INVALID_ACTION"' in user:
            return json.dumps({"action": "submit", "draft": VALID_P1})
        return json.dumps({"action": "submit", "draft": sentinel})

    llm = FakeLLMClient(responder=respond)
    P1Extractor(llm, repo).run(
        filing_meta={"accession_number": "a-1", "ticker": "T", "form_type": "8-K"},
        sections={},
    )

    assert len(llm.calls) == 2
    repair_user = llm.calls[1][1]
    assert '"last_error": "INVALID_ACTION"' in repair_user
    assert sentinel not in repair_user


def test_more_than_three_findings_is_stage_error_and_leaves_no_orphan_row():
    # The launch cap is part of schema validation and runs before any DB write.
    repo = Repo(init_db(":memory:"))
    invalid = {
        **VALID_P1,
        "classification": {"overall_severity": "medium"},
        "findings": [
            _finding(finding_id=f"f{index}", headline=name)
            for index, name in enumerate(("Alpha", "Beta", "Gamma", "Delta"), start=1)
        ],
    }
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(
        {"action": "submit", "draft": invalid}
    ))
    with pytest.raises(StageError):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={})
    assert repo.latest_analysis("a-1", "P1") is None
    trace = repo.latest_analysis("a-1", "P1_TRACE")
    assert trace is not None
    assert json.loads(trace.output_json)["terminal_reason"] == "malformed_action_breakdown"
