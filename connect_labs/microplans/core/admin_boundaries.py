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

from connect_labs.microplans.core import boundaries, iso

logger = logging.getLogger(__name__)

# Alias for methods that take a kwarg named ``iso`` (which would otherwise shadow
# the ``iso`` module inside the method body).
_iso = iso

LEVEL_REGION, LEVEL_COUNTY, LEVEL_LOCALITY = 1, 2, 3
# Country-agnostic fallback labels (shown when we don't have a country-specific
# vocabulary). The canonical LEVEL numbers never change — this is display only.
LEVEL_LABELS = {
    LEVEL_REGION: "Region / State",
    LEVEL_COUNTY: "County / District / LGA",
    LEVEL_LOCALITY: "Locality / Ward",
}
# Per-country level vocabularies (alpha-3). When a country is known we show its own
# word for each tier instead of the slash-list; matching/resolving/export are all
# keyed on the numeric level, so this is purely cosmetic. Add a country by adding a
# row here. Unknown country → LEVEL_LABELS fallback.
COUNTRY_LEVEL_LABELS: dict[str, dict[int, str]] = {
    "NGA": {LEVEL_REGION: "State", LEVEL_COUNTY: "LGA", LEVEL_LOCALITY: "Ward"},
    "KEN": {LEVEL_REGION: "County", LEVEL_COUNTY: "Sub-county", LEVEL_LOCALITY: "Ward"},
    "IND": {LEVEL_REGION: "State", LEVEL_COUNTY: "District", LEVEL_LOCALITY: "Locality"},
}


def level_label(level: int, country: str | None = None) -> str:
    """Human label for a canonical level, country-specific when we know the country
    (e.g. NGA level 3 -> "Ward"), else the generic slash-list. Display only."""
    a3 = (iso.to_alpha3(country) or (country or "").upper()) if country else ""
    by_country = COUNTRY_LEVEL_LABELS.get(a3)
    if by_country and level in by_country:
        return by_country[level]
    return LEVEL_LABELS.get(level, f"ADM{level}")


_LEVEL_TO_OVERTURE = {LEVEL_REGION: "region", LEVEL_COUNTY: "county", LEVEL_LOCALITY: "locality"}
_OVERTURE_TO_LEVEL = {v: k for k, v in _LEVEL_TO_OVERTURE.items()}

DEFAULT_SOURCE_ORDER: tuple[str, ...] = ("labs", "geopode", "grid3", "geoboundaries", "overture")

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
    # Individual underlying loaders, selectable on their own (not just merged).
    "geoboundaries": "geoBoundaries (admin)",
    "geopode": "GeoPoDe / WHO (wards + pop)",
    "grid3": "GRID3 (wards)",
}

# Total-population keys in a boundary's ``extra.populations`` bag, in fallback
# preference order. These are whole-population zonal-stat estimates (WorldPop /
# Meta / GRID3 rasters) plus GeoPoDe's own scalar — comparable in scale to the
# scalar ``population`` field (GeoPoDe ``population_1``).
#
# ``geopode_total`` is the relabelled GeoPoDe scalar (was ``geopode_u5`` — the data
# proved population_1 is a whole-area TOTAL, e.g. Zankan 22,926 ≈ worldpop_total
# ~28,543, NOT the ~5k under-5; see ``load_ward_populations``). The legacy
# ``geopode_u5`` key is kept here as a transitional alias so deployments whose bags
# predate the loader re-run still surface the GeoPoDe total instead of an honest
# blank. Both resolve to the same number; the loader writes ``geopode_total`` going
# forward.
#
# Deliberately EXCLUDES the genuine under-5 keys (``worldpop_u5``, ``meta_u5``):
# substituting a ~5k u5 figure into a column that otherwise shows ~25k totals would
# silently mislabel u5 as total population.
_TOTAL_POPULATION_KEYS: tuple[str, ...] = (
    "worldpop_total",
    "meta_total",
    "grid3_v3_total",
    "geopode_total",
    "geopode_u5",  # transitional alias for geopode_total (pre-reload bags)
)

# Provider-preference order for resolving same-ward duplicates in the merged
# ("Enriched Boundaries" / labs) view. NGA wards are loaded from BOTH ``geopode``
# and ``grid3`` as separate, overlapping AdminBoundary rows for the same ward, so a
# geometry-intersect or name search returns two rows per ward. We keep ONE,
# preferring ``geopode`` because it carries the richest enrichment: its
# ``extra.populations`` bag is a SUPERSET (it accumulates worldpop_total /
# meta_total / grid3_v3_total plus its own geopode_total), it has the widest ward
# coverage for the enriched countries, and its scalar ``population`` is populated
# where the grid3 twin's is blank. Anything not listed sorts LAST (fallback only),
# so a ward that exists in a single provider is always kept.
LABS_PROVIDER_PREFERENCE: tuple[str, ...] = ("geopode", "grid3", "geoboundaries", "osm")


def _provider_rank(source: str | None) -> int:
    """Lower is more preferred. Unlisted providers sort last (kept only as fallback)."""
    try:
        return LABS_PROVIDER_PREFERENCE.index(source or "")
    except ValueError:
        return len(LABS_PROVIDER_PREFERENCE)


def _normalize_ward_name(name: str | None) -> str:
    """Key for same-ward duplicate detection: case/whitespace-insensitive name.

    Two rows are treated as the same ward (a provider duplicate) only when their
    normalized names are equal AND they're already among same-level candidates that
    co-locate (the callers pre-filter to same admin_level + intersecting/same-country
    rows). Equal-name is the conservative gate — genuinely different wards have
    different names, so this never collapses distinct wards together."""
    return " ".join((name or "").strip().casefold().split())


def _dedupe_by_provider_preference(rows, *, name_of, source_of):
    """Collapse same-ward provider duplicates, keeping the most-preferred provider.

    ``rows`` is any iterable; ``name_of(row)`` / ``source_of(row)`` extract the ward
    name and provider source. Rows are grouped by normalized name; within a group the
    row whose source ranks best in ``LABS_PROVIDER_PREFERENCE`` wins (geopode over
    grid3, etc.). A ward present under only one provider is kept unchanged (the group
    has one row). Deterministic: input order is preserved for the surviving rows, and
    ties within a name-group are broken by first-seen order (stable)."""
    best_by_name: dict[str, object] = {}
    order: list[str] = []
    for row in rows:
        key = _normalize_ward_name(name_of(row))
        if key not in best_by_name:
            best_by_name[key] = row
            order.append(key)
            continue
        if _provider_rank(source_of(row)) < _provider_rank(source_of(best_by_name[key])):
            best_by_name[key] = row
    return [best_by_name[k] for k in order]


def resolve_population(population, populations: dict | None):
    """The scalar population to display for a boundary, with a documented fallback.

    Rule (kept consistent so a single column never mixes total- and under-5-scale
    figures):

      1. Use the boundary's own ``population`` (GeoPoDe ``population_1`` — a
         whole-area estimate) when it is present.
      2. Otherwise fall back to the first available TOTAL-population source in the
         ``populations`` bag, in ``_TOTAL_POPULATION_KEYS`` order
         (worldpop_total → meta_total → grid3_v3_total).
      3. Otherwise return ``None`` (the UI renders an honest blank, not a fabricated
         number; under-5 figures are never promoted to a total here).

    Returns a ``float`` (or ``None``). The caller decides on rounding/int coercion.
    """
    if population is not None:
        return population
    if isinstance(populations, dict):
        for key in _TOTAL_POPULATION_KEYS:
            value = populations.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
    return None


def _default_boundary_sources():
    """Enriched (merged) first, then each underlying loader as its own source, then
    Overture. A specific loader is useful when you want one source, not the blend."""
    return [
        LabsAdminBoundarySource(),  # "labs" = Enriched (all sources merged)
        LabsAdminBoundarySource(db_source="geoboundaries", name="geoboundaries"),
        LabsAdminBoundarySource(db_source="geopode", name="geopode"),
        LabsAdminBoundarySource(db_source="grid3", name="grid3"),
        OvertureBoundarySource(),
    ]


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
            "level_label": level_label(self.level, self.country),
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
    # Per-source population bag (worldpop_u5/meta_total/…), so a boundary CLICKED on
    # the map carries the same numbers the search dropdown does — the setup planning
    # table reads this to fill the Total/U5 columns.
    populations: dict | None = None
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
                "populations": self.populations,
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
    """labs.admin_boundaries (PostGIS) — bespoke per-country, spatial narrowing.

    With ``db_source=None`` this is the merged "Enriched Boundaries" view (every
    loaded source). Pass a specific ``db_source`` ("geoboundaries"/"geopode"/"grid3")
    + ``name`` to expose just that underlying loader as its own selectable source."""

    name = "labs"

    def __init__(self, db_source: str | None = None, name: str | None = None):
        self.db_source = db_source
        if name:
            self.name = name

    def _model(self):
        from connect_labs.labs.admin_boundaries.models import AdminBoundary

        return AdminBoundary

    def _scoped(self, qs):
        return qs.filter(source=self.db_source) if self.db_source else qs

    def covers(self, country3: str, level: int) -> bool:
        a3 = iso.to_alpha3(country3) or country3
        return self._scoped(self._model().objects.filter(iso_code=a3, admin_level=level)).exists()

    def list_areas(self, country3, level, *, name_contains=None, parent=None, parent_geom=None, limit=500):
        a3 = iso.to_alpha3(country3) or country3
        qs = self._scoped(self._model().objects.filter(iso_code=a3, admin_level=level))
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
        # In the merged ("Enriched") view a ward can appear under multiple providers
        # (NGA wards are loaded from both geopode AND grid3). Over-fetch, collapse
        # same-name provider duplicates preferring geopode (richest enrichment — see
        # LABS_PROVIDER_PREFERENCE), THEN apply the page limit so dedupe doesn't drop
        # real candidates. A source-scoped picker (db_source set) has no cross-source
        # twins, so it skips the dedupe and keeps its single-provider rows untouched.
        ordered = qs.order_by("name").values("name", "boundary_id", "population", "extra", "source")
        if self.db_source is None:
            rows = _dedupe_by_provider_preference(
                list(ordered),
                name_of=lambda r: r["name"],
                source_of=lambda r: r.get("source"),
            )[: int(limit)]
        else:
            rows = list(ordered[: int(limit)])
        out = []
        for r in rows:
            pops = (r.get("extra") or {}).get("populations")
            out.append(
                AdminArea(
                    name=r["name"],
                    level=level,
                    source=self.name,
                    country=a3,
                    region=r["boundary_id"],
                    # Display population: scalar population_1, else a total from the bag.
                    population=resolve_population(r.get("population"), pops),
                    populations=pops,
                    ref={"boundary_id": r["boundary_id"]},
                )
            )
        return out

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
                    population=resolve_population(obj.population, (obj.extra or {}).get("populations")),
                    populations=(obj.extra or {}).get("populations"),
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
        srcs = sources if sources is not None else _default_boundary_sources()
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
                    "label": level_label(level, a3),
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
    from connect_labs.labs.admin_boundaries.models import AdminBoundary

    ref = AdminBoundary.objects.filter(boundary_id=boundary_id).first() if boundary_id else None
    if ref is None or ref.geometry is None:
        return {"supported": False, "reference": None, "candidates": [], "truncated": False}

    def _row(b):
        # Fall back to a total-population source in the populations bag when the
        # scalar population (GeoPoDe population_1) is absent — otherwise wards that
        # lack population_1 show "—" in the compare table even though the bag has a
        # usable total. See resolve_population for the exact rule.
        pop = resolve_population(b.population, (b.extra or {}).get("populations"))
        return {
            "boundary_id": b.boundary_id,
            "name": b.name,
            "population": int(pop) if pop is not None else None,
            "geometry": json.loads(b.geometry.geojson),
        }

    qs = (
        AdminBoundary.objects.filter(
            iso_code=ref.iso_code, admin_level=ref.admin_level, geometry__intersects=ref.geometry
        )
        .exclude(boundary_id=ref.boundary_id)
        .order_by("name")
    )
    # The intersect set is over the merged Enriched table, where a neighbouring ward
    # can appear twice (geopode + grid3 rows that overlap). Collapse same-ward provider
    # duplicates to ONE row, preferring geopode (its scalar population is populated
    # where the grid3 twin's is blank — the "Sopp shows blank pop" bug; see
    # LABS_PROVIDER_PREFERENCE). Dedupe over the FULL set, then apply the limit, so a
    # ward isn't dropped just because its twin happened to sort first. A ward present
    # in only one provider is kept (fallback).
    deduped = _dedupe_by_provider_preference(list(qs), name_of=lambda b: b.name, source_of=lambda b: b.source)
    truncated = len(deduped) > limit
    return {
        "supported": True,
        "reference": _row(ref),
        "candidates": [_row(b) for b in deduped[:limit]],
        "truncated": truncated,
    }


def get_resolver() -> BoundaryResolver:
    return BoundaryResolver()
