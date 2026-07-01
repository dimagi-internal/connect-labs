"""Proxy models for microplans LocalLabsRecords.

Persistence rides the production LabsRecord API (no Django models / tables) per
the labs convention: every record carries experiment=<opportunity_id> and a
`type` discriminator. These proxy classes give typed `@property` access to the
JSON `data`; they are transient (no .save()).
"""

from __future__ import annotations

from connect_labs.labs.models import LocalLabsRecord

TYPE_PLAN = "microplan_plan"
TYPE_PLAN_GROUP = "microplan_plan_group"


class PlanRecord(LocalLabsRecord):
    """A candidate microplan within a program. Holds the editable work areas the
    LLO reviews before upload (each mirrors Connect's WorkArea mutable fields + a
    phase=planning audit log) plus a plan-level lifecycle ``status``.

    Program-scoped (experiment=<program_id>); the deploy-bound opportunity id lives
    in ``data["opportunity_id"]`` (set only when the plan is Deployed). We do NOT
    redefine ``program_id``/``opportunity_id`` as properties — the base
    ``LocalLabsRecord.__init__`` assigns them as instance attributes, and a
    read-only property here would shadow that assignment and break instantiation.
    Read the deploy-bound opp via ``record.data.get("opportunity_id")``.
    """

    @property
    def status(self) -> str:
        return self.data.get("status", "draft")

    @property
    def region(self) -> str:
        return self.data.get("region", "")

    @property
    def lga(self) -> str:
        """LGA label for the Connect work-area export (Connect requires it
        non-empty). Captured at creation; falls back to ``region``."""
        return self.data.get("lga", "") or self.data.get("region", "")

    @property
    def state(self) -> str:
        """State label for the Connect work-area export (Connect requires it
        non-empty). Captured at creation; "" if never set."""
        return self.data.get("state", "")

    @property
    def mode(self) -> str:
        return self.data.get("mode", "sampling")

    @property
    def frame_record_id(self):
        return self.data.get("frame_record_id")

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def work_areas(self) -> list[dict]:
        return self.data.get("work_areas", [])

    @property
    def phase(self) -> str:
        """Whether the sampling/gridding algorithm has run yet.

        ``"boundary"`` — the plan's area is defined (``input_areas``) but no work
        areas have been generated; ``"sampled"`` — work areas exist. Group
        management branches on this (e.g. "Generate samples" acts on the
        boundary-only members)."""
        return "sampled" if self.work_areas else "boundary"

    @property
    def status_log(self) -> list[dict]:
        return self.data.get("status_log", [])

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class PlanGroupRecord(LocalLabsRecord):
    """A named, shareable subset of a program's plans — the bundle offered to a
    particular LLO. Program-scoped (experiment=<program_id>). ``program_id`` comes
    from the base ``LocalLabsRecord`` instance attribute (don't shadow it)."""

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def plan_ids(self) -> list[int]:
        return self.data.get("plan_ids", [])

    @property
    def offered_to(self) -> str:
        return self.data.get("offered_to", "")  # the LLO this bundle is for

    @property
    def shared(self) -> bool:
        return bool(self.data.get("shared", False))

    @property
    def kind(self) -> str:
        """``"bundle"`` (a curated set offered to an LLO — the original use) or
        ``"study"`` (a two-/multi-arm study, ``arms`` assigned)."""
        return self.data.get("kind", "bundle")

    @property
    def arms(self) -> dict[str, str]:
        """Per-member arm assignment ``{plan_id: "intervention"|"control"|…}``.

        Labs-side only — the arm is NEVER written onto a plan or its work areas,
        so execution stays blind by construction. Keys are stringified plan ids
        (JSON object keys); use :meth:`arm_for` for lookups by int id."""
        return self.data.get("arms", {})

    def arm_for(self, plan_id) -> str | None:
        """The arm of ``plan_id`` in this study, or ``None`` if unassigned."""
        return self.arms.get(str(plan_id))

    @property
    def sampling_config(self) -> dict:
        """Shared sampling config applied across every member plan, so the only
        intended difference between arms is the area each covers."""
        return self.data.get("sampling_config", {})

    @property
    def status(self) -> str:
        """``defining`` → ``sampled`` → ``reviewed`` → ``exported``."""
        return self.data.get("status", "defining")

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")
