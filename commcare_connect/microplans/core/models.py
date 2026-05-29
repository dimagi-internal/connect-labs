"""Proxy models for microplans LocalLabsRecords.

Persistence rides the production LabsRecord API (no Django models / tables) per
the labs convention: every record carries experiment=<opportunity_id> and a
`type` discriminator. These proxy classes give typed `@property` access to the
JSON `data`; they are transient (no .save()).
"""

from __future__ import annotations

from commcare_connect.labs.models import LocalLabsRecord

TYPE_AREA = "rooftop_area"
TYPE_FRAME = "rooftop_frame"
TYPE_PLAN = "microplan_plan"
TYPE_PLAN_GROUP = "microplan_plan_group"


class RooftopAreaRecord(LocalLabsRecord):
    """The drawn intervention/comparison area(s) + frame config for one run."""

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def areas(self) -> list[dict]:
        """[{"arm": "intervention"|"comparison", "geometry": <GeoJSON>}, ...]"""
        return self.data.get("areas", [])

    @property
    def config(self) -> dict:
        return self.data.get("config", {})

    @property
    def mode(self) -> str:
        """ "sampling" (PPS subset) | "coverage" (visit every household)."""
        return self.data.get("mode", "sampling")

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class RooftopFrameRecord(LocalLabsRecord):
    """A generated frame.

    Sampling mode: pins + cluster hulls + per-arm stats. Coverage mode: the
    cluster polygons live in `hulls` (pins is empty). Parented (labs_record_id)
    to the RooftopAreaRecord it was generated from.
    """

    @property
    def mode(self) -> str:
        return self.data.get("mode", "sampling")

    @property
    def pins(self) -> dict:
        return self.data.get("pins", {"type": "FeatureCollection", "features": []})

    @property
    def hulls(self) -> dict:
        return self.data.get("hulls", {"type": "FeatureCollection", "features": []})

    @property
    def stats(self) -> list[dict]:
        return self.data.get("stats", [])

    @property
    def pin_count(self) -> int:
        return len(self.pins.get("features", []))

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class RooftopPlanRecord(LocalLabsRecord):
    """A candidate microplan within a program. Holds the editable work areas the
    LLO reviews before upload (each mirrors Connect's WorkArea mutable fields + a
    phase=planning audit log) plus a plan-level lifecycle ``status``.

    Program-scoped (experiment=<program_id>); ``opportunity_id`` is a late binding
    set only when the plan is Deployed to a live Connect opp.
    """

    @property
    def program_id(self):
        return self.data.get("program_id")

    @property
    def opportunity_id(self):
        return self.data.get("opportunity_id")  # None until Deployed

    @property
    def status(self) -> str:
        return self.data.get("status", "draft")

    @property
    def region(self) -> str:
        return self.data.get("region", "")

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
    def status_log(self) -> list[dict]:
        return self.data.get("status_log", [])

    @property
    def created_at(self) -> str:
        return self.data.get("created_at", "")


class RooftopPlanGroupRecord(LocalLabsRecord):
    """A named, shareable subset of a program's plans — the bundle offered to a
    particular LLO. Program-scoped (experiment=<program_id>)."""

    @property
    def program_id(self):
        return self.data.get("program_id")

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
    def created_at(self) -> str:
        return self.data.get("created_at", "")
