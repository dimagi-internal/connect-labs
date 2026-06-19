"""Single idempotent entry point for registering labs-only SyntheticOpportunity
rows. Used by the clone pipeline and the create/clone MCP tools so the
update_or_create idiom lives in exactly one place — and never clobbers an
existing folder/program when re-registered."""

from __future__ import annotations

from .models import LABS_ONLY_OPP_ID_FLOOR, SyntheticOpportunity  # noqa: F401
from .registry import invalidate_cache


def allocate_shared_program_id() -> int:
    """Reserve a labs-only program id (>= floor) for a cohort of synthetic opps.

    This id is RESERVED for use as a program identifier and must not be reused as
    an opportunity id.  ``generate_opp_from_bundle`` allocates opp ids strictly
    above this value (``max(next_labs_only_opp_id(), program_id + 1)``) so the
    program id and any opp id in the same cohort can never collide.

    Call this BEFORE creating the cohort's opps so subsequent ``next_labs_only_opp_id``
    calls land above it.
    """
    return SyntheticOpportunity.next_labs_only_opp_id()


def register_labs_only_opp(
    *,
    opportunity_id: int | None = None,
    label: str,
    gdrive_folder_id: str | None = None,
    org_name: str | None = None,
    program_name: str | None = None,
    program_id: int | None = None,
    allowed_domains: list[str] | None = None,
    cloned_from: int | None = None,
    enabled: bool = True,
    created_by=None,
) -> SyntheticOpportunity:
    if opportunity_id is None:
        opportunity_id = SyntheticOpportunity.next_labs_only_opp_id()

    defaults: dict = {"labs_only": True, "enabled": enabled, "label": label}
    # Only set keys that were explicitly provided, so re-registration never
    # wipes an existing folder/program/etc.
    if gdrive_folder_id is not None:
        defaults["gdrive_folder_id"] = gdrive_folder_id
    if org_name is not None:
        defaults["org_name"] = org_name
    if program_name is not None:
        defaults["program_name"] = program_name
    if program_id is not None:
        defaults["program_id"] = program_id
    if allowed_domains is not None:
        defaults["allowed_domains"] = allowed_domains
    if cloned_from is not None:
        defaults["cloned_from_opportunity_id"] = cloned_from
    if created_by is not None:
        defaults["created_by"] = created_by

    row, _created = SyntheticOpportunity.objects.update_or_create(opportunity_id=opportunity_id, defaults=defaults)
    invalidate_cache()
    return row
