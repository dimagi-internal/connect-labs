"""Whether a synthetic opportunity's visit data was GENERATED, and so can never be real.

"Synthetic" in this package means "served from fixtures instead of Connect's export
API". Most such opps hold data the generator made, but a fixture folder can also be a
dump of real production exports (``dump.py``, the power-user path into profiling), and
several tools register an opp against any folder a caller names. So "labs-only" and
"synthetic" say nothing about whether the rows are real.

This module is the one answer to that question. The generator records the folder it
wrote (``SyntheticOpportunity.generated_folder_id``); an opp counts as generated only
while it is labs-only and still serves that exact folder. Re-pointing it anywhere else
un-marks it by construction. Only server code that has just written generated data may
call ``mark_generated``; nothing a caller supplies can set it.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import SyntheticOpportunity


def is_generated(row: SyntheticOpportunity | None) -> bool:
    return bool(
        row is not None
        and row.labs_only
        and row.enabled
        and row.generated_folder_id is not None
        and row.generated_folder_id == row.gdrive_folder_id
    )


def generated_opportunity_ids(opportunity_ids: Iterable) -> set[int]:
    """The subset of ``opportunity_ids`` whose data is generated."""
    ids = set()
    for value in opportunity_ids:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    rows = SyntheticOpportunity.objects.filter(opportunity_id__in=ids)
    return {row.opportunity_id for row in rows if is_generated(row)}


def all_generated(opportunity_ids: Iterable) -> bool:
    """True when there is at least one id and every one of them is generated."""
    ids = list(opportunity_ids)
    if not ids:
        return False
    try:
        wanted = {int(value) for value in ids}
    except (TypeError, ValueError):
        return False
    return generated_opportunity_ids(wanted) == wanted


def mark_generated(opportunity_id: int, folder_id: str) -> None:
    """Record that ``folder_id`` holds data the generator just wrote for this opp.

    Call only from generation code, right after it wrote ``folder_id``. ``""`` is the
    folder of an opp whose data lives in labs-local records authored from a manifest.
    """
    SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id).update(generated_folder_id=folder_id)
