"""Brokerage ticker import.

One canonical importer. Its input is a list of :class:`BrokerPosition` values already
reduced at the network boundary to identity only — no quantities, cost basis, market
values, or account identifiers exist on the type, so no downstream code can persist
them. Its output names every submitted symbol with a fixed outcome code.
"""

from finwatch.broker.importer import (
    Candidate,
    ImportPlan,
    ImportResult,
    ImportRow,
    apply_plan,
    plan_import,
    plan_symbols,
)
from finwatch.broker.symbols import (
    MAX_PASTED_SYMBOLS,
    BrokerPosition,
    is_trackable_instrument,
    normalize_broker_symbol,
    parse_symbol_list,
)

__all__ = [
    "MAX_PASTED_SYMBOLS",
    "BrokerPosition",
    "Candidate",
    "ImportPlan",
    "ImportResult",
    "ImportRow",
    "apply_plan",
    "is_trackable_instrument",
    "normalize_broker_symbol",
    "parse_symbol_list",
    "plan_import",
    "plan_symbols",
]
