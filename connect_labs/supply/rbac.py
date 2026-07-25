"""Server-side permission matrix — the real gate.

``connect_labs/static/supply/perms.js`` mirrors this literal for client-side
show/hide only. ``tests/test_rbac_contract.py`` parses that file and asserts
equality, so the two can never drift.

Phase 3 adds the visualization modules (``command``, ``gov``, ``funder``) to
the gov_observer / funder rows.
"""

ROLE_PERMS = {
    "supplier": {
        "org": ["view", "edit"],
        "eoi": ["view", "submit"],
        "bids": ["view", "submit"],
        "execution": ["view", "report"],
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
        "audit": ["view"],
    },
    "gov_observer": {"execution": ["view"]},
    "funder": {"execution": ["view"]},
}


def can(role, module, verb):
    return verb in ROLE_PERMS.get(role, {}).get(module, [])
