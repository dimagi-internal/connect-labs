"""Build opportunity.json from live Connect opp detail."""

from __future__ import annotations

from typing import Any


_DEFAULTS = {
    "organization": "",
    "currency": "USD",
    "is_active": True,
}


def build_opportunity(
    detail: dict[str, Any],
    *,
    opportunity_name_override: str | None = None,
) -> dict[str, Any]:
    out = {**_DEFAULTS, **detail}
    if opportunity_name_override:
        out["name"] = opportunity_name_override
    return out
