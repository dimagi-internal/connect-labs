"""Role-id ↔ short-id mapping for CampaignUser roles.

The DB and RBAC layer use full key names (e.g. "campaign_admin"). The React UI and
bootstrap payload use compact short ids (e.g. "admin") so the JS is readable and
the JSON payload is small. This module is the single source of truth for that mapping.
"""

from __future__ import annotations

SHORT_BY_KEY = {
    "campaign_admin": "admin",
    "payment_admin": "payment",
    "compliance_admin": "compliance",
    "operations_manager": "operations",
    "reporting_user": "reporting",
}
KEY_BY_SHORT = {v: k for k, v in SHORT_BY_KEY.items()}

LABEL_BY_KEY = {
    "campaign_admin": "Campaign Administrator",
    "payment_admin": "Payment Administrator",
    "compliance_admin": "Compliance Administrator",
    "operations_manager": "Operations Manager",
    "reporting_user": "Reporting User",
}


def to_label(key: str) -> str:
    """Return the human display name for a role key, or the key if unknown."""
    return LABEL_BY_KEY.get(key, key)


def to_short(key: str) -> str:
    """Return the short id for a role key, or the key itself if unknown."""
    return SHORT_BY_KEY.get(key, key)


def to_key(short: str) -> str | None:
    """Return the full role key for a short id, or None if unknown."""
    return KEY_BY_SHORT.get(short)
