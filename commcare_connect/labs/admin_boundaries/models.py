"""
Admin Boundary models for geospatial data.

Each row records its provenance in ``AdminBoundary.source``. Data can be sourced
from:
- GeoPoDe ("Geographic, Population & Demographic Data", https://geopode.world/) -
  a humanitarian geospatial repository operated by Novel-T (WHO-affiliated). It is
  an AGGREGATOR: per country its boundaries originate upstream from WHO, HDX/OCHA,
  or GRID3 (recorded per-country in ``fixtures/geopode_sources.json``). This is the
  ward-level (ADM3) source for our loaded African countries - e.g. Nigeria's ~9,300
  wards (upstream: WHO). LICENSE: no standard open license; GeoPoDe's terms state
  its datasets are "available to non-profit or humanitarian applications" and are
  "operational" in nature and "do not constitute authoritative, government-sanctioned
  reference data." Our use is non-profit/humanitarian; attribute GeoPoDe (Novel-T)
  + the upstream provider. License/redistribution specifics: info@geopode.world.
- geoBoundaries (https://www.geoboundaries.org/) - CC BY 4.0
- OpenStreetMap (https://www.openstreetmap.org/) - ODbL
- GRID3 (https://grid3.org/) - varies by country
- HDX/OCHA COD (https://data.humdata.org/) - varies by dataset

In the microplans UI these are shown collectively as "Other 3rd Party Sources"
(vs Overture); see ``microplans.core.admin_boundaries.SOURCE_LABELS``.
"""

from __future__ import annotations

from typing import Any

from django.contrib.gis.db import models as gis_models
from django.db import models


class AdminBoundarySourceConfig:
    """Configuration for a single data source for a country.

    Represents the availability and configuration of a data source
    (geoBoundaries, OSM, GRID3, or HDX) for loading admin boundaries.
    """

    def __init__(self, source_id: str, config: dict[str, Any]):
        self.source_id = source_id
        self.source_type = config.get("type", "api")  # "api" or "url"
        self.max_level = config.get("max_level", 2)
        self.levels = config.get("levels", {})  # For URL-based: level -> {url, name_field, id_field}

    def get_level_config(self, level: int) -> dict[str, Any] | None:
        """Get configuration for a specific admin level (URL-based sources only)."""
        return self.levels.get(str(level))

    def has_level(self, level: int) -> bool:
        """Check if this source has data for the given level."""
        if self.source_type == "api":
            return level <= self.max_level
        else:
            return str(level) in self.levels

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "type": self.source_type,
            "max_level": self.max_level,
        }
        if self.levels:
            result["levels"] = self.levels
        return result


class AdminBoundaryStaticLoadRecord:
    """Static configuration for loading admin boundaries for a country.

    Follows the LocalLabsRecord pattern so it can be stored as a LabsRecord
    in the future. For now, loaded from JSON fixture.

    When stored as a LabsRecord:
        experiment: "admin_boundaries"
        type: "static_load_config"
    """

    def __init__(self, data: dict[str, Any]):
        """Initialize from fixture data or API response.

        Args:
            data: Dictionary with iso_code, name, sources, and recommended fields
        """
        self.iso_code: str = data["iso_code"]
        self.name: str = data["name"]
        self._sources_raw: dict[str, Any] = data.get("sources", {})
        self.recommended: dict[str, str] = data.get("recommended", {})

        # Parse source configs
        self._sources: dict[str, AdminBoundarySourceConfig] = {}
        for source_id, config in self._sources_raw.items():
            self._sources[source_id] = AdminBoundarySourceConfig(source_id, config)

    def get_available_sources(self) -> list[str]:
        """Return list of available source IDs for this country."""
        return list(self._sources.keys())

    def get_source_config(self, source: str) -> AdminBoundarySourceConfig | None:
        """Get config for a specific source."""
        return self._sources.get(source)

    def get_recommended_source(self, level: int) -> str | None:
        """Get recommended source for an admin level."""
        return self.recommended.get(str(level))

    def get_max_level(self, source: str) -> int:
        """Get max admin level available from a source."""
        config = self._sources.get(source)
        return config.max_level if config else 0

    def get_available_levels(self, source: str) -> list[int]:
        """Get list of available admin levels for a source."""
        config = self._sources.get(source)
        if not config:
            return []
        if config.source_type == "api":
            return list(range(config.max_level + 1))
        else:
            return [int(level) for level in config.levels.keys()]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary (for JSON fixture format)."""
        return {
            "iso_code": self.iso_code,
            "name": self.name,
            "sources": self._sources_raw,
            "recommended": self.recommended,
        }

    def to_labs_record_dict(self) -> dict[str, Any]:
        """Serialize to LabsRecord format for future API storage."""
        return {
            "experiment": "admin_boundaries",
            "type": "static_load_config",
            "data": self.to_dict(),
            "public": True,
        }

    def __str__(self) -> str:
        return f"{self.iso_code} ({self.name})"

    def __repr__(self) -> str:
        return f"<AdminBoundaryStaticLoadRecord: {self}>"


class AdminBoundary(models.Model):
    """Administrative boundary polygon for spatial analysis.

    Stores admin boundaries (countries, states, districts, etc.) for spatial
    analysis and map visualization. Data can be sourced from geoBoundaries
    or OpenStreetMap.

    Supports spatial queries like point-in-polygon to determine which admin
    region a GPS coordinate falls within.

    Example usage:
        from django.contrib.gis.geos import Point
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary

        # Find which county contains Nairobi
        point = Point(36.8219, -1.2921, srid=4326)
        region = AdminBoundary.objects.filter(
            geometry__contains=point,
            admin_level=1
        ).first()
        print(region.name)  # "Nairobi"
    """

    class Source(models.TextChoices):
        GEOBOUNDARIES = "geoboundaries", "geoBoundaries"
        OSM = "osm", "OpenStreetMap"
        GRID3 = "grid3", "GRID3"
        HDX = "hdx", "HDX (OCHA COD)"
        GEOPODE = "geopode", "GeoPoDe"

    iso_code = models.CharField(max_length=3, db_index=True, help_text="ISO 3166-1 alpha-3 country code")
    admin_level = models.PositiveSmallIntegerField(
        db_index=True, help_text="Admin level (0=country, 1=state/province, 2=district, etc.)"
    )
    name = models.CharField(max_length=255, help_text="Name of the administrative unit")
    name_local = models.CharField(max_length=255, blank=True, help_text="Local/native name if available")
    boundary_id = models.CharField(max_length=100, help_text="ID from source (shapeID/OSM ID); unique per source")
    geometry = gis_models.MultiPolygonField(srid=4326, help_text="Boundary polygon in WGS84")

    # Source tracking
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.GEOBOUNDARIES,
        db_index=True,
        help_text="Data source (geoBoundaries, OpenStreetMap, GRID3, or HDX)",
    )
    source_url = models.URLField(blank=True, help_text="URL where boundary data was downloaded from")
    downloaded_at = models.DateTimeField(auto_now_add=True, help_text="When this boundary was downloaded")

    # Population for the unit, when the source provides it (e.g. GeoPoDe population_1).
    population = models.FloatField(null=True, blank=True, help_text="Population of the unit, if known")
    # Immediate parent's boundary_id (e.g. an LGA points at its State). Lets callers
    # walk the hierarchy and narrow children by an indexed key instead of a spatial join.
    parent_boundary_id = models.CharField(
        max_length=100, blank=True, db_index=True, help_text="boundary_id of the immediate parent unit"
    )
    # Provider/provenance + denormalized parent codes/names that don't have their own
    # columns (e.g. {"provider": "WHO", "source_date": "...", "global_id": "...",
    # "parent_codes": {...}, "parent_names": {...}}). Source-specific, kept verbatim.
    extra = models.JSONField(default=dict, blank=True, help_text="Provider + denormalized hierarchy metadata")

    class Meta:
        db_table = "labs_admin_boundary"
        indexes = [
            models.Index(fields=["iso_code", "admin_level"]),
            models.Index(fields=["source", "iso_code"]),
        ]
        constraints = [
            # boundary_id need only be unique per source (matches migration 0002).
            models.UniqueConstraint(
                fields=["source", "boundary_id"],
                name="labs_admin_boundary_source_boundary_id_uniq",
            ),
        ]
        verbose_name = "Admin boundary"
        verbose_name_plural = "Admin boundaries"
        ordering = ["iso_code", "admin_level", "name"]

    def __str__(self):
        return f"{self.iso_code} ADM{self.admin_level}: {self.name}"

    @classmethod
    def get_countries_summary(cls):
        """Get summary of loaded countries with boundary counts per level and source."""
        from django.db.models import Count

        return (
            cls.objects.values("iso_code", "source")
            .annotate(
                total=Count("id"),
                adm0=Count("id", filter=models.Q(admin_level=0)),
                adm1=Count("id", filter=models.Q(admin_level=1)),
                adm2=Count("id", filter=models.Q(admin_level=2)),
                adm3=Count("id", filter=models.Q(admin_level=3)),
                adm4=Count("id", filter=models.Q(admin_level=4)),
            )
            .order_by("iso_code", "source")
        )
