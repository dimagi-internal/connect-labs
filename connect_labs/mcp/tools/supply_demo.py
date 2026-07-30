"""MCP tool for reseeding the OES supply demo world.

Thin ``@register`` shim, same shape as ``program_admin_demo.py`` — the
implementation lives with the app it seeds
(``connect_labs/supply/api/demo.reseed_demo_world``), shared with the HTTP route
so both paths cannot drift.

Why this exists as an MCP tool: every OES walkthrough mutates state on purpose,
so a re-render needs the world put back, and on a deployed instance there is no
shell to run ``manage.py seed_supply_demo --reset`` in. This is the labs-native
way to reach into a deployed instance — PAT-authenticated, in-process, no AWS
session, nothing to provision.
"""

from __future__ import annotations

from typing import Any

from ..tool_registry import register


@register(
    name="supply_demo_reseed",
    description=(
        "Reset the OES supply demo world (/supply/) to its seeded state and "
        "return the seeder's own summary. Deletes and rebuilds the supply_* "
        "tables via seed_supply_demo --reset; deterministic, so the rebuilt "
        "world is identical every time. Use between walkthrough takes: every OES "
        "narrative mutates state on purpose (a reviewer records a qualification, "
        "a buyer awards two lots), and award_lot refuses a lot that already has "
        "an award — so a second render does not merely look different, it fails. "
        "Pass `password` to also set every demo persona's password, which is how "
        "a render gets a known credential without a pre-shared secret: reseed, "
        "then sign in with what you just set. Only touches connect_labs/supply, "
        "which is demo-only — no Connect or CommCare data is involved."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "password": {
                "type": ["string", "null"],
                "default": None,
                "description": (
                    "Set every demo persona's password to this value as part of the "
                    "reseed (the seeder rotates passwords on every run regardless, so "
                    "this only chooses the value). Omit to keep whatever "
                    "SUPPLY_DEMO_PASSWORD the instance is configured with. Minimum 8 "
                    "characters — this is a login on a publicly reachable host."
                ),
            },
        },
        "additionalProperties": False,
    },
    is_write=True,
)
def supply_demo_reseed(password: str | None = None, **_: Any) -> dict:
    from connect_labs.supply.api.demo import reseed_demo_world

    password = (password or "").strip() or None
    if password is not None and len(password) < 8:
        raise ValueError("password must be at least 8 characters — this sets a login on a " "publicly reachable host.")

    summary = reseed_demo_world(password=password)
    return {
        "ok": True,
        "reseeded": True,
        "password_set": bool(password),
        "summary": summary,
    }
