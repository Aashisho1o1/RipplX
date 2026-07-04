"""concept_map.yaml must stay an exact mirror of the Tier 1 CONCEPT_MAP."""
from __future__ import annotations

import importlib.resources

import yaml

from finwatch.xbrl.normalize import CONCEPT_MAP


def test_concept_map_yaml_mirrors_source():
    text = (
        importlib.resources.files("finwatch.xbrl")
        .joinpath("concept_map.yaml")
        .read_text(encoding="utf-8")
    )
    assert yaml.safe_load(text) == dict(CONCEPT_MAP)
