"""Form-type classification for the preprocessor."""
from __future__ import annotations


def is_amendment(form_type: str) -> bool:
    return form_type.strip().upper().endswith("/A")


def base_form(form_type: str) -> str:
    """Strip the ``/A`` amendment suffix, uppercased."""
    f = form_type.strip().upper()
    return f[:-2] if f.endswith("/A") else f


def form_family(form_type: str) -> str:
    """Coarse family used to pick a section-routing strategy.

    Returns one of '10-K', '10-Q', '8-K', '20-F', '6-K', or the base form itself.
    """
    base = base_form(form_type)
    for family in ("10-K", "10-Q", "8-K", "20-F", "6-K"):
        if base.startswith(family):
            return family
    return base
