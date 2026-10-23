"""A clone's stock as the programme says it moves: warehouse -> PHC -> field worker.

The programme's own account (Sophie, 2026-10-23): RUTF sits in cartons in one
warehouse, is transferred every week to the primary health centre (PHC) near the
field workers, and the workers collect it from there. This module holds that
layout for `clone_supply.seed(..., phcs=N)`:

- `assign_phcs` splits the workers between N PHCs by where their visits are, so
  each PHC is drawn among the workers it serves (the map places a store nobody
  gave an address at the middle of the places it restocks -- placement.py);
- `size_transfer` sizes one week's warehouse -> PHC transfer, in whole cartons.

Which PHC serves which worker, and every transfer's size, are ASSUMED (each
record's note says so): the programme has said how stock moves, not how much.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal
from statistics import median

CARTON = 150  # sachets per carton
# A PHC is restocked to hold this many weeks of its workers' combined pace: a
# worker collects every fortnight, so two weeks is what one pickup round takes and
# the third is the PHC's own buffer.
PHC_WEEKS = 3
PHC_NOTE = (
    "Inferred for the demo: the programme says field workers collect RUTF from a nearby PHC that the "
    "warehouse restocks every week. Which PHC serves which worker is assumed from where their visits are."
)
TRANSFER_NOTE = (
    "Inferred for the demo: the programme says the warehouse restocks each PHC every week. The quantity is "
    "assumed: whole cartons, enough for the week's pickups and about three weeks of its workers' pace."
)


def worker_locations(visits, visit_location) -> dict:
    """{username: (lat, lng)} -- the median of each worker's visit GPS; workers without GPS are left out."""
    points: dict = {}
    for visit in visits:
        username = str(visit.get("username") or "").strip()
        lat, lng = visit_location(visit)
        if username and lat is not None and lng is not None:
            points.setdefault(username, []).append((lat, lng))
    return {u: (median(p[0] for p in ps), median(p[1] for p in ps)) for u, ps in points.items()}


def assign_phcs(usernames, locations: dict, phcs: int) -> dict:
    """{username: PHC index 0..phcs-1}: workers cut into `phcs` equal runs along their wider spread.

    Deterministic, so a top-up and a reseed agree. Workers with no GPS go to the
    first PHC. One cut along the longer axis keeps each PHC's workers together on
    the map, which is all the layout needs to read right.
    """
    names = sorted(usernames)
    if phcs <= 1:
        return {u: 0 for u in names}
    placed = sorted(u for u in names if u in locations)
    if placed:
        lats = [locations[u][0] for u in placed]
        lngs = [locations[u][1] for u in placed]
        axis = 0 if (max(lats) - min(lats)) >= (max(lngs) - min(lngs)) else 1
        placed.sort(key=lambda u: (locations[u][axis], u))
    out = {u: 0 for u in names}
    for rank, username in enumerate(placed):
        out[username] = min(rank * phcs // len(placed), phcs - 1)
    return out


def nearest_phc(location, phc_locations: dict) -> int:
    """Index of the PHC nearest `location` ((lat, lng) or None); the first when either is unknown."""
    if location is None or not phc_locations:
        return 0
    return min(
        phc_locations,
        key=lambda i: (phc_locations[i][0] - location[0]) ** 2 + (phc_locations[i][1] - location[1]) ** 2,
    )


def to_cartons(sachets: Decimal) -> Decimal:
    """Sachets rounded UP to whole cartons, in sachets."""
    if sachets <= 0:
        return Decimal(0)
    return (sachets / CARTON).to_integral_value(rounding=ROUND_CEILING) * CARTON


def size_transfer(*, held: Decimal, pickups_this_week: Decimal, daily_pace: Decimal) -> Decimal:
    """Sachets to send a PHC holding `held`, whole cartons.

    Enough for the week's pickups, and to leave about PHC_WEEKS of its workers'
    combined daily pace on the shelf; nothing when it already holds that.
    """
    target = max(daily_pace * 7 * PHC_WEEKS, pickups_this_week)
    return to_cartons(max(target - held, pickups_this_week - held, Decimal(0)))
