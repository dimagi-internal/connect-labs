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

#: geoBoundaries is the boundary source for targeting: CC BY 4.0, ADM0-2 for
#: every African country, and the tessellation every indicator here was matched
#: against. See README § "Where the data comes from".
SOURCE = AdminBoundary.Source.GEOBOUNDARIES

#: The levels targeting works at: country, region, district. Deeper levels of the
#: same source belong to whatever loaded them — geoBoundaries publishes down to
#: ADM5 in a handful of countries, and one of those is loaded here for a question
#: about villages that this app cannot answer. See ``docs`` on the village note.
LEVELS = (0, 1, 2)


def owned() -> QuerySet[AdminBoundary]:
    """Boundaries this app loaded, and the only ones it may count."""
    return AdminBoundary.objects.filter(source=SOURCE, admin_level__in=LEVELS)
