"""Single idempotent entry point for registering labs-only SyntheticOpportunity
rows. Used by the clone pipeline and the create/clone MCP tools so the
update_or_create idiom lives in exactly one place — and never clobbers an
existing folder/program when re-registered."""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .invalidation import invalidate_synthetic_caches
from .models import LABS_ONLY_OPP_ID_FLOOR, LabsOnlyIdReservation, SyntheticOpportunity  # noqa: F401
from .visit_count import resync_visit_count

#: Attempts before giving up; each failure means another job took the id we read.
_RESERVE_ATTEMPTS = 50


def reserve_labs_only_id(kind: str = LabsOnlyIdReservation.KIND_OPPORTUNITY, *, above: int | None = None) -> int:
    """Take the next free labs-only id for good, safe against other jobs doing the same.

    Reads the next free id and inserts a reservation for it; the unique constraint
    decides between two jobs that read the same value, and the loser reads again.
    ``above`` keeps the id strictly greater than a given value (a cohort's program id).
    """
    for _ in range(_RESERVE_ATTEMPTS):
        candidate = SyntheticOpportunity.next_labs_only_opp_id()
        if above is not None and candidate <= above:
            candidate = above + 1
        try:
            with transaction.atomic():
                LabsOnlyIdReservation.objects.create(value=candidate, kind=kind)
        except IntegrityError:
            continue
        return candidate
    raise RuntimeError(f"Could not reserve a labs-only id after {_RESERVE_ATTEMPTS} attempts")


def allocate_shared_program_id() -> int:
    """Reserve a labs-only program id (>= floor) for a cohort of synthetic opps.

    The id is reserved for good (``reserve_labs_only_id``), so no later program or
    opportunity is ever handed it -- not even when this cohort ends up registering
    nothing. Opp ids reserved afterwards are strictly above it.
    """
    return reserve_labs_only_id(LabsOnlyIdReservation.KIND_PROGRAM)


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
        opportunity_id = reserve_labs_only_id(above=program_id)

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

    existing = SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id).first()
    previous_folder_id = existing.gdrive_folder_id if existing else None
    row, _created = SyntheticOpportunity.objects.update_or_create(opportunity_id=opportunity_id, defaults=defaults)
    invalidate_synthetic_caches(opportunity_id)
    # Re-registration deliberately preserves an existing folder when none is
    # passed, so this no-ops on that path and only fires when the fixtures
    # actually moved (#1197).
    resync_visit_count(row, previous_folder_id=previous_folder_id)
    return row
