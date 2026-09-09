"""Proxy models for mop-up LocalLabsRecords.

Persistence rides the production LabsRecord API (no Django models / tables)
per the labs convention — mirrors `connect_labs.microplans.core.models`:
every record carries experiment=<program_id> and a `type` discriminator.
These proxy classes give typed `@property` access to the JSON `data`; they
are transient (no `.save()`).
"""

from __future__ import annotations

from connect_labs.labs.models import LocalLabsRecord

TYPE_RUN = "mopup_run"

STATUS_SETUP = "setup"  # Phase 1: opportunity/ward(s)/date not yet all picked
STATUS_ANALYSIS = "analysis"  # Phase 2: analyzing, thresholds still live
STATUS_LOCKED = "locked"  # Phase 2->3 boundary: candidate set frozen


class MopupRunRecord(LocalLabsRecord):
    """One mop-up review in progress: which opportunity, which ward(s) and
    date window were picked (Phase 1), the threshold/toggle config and locked
    candidate set (Phase 2) once analysis starts.

    Program-scoped (experiment=<program_id>) since a mop-up round is reviewed
    across a program's LLO opportunities — same convention as
    `microplans.core.models.PlanRecord`. We do NOT redefine `program_id` as a
    property — the base `LocalLabsRecord.__init__` assigns it as an instance
    attribute, and a read-only property here would shadow that and break
    instantiation (same caveat `PlanRecord` documents).
    """

    @property
    def status(self) -> str:
        return self.data.get("status", STATUS_SETUP)

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def target_opportunity_id(self) -> int | None:
        """The single opportunity this run analyzes (Phase 1's opportunity
        picker) — distinct from the base class's own `opportunity_id`
        attribute, which is unset (None) for this program-scoped record."""
        return self.data.get("target_opportunity_id")

    @property
    def selected_wards(self) -> list[dict]:
        """``[{"ward": ..., "lga": ..., "state": ...}, ...]`` picked in
        Phase 1. Empty means "all wards" (no narrowing chosen yet)."""
        return self.data.get("selected_wards", [])

    @property
    def date_from(self) -> str | None:
        return self.data.get("date_from")

    @property
    def date_to(self) -> str | None:
        return self.data.get("date_to")

    @property
    def thresholds(self) -> dict:
        """Per-indicator threshold/granularity config + the global cluster
        settings (§6's control inventory) — set in Phase 2."""
        return self.data.get("thresholds", {})

    @property
    def candidate_work_areas(self) -> list[dict]:
        """The locked candidate set, frozen at the Phase 2->3 boundary. Empty
        until the reviewer explicitly locks."""
        return self.data.get("candidate_work_areas", [])

    @property
    def fetch_task_id(self) -> str | None:
        """The Celery task id (if any) for `mopup.tasks.fetch_evaluation_data`
        — this run's one expensive work-area/visit/geometry pull. `None`
        means no fetch has been dispatched yet (or the last one failed and
        was cleared so the next poll re-dispatches). See
        `MopupCandidatesView`/`MopupLockView` for how this is read back via
        `AsyncResult`."""
        return self.data.get("fetch_task_id")

    @property
    def create_plan_task_id(self) -> str | None:
        """The Celery task id (if any) for `mopup.tasks.create_mopup_plan` —
        Phase 3's hand-off, offloaded the same way as `fetch_task_id` since a
        real multi-ward hand-off is worth guarding against a gateway timeout
        even though (since planning-gap computation moved to Phase 2's Step
        2 — see `planning_gap_features`) it's typically fast now. Unlike
        `fetch_task_id` (a run's ONE-TIME data pull), this is cleared as soon
        as a terminal state (success or failure) is read back — each
        "Create mop-up plan" click is its own attempt, not a single
        run-lifetime action, so a later click must dispatch a genuinely new
        task rather than replay a finished one. See
        `MopupCreatePlanView`/`_create_plan_result_or_progress` for how this
        is read back via `AsyncResult`."""
        return self.data.get("create_plan_task_id")

    @property
    def planning_gap_task_id(self) -> str | None:
        """The Celery task id (if any) for `mopup.tasks.preview_planning_gaps`
        — Phase 2's Step 2 (locked-run-only) planning-gap preview. Cleared on
        any terminal state the same way `create_plan_task_id` is, since each
        "Recompute" click in Step 2 is its own attempt with its own config."""
        return self.data.get("planning_gap_task_id")

    @property
    def planning_gap_features(self) -> list[dict]:
        """The gap-fill WorkArea features from the LATEST successful Step 2
        preview (empty until Step 2 has run at least once) — GeoJSON
        Features in the same shape `core.gaps.planning_gap_features`
        produces. Phase 3's hand-off (`core.handoff.create_plan_from_locked_run`)
        carries these forward as-is; it does not recompute them."""
        return self.data.get("planning_gap_features", [])

    @property
    def planning_gap_config(self) -> dict:
        """The mode ("skip"/"overture"/"upload")/building-source/confidence/
        min-buildings/cell-size settings used to produce
        `planning_gap_features`, for redisplaying Step 2's form with
        whatever was last used rather than always resetting to defaults."""
        return self.data.get("planning_gap_config", {})

    @property
    def uploaded_buildings_key(self) -> str | None:
        """Storage key (under `default_storage`, e.g. `MediaRootS3Boto3Storage`
        in production) for the raw CSV last uploaded via
        `MopupUploadBuildingsView` — read back by
        `mopup.tasks.preview_planning_gaps` when Step 2's mode is "upload".
        `None` until a file has been uploaded for this run."""
        return self.data.get("uploaded_buildings_key")

    @property
    def uploaded_buildings_filename(self) -> str | None:
        """The original filename of the last uploaded buildings CSV, purely
        for redisplaying "X uploaded" on Step 2's form — never used to
        resolve the actual stored file (that's `uploaded_buildings_key`)."""
        return self.data.get("uploaded_buildings_filename")

    @property
    def planning_gap_warnings(self) -> dict:
        """{ward: reason} for any ward whose Step 2 gap computation failed on
        the latest run (e.g. an expired CommCare HQ session) — surfaced so a
        failure never looks identical to "this ward has no gaps." See
        `core.gaps.planning_gap_features`'s docstring for the failure mode
        this exists to catch (found live this session)."""
        return self.data.get("planning_gap_warnings", {})

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")
