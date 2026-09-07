"""The boundary set this app owns.

``AdminBoundary`` is shared. Targeting loads geoBoundaries ADM0/1/2 for Africa;
microplans loads GeoPoDe, and GRID3 supplies wards elsewhere. On a laptop that
has only ever run the targeting loader the distinction is invisible — every row
in the table belongs to this app — so a query filtered on ``admin_level`` alone
looks exactly right.

On the deployed database it is not. There the table also holds 204 GeoPoDe ADM1
and 2,291 GeoPoDe ADM2 units, which are a *second tessellation of the same
land*. Selecting on admin level alone swept them in: a threshold query that
returns 307 regions locally returned 1,203 on the server, and the birth total
fell from 7.2M to 1.0M because those units carry none of this app's population
data — they only inherit a rate from an ancestor, which is enough to clear a
threshold but contributes nothing to a count.

The same collision happens *within* a source. Investigating whether villages
could be targeted loaded Rwanda's 14,815 umudugudu as geoBoundaries ADM5 — the
right source, a level this app does not use, and enough to put 14,815 Rwandan
village polygons into a continental snapshot bound for production. Ownership is
therefore a source **and** a set of levels, both stated, because "the rows we
loaded" and "the rows tagged with our source" have already turned out to be
different things twice.

So every query that *enumerates* boundaries for targeting goes through here.
Resolution by primary key does not need it — a value's boundary is whatever
wrote it — and neither does the snapshot importer, which matches on the natural
key ``(source, boundary_id)`` and so can only touch its own rows.
"""

from __future__ import annotations

from django.db.models import QuerySet

from connect_labs.labs.admin_boundaries.models import AdminBoundary

#: geoBoundaries is the boundary source for targeting: CC BY 4.0, and the
#: tessellation every indicator here was matched against. See README §
#: "Where the data comes from".
#:
#: ADM0 and ADM1 cover every African country. **ADM2 does not** — 1,518 units
#: across 18 countries are loaded, and the 37 without one include Nigeria, DR
#: Congo, Kenya, Côte d'Ivoire, Mozambique and CAR. This comment used to claim
#: "ADM0-2 for every African country", which is how the partial load went
#: unnoticed: a pinned `admin_level=2` DROPS a country with no boundary at that
#: level (subnational spans levels 1-2, with no ADM0 to fall back to), so asking
#: for Nigerian districts returned "nothing qualifies" rather than an error.
#: `countries_missing_level` on a Selection now names them.
#:
#: geoBoundaries publishes ADM2 for all six under CC BY, so closing the gap is a
#: LOAD, not an acquisition: `load_boundaries NGA KEN COD CIV MOZ CAF --levels 2`.
#: Know what it buys before running it — an indicator measured at ADM1 is
#: INHERITED down to ADM2 rather than refined (Liberia: 54 districts carrying 4
#: distinct ORS values), so it is a finer delivery grid over the same signal.
#: Population is real per-district, and needs its own WorldPop backfill.
SOURCE = AdminBoundary.Source.GEOBOUNDARIES

#: The levels targeting works at: country, region, district. Deeper levels of the
#: same source belong to whatever loaded them — geoBoundaries publishes down to
#: ADM5 in a handful of countries, and one of those is loaded here for a question
#: about villages that this app cannot answer. See ``docs`` on the village note.
LEVELS = (0, 1, 2)


def owned() -> QuerySet[AdminBoundary]:
    """Boundaries this app loaded, and the only ones it may count."""
    return AdminBoundary.objects.filter(source=SOURCE, admin_level__in=LEVELS)
