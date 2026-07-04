"""Golden-set manifest + fixture loader (bundled under finwatch/evals/golden_set)."""
from __future__ import annotations

import importlib.resources
from dataclasses import dataclass, field

import yaml


@dataclass
class GoldenCase:
    id: str
    accession: str
    cik: str
    ticker: str
    form_type: str
    primary_doc: str
    category: str                       # 'critical' | 'boring'
    expected_critical_flags: list[str] = field(default_factory=list)


def _golden_dir():
    return importlib.resources.files("finwatch.evals").joinpath("golden_set")


def load_manifest() -> list[GoldenCase]:
    data = yaml.safe_load(_golden_dir().joinpath("manifest.yaml").read_text(encoding="utf-8"))
    return [GoldenCase(**c) for c in data["cases"]]


def load_case_html(case_id: str) -> str:
    return _golden_dir().joinpath(case_id, "filing.html").read_text(encoding="utf-8")


def load_recorded_p1(case_id: str) -> str:
    return _golden_dir().joinpath(case_id, "recorded_p1.json").read_text(encoding="utf-8")
