"""P0 filing preprocessor: form routing, canonical sections, furnished/amendment,
risk-factor diff (all deterministic)."""

from finwatch.preprocess.diff import (
    ModifiedPair,
    Paragraph,
    RiskFactorDiff,
    diff_risk_factors,
    split_paragraphs,
)
from finwatch.preprocess.eightk import furnishing_present, split_8k
from finwatch.preprocess.forms import base_form, form_family, is_amendment
from finwatch.preprocess.html import NormalizedDoc, html_to_text
from finwatch.preprocess.preprocessor import (
    Preprocessor,
    PreprocessResult,
    route_sections,
)
from finwatch.preprocess.sections import Section, split_10k, split_10q

__all__ = [
    "html_to_text",
    "NormalizedDoc",
    "Section",
    "split_10k",
    "split_10q",
    "split_8k",
    "furnishing_present",
    "base_form",
    "form_family",
    "is_amendment",
    "diff_risk_factors",
    "split_paragraphs",
    "RiskFactorDiff",
    "Paragraph",
    "ModifiedPair",
    "Preprocessor",
    "PreprocessResult",
    "route_sections",
]
