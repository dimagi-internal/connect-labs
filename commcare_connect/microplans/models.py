"""Durable Postgres cache for Overture building footprints.

Footprints are expensive to fetch (DuckDB over Overture's S3 Parquet) but
near-static, and we reuse the same wards across a study and across video
re-takes. They were previously cached in Redis, which is the wrong store for
multi-MB reference data: it shares memory with the Celery broker + sessions and
evicts under pressure, so "cached" wasn't durable. Here footprints live as
structured rows keyed by an area-geometry hash — fetched once per area and served
to both sampling and coverage at any confidence (the confidence filter is applied
at read time, so a ward isn't re-fetched per confidence threshold).
"""

from __future__ import annotations

from django.db import models


class FootprintArea(models.Model):
    """One row per fetched area (geometry + Overture release).

    Acts as the cache marker — its presence means the area's footprints are
    cached, which distinguishes "fetched, genuinely 0 buildings" from "never
    fetched". The hash is sha256(overture_release | area_wkt); confidence is NOT
    part of the key (we store all buildings + filter on read).
    """

    area_hash = models.CharField(max_length=64, unique=True)
    overture_release = models.CharField(max_length=32)
    n_buildings = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.area_hash[:12]} ({self.n_buildings} buildings)"


class FootprintBuilding(models.Model):
    """One building within a cached area.

    `lon/lat` are the centroid (the input shape the sampling / coverage pipelines
    consume — they only need a point + size). `geom_json` carries the actual
    Overture footprint polygon as GeoJSON-encoded coordinates for the review-page
    overlay; nullable so rows from the centroid-only era keep working.
    """

    area = models.ForeignKey(FootprintArea, on_delete=models.CASCADE, related_name="buildings")
    lon = models.FloatField()
    lat = models.FloatField()
    area_m2 = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)  # Google-source confidence; null for MS/OSM
    # GeoJSON coordinates of the actual polygon (e.g. [[[lon,lat],...]] for Polygon).
    # Stored as JSON to keep the model GIS-free; the review-page footprints overlay
    # reads this when present, otherwise falls back to a centroid marker.
    geom_json = models.JSONField(null=True, blank=True)
