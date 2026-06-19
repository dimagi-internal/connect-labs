"""Role-based access control for the Campaign Utility Tool.

Single source of truth for permissions. The React `perms.js` mirrors this
matrix for show/hide only; the server is the real gate.
"""
from __future__ import annotations

ROLES = [
    "campaign_admin",
    "payment_admin",
    "compliance_admin",
    "operations_manager",
    "reporting_user",
]

MODULES = [
    "overview",
    "workers",
    "kyc",
    "payments",
    "activities",
    "planning",
    "reporting",
    "users",
    "connections",
    "training",
]

VERBS = ["view", "create", "edit", "approve", "manage", "export", "delete"]

_FULL = set(VERBS)
_VIEW = {"view"}
_VIEW_EXPORT = {"view", "export"}


def _row(**modules: set) -> dict[str, set]:
    """Build a module->verbs row, defaulting unspecified modules to no access."""
    return {m: set(modules.get(m, set())) for m in MODULES}


MATRIX: dict[str, dict[str, set]] = {
    "campaign_admin": {m: set(_FULL) for m in MODULES},
    "payment_admin": _row(
        overview=_VIEW,
        workers=_VIEW,
        payments={"view", "approve"},
        reporting=_VIEW_EXPORT,
        training=_VIEW,
    ),
    "compliance_admin": _row(
        overview=_VIEW,
        workers=_VIEW,
        kyc={"view", "create", "edit", "approve"},
        reporting=_VIEW_EXPORT,
        training=_VIEW,
    ),
    "operations_manager": _row(
        overview=_VIEW,
        workers=_VIEW,
        kyc=_VIEW,
        payments=_VIEW,
        activities={"create", "edit", "manage"},
        planning=_VIEW,
        reporting=_VIEW_EXPORT,
        training=_VIEW,
    ),
    "reporting_user": _row(
        overview=_VIEW,
        workers=_VIEW,
        kyc=_VIEW,
        payments=_VIEW,
        activities=_VIEW,
        planning=_VIEW,
        reporting=_VIEW_EXPORT,
        training=_VIEW,
    ),
}


def can(role: str, module: str, verb: str) -> bool:
    return verb in MATRIX.get(role, {}).get(module, set())


def access_label(role: str, module: str) -> str:
    verbs = MATRIX.get(role, {}).get(module, set())
    if not verbs:
        return "No Access"
    if verbs == _FULL:
        return "Full Access"
    # Order labels by the canonical verb order for stable output.
    ordered = [v.capitalize() for v in VERBS if v in verbs]
    return ", ".join(ordered)
