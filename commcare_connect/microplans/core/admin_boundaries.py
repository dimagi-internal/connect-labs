"""Admin-boundary resolver: pick the best source per (country, level).

Two sources, in default preference order:

  * **labs** — ``labs.admin_boundaries.AdminBoundary`` (PostGIS), the curated
    per-country library aggregated from third-party providers (GeoPoDe, which
    itself sources WHO/HDX/GRID3; geoBoundaries; OSM — each row records its own
    ``source``). Where a country has these boundaries loaded (e.g. Nigeria's
    ~9,300 wards from GeoPoDe/WHO) it is *better than Overture's default*, so it
    wins wherever it has data for the requested level. Shown in the UI as
    "Enriched Boundaries" — the curated/corrected set (see ``SOURCE_LABELS``).
  * **overture** — Overture Maps' global ``divisions`` theme (via ``boundaries``),
    the universal default/fallback for every country.

The preference order is overridable **at the country level** (the granularity
the field actually needs): a per-country order in ``settings`` or passed to the
resolver. With no override, ``("labs", "overture")`` means "bespoke where we
have it, Overture everywhere else" — chosen per level via each source's
``covers()`` check, so a country with labs ADM1–2 but no ward layer still falls
back to Overture for ADM3 automatically.

Vocabulary differs by source, so the resolver speaks **canonical levels**:
1 = region/state, 2 = county/district/LGA, 3 = locality/ward. Overture subtypes
(region/county/locality) and labs numeric ``admin_level`` both map onto these.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from django.conf import settings

from commcare_connect.microplans.core import boundaries, iso

logger = logging.getLogger(__name__)

# Alias for methods that take a kwarg named ``iso`` (which would otherwise shadow
# the ``iso`` module inside the method body).
_iso = iso

LEVEL_REGION, LEVEL_COUNTY, LEVEL_LOCALITY = 1, 2, 3
LEVEL_LABELS = {
    LEVEL_REGION: "Region / State",
    LEVEL_COUNTY: "County / District / LGA",
    LEVEL_LOCALITY: "Locality / Ward",
}
_LEVEL_TO_OVERTURE = {LEVEL_REGION: "region", LEVEL_COUNTY: "county", LEVEL_LOCALITY: "locality"}
_OVERTURE_TO_LEVEL = {v: k for k, v in _LEVEL_TO_OVERTURE.items()}

DEFAULT_SOURCE_ORDER: tuple[str, ...] = ("labs", "overture")

# Friendly labels for the UI source picker. New sources just add an entry.
#
# The ``labs`` key is NOT a single data source — it's the curated AdminBoundary
# table, a UNION of per-row sources (GeoPoDe, geoBoundaries, OSM, GRID3, …) that
# someone vetted/corrected for a country, so we label it "Enriched Boundaries":
# the human-curated set that's *better than Overture's generic default* and wins
# wherever it has data. Each row's true origin is recorded in
# ``AdminBoundary.source``; see that model's docstring for the per-source
# provenance + licensing. (To surface per-row provenance in the picker later, swap
# this static label for one derived from the boundary's ``source`` + provider.)
SOURCE_LABELS: dict[str, str] = {
    "labs": "Enriched Boundaries",
    "overture": "Overture",
}


@dataclass(frozen=True)
class AdminArea:
    """One admin area, normalised across sources.

    ``ref`` carries the source-specific keys ``get_geometry`` needs; ``region``
    is the opaque token a source uses to narrow this area's children (Overture
    region code, or a labs ``boundary_id``).
    """

    name: str
    level: int
    source: str
    country: str  # alpha-3
    region: str = ""
    area_km2: float | None = None
    population: float | None = None
    # Per-source populations (e.g. {"worldpop_u5": 1234, "meta_u5": 1180, ...}) for
    # the microplan population-source picker. None when the boundary has no such bag.
    populations: dict | None = None
    ref: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "level": self.level,
            "level_label": LEVEL_LABELS.get(self.level, f"ADM{self.level}"),
            "source": self.source,
            "country": self.country,
            "region": self.region,
            "area_km2": self.area_km2,
            "population": self.population,
            "populations": self.populations,
            "ref": self.ref,
        }

    @classmethod
    def from_json(cls, d: dict) -> AdminArea:
        """Rebuild from a (client-supplied) dict, whitelisting fields."""
        ref = d.get("ref")
        pops = d.get("populations")
        return cls(
            name=str(d.get("name", "")),
            level=int(d.get("level", 0)),
            source=str(d.get("source", "")),
            country=str(d.get("country", "")),
            region=str(d.get("region", "")),
            area_km2=d.get("area_km2"),
            population=d.get("population"),
            populations=pops if isinstance(pops, dict) else None,
            ref=ref if isinstance(ref, dict) else {},
        )


@dataclass(frozen=True)
class BoundaryFeature:
    """One boundary *with geometry*, normalised across sources, for viewport rendering.

    Unlike ``AdminArea`` (a picker row, no geometry), this carries the (possibly
    simplified) GeoJSON geometry so the map layer can draw an outline. ``ref`` carries
    the source keys needed to later fetch the *full-resolution* geometry on select.
    """

    name: str
    level: int  # canonical 1/2/3
    source: str
    country: str  # alpha-3 (labs) / alpha-3 passthrough (overture)
    boundary_id: str
    geometry: dict  # GeoJSON geometry (WGS84)
    area_km2: float | None = None
    population: float | None = None
    name_local: str = ""
    parent_name: str = ""
    ref: dict = field(default_factory=dict)

    def to_feature(self) -> dict:
        """As a GeoJSON Feature for a FeatureCollection."""
        return {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": {
                "name": self.name,
                "name_local": self.name_local,
                "admin_level": self.level,
                "iso_code": self.country,
                "source": self.source,
                "boundary_id": self.boundary_id,
                "area_km2": self.area_km2,
                "population": self.population,
                "parent_name": self.parent_name,
                "ref": self.ref,
            },
        }


# Tolerance (in WGS84 degrees) used to simplify rendered outlines, by map zoom.
# Coarser when zoomed out; ~0 when zoomed in so smallest-wins hit-testing stays sharp.
def tolerance_for_zoom(zoom: float | None) -> float:
    if zoom is None:
        return 0.005
    if zoom >= 12:
        return 0.0
    if zoom >= 10:
        return 0.0005
    if zoom >= 8:
        return 0.002
    if zoom >= 6:
        return 0.01
    return 0.03


class BoundarySource:
    name = "base"

    def covers(self, country3: str, level: int) -> bool:
        raise NotImplementedError

    def list_in_bbox(
        self, bbox, *, iso: str | None = None, levels=None, tolerance: float = 0.0, limit: int = 1500
    ) -> list[BoundaryFeature]:
        """Boundaries (with geometry) intersecting ``bbox`` (a WGS84 GEOS polygon),
        across all available levels by default, largest-area first."""
        raise NotImplementedError

    def list_areas(
        self,
        country3: str,
        level: int,
        *,
        name_contains: str | None = None,
        parent: AdminArea | None = None,
        parent_geom=None,
        limit: int = 500,
    ) -> list[AdminArea]:
        raise NotImplementedError

    def get_geometry(self, area: AdminArea) -> dict | None:
        """Return the GeoJSON geometry (WGS84) for one area, using its ``ref`` keys."""
        raise NotImplementedError


class OvertureBoundarySource(BoundarySource):
    """Overture divisions — global default. Narrows children by region code."""

    name = "overture"

    def covers(self, country3: str, level: int) -> bool:
        return level in _LEVEL_TO_OVERTURE and iso.to_alpha2(country3) is not None

    def list_areas(self, country3, level, *, name_contains=None, parent=None, parent_geom=None, limit=500):
        a2 = iso.to_alpha2(country3)
        if not a2 or level not in _LEVEL_TO_OVERTURE:
            return []
        subtype = _LEVEL_TO_OVERTURE[level]
        # Same-source children narrow cheaply by the parent's Overture region code.
        region = parent.region if (parent is not None and parent.source == self.name) else ""
        rows = boundaries.list_admin_areas(
            a2, subtype=subtype, region=region or None, name_contains=name_contains, limit=limit
        )
        out = []
        for r in rows:
            r_region = r.get("region") or ""
            out.append(
                AdminArea(
                    name=r["name"],
                    level=_OVERTURE_TO_LEVEL.get(r.get("subtype"), level),
                    source=self.name,
                    country=country3,
                    region=r_region,
                    area_km2=r.get("area_km2"),
                    ref={"alpha2": a2, "subtype": r.get("subtype", subtype), "region": r_region},
                )
            )
        return out

    def get_geometry(self, area: AdminArea) -> dict | None:
        return boundaries.get_admin_area_geojson(
            area.ref.get("alpha2") or iso.to_alpha2(area.country),
            area.name,
            area.ref.get("subtype") or _LEVEL_TO_OVERTURE.get(area.level, "locality"),
            region=area.ref.get("region") or None,
        )

    def list_in_bbox(self, bbox, *, iso=None, levels=None, tolerance=0.0, limit=1500):
        # Overture's parquet is partitioned by country, so we need an iso to prune
        # the scan — without it a bbox query would read the whole global file.
        a2 = _iso.to_alpha2(iso) if iso else None
        if not a2:
            return []
        subtypes = [_LEVEL_TO_OVERTURE[lvl] for lvl in (levels or _LEVEL_TO_OVERTURE) if lvl in _LEVEL_TO_OVERTURE]
        rows = boundaries.list_admin_areas_in_bbox(
            a2, bbox.wkt, subtypes=subtypes, simplify=(tolerance or None), limit=limit
        )
        a3 = _iso.to_alpha3(iso) or iso
        out = []
        for r in rows:
            subtype = r.get("subtype")
            region = r.get("region") or ""
            out.append(
                BoundaryFeature(
                    name=r["name"],
                    level=_OVERTURE_TO_LEVEL.get(subtype, 0),
                    source=self.name,
                    country=a3,
                    boundary_id=f"{subtype}:{region}:{r['name']}",
                    geometry=r.get("geometry"),
                    area_km2=r.get("area_km2"),
                    ref={"alpha2": a2, "subtype": subtype, "region": region, "name": r["name"]},
                )
            )
        return out


class LabsAdminBoundarySource(BoundarySource):
    """labs.admin_boundaries (PostGIS) — bespoke per-country, spatial narrowing."""

    name = "labs"

    def _model(self):
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary

        return AdminBoundary

    def covers(self, country3: str, level: int) -> bool:
        a3 = iso.to_alpha3(country3) or country3
        return self._model().objects.filter(iso_code=a3, admin_level=level).exists()

    def list_areas(self, country3, level, *, name_contains=None, parent=None, parent_geom=None, limit=500):
        a3 = iso.to_alpha3(country3) or country3
        qs = self._model().objects.filter(iso_code=a3, admin_level=level)
        if name_contains:
            qs = qs.filter(name__icontains=name_contains)
        # Same-source children narrow by the indexed parent_boundary_id (exact, no
        # spatial work). Cross-source (parent from Overture) falls back to the
        # parent polygon via centroid-within.
        if parent is not None and parent.source == self.name and parent.ref.get("boundary_id"):
            qs = qs.filter(parent_boundary_id=parent.ref["boundary_id"])
        elif parent_geom is not None:
            from django.contrib.gis.db.models.functions import Centroid

            qs = qs.annotate(_centroid=Centroid("geometry")).filter(_centroid__within=parent_geom)
        rows = qs.order_by("name").values("name", "boundary_id", "population", "extra")[: int(limit)]
        return [
            AdminArea(
                name=r["name"],
                level=level,
                source=self.name,
                country=a3,
                region=r["boundary_id"],
                population=r.get("population"),
                populations=(r.get("extra") or {}).get("populations"),
                ref={"boundary_id": r["boundary_id"]},
            )
            for r in rows
        ]

    def _fetch(self, area: AdminArea):
        return self._model().objects.filter(boundary_id=area.ref.get("boundary_id")).first()

    def get_geometry(self, area: AdminArea) -> dict | None:
        obj = self._fetch(area)
        return json.loads(obj.geometry.geojson) if obj else None

    def list_in_bbox(self, bbox, *, iso=None, levels=None, tolerance=0.0, limit=1500):
        from django.contrib.gis.db.models.functions import Area, Transform

        qs = self._model().objects.filter(geometry__intersects=bbox)
        if iso:
            qs = qs.filter(iso_code=(_iso.to_alpha3(iso) or iso))
        if levels:
            qs = qs.filter(admin_level__in=[int(lvl) for lvl in levels])
        # Equal-area projection (EPSG:6933) so the area + largest-first ordering are
        # in real km², not square degrees. Ordering in SQL keeps the cap server-side.
        qs = qs.annotate(_area=Area(Transform("geometry", 6933))).order_by("-_area")
        out = []
        for obj in qs[: int(limit)]:
            geom = obj.geometry
            if tolerance:
                geom = geom.simplify(tolerance, preserve_topology=True)
            out.append(
                BoundaryFeature(
                    name=obj.name,
                    level=obj.admin_level,
                    source=self.name,
                    country=obj.iso_code,
                    boundary_id=obj.boundary_id,
                    geometry=json.loads(geom.geojson),
                    area_km2=round(obj._area.sq_km, 1) if obj._area is not None else None,
                    population=obj.population,
                    name_local=obj.name_local or "",
                    parent_name=_labs_parent_name(obj),
                    ref={"boundary_id": obj.boundary_id, "source": self.name},
                )
            )
        return out


def _labs_parent_name(obj) -> str:
    """Best-effort parent chain for the Inspect panel, from the denormalised
    ``extra.parent_names`` the loaders store (shape varies by source)."""
    names = (getattr(obj, "extra", None) or {}).get("parent_names")
    if isinstance(names, dict):
        return " › ".join(str(v) for v in names.values() if v)
    if isinstance(names, (list, tuple)):
        return " › ".join(str(v) for v in names if v)
    return ""


def _geos_from_geojson(geom: dict | None):
    """GeoJSON geometry -> WGS84 GEOSGeometry, or None."""
    if not geom:
        return None
    from django.contrib.gis.geos import GEOSGeometry

    g = GEOSGeometry(json.dumps(geom))
    if g.srid is None:
        g.srid = 4326
    return g


class BoundaryResolver:
    def __init__(self, sources=None, country_order: dict[str, tuple[str, ...]] | None = None):
        srcs = sources if sources is not None else [LabsAdminBoundarySource(), OvertureBoundarySource()]
        self._sources = {s.name: s for s in srcs}
        # settings override (per-deployment) layered under any explicit arg.
        configured = dict(getattr(settings, "MICROPLANS_BOUNDARY_SOURCE_ORDER", {}) or {})
        self._country_order = {**configured, **(country_order or {})}

    def _order_for(self, country3: str) -> tuple[str, ...]:
        a3 = iso.to_alpha3(country3) or (country3 or "").upper()
        order = self._country_order.get(a3, DEFAULT_SOURCE_ORDER)
        return tuple(order)

    def source_names(self) -> list[str]:
        """All registered source names (no country scoping). Public accessor so
        callers don't reach into the private ``_sources`` registry."""
        return list(self._sources)

    def sources_for(self, country3: str, level: int) -> list[str]:
        """Names of every source that has data for this (country, level), in
        preference order (the default is first)."""
        ordered = list(self._order_for(country3)) + [n for n in self._sources if n not in self._order_for(country3)]
        return [n for n in ordered if (s := self._sources.get(n)) and s.covers(country3, level)]

    def source_for(self, country3: str, level: int, prefer: str | None = None) -> BoundarySource:
        """The source to use. ``prefer`` (a user pick) wins if it covers the level;
        otherwise fall back to the country's preference order, then Overture."""
        if prefer and (s := self._sources.get(prefer)) and s.covers(country3, level):
            return s
        for name in self._order_for(country3):
            s = self._sources.get(name)
            if s and s.covers(country3, level):
                return s
        return self._sources.get("overture") or next(iter(self._sources.values()))

    def describe(self, country3: str) -> dict:
        """Per-level default source + the full pickable-source list, for the UI."""
        a3 = iso.to_alpha3(country3) or (country3 or "").upper()
        return {
            "country": a3,
            "name": iso.country_name(a3),
            "order": list(self._order_for(country3)),
            "source_labels": {n: SOURCE_LABELS.get(n, n) for n in self._sources},
            "levels": {
                level: {
                    "label": LEVEL_LABELS[level],
                    "default_source": self.source_for(a3, level).name,
                    "available_sources": self.sources_for(a3, level),
                }
                for level in (LEVEL_REGION, LEVEL_COUNTY, LEVEL_LOCALITY)
            },
        }

    def list_areas(
        self,
        country3: str,
        level: int,
        *,
        name_contains: str | None = None,
        parent: AdminArea | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> list[AdminArea]:
        limit = max(1, min(int(limit), 10000))  # bound user-supplied page size
        src = self.source_for(country3, level, prefer=source)
        # Cross-source narrowing (parent from a different source) needs the parent
        # polygon; same-source narrowing is handled inside the source (region code
        # for Overture, parent_boundary_id for labs).
        parent_geom = None
        if parent is not None and parent.source != src.name:
            parent_geom = _geos_from_geojson(self.geometry(parent))
        return src.list_areas(
            country3, level, name_contains=name_contains, parent=parent, parent_geom=parent_geom, limit=limit
        )

    def geometry(self, area: AdminArea) -> dict | None:
        source = self._sources.get(area.source) or self.source_for(area.country, area.level)
        return source.get_geometry(area)

    def _default_bbox_source(self, iso_code: str | None) -> str:
        """Source for a viewport query when the user hasn't picked one: the country's
        preferred source that has *any* data, else Overture.

        Cold-start (no iso yet): prefer **labs**. The boundary layer infers the
        country from the first boundaries it loads, but Overture needs an iso up
        front (parquet partition pruning) so it returns nothing without one — which
        strands that auto-detect in a chicken-and-egg (no iso → no boundaries → no
        iso). The labs source intersects by geometry alone, so it returns the
        curated boundaries under the viewport with no iso, the country detects, and
        the by-name search starts working. Fall back to Overture only when labs
        isn't configured (over a country with no labs data, both return nothing, so
        there's no regression)."""
        if iso_code:
            a3 = _iso.to_alpha3(iso_code) or iso_code
            for name in self._order_for(a3):
                src = self._sources.get(name)
                if src and any(src.covers(a3, lvl) for lvl in (LEVEL_REGION, LEVEL_COUNTY, LEVEL_LOCALITY)):
                    return name
            return "overture" if "overture" in self._sources else next(iter(self._sources))
        if "labs" in self._sources:
            return "labs"
        return "overture" if "overture" in self._sources else next(iter(self._sources))

    def bbox_source_name(self, source: str | None, iso: str | None) -> str:
        """The source name a viewport query will use: the picked one if known, else
        the country default. (Lets a view report the used source without re-querying.)"""
        return source if (source and source in self._sources) else self._default_bbox_source(iso)

    def boundaries_in_bbox(
        self, bbox, *, source: str | None = None, iso: str | None = None, levels=None, zoom=None, limit: int = 1500
    ) -> tuple[list[BoundaryFeature], bool]:
        """Boundaries intersecting ``bbox`` from a single source (the picked one, or the
        country default). Returns ``(features, truncated)`` — ``truncated`` is True when
        the intersect set exceeded ``limit`` (the FE prompts "zoom in")."""
        name = self.bbox_source_name(source, iso)
        src = self._sources[name]
        tolerance = tolerance_for_zoom(zoom)
        # Fetch one past the cap so we can detect (and report) truncation.
        feats = src.list_in_bbox(bbox, iso=iso, levels=levels, tolerance=tolerance, limit=int(limit) + 1)
        truncated = len(feats) > limit
        if truncated:
            logger.info("viewport boundaries truncated to %d (source=%s iso=%s)", limit, name, iso)
        return feats[:limit], truncated


def adjacent_boundaries(boundary_id: str, *, limit: int = 10) -> dict:
    """Same-level labs boundaries that share a border with ``boundary_id``.

    The candidate pool for the "compare surrounding boundaries" control finder:
    a control ward should be geographically proximate (same district, similar
    access, low spillover), so candidates are the wards that physically touch the
    reference at the SAME admin level. Uses the labs PostGIS table's spatial index
    (``geometry__intersects`` excluding self) — so this is Enriched-Boundaries
    only; an Overture-sourced selection has no spatial table to intersect against.

    Returns ``{"supported": bool, "reference": {...}|None, "candidates":
    [{boundary_id, name, population, geometry}], "truncated": bool}`` — geometry is
    full-resolution GeoJSON (the candidates feed straight into footprint fetches).
    """
    from commcare_connect.labs.admin_boundaries.models import AdminBoundary

    ref = AdminBoundary.objects.filter(boundary_id=boundary_id).first() if boundary_id else None
    if ref is None or ref.geometry is None:
        return {"supported": False, "reference": None, "candidates": [], "truncated": False}

    def _row(b):
        return {
            "boundary_id": b.boundary_id,
            "name": b.name,
            "population": int(b.population) if b.population is not None else None,
            "geometry": json.loads(b.geometry.geojson),
        }

    qs = (
        AdminBoundary.objects.filter(
            iso_code=ref.iso_code, admin_level=ref.admin_level, geometry__intersects=ref.geometry
        )
        .exclude(boundary_id=ref.boundary_id)
        .order_by("name")
    )
    rows = list(qs[: int(limit) + 1])
    truncated = len(rows) > limit
    return {
        "supported": True,
        "reference": _row(ref),
        "candidates": [_row(b) for b in rows[:limit]],
        "truncated": truncated,
    }


def get_resolver() -> BoundaryResolver:
    return BoundaryResolver()
