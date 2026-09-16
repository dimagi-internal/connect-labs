"""Models for the CHC mop-up feature.

This app is a sibling of `connect_labs.microplans`, not a plan-type bolted onto
it — Phase 1/2 (scope selection, ward-scoped visit pulling, cluster detection,
thresholds) are genuinely new and touch none of microplans' own files; at the
Phase 2->3 handoff this app builds the same `areas`/`area_targets` inputs
microplans' `core.data_access.ProgramPlanDataAccess.create_plan()` already
expects and calls it directly, then redirects into the existing, unmodified
microplans review page.

Run/session state is still a `LocalLabsRecord` proxy
(`connect_labs.mopup.core.models.MopupRunRecord`), matching the labs
convention (see `microplans.core.models` for the identical pattern) — these
ARE the first real Django models this app has needed: a genuine local
Postgres cache, the way `microplans.models.FootprintArea`/`FootprintBuilding`
are one (see `connect_labs.mopup.core.open_buildings` for what it caches and
why — a ward's own building set, not raw upstream shard files).
"""

from __future__ import annotations

from django.db import models


class OpenBuildingsArea(models.Model):
    """One row per fetched ward boundary (Google Open Buildings, fetched
    directly from Google's own public dataset — NOT Overture's conflated
    "Google Open Buildings" source, see `core.open_buildings`'s module
    docstring for why the two differ). Acts as the cache marker, same
    pattern as `microplans.models.FootprintArea`: presence means this ward's
    buildings are cached, distinguishing "fetched, genuinely 0 buildings"
    from "never fetched".

    Caches the ward-FILTERED result, not raw upstream S2 shards — a raw
    shard is ~1M rows / 100MB+ and spans many wards; a ward's own buildings
    are usually a few hundred to a few thousand rows.
    """

    area_hash = models.CharField(max_length=64, unique=True)
    n_buildings = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.area_hash[:12]} ({self.n_buildings} buildings)"


class OpenBuildingsBuilding(models.Model):
    """One building centroid within a cached `OpenBuildingsArea`. Points
    only, no footprint polygon — this app's map only ever plots building
    POINTS regardless of source (see `analysis.js`'s `renderBuildingPoints`),
    so there's no reason to pay for the ~4x larger polygon shard files
    upstream."""

    area = models.ForeignKey(OpenBuildingsArea, on_delete=models.CASCADE, related_name="buildings")
    lon = models.FloatField()
    lat = models.FloatField()
    area_m2 = models.FloatField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
