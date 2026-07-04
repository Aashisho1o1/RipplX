"""Sector-aware metrics: the Tier 1 envelope + formulas, plus the persistence service.

Import submodules directly (``finwatch.metrics.formulas``, ``.service``): the package
``__init__`` stays import-free because the Tier 1 ``xbrl.normalize`` imports
``finwatch.metrics.envelope``, so any eager import of ``formulas``/``service`` here
(both of which import ``normalize``) would create a circular import.
"""
