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
    # Overture primary source / provider (e.g. "Google Open Buildings",
    # "OpenStreetMap", "Microsoft ML Buildings"). Drives the source picker. Null for
    # rows cached before source capture (those areas re-fetch on the bumped cache key).
    dataset = models.CharField(max_length=64, null=True, blank=True)
    # GeoJSON coordinates of the actual polygon (e.g. [[[lon,lat],...]] for Polygon).
    # Stored as JSON to keep the model GIS-free; the review-page footprints overlay
    # reads this when present, otherwise falls back to a centroid marker.
    geom_json = models.JSONField(null=True, blank=True)


class BarrierArea(models.Model):
    """Durable cache of an area's physical barriers (major roads, railways, water)
    for the barrier-aware WAG grouping strategy.

    Like FootprintArea, one row per fetched area keyed by sha256(release | wkt).
    The barrier lines are stored as a single GeoJSON geometry (MultiLineString of
    road/rail centrelines + water outlines) — the grouper only needs the lines to
    test whether the segment between two work areas crosses one, so we keep this
    GIS-free (plain JSON) and load it into shapely at group time. ``n_features`` = 0
    is a valid "fetched, none here" result, distinct from "never fetched"."""

    area_hash = models.CharField(max_length=64, unique=True)
    overture_release = models.CharField(max_length=32)
    n_features = models.IntegerField(default=0)
    geom_json = models.JSONField(null=True, blank=True)  # merged barrier lines (GeoJSON geometry)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.area_hash[:12]} ({self.n_features} barriers)"


class SimpleMicroplan(models.Model):
    """A microplan made in the Simple Microplan creator (`/microplans/simple/`).

    Unlike program plans (LabsRecords on Connect), these live in the labs DB.
    ``data`` is the creator's whole state: the boundary parts, its settings and
    the work-area rows exactly as they export to CSV.

    Two kinds, told apart by ``program_id``:

      * NULL -- a global plan, listed at `/microplans/simple/`. It records the labs
        context (organisation / program / opportunity) picked when it was made, and
        whoever can pick that context may open and change it
        (``simple_views.ContextAccess``). One made with nothing picked (and every
        plan from before contexts) is its creator's, and Dimagi staff's, only.
      * set -- a plan in a WORKSPACE program (``labs.workspaces``), made at
        `/microplans/program/<id>/simple/` and filed under ``team`` (a Connect org
        slug; blank = Program Managers only). It follows the workspace's roles and
        never appears in the global list.
    """

    name = models.CharField(max_length=200)
    # The labs-only workspace program this plan belongs to; NULL for a global plan.
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    # Workspace plans only: the Connect org slug of the team that owns it ("" = PMs only).
    team = models.CharField(max_length=200, blank=True, default="")
    # Global plans only: the context picked when the plan was made (any may be blank).
    # Kept apart from ``program_id``, which files a plan inside a workspace program.
    # Names are copied in so the plan can say where it belongs to someone whose own
    # Connect tree does not hold that organisation / program / opportunity.
    organization_slug = models.CharField(max_length=255, blank=True, default="", db_index=True)
    organization_name = models.CharField(max_length=255, blank=True, default="")
    context_program_id = models.IntegerField(null=True, blank=True, db_index=True)
    program_name = models.CharField(max_length=255, blank=True, default="")
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    opportunity_name = models.CharField(max_length=255, blank=True, default="")
    created_by = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="simple_microplans"
    )
    # Kept beside the FK so the list still says who made it after the user row goes.
    created_by_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Mirrors len(data["rows"]) so the list page can defer `data`, which can be MBs.
    n_work_areas = models.IntegerField(default=0)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.name

    @property
    def context_names(self) -> list[str]:
        """The recorded organisation, program and opportunity names, in that order."""
        names = [self.organization_name or self.organization_slug]
        names.append(self.program_name or (f"Program #{self.context_program_id}" if self.context_program_id else ""))
        names.append(self.opportunity_name or (f"Opportunity #{self.opportunity_id}" if self.opportunity_id else ""))
        return [n for n in names if n]


class TaskOwner(models.Model):
    """Who started a microplans Celery task, so only they can read its status.

    A Celery task id is a bare handle: anyone holding it can ask the result backend
    for the task's progress and result. Every microplans enqueue -- the previews, plan
    mutations, the surrounding-ward comparison, group sampling, bulk create (page and
    the ``microplans_bulk_create_plans`` MCP tool) -- records the starter here, and
    every status reader refuses anyone else with the same answer as an unknown id, so
    a guessed id reveals nothing. ``restricted`` mirrors ``MCPJob.restricted``: a
    "no user visit data" MCP call sees only tasks a restricted call started.
    """

    task_id = models.CharField(max_length=255, primary_key=True)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="microplans_tasks")
    restricted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def record(cls, task_id, user, *, restricted: bool = False) -> None:
        cls.objects.update_or_create(task_id=str(task_id), defaults={"user": user, "restricted": bool(restricted)})

    @classmethod
    def owned_by(cls, task_id, user, *, restricted: bool = False) -> bool:
        if not getattr(user, "pk", None):
            return False
        qs = cls.objects.filter(task_id=str(task_id), user=user)
        if restricted:
            qs = qs.filter(restricted=True)
        return qs.exists()

    def __str__(self) -> str:
        return f"{self.task_id} ({self.user_id})"
