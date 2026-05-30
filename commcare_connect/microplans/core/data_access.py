"""Data access for microplans — wraps LabsRecordAPIClient.

Persists the drawn area + generated frame as LabsRecords scoped by
experiment=<opportunity_id>. No Django models; reads/writes go to the
production LabsRecord API via BaseDataAccess.labs_api.
"""

from __future__ import annotations

from datetime import datetime, timezone

from commcare_connect.microplans.core import plan as plan_lib
from commcare_connect.microplans.core.models import (
    TYPE_AREA,
    TYPE_FRAME,
    TYPE_PLAN,
    TYPE_PLAN_GROUP,
    RooftopAreaRecord,
    RooftopFrameRecord,
    RooftopPlanGroupRecord,
    RooftopPlanRecord,
)
from commcare_connect.workflow.data_access import BaseDataAccess

# Bump when the rooftop_area / rooftop_frame `data` shape changes, so readers
# can branch on schema_version instead of guessing (cheap migration insurance).
# v2 added `mode` ("sampling" | "coverage").
# v3 added the editable `microplan_plan` record (planning-phase work areas + audit).
# v4 made plans program-scoped (program_id, opportunity_id, status) + plan groups.
SCHEMA_VERSION = 4


class RooftopDataAccess(BaseDataAccess):
    """CRUD for rooftop_area + rooftop_frame records, scoped to one opportunity."""

    @property
    def _experiment(self) -> str:
        return str(self.opportunity_id)

    def save_area(self, areas: list[dict], config: dict, name: str = "", mode: str = "sampling") -> RooftopAreaRecord:
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_AREA,
            data={
                "schema_version": SCHEMA_VERSION,
                "name": name,
                "mode": mode,
                "areas": areas,
                "config": config,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return RooftopAreaRecord(record.to_api_dict())

    def save_frame(
        self,
        area_record_id: int,
        pins: dict,
        hulls: dict,
        stats: list[dict],
        mode: str = "sampling",
    ) -> RooftopFrameRecord:
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_FRAME,
            data={
                "schema_version": SCHEMA_VERSION,
                "mode": mode,
                "pins": pins,
                "hulls": hulls,
                "stats": stats,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            labs_record_id=area_record_id,
        )
        return RooftopFrameRecord(record.to_api_dict())

    def list_frames(self) -> list[RooftopFrameRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_FRAME,
            model_class=RooftopFrameRecord,
        )

    def list_areas(self) -> list[RooftopAreaRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_AREA,
            model_class=RooftopAreaRecord,
        )

    # ---- planning-phase plan (the editable, LLO-reviewed work areas) ----

    def materialize_plan(self, frame: RooftopFrameRecord, name: str = "") -> RooftopPlanRecord:
        """Create an editable plan from a generated frame: one work area per
        cluster (coverage) or pin (sampling), each UNASSIGNED with an empty audit."""
        work_areas = plan_lib.materialize_work_areas(frame.mode, frame.pins, frame.hulls)
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_PLAN,
            data={
                "schema_version": SCHEMA_VERSION,
                "mode": frame.mode,
                "name": name,
                "frame_record_id": frame.id,
                "work_areas": work_areas,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            labs_record_id=frame.id,
        )
        return RooftopPlanRecord(record.to_api_dict())

    def get_plan(self, plan_id: int) -> RooftopPlanRecord:
        record = self.labs_api.get_record_by_id(plan_id, model_class=RooftopPlanRecord)
        return record

    def list_plans(self) -> list[RooftopPlanRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN,
            model_class=RooftopPlanRecord,
        )

    def _save_work_areas(self, plan: RooftopPlanRecord, work_areas: list[dict]) -> RooftopPlanRecord:
        data = dict(plan.data)
        data["work_areas"] = work_areas
        record = self.labs_api.update_record(
            record_id=plan.id,
            experiment=self._experiment,
            type=TYPE_PLAN,
            data=data,
            current_record=plan,
        )
        return RooftopPlanRecord(record.to_api_dict())

    def apply_plan_edits(
        self, plan_id: int, wa_ids: list[str], action: str, params: dict, actor: str
    ) -> RooftopPlanRecord:
        """Apply one edit to one or more work areas in a single read-modify-write
        (audit appended per area, phase=planning). Loading once + saving once avoids
        the lost-update race a per-id loop would create within a request.

        Across concurrent requests this is last-write-wins (no version check) — an
        accepted tradeoff for planning, which is a single-reviewer activity before
        upload, not the concurrent operational editing Connect handles post-upload.
        """
        plan = self.get_plan(plan_id)
        work_areas = [dict(w) for w in plan.work_areas]
        for wa_id in wa_ids:
            wa = plan_lib.find(work_areas, wa_id)
            if wa is None:
                raise ValueError(f"work area {wa_id!r} not in plan {plan_id}")
            plan_lib.apply_action(wa, action, params, actor)
        return self._save_work_areas(plan, work_areas)


class ProgramPlanDataAccess(BaseDataAccess):
    """Program-scoped CRUD for microplans + plan groups.

    Plans and groups are LabsRecords scoped by experiment=<program_id> (the same
    convention solicitations use), so a program owns a portfolio of candidate
    plans. opportunity_id on a plan is a late binding set only at Deploy.
    """

    def __init__(self, program_id, **kwargs):
        super().__init__(program_id=int(program_id), **kwargs)

    @property
    def _experiment(self) -> str:
        return str(self.program_id)

    # ---- plans ----

    def create_plan(
        self,
        region: str,
        name: str,
        mode: str,
        pins: dict,
        hulls: dict,
        input_areas: list | None = None,
        grouping: dict | None = None,
    ) -> RooftopPlanRecord:
        """Create a Draft plan in the program from a generated frame (one work area
        per cluster/pin). ``input_areas`` is the original draw/admin/pin payload
        (stored so footprints overlay can reuse the cached fetch geometry).
        ``grouping`` is the Phase-1 strategy/params for cell→group bucketing
        (defaults to BFS adjacency — Connect-GIS parity)."""
        work_areas = plan_lib.materialize_work_areas(mode, pins, hulls, grouping=grouping)
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            data={
                "schema_version": SCHEMA_VERSION,
                "program_id": self.program_id,
                "opportunity_id": None,
                "status": plan_lib.PLAN_DRAFT,
                "region": region,
                "name": name,
                "mode": mode,
                "work_areas": work_areas,
                "input_areas": list(input_areas or []),
                "grouping": dict(grouping or {}),
                "status_log": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return RooftopPlanRecord(record.to_api_dict())

    def regroup_plan(self, plan_id: int, grouping: dict, actor: str) -> RooftopPlanRecord:
        """Re-apply grouping (cells → work_area_group) to an existing plan.

        Phase 1 of the two-phase pipeline. Snapshots each cell's old group, runs
        the strategy, and then routes through ``apply_action("regroup", ...)`` so
        per-cell audits are appended consistently with manual regrouping.
        """
        from commcare_connect.microplans.core import grouping as grouping_lib

        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        cfg = grouping_lib.GroupingConfig.from_payload(grouping)
        # Apply to ACTIVE cells; excluded ones keep their old group.
        active = [w for w in work_areas if w.get("status") != plan_lib.STATUS_EXCLUDED]
        # Snapshot old groups, then run the strategy + restore them so apply_action
        # can compute a real before→after diff and emit the audit.
        old_groups = {w["id"]: w.get("work_area_group", "") for w in active}
        grouping_lib.group_work_areas(active, cfg)
        new_groups = {w["id"]: w.get("work_area_group", "") for w in active}
        for w in active:
            w["work_area_group"] = old_groups[w["id"]]
            plan_lib.apply_action(w, "regroup", {"work_area_group": new_groups[w["id"]]}, actor)
        data["work_areas"] = work_areas
        data["grouping"] = grouping
        return self._save_plan(plan, data)

    def list_plans(self) -> list[RooftopPlanRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            model_class=RooftopPlanRecord,
        )

    def get_plan(self, plan_id: int) -> RooftopPlanRecord:
        return self.labs_api.get_record_by_id(
            int(plan_id), experiment=self._experiment, type=TYPE_PLAN, model_class=RooftopPlanRecord
        )

    def _save_plan(self, plan: RooftopPlanRecord, data: dict) -> RooftopPlanRecord:
        record = self.labs_api.update_record(
            record_id=plan.id,
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            data=data,
            current_record=plan,
        )
        return RooftopPlanRecord(record.to_api_dict())

    def apply_plan_edits(
        self, plan_id: int, wa_ids: list[str], action: str, params: dict, actor: str
    ) -> RooftopPlanRecord:
        """Apply one edit to one or more work areas in a single read-modify-write
        (phase=planning audit per area). Last-write-wins across concurrent requests
        — acceptable for single-reviewer planning."""
        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        for wa_id in wa_ids:
            wa = plan_lib.find(work_areas, wa_id)
            if wa is None:
                raise ValueError(f"work area {wa_id!r} not in plan {plan_id}")
            plan_lib.apply_action(wa, action, params, actor)
        data["work_areas"] = work_areas
        return self._save_plan(plan, data)

    def transition_plan(self, plan_id: int, to: str, actor: str, opportunity_id=None) -> RooftopPlanRecord:
        """Advance a plan's lifecycle status (Draft→In review→Approved→Deployed /
        Archived). Deploying binds the live Connect opportunity_id."""
        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        plan_lib.transition_plan(data, to, actor, opportunity_id=opportunity_id)
        return self._save_plan(plan, data)

    def delete_plan(self, plan_id: int) -> None:
        """Hard-delete a plan record. Use sparingly — Archive (status transition) is
        the safer default for normal lifecycle. This is for wiping sample data."""
        self.labs_api.delete_record(int(plan_id))

    # ---- plan groups (shareable subset offered to an LLO) ----

    def create_group(self, name: str, plan_ids: list[int], offered_to: str = "") -> RooftopPlanGroupRecord:
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            data={
                "schema_version": SCHEMA_VERSION,
                "program_id": self.program_id,
                "name": name,
                "plan_ids": [int(p) for p in plan_ids],
                "offered_to": offered_to,
                "shared": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return RooftopPlanGroupRecord(record.to_api_dict())

    def list_groups(self) -> list[RooftopPlanGroupRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            model_class=RooftopPlanGroupRecord,
        )

    def get_group(self, group_id: int) -> RooftopPlanGroupRecord:
        return self.labs_api.get_record_by_id(
            int(group_id), experiment=self._experiment, type=TYPE_PLAN_GROUP, model_class=RooftopPlanGroupRecord
        )

    def update_group(self, group_id: int, **fields) -> RooftopPlanGroupRecord:
        group = self.get_group(group_id)
        data = dict(group.data)
        for key in ("name", "offered_to", "shared"):
            if key in fields and fields[key] is not None:
                data[key] = fields[key]
        if "plan_ids" in fields and fields["plan_ids"] is not None:
            data["plan_ids"] = [int(p) for p in fields["plan_ids"]]
        record = self.labs_api.update_record(
            record_id=group.id,
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            data=data,
            current_record=group,
        )
        return RooftopPlanGroupRecord(record.to_api_dict())

    def delete_group(self, group_id: int) -> None:
        """Hard-delete a plan group record. Use sparingly."""
        self.labs_api.delete_record(int(group_id))
