"""Turning Pulse's coordinates into named places and printable maps.

Pulse stores where work happened as bare coordinates -- ``PulseEvent.lat/lon``
while the rows live, and ``PulseGridCell`` (~1 km cells) forever after. A donor
report has to say *"Jere, MMC, Konduga and Ngala"*, not plot anonymous dots, so
something has to turn points into admin units. ``labs.admin_boundaries`` already
holds those polygons in PostGIS for microplans; this module is the join.

Two design points worth stating, because both were tempting to get wrong:

**The window decides which spine draws the map.** Grid cells accumulate across
all time and cannot be narrowed to a reporting window -- a cell recording 412
services says nothing about *when*. Events can be windowed exactly but are
retained ~30 days. So a recent window is drawn from events and is precise; an
older one falls back to cells and is honestly labelled as all-time coverage
rather than quietly presented as the window's own geography.

**Maps are SVG, not tiles.** These pages are printed. A raster basemap prints
half-loaded or blurry and needs a Mapbox token on a page that may be shared
publicly; boundary polygons we already hold print crisp at any size and need
neither. The output is a plain ``<svg>`` string with no script and no external
reference, so it survives both the browser and Save-as-PDF unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.microplans.core import iso as iso_codes
from connect_labs.pulse.normalize import COUNTRY_NAMES

# Enough cells to draw a faithful map without an unbounded spatial pass on a
# portfolio-wide scope. Ordered by volume, so what gets dropped is always the
# thinnest tail.
MAX_POINTS = 6000
# A report naming fifty districts is not communicating; it is listing.
MAX_AREAS = 24

# Simplification tolerance in degrees, by how much of the world is in frame.
# A national outline can lose far more detail than a single district before it
# stops being recognisable, and the SVG is ~10x smaller for it.
_SIMPLIFY_WIDE = 0.02
_SIMPLIFY_CLOSE = 0.002


@dataclass
class Place:
    """One admin unit the work touched."""

    name: str
    level: int
    services: int = 0
    boundary_id: str = ""


@dataclass
class Geography:
    """Everything the report's two map panels and its place names need."""

    country_names: list[str] = field(default_factory=list)
    regions: list[Place] = field(default_factory=list)  # ADM1 — states/provinces
    districts: list[Place] = field(default_factory=list)  # ADM2 — LGAs/districts
    locator_svg: str = ""
    detail_svg: str = ""
    density_svg: str = ""
    point_count: int = 0
    # "events" (precise, inside retention) or "grid" (all-time accumulation).
    source: str = ""
    # True when the map is drawn from grid cells and therefore shows coverage
    # accumulated outside the report's own window. The template says so out
    # loud rather than letting the reader assume otherwise.
    is_all_time: bool = False
    # Set when boundaries simply are not loaded for this country. The report
    # still renders -- it just falls back to plotting points with no outline,
    # which is better than an empty panel that looks like a bug.
    missing_boundaries: bool = False


def _points_from_events(events) -> list[tuple[float, float, int]]:
    rows = (
        events.exclude(lat=None)
        .exclude(lon=None)
        .filter(status="approved")
        .values_list("lon", "lat")[: MAX_POINTS * 4]
    )
    tally: dict[tuple[float, float], int] = {}
    for lon, lat in rows:
        # Quantise to the same ~1 km cell the grid uses, so the two sources
        # produce comparably dense maps instead of one looking like noise.
        key = (round(lon, 2), round(lat, 2))
        tally[key] = tally.get(key, 0) + 1
    out = [(lon, lat, n) for (lon, lat), n in tally.items()]
    out.sort(key=lambda r: -r[2])
    return out[:MAX_POINTS]


def _points_from_grid(cells) -> list[tuple[float, float, int]]:
    rows = cells.order_by("-n").values_list("lon_q", "lat_q", "n")[:MAX_POINTS]
    return [(lon_q / 100.0, lat_q / 100.0, n) for lon_q, lat_q, n in rows]


def resolve(sc, grid_cells, *, retention_days: int = 30) -> Geography:
    """Build the report's geography from whichever spine can honestly answer.

    ``sc`` is a ``_program_scope`` result; ``grid_cells`` the matching
    ``PulseGridCell`` queryset. Events win when the window falls inside
    retention because they can be filtered to it exactly.
    """
    from datetime import timedelta

    from django.utils import timezone

    geo = Geography()

    window_from = sc.get("window_from")
    cutoff = timezone.now() - timedelta(days=retention_days)
    use_events = window_from is not None and window_from >= cutoff
    points = _points_from_events(sc["events"]) if use_events else []
    if points:
        geo.source = "events"
    else:
        # Either the window predates retention, or it is inside it but the
        # events carry no GPS. Either way the accumulated cells are the only
        # remaining answer, and they are all-time.
        points = _points_from_grid(grid_cells)
        geo.source = "grid"
        geo.is_all_time = bool(points)

    geo.point_count = sum(n for _, _, n in points)
    if not points:
        return geo

    countries = sorted({c for c in sc["events"].exclude(country="").values_list("country", flat=True).distinct()})
    if not countries:
        countries = sorted({c for c in sc["works"].exclude(country="").values_list("country", flat=True).distinct()})
    # Pulse's own table first: it says "DR Congo" where the ISO list says
    # "Congo, The Democratic Republic of the", and the report is prose.
    geo.country_names = [COUNTRY_NAMES.get(c) or iso_codes.country_name(c) or c for c in countries]

    iso3 = [iso_codes.to_alpha3(c) or c for c in countries]
    _attach_places(geo, points, iso3)
    _attach_maps(geo, points, iso3)
    return geo


def _prepared_hits(boundaries, points) -> dict[str, int]:
    """Tally point volume per boundary using prepared geometries.

    One spatial query fetches the candidates; the containment test itself runs
    in GEOS in-process. Asking the database per point instead would be tens of
    thousands of round trips for a map nobody would wait for.
    """
    from django.contrib.gis.geos import Point

    # Built once and reused across every boundary: constructing them inside the
    # inner loop turns an O(boundaries x points) test into O(boundaries x
    # points) *object allocations*, which dominates the actual containment work.
    geos_points = [(Point(lon, lat, srid=4326), n) for lon, lat, n in points]

    tally: dict[str, int] = {}
    for b in boundaries:
        prepared = b.geometry.prepared
        total = sum(n for pt, n in geos_points if prepared.contains(pt))
        if total:
            tally[b.boundary_id] = total
    return tally


def _touched(iso3: list[str], level: int, points) -> list[AdminBoundary]:
    """The admin units at ``level`` that any of the points fall inside."""
    from django.contrib.gis.geos import MultiPoint, Point

    if not points:
        return []
    mp = MultiPoint([Point(lon, lat, srid=4326) for lon, lat, _ in points], srid=4326)
    return list(AdminBoundary.objects.filter(iso_code__in=iso3, admin_level=level, geometry__intersects=mp)[:200])


def _attach_places(geo: Geography, points, iso3: list[str]) -> None:
    regions = _touched(iso3, 1, points)
    if not regions:
        geo.missing_boundaries = not AdminBoundary.objects.filter(iso_code__in=iso3).exists()
        return

    region_hits = _prepared_hits(regions, points)
    geo.regions = sorted(
        (
            Place(name=b.name, level=1, services=region_hits.get(b.boundary_id, 0), boundary_id=b.boundary_id)
            for b in regions
        ),
        key=lambda p: -p.services,
    )[:MAX_AREAS]

    districts = _touched(iso3, 2, points)
    if districts:
        district_hits = _prepared_hits(districts, points)
        geo.districts = sorted(
            (
                Place(name=b.name, level=2, services=district_hits.get(b.boundary_id, 0), boundary_id=b.boundary_id)
                for b in districts
            ),
            key=lambda p: -p.services,
        )[:MAX_AREAS]


def _attach_maps(geo: Geography, points, iso3: list[str]) -> None:
    """Three panels: a national locator, a district detail, and GPS density."""
    served_regions = {p.boundary_id for p in geo.regions}
    served_districts = {p.boundary_id for p in geo.districts}

    countries = list(AdminBoundary.objects.filter(iso_code__in=iso3, admin_level=0)[:8])
    all_regions = list(AdminBoundary.objects.filter(iso_code__in=iso3, admin_level=1)[:400])

    if countries or all_regions:
        # The locator answers "where in the world is this" -- national outline,
        # with the states that saw work picked out.
        geo.locator_svg = _svg(
            [(b, False) for b in countries] + [(b, b.boundary_id in served_regions) for b in all_regions],
            width=300,
            height=240,
            simplify=_SIMPLIFY_WIDE,
        )

    # The detail map zooms to the served states and draws their districts, so
    # the named LGAs in the copy have something to point at.
    focus = [b for b in all_regions if b.boundary_id in served_regions]
    if focus:
        siblings = _districts_of(focus, iso3)
        geo.detail_svg = _svg(
            [(b, False) for b in focus] + [(b, b.boundary_id in served_districts) for b in siblings],
            width=380,
            height=320,
            simplify=_SIMPLIFY_CLOSE,
            frame=focus,
        )
        geo.density_svg = _svg(
            [(b, False) for b in focus],
            width=380,
            height=320,
            simplify=_SIMPLIFY_CLOSE,
            frame=focus,
            points=points,
        )
    elif points:
        geo.density_svg = _svg([], width=380, height=320, simplify=_SIMPLIFY_CLOSE, points=points)


def _districts_of(regions: list[AdminBoundary], iso3: list[str]) -> list[AdminBoundary]:
    """ADM2 children of the given regions, by parent key then by geometry.

    ``parent_boundary_id`` is populated by some sources and not others, so the
    spatial fallback is not defensive padding -- it is the only path for
    boundaries loaded from a source that never carried the parent key.
    """
    parents = [r.boundary_id for r in regions if r.boundary_id]
    if parents:
        rows = list(
            AdminBoundary.objects.filter(iso_code__in=iso3, admin_level=2, parent_boundary_id__in=parents)[:600]
        )
        if rows:
            return rows
    from django.contrib.gis.geos import MultiPolygon

    union = regions[0].geometry
    for r in regions[1:]:
        union = union.union(r.geometry)
    if not isinstance(union, MultiPolygon):
        union = MultiPolygon(union)
    return list(AdminBoundary.objects.filter(iso_code__in=iso3, admin_level=2, geometry__intersects=union)[:600])


def _svg(features, *, width: int, height: int, simplify: float, frame=None, points=None) -> str:
    """Project boundaries (and optionally points) into a standalone SVG.

    Equirectangular with a cosine correction on longitude, which is accurate
    enough at the scale of one country and needs no projection library. The
    frame is taken from ``frame`` when given so a detail map stays zoomed to its
    subject rather than to whatever stray district crossed the query.
    """
    import math

    geoms = [(b.geometry, hi) for b, hi in features]
    bbox_source = [b.geometry for b in frame] if frame else [g for g, _ in geoms]
    if not bbox_source and points:
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        min_x, min_y, max_x, max_y = min(lons), min(lats), max(lons), max(lats)
    elif bbox_source:
        xs0, ys0, xs1, ys1 = zip(*[g.extent for g in bbox_source])
        min_x, min_y, max_x, max_y = min(xs0), min(ys0), max(xs1), max(ys1)
    else:
        return ""

    # A single-point or single-district extent has zero span in one axis, which
    # would divide by zero below. Pad to a minimum footprint instead.
    if max_x - min_x < 1e-6:
        min_x, max_x = min_x - 0.05, max_x + 0.05
    if max_y - min_y < 1e-6:
        min_y, max_y = min_y - 0.05, max_y + 0.05

    pad = 0.04
    span_x, span_y = (max_x - min_x), (max_y - min_y)
    min_x, max_x = min_x - span_x * pad, max_x + span_x * pad
    min_y, max_y = min_y - span_y * pad, max_y + span_y * pad

    k = math.cos(math.radians((min_y + max_y) / 2)) or 1.0
    span_x, span_y = (max_x - min_x) * k, (max_y - min_y)
    scale = min(width / span_x, height / span_y)
    off_x = (width - span_x * scale) / 2
    off_y = (height - span_y * scale) / 2

    def project(lon: float, lat: float) -> tuple[float, float]:
        return (
            off_x + (lon - min_x) * k * scale,
            # SVG y grows downward; latitude grows upward.
            off_y + (max_y - lat) * scale,
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" height="auto" role="img" aria-label="Coverage map">'
    ]
    for geom, highlighted in geoms:
        simplified = geom.simplify(simplify, preserve_topology=True) or geom
        d = _path_data(simplified, project)
        if not d:
            continue
        cls = "svg-area svg-area--served" if highlighted else "svg-area"
        parts.append(f'<path class="{cls}" d="{d}"/>')

    if points:
        biggest = max(n for _, _, n in points) or 1
        for lon, lat, n in points:
            x, y = project(lon, lat)
            if not (0 <= x <= width and 0 <= y <= height):
                continue
            # Area-proportional so a cell with 100x the volume reads as 10x the
            # radius rather than swallowing the panel.
            r = 1.1 + 4.4 * math.sqrt(n / biggest)
            parts.append(f'<circle class="svg-dot" cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}"/>')

    parts.append("</svg>")
    return "".join(parts)


def _path_data(geom, project) -> str:
    """SVG path data for a (Multi)Polygon, holes included via the even-odd rule."""
    polygons = geom if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for poly in polygons:
        for ring in poly:
            coords = ring.coords if hasattr(ring, "coords") else ring
            if len(coords) < 3:
                continue
            pts = [project(lon, lat) for lon, lat in coords]
            head = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
            tail = "".join(f"L{x:.1f},{y:.1f}" for x, y in pts[1:])
            out.append(head + tail + "Z")
    return "".join(out)
