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
    RooftopAreaRecord,
    RooftopFrameRecord,
    RooftopPlanRecord,
)
from commcare_connect.workflow.data_access import BaseDataAccess

# Bump when the rooftop_area / rooftop_frame `data` shape changes, so readers
# can branch on schema_version instead of guessing (cheap migration insurance).
# v2 added `mode` ("sampling" | "coverage").
# v3 added the editable `microplan_plan` record (planning-phase work areas + audit).
SCHEMA_VERSION = 3


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
        return RooftopAreaRecord(record.to_dict())

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
        return RooftopFrameRecord(record.to_dict())

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
        return RooftopPlanRecord(record.to_dict())

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
        return RooftopPlanRecord(record.to_dict())

    def apply_plan_edit(self, plan_id: int, wa_id: str, action: str, params: dict, actor: str) -> RooftopPlanRecord:
        """Apply one LLO edit to a work area and persist (audit appended, phase=planning)."""
        plan = self.get_plan(plan_id)
        work_areas = [dict(w) for w in plan.work_areas]
        wa = plan_lib.find(work_areas, wa_id)
        if wa is None:
            raise ValueError(f"work area {wa_id!r} not in plan {plan_id}")
        plan_lib.apply_action(wa, action, params, actor)
        return self._save_work_areas(plan, work_areas)
