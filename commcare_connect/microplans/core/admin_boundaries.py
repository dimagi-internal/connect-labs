"""Admin-boundary resolver: pick the best source per (country, level).

Two sources, in default preference order:

  * **labs** — ``labs.admin_boundaries.AdminBoundary`` (PostGIS), the curated
    per-country library. Where a country has bespoke boundaries loaded
    (e.g. Nigeria GRID3 wards) it is *better than Overture's default*, so it
    wins wherever it has data for the requested level.
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
SOURCE_LABELS: dict[str, str] = {
    "labs": "Local data (bespoke)",
    "overture": "Overture (global)",
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
            "ref": self.ref,
        }

    @classmethod
    def from_json(cls, d: dict) -> AdminArea:
        """Rebuild from a (client-supplied) dict, whitelisting fields."""
        ref = d.get("ref")
        return cls(
            name=str(d.get("name", "")),
            level=int(d.get("level", 0)),
            source=str(d.get("source", "")),
            country=str(d.get("country", "")),
            region=str(d.get("region", "")),
            area_km2=d.get("area_km2"),
            population=d.get("population"),
            ref=ref if isinstance(ref, dict) else {},
        )


class BoundarySource:
    name = "base"

    def covers(self, country3: str, level: int) -> bool:
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
        rows = qs.order_by("name").values("name", "boundary_id", "population")[: int(limit)]
        return [
            AdminArea(
                name=r["name"],
                level=level,
                source=self.name,
                country=a3,
                region=r["boundary_id"],
                population=r.get("population"),
                ref={"boundary_id": r["boundary_id"]},
            )
            for r in rows
        ]

    def _fetch(self, area: AdminArea):
        return self._model().objects.filter(boundary_id=area.ref.get("boundary_id")).first()

    def get_geometry(self, area: AdminArea) -> dict | None:
        obj = self._fetch(area)
        return json.loads(obj.geometry.geojson) if obj else None


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


def get_resolver() -> BoundaryResolver:
    return BoundaryResolver()
