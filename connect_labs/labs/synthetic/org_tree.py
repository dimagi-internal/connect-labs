"""Derivation rules for the org/program shell of labs-only synthetic opps.

Shared by ``labs.context._merge_labs_only_opps`` (the UI's user_organizations
tree) and the ``/api/export/opp_org_program_list`` endpoint so the synthesized
org slugs and program ids can never drift between the two surfaces.
"""

from __future__ import annotations


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, alnum-only slug. Empty/blank input -> 'labs'."""
    return "".join(c if c.isalnum() else "-" for c in (value or "").strip().lower()).strip("-") or "labs"


def synthetic_org_slug(opp) -> str:
    """Stable org slug for a synthetic opp: ``labs-synthetic-<slugified org name>``."""
    return f"labs-synthetic-{slugify(opp.org_name or 'Labs Synthetic')}"


def synthetic_program_id(opp) -> int:
    """Program id for a synthetic opp: its registered ``program_id``, else its own opp id."""
    return opp.program_id or opp.opportunity_id
