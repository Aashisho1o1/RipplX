"""Shared, fail-closed validation for SEC companyfacts entries. Trust-critical.

Two consumers flatten the same companyfacts JSON: the metrics ``FactStore``
(:mod:`finwatch.xbrl.normalize`) and the DB row builder
(:func:`finwatch.ingest.service.companyfacts_to_rows`). Both must agree on exactly
which entries are usable.

The failure this centralizes: ``json.loads`` non-standardly accepts ``NaN``,
``Infinity`` and ``-Infinity``, and ``1e309`` overflows to ``inf``. A single such
value in an issuer's companyfacts used to raise out of the whole parse (via
``Fact.value: FiniteFloat``), discarding *every* good fact for that issuer. The fix
is NOT to silently skip the bad value and continue — dropping the newest datapoint
would let an older period slide into the "current" slot and be presented as current
(a plausible stale-looks-current growth number). Instead:

* skip entries with an explicit ``val is None`` (an explicit absence, not an error);
* reject unusable values (bool, non-numeric, non-finite) but keep the rest; and
* return the rejections so the accessor layer can fail closed when the rejected
  datapoint could be at least as recent as what it would otherwise show.

Malformed *dates* are intentionally left to flow through as facts: the metric
formula layer already fails closed on unparseable period dates (its spec tests
cover this), so no additional handling is needed here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FactRejection:
    """A companyfacts entry whose value could not be trusted, kept for the poison guard."""

    taxonomy: str
    tag: str
    unit: str
    end: str | None      # period end / instant date, or None when absent/non-string
    reason: str


@dataclass(frozen=True)
class CompanyFactsEntry:
    """A validated companyfacts entry: a finite ``value`` plus the raw entry dict."""

    taxonomy: str
    tag: str
    unit: str
    value: float
    entry: dict


def usable_value(val: object) -> float | None:
    """Return a finite float for a usable ``val``, or ``None`` if it must be rejected.

    Rejects booleans (``float(True) == 1.0`` would otherwise be silently ingested as
    a real 1.0), non-numeric values, and non-finite numbers — ``NaN``, ``±Infinity``,
    and ``1e309``-style overflow to ``inf`` — which SEC's own payloads never legitimately
    contain but a corrupted/anomalous one might.
    """
    if isinstance(val, bool):
        return None
    try:
        number = float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def iter_companyfacts(
    cf_json: dict,
) -> tuple[list[CompanyFactsEntry], list[FactRejection]]:
    """Flatten companyfacts into ``(valid_entries, rejections)``.

    An entry with ``val is None`` is an explicit absence and is skipped without being
    counted as a rejection. Any other unusable value is recorded as a rejection so a
    caller can reason about whether the rejected datapoint could be more recent than
    the facts it keeps.
    """
    valid: list[CompanyFactsEntry] = []
    rejected: list[FactRejection] = []
    for taxonomy, tags in (cf_json.get("facts") or {}).items():
        for tag, body in (tags or {}).items():
            for unit, entries in ((body or {}).get("units") or {}).items():
                for e in entries or ():
                    if not isinstance(e, dict):
                        continue
                    val = e.get("val")
                    if val is None:
                        continue
                    end = e.get("end")
                    end = end if isinstance(end, str) else None
                    number = usable_value(val)
                    if number is None:
                        rejected.append(
                            FactRejection(taxonomy, tag, unit, end, "non_finite_or_nonnumeric")
                        )
                        continue
                    valid.append(CompanyFactsEntry(taxonomy, tag, unit, number, e))
    return valid, rejected
