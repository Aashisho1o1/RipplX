"""LLM layer: prompt loader, router/JSON extraction, stage schemas, stage runners."""
from __future__ import annotations

import json

import pytest

from finwatch.db import Repo, init_db
from finwatch.llm.prompts import STAGE_P1, load_prompt
from finwatch.llm.router import FakeLLMClient, LiteLLMClient, extract_json
from finwatch.llm.schemas import P1Output, P2Output
from finwatch.llm.stages import P1Extractor, StageError

VALID_P1 = {
    "accession_number": "a-1", "ticker": "T", "form_type": "8-K",
    "classification": {"items_8k": [], "overall_severity": "low"},
    "claims": [], "material_items": [],
    "guidance_direction": {"value": "none_stated", "claim_id": None},
    "red_flags": [], "extraction_confidence": "high", "gaps": [],
}


# ---- prompt loader ---------------------------------------------------------
def test_prompt_loader_splices_foundation_and_versions():
    text, version = load_prompt(STAGE_P1)
    assert "[FOUNDATION BLOCK]" not in text
    assert "R1. NUMBERS" in text            # foundation content spliced in
    assert "senior buy-side research analyst" in text  # P1 role
    assert version == "P1_extractor.v2+foundation.v1"
    assert '"claim_id"' in text and '"claim_type"' in text
    assert '{"value":"none_stated","claim_id":null}' in text


def test_foundation_prompt_has_its_own_version():
    _text, version = load_prompt("foundation")
    assert version == "foundation.v1"


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


# ---- schemas ---------------------------------------------------------------
def test_p1_schema_accepts_valid_and_rejects_missing_required():
    from pydantic import ValidationError

    P1Output.model_validate(VALID_P1)
    bad = {k: v for k, v in VALID_P1.items() if k != "guidance_direction"}
    with pytest.raises(ValidationError):
        P1Output.model_validate(bad)


def test_p2_schema_roundtrips():
    p2 = P2Output.model_validate({
        "accession_number": "a-1",
        "records_affected": [{
            "ticker": "T", "owned": True, "impact_class": "direct", "channels": {},
            "guidance_direction": "maintained", "liquidity_read": "stable",
            "net_direction": "neutral",
            "thesis_check": {"verdict": "intact"}, "net_read": {"text": "noise"},
            "confidence": "medium"}],
        "claims": [], "portfolio_level_notes": None,
    })
    assert p2.records_affected[0].thesis_check.verdict == "intact"


# ---- F3: strict claim-graph + vocabulary enforcement -----------------------
def test_out_of_vocabulary_enums_are_rejected():
    from pydantic import ValidationError

    for bad in [{"classification": {"items_8k": [], "overall_severity": "banana"}},
                {"extraction_confidence": "LOUD"},
                {"guidance_direction": {"value": "invented", "claim_id": None}}]:
        with pytest.raises(ValidationError):
            P1Output.model_validate({**VALID_P1, **bad})
    # well-formed but differently-cased value is normalised, not rejected
    assert P1Output.model_validate(
        {**VALID_P1, "extraction_confidence": "HIGH"}).extraction_confidence == "high"


def test_evidence_claim_without_provenance_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "claims": [
            {"claim_id": "c_1", "claim_type": "evidence", "text": "x"}]})   # no provenance


def test_judgment_claim_without_basis_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "claims": [
            {"claim_id": "c_1", "claim_type": "judgment", "text": "x"}]})   # no basis_claim_ids


def test_dangling_red_flag_claim_ref_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1,
            "red_flags": [{"flag": "going_concern", "severity": "critical",
                           "claim_ids": ["c_missing"]}]})


def test_dangling_judgment_basis_claim_ref_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "claims": [
            {"claim_id": "c_1", "claim_type": "judgment", "text": "x",
             "basis_claim_ids": ["c_missing"]}]})


def test_dangling_guidance_and_8k_rationale_claim_refs_are_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1,
            "guidance_direction": {"value": "lowered", "claim_id": "c_missing"}})
    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "classification": {"items_8k": [
            {"item": "4.02", "base_severity": "high", "final_severity": "high",
             "adjustment_rationale_claim_id": "c_missing"}],
            "overall_severity": "high"}})


def test_resolvable_judgment_basis_claim_ref_is_accepted():
    ev = {"claim_id": "c_1", "claim_type": "evidence", "text": "x", "confidence": "high",
          "provenance": {"accession_number": "a-1", "form_type": "8-K",
                         "section_key": "item_2_02", "char_start": 0, "char_end": 5,
                         "text_sha256_prefix": "z", "snippet": "hello"}}
    jg = {"claim_id": "c_2", "claim_type": "judgment", "text": "y", "basis_claim_ids": ["c_1"]}
    assert len(P1Output.model_validate({**VALID_P1, "claims": [ev, jg]}).claims) == 2


def test_p2_dangling_thesis_judgment_claim_ref_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P2Output.model_validate({
            "accession_number": "a-1",
            "records_affected": [{
                "ticker": "T", "owned": True, "impact_class": "direct", "channels": {},
                "guidance_direction": "maintained", "liquidity_read": "stable",
                "net_direction": "neutral",
                "thesis_check": {"verdict": "broken", "judgment_claim_id": "c_missing"},
                "net_read": {"text": "noise"}, "confidence": "medium"}],
            "claims": [], "portfolio_level_notes": None})


def _p2_record(thesis_jid, net_jid):
    return {"ticker": "T", "owned": True, "impact_class": "direct", "channels": {},
            "guidance_direction": "maintained", "liquidity_read": "stable",
            "net_direction": "neutral",
            "thesis_check": {"verdict": "weakened", "judgment_claim_id": thesis_jid},
            "net_read": {"text": "Mild pressure on the thesis.", "judgment_claim_id": net_jid},
            "confidence": "medium"}


def test_p2_synthesis_judgment_without_inline_basis_is_accepted():
    # Real models emit thesis/net judgment claims whose basis is P1's (separate)
    # evidence, so basis_claim_ids come back empty. That must validate: P2's basis
    # references the separate, already-verified P1 analysis. (This is the exact shape
    # that previously failed every material-filing analysis.)
    out = P2Output.model_validate({
        "accession_number": "a-1",
        "records_affected": [_p2_record("jud_005", "jud_006")],
        "claims": [
            {"claim_id": "jud_005", "claim_type": "judgment", "text": "thesis weakened",
             "basis_claim_ids": []},
            {"claim_id": "jud_006", "claim_type": "judgment", "text": "net neutral",
             "basis_claim_ids": []}],
        "portfolio_level_notes": None})
    assert {c.claim_id for c in out.claims} == {"jud_005", "jud_006"}
    assert out.records_affected[0].thesis_check.judgment_claim_id == "jud_005"


def test_p2_judgment_may_reference_p1_evidence_ids_in_basis():
    # A P2 judgment may cite P1 evidence ids; those live in the separate P1 analysis
    # and must NOT be rejected here as "unknown claim_id".
    out = P2Output.model_validate({
        "accession_number": "a-1",
        "records_affected": [_p2_record("jud_1", None)],
        "claims": [{"claim_id": "jud_1", "claim_type": "judgment", "text": "still intact",
                    "basis_claim_ids": ["p1_ev_001"]}],
        "portfolio_level_notes": None})
    assert out.claims[0].basis_claim_ids == ["p1_ev_001"]


def test_unknown_fields_are_forbidden():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        P1Output.model_validate({**VALID_P1, "surprise_field": 1})


# ---- stage runners ---------------------------------------------------------
def test_p1_extractor_parses_persists_and_namespaces_claims():
    repo = Repo(init_db(":memory:"))
    p1_json = dict(VALID_P1)
    p1_json["claims"] = [
        {"claim_id": "c_0001", "claim_type": "evidence", "text": "x", "confidence": "high",
         "provenance": {"accession_number": "a-1", "form_type": "8-K", "section_key": "item_2_02",
                        "char_start": 0, "char_end": 5, "text_sha256_prefix": "z",
                        "snippet": "hello"}},
    ]
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(p1_json))
    out, aid, _ = P1Extractor(llm, repo, model_label="fake/m", now_fn=lambda: "t").run(
        filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={})
    assert isinstance(out, P1Output)
    stored = repo.get_analysis(aid)
    assert stored.stage == "P1" and stored.model == "fake/m"
    claims = repo.list_analysis_claims(aid)
    assert [c.claim_id for c in claims] == [f"{aid}_c_0001"]      # namespaced
    assert claims[0].provenance_json is not None


def test_stage_error_on_unparseable_output():
    repo = Repo(init_db(":memory:"))
    llm = FakeLLMClient(responder=lambda _s, _u: "not json at all")
    with pytest.raises(StageError):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={})


def test_p1_extractor_repairs_one_schema_invalid_response():
    repo = Repo(init_db(":memory:"))

    def respond(_system, user):
        if '"_schema_repair"' in user:
            return json.dumps(VALID_P1)
        return json.dumps({**VALID_P1, "claims": [{"id": "j1", "type": "judgment"}]})

    llm = FakeLLMClient(responder=respond)
    out, _, _ = P1Extractor(llm, repo).run(
        filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={}
    )
    assert out.guidance_direction.value == "none_stated"
    assert len(llm.calls) == 2


def test_duplicate_claim_id_is_stage_error_and_leaves_no_orphan_row():
    # A malformed output with duplicate claim_ids must be caught as a schema-validity
    # failure BEFORE any DB write (not a raw IntegrityError), so it can regenerate.
    repo = Repo(init_db(":memory:"))
    dup = dict(VALID_P1)
    dup["claims"] = [
        {"claim_id": "c_1", "claim_type": "evidence", "text": "x",
         "provenance": {"accession_number": "a-1", "form_type": "8-K",
                        "section_key": "item_2_02", "char_start": 0, "char_end": 1,
                        "text_sha256_prefix": "z", "snippet": "h"}},
        {"claim_id": "c_1", "claim_type": "judgment", "text": "y", "basis_claim_ids": ["c_1"]},
    ]
    llm = FakeLLMClient(responder=lambda _s, _u: json.dumps(dup))
    with pytest.raises(StageError):
        P1Extractor(llm, repo).run(
            filing_meta={"accession_number": "a-1", "ticker": "T"}, sections={})
    assert repo.conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 0
