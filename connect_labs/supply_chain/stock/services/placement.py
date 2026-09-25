"""Every supply point carries a latitude and longitude -- and says how it got one.

A place nobody has surveyed is still somewhere, and the network map is useless
if half the network is "not on the map". So a point with no coordinates of its
own is given a STAND-IN: the head office of the organisation that manages it,
as the LLO directory records it -- the same `OrgProfile` coordinates Pulse
draws partners at. That is deliberately temporary. It is right to the town at
best and to the country at worst, and the point says which, so a map can draw
it as approximate and nobody routes a truck to it.

The order, finest first:

  org_hq   the managing organisation's head office (`OrgProfile.lat/lon`), or
           that of the directory organisation with exactly the same name --
           the OES demo seeded partners as rows of their own beside the
           directory's, and matching is exact-only, as everywhere else in the
           marketplace, so a near-miss name is never guessed at;
  parent   the point it is restocked from, when that one is placed -- a field
           worker with no organisation of its own sits at its store;
  country  the centre of the managing organisation's country.

**A recorded coordinate always wins, and is never overwritten.** A stand-in is
refreshed whenever the point is written, so it follows its organisation's head
office; a coordinate someone entered is `recorded` and left alone. Submitting
the stand-in back unchanged (an edit form pre-fills it) does NOT promote it to
recorded -- only a different coordinate does.
"""

from dataclasses import dataclass

RECORDED = "recorded"
STAND_INS = ("org_hq", "parent", "country")


@dataclass(frozen=True)
class Placement:
    lat: float
    lng: float
    source: str  # org_hq | parent | country
    precision: str  # city | region | country -- how fine the stand-in is
    label: str  # what it is the location OF, in words


def _profile_of(org, OrgProfile):
    """The directory profile carrying `org`'s head office, or None.

    Its own first; else one belonging to a directory organisation with exactly
    this name, because the same partner can exist as two LabsOrg rows (see the
    module docstring) and only one of them holds the directory's facts.
    """
    own = OrgProfile.objects.filter(org=org, lat__isnull=False, lon__isnull=False).first()
    if own is not None:
        return own
    return (
        OrgProfile.objects.filter(org__name__iexact=org.name.strip(), lat__isnull=False, lon__isnull=False)
        .select_related("org")
        .order_by("org_id")
        .first()
    )


def _country_of(org):
    from connect_labs.microplans.core import iso as iso_codes
    from connect_labs.pulse.hq_location import resolve

    if not org.country:
        return None
    return resolve(iso_codes.country_name(org.country) or org.country, "", "")


def _org_profile_model():
    from connect_labs.marketplace.models import OrgProfile

    return OrgProfile


def stand_in(point, OrgProfile=None) -> Placement | None:
    """Where to draw a point that has no coordinates of its own, or None.

    Model classes are parameters so the backfill migration can pass its
    historical ones; everything else uses the defaults.
    """
    OrgProfile = OrgProfile or _org_profile_model()
    org = point.managed_by_org
    if org is not None:
        profile = _profile_of(org, OrgProfile)
        if profile is not None:
            where = profile.location_label or profile.location_precision
            return Placement(
                profile.lat,
                profile.lon,
                "org_hq",
                profile.location_precision or "country",
                f"{profile.org.name} head office" + (f" ({where})" if where else ""),
            )

    parent = point.parent
    if parent is not None and parent.latitude is not None and parent.longitude is not None:
        return Placement(
            parent.latitude,
            parent.longitude,
            "parent",
            # A parent's own stand-in is no finer for being inherited.
            parent.location_precision if parent.location_source in STAND_INS else "",
            parent.name,
        )

    if org is not None:
        country = _country_of(org)
        if country is not None:
            return Placement(country.lat, country.lon, "country", "country", f"{country.label}, country centre")
    return None


def place(point, *, submitted=None, OrgProfile=None) -> bool:
    """Give `point` coordinates if it has none, or refresh a stand-in. Saves; True if it changed.

    `submitted` is the (lat, lng) the caller wrote, if any. A coordinate that
    differs from the stand-in on file is a real one, and is marked recorded.
    """
    if submitted is not None and None not in submitted:
        current = (point.latitude, point.longitude)
        if point.location_source in STAND_INS and _same(submitted, current):
            submitted = None  # the stand-in echoed back, not a new fact
        else:
            return _set(point, submitted[0], submitted[1], RECORDED, "", "")

    if point.location_source == RECORDED and point.latitude is not None and point.longitude is not None:
        return False
    if point.location_source == "" and point.latitude is not None and point.longitude is not None:
        # Coordinates from before this field existed were entered by someone.
        return _set(point, point.latitude, point.longitude, RECORDED, "", "")

    found = stand_in(point, OrgProfile)
    if found is None:
        return False
    return _set(point, found.lat, found.lng, found.source, found.precision, found.label)


def _same(a, b) -> bool:
    return all(x is not None and y is not None and abs(float(x) - float(y)) < 1e-6 for x, y in zip(a, b))


def _set(point, lat, lng, source, precision, label) -> bool:
    new = (lat, lng, source, precision, label)
    old = (point.latitude, point.longitude, point.location_source, point.location_precision, point.location_label)
    if new == old:
        return False
    point.latitude, point.longitude = lat, lng
    point.location_source, point.location_precision, point.location_label = source, precision, label
    point.save(
        update_fields=[
            "latitude",
            "longitude",
            "location_source",
            "location_precision",
            "location_label",
            "updated_at",
        ]
    )
    return True


def place_all(program_id=None, *, SupplyPoint=None, OrgProfile=None) -> dict:
    """Place every supply point (optionally one programme's). Parents first, so children can inherit."""
    if SupplyPoint is None:
        from connect_labs.supply_chain.models import SupplyPoint

    points = SupplyPoint.objects.select_related("managed_by_org", "parent")
    if program_id is not None:
        points = points.filter(program_id=program_id)
    tally = {"changed": 0, "unplaced": 0, "total": 0}
    # Roots before their children: a field worker inherits its store's spot.
    for point in sorted(points, key=_depth):
        tally["total"] += 1
        if point.parent_id:
            point.parent.refresh_from_db()
        if place(point, OrgProfile=OrgProfile):
            tally["changed"] += 1
        if point.latitude is None:
            tally["unplaced"] += 1
    return tally


def _depth(point, _seen=None):
    depth, node, seen = 0, point, set()
    while node.parent_id and node.pk not in seen:
        seen.add(node.pk)
        node = node.parent
        depth += 1
    return depth
