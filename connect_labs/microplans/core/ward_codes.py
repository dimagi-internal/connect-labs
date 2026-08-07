"""Lookup a precomputed, nationally-unique ward code from the GRID3 Nigeria
operational-wards reference table (``data/ward_codes.json`` — see
``data/build_ward_codes.py`` for how it's generated).

Why this exists, alongside ``grouping._disambiguated_ward_prefixes``: that
function only prevents label collisions between wards that happen to appear
in the SAME ``group_work_areas`` call. Two wards processed in separate
plans/calls have no visibility into each other's chosen labels — and a real
production plan hit exactly that (two different ~15km-apart wards, "Doka"
and "Doka Dawa", both naturally truncating to "DOK", silently merging their
groups' stats together downstream). This table is precomputed ONCE, offline,
with global knowledge of every ward in the country, so a lookup hit is
guaranteed unique regardless of which other wards happen to share a plan.

Matching is deliberately simple: normalize (trim, casefold) and look up the
exact (state, lga, ward) triple — no fuzzy or alt-name matching. A work
area's own recorded ward/lga doesn't always match GRID3's canonical spelling
(confirmed: "Galinja (non-RCT)" has no GRID3 match at all — a local
annotation), so trailing parenthetical annotations are stripped before
matching, but nothing fancier. A miss just means no code for that ward —
the caller falls back to ``_disambiguated_ward_prefixes``, which still
guarantees no collision within that one plan even without table coverage.
Deliberately kept simple: multi-ward collisions are rare in practice (plans
rarely span more than ~20 wards) and the runtime fallback already covers
that case well."""

from __future__ import annotations

import json
import re
from pathlib import Path

_TABLE_PATH = Path(__file__).parent / "data" / "ward_codes.json"
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")

_index: dict[tuple[str, str, str], str] | None = None


def _normalize(s: str | None) -> str:
    return _TRAILING_PAREN.sub("", (s or "").strip()).casefold()


def _load_index() -> dict[tuple[str, str, str], str]:
    global _index
    if _index is None:
        try:
            with open(_TABLE_PATH) as f:
                rows = json.load(f)
        except (OSError, ValueError):
            rows = []
        _index = {(_normalize(r["state"]), _normalize(r["lga"]), _normalize(r["ward"])): r["code"] for r in rows}
    return _index


def lookup_ward_code(state: str | None, lga: str | None, ward: str | None) -> str | None:
    """The precomputed, nationally-unique code for this (state, lga, ward),
    or None if it's not in the reference table (no fuzzy matching — see
    module docstring)."""
    if not ward:
        return None
    return _load_index().get((_normalize(state), _normalize(lga), _normalize(ward)))
