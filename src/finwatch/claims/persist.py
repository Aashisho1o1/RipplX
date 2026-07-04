"""Convert stage claims (evidence / judgment) into persistable analysis_claims rows.

Claim ids are namespaced by the analysis id so the ``analysis_claims`` primary key
stays globally unique across re-runs (references inside the stored output_json remain
self-consistent under their original ids).
"""
from __future__ import annotations

import json

from finwatch.db.repositories import AnalysisClaim
from finwatch.llm.schemas import Claim


def to_analysis_claims(analysis_id: int, claims: list[Claim]) -> list[AnalysisClaim]:
    rows: list[AnalysisClaim] = []
    for c in claims:
        provenance_json = (
            json.dumps(c.provenance.model_dump()) if c.provenance is not None else None
        )
        basis_json = json.dumps(c.basis_claim_ids) if c.basis_claim_ids else None
        rows.append(AnalysisClaim(
            claim_id=f"{analysis_id}_{c.claim_id}",
            analysis_id=analysis_id,
            claim_type=c.claim_type,
            text=c.text,
            provenance_json=provenance_json,
            basis_claim_ids_json=basis_json,
            confidence=c.confidence,
        ))
    return rows
