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
    def created_at(self) -> str:
        return self.data.get("created_at", "")
