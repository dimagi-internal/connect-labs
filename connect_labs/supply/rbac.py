"""Server-side permission matrix — the real gate.

``connect_labs/static/supply/perms.js`` mirrors this literal for client-side
show/hide only. ``tests/test_rbac_contract.py`` parses that file and asserts
equality, so the two can never drift.

Phase 3 adds the visualization modules (``command``, ``gov``, ``funder``) to
the gov_observer / funder rows.

The ``partner`` row is the implementing partner — the organisation running the
feeding sites. They are neither a supplier (they never bid, so no ``eoi`` or
``bids``) nor an observer (they act: they receive stock, raise shortfalls and
record what they distributed).
"""

ROLE_PERMS = {
    "supplier": {
        "org": ["view", "edit"],
        "eoi": ["view", "submit"],
        "bids": ["view", "submit"],
        "execution": ["view", "report"],
        "tokens": ["manage"],
    },
    "partner": {
        "org": ["view", "edit"],
        "execution": ["view", "report"],
        "distribution": ["view", "manage"],
        "signals": ["view", "raise"],
        "outcomes": ["view"],
        "tokens": ["manage"],
    },
    "reviewer": {
        "eoi_review": ["view", "decide"],
        "registry": ["view"],
        "scoring": ["view", "score"],
        "execution": ["view"],
    },
    "procurement_admin": {
        "eoi_review": ["view", "decide"],
        "registry": ["view"],
        "scoring": ["view", "score"],
        "rounds": ["view", "manage"],
        "rfps": ["view", "manage", "award"],
        "execution": ["view", "resolve"],
        "signals": ["view", "resolve"],
        "actions": ["view", "create"],
        "outcomes": ["view"],
        "audit": ["view"],
    },
    "gov_observer": {"execution": ["view"]},
    "funder": {"execution": ["view"], "outcomes": ["view"]},
}


def can(role, module, verb):
    return verb in ROLE_PERMS.get(role, {}).get(module, [])
