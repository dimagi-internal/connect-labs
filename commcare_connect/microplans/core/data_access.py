"""Data access for microplans — wraps LabsRecordAPIClient.

Persists program-scoped plans + plan groups as LabsRecords (experiment=<program_id>).
No Django models; reads/writes go to the production LabsRecord API via
BaseDataAccess.labs_api.
"""

from __future__ import annotations

from datetime import datetime, timezone

from commcare_connect.microplans.core import plan as plan_lib
from commcare_connect.microplans.core.models import (
    TYPE_PLAN,
    TYPE_PLAN_GROUP,
    PlanGroupRecord,
    PlanRecord,
)
from commcare_connect.workflow.data_access import BaseDataAccess

# Bump when the `microplan_plan` `data` shape changes, so readers can branch on
# schema_version instead of guessing (cheap migration insurance).
# v3 added the editable `microplan_plan` record (planning-phase work areas + audit).
# v4 made plans program-scoped (program_id, opportunity_id, status) + plan groups.
SCHEMA_VERSION = 4


class StalePlanError(Exception):
    """A save was attempted against a revision older than the stored one — i.e. the
    plan changed (another tab/session, or a `regenerate`) since the caller loaded
    it. The view turns this into a 409 so the UI can warn + reload instead of
    silently clobbering the newer state. See ``ProgramPlanDataAccess._save_plan``."""


class RecordNotInProgramError(Exception):
    """A delete targeted a record id that isn't in this program — it doesn't exist,
    or it belongs to another program the caller can't see. We refuse rather than
    delete by raw id: the production DELETE endpoint authorizes the caller's
    *membership* of any scope in the payload but then deletes by ``pk__in`` without
    checking the record actually belongs there, so a bare id would let a member of
    one program delete another program's records. Reading the record scoped to this
    program first (404 → None) closes that. The view maps this to a 404."""


class ProgramPlanDataAccess(BaseDataAccess):
    """Program-scoped CRUD for microplans + plan groups.

    Plans and groups are LabsRecords scoped by experiment=<program_id> (the same
    convention solicitations use), so a program owns a portfolio of candidate
    plans. opportunity_id on a plan is a late binding set only at Deploy.
    """

    def __init__(self, program_id, **kwargs):
        program_id = int(program_id)
        # Labs-only program: a synthetic opp surfaced in user_programs as a negative
        # id (= -opportunity_id) by labs.context._merge_labs_only_opps. Carry the
        # backing synthetic opp id so the LabsRecord API client short-circuits to the
        # labs DB (no prod round-trip, no membership check) — exactly like a synthetic
        # opportunity. Real programs (positive PKs) are untouched and still hit prod.
        if program_id < 0 and not kwargs.get("opportunity_id"):
            from commcare_connect.labs.synthetic.local_records_backend import (
                is_labs_only_opportunity_id,
            )

            backing_opp = -program_id
            if is_labs_only_opportunity_id(backing_opp):
                kwargs["opportunity_id"] = backing_opp
        super().__init__(program_id=program_id, **kwargs)

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
        lga: str = "",
        state: str = "",
    ) -> PlanRecord:
        """Create a Draft plan in the program from a generated frame (one work area
        per cluster/pin). ``input_areas`` is the original draw/admin/pin payload
        (stored so footprints overlay can reuse the cached fetch geometry).
        ``grouping`` is the Phase-1 strategy/params for cell→group bucketing
        (defaults to BFS adjacency — Connect-GIS parity).

        ``lga``/``state`` are the administrative labels Connect's work-area importer
        requires non-empty (see ``microplans/CONNECT_IMPORT_CONTRACT.md``); stored at
        creation so the Connect-import CSV export populates them without the caller
        re-supplying them. ``lga`` falls back to ``region`` when blank."""
        work_areas = plan_lib.materialize_work_areas(
            mode, pins, hulls, grouping=grouping
        )
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
                "lga": (lga or region or "").strip(),
                "state": (state or "").strip(),
                "name": name,
                "mode": mode,
                "work_areas": work_areas,
                "input_areas": list(input_areas or []),
                "grouping": dict(grouping or {}),
                "status_log": [],
                # Optimistic-concurrency counter; bumped on every _save_plan.
                "revision": 0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PlanRecord(record.to_api_dict())

    def reassign_plan(
        self,
        plan_id: int,
        assignment: dict,
        actor: str,
        base_revision: int | None = None,
    ) -> PlanRecord:
        """Re-apply CHW assignment to a plan's groups.

        Phase 2 of the two-phase pipeline. Snapshots each cell's old worker,
        runs the strategy, restores so ``apply_action("reassign", ...)`` can
        emit a real audit per cell.
        """
        from commcare_connect.microplans.core import assignment as assignment_lib

        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        cfg = assignment_lib.AssignmentConfig.from_payload(assignment)
        active = [w for w in work_areas if w.get("status") != plan_lib.STATUS_EXCLUDED]
        old_workers = {w["id"]: w.get("opportunity_access") for w in active}
        assignment_lib.assign_groups_to_chws(active, cfg)
        new_workers = {w["id"]: w.get("opportunity_access") for w in active}
        for w in active:
            if old_workers[w["id"]] != new_workers[w["id"]]:
                w["opportunity_access"] = old_workers[w["id"]]
                plan_lib.apply_action(
                    w, "reassign", {"opportunity_access": new_workers[w["id"]]}, actor
                )
        data["work_areas"] = work_areas
        data["assignment"] = assignment
        return self._save_plan(plan, data, base_revision)

    def regroup_plan(
        self, plan_id: int, grouping: dict, actor: str, base_revision: int | None = None
    ) -> PlanRecord:
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
            plan_lib.apply_action(
                w, "regroup", {"work_area_group": new_groups[w["id"]]}, actor
            )
        data["work_areas"] = work_areas
        data["grouping"] = grouping
        return self._save_plan(plan, data, base_revision)

    def regenerate_plan(
        self,
        plan_id: int,
        mode: str,
        pins: dict,
        hulls: dict,
        input_areas: list,
        grouping: dict | None = None,
        base_revision: int | None = None,
        stats: list | None = None,
    ) -> PlanRecord:
        """Destructive re-creation of the work areas for an existing plan.

        Same end state as `create_plan` (one work area per cluster/pin, auto-
        grouped via Phase 1) — the only "preservation" is that the plan keeps
        its id, name, region, mode, status, and status_log. CHW assignments,
        per-area resizes, exclusions, audit history, and the previous
        grouping/assignment configs are all wiped, since the underlying work
        areas are different objects.

        Use when the LLO changes the boundary or cell size on an existing plan
        and wants the new layout to replace the old one outright.
        """
        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        work_areas = plan_lib.materialize_work_areas(
            mode, pins, hulls, grouping=grouping
        )
        data["work_areas"] = work_areas
        data["input_areas"] = list(input_areas or [])
        data["mode"] = mode
        data["grouping"] = dict(grouping or {})
        data["assignment"] = {}  # destructive reset — no CHWs carried over
        # Persist the selected-PSU hulls (sampling only) so the saved plan can show the
        # surveyed settlements on a map after creation, without re-fetching footprints.
        if mode == "sampling" and hulls is not None:
            data["psu_hulls"] = hulls
        if stats is not None:
            # Per-arm sampling summary (incl. PSU/building balance stats) for
            # cross-arm comparability; never shared/pushed to Connect.
            data["sampling_stats"] = stats
        return self._save_plan(plan, data, base_revision)

    def list_plans(self) -> list[PlanRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            model_class=PlanRecord,
        )

    def get_plan(self, plan_id: int) -> PlanRecord:
        return self.labs_api.get_record_by_id(
            int(plan_id),
            experiment=self._experiment,
            type=TYPE_PLAN,
            model_class=PlanRecord,
        )

    def _save_plan(
        self, plan: PlanRecord, data: dict, base_revision: int | None = None
    ) -> PlanRecord:
        """Persist mutated plan ``data``, bumping the optimistic-concurrency
        ``revision``. If ``base_revision`` is given (the revision the caller loaded)
        and it no longer matches the freshly-read plan, raise ``StalePlanError`` —
        the plan changed underneath us, so saving would clobber the newer state.

        ``plan`` was just read by the calling method, so ``plan.data['revision']`` is
        the current stored value. There's a tiny residual TOCTOU window before the
        write (the Labs Record API has no conditional update), which is acceptable
        for single-reviewer planning — this catches the common stale-tab case the
        UI surfaces as a reload prompt, not distributed locking."""
        current_rev = int(plan.data.get("revision", 0))
        if base_revision is not None and int(base_revision) != current_rev:
            raise StalePlanError(
                f"This plan changed since you opened it (you have r{int(base_revision)}, "
                f"it's now r{current_rev}). Reload to get the latest before saving."
            )
        data["revision"] = current_rev + 1
        record = self.labs_api.update_record(
            record_id=plan.id,
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            data=data,
            current_record=plan,
        )
        return PlanRecord(record.to_api_dict())

    def apply_plan_edits(
        self,
        plan_id: int,
        wa_ids: list[str],
        action: str,
        params: dict,
        actor: str,
        base_revision: int | None = None,
    ) -> PlanRecord:
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
        return self._save_plan(plan, data, base_revision)

    def transition_plan(
        self,
        plan_id: int,
        to: str,
        actor: str,
        opportunity_id=None,
        base_revision: int | None = None,
    ) -> PlanRecord:
        """Advance a plan's lifecycle status (Draft→In review→Approved→Deployed /
        Archived). Deploying binds the live Connect opportunity_id."""
        plan = self.get_plan(plan_id)
        data = dict(plan.data)
        plan_lib.transition_plan(data, to, actor, opportunity_id=opportunity_id)
        return self._save_plan(plan, data, base_revision)

    def delete_plan(self, plan_id: int) -> None:
        """Hard-delete a plan record. Use sparingly — Archive (status transition) is
        the safer default for normal lifecycle. This is for wiping sample data.

        Reads the plan scoped to this program first (``get_plan`` sends program_id,
        so the prod GET only returns it if it's in this program and the caller is a
        member). Refuses if it's not ours — never deletes by raw id. See
        :class:`RecordNotInProgramError`."""
        if self.get_plan(int(plan_id)) is None:
            raise RecordNotInProgramError(
                f"plan {plan_id} is not in program {self.program_id}"
            )
        self.labs_api.delete_record(int(plan_id))

    # ---- plan groups (shareable subset offered to an LLO) ----

    def create_group(
        self,
        name: str,
        plan_ids: list[int],
        offered_to: str = "",
        kind: str = "bundle",
        arms: dict | None = None,
        sampling_config: dict | None = None,
    ) -> PlanGroupRecord:
        """Create a plan group. ``kind="study"`` + ``arms`` make it a controlled
        study (arm assignment is labs-side — never written onto plans)."""
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
                "kind": kind,
                "arms": {str(k): v for k, v in (arms or {}).items()},
                "sampling_config": sampling_config or {},
                "status": "defining",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return PlanGroupRecord(record.to_api_dict())

    def list_groups(self) -> list[PlanGroupRecord]:
        return self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            model_class=PlanGroupRecord,
        )

    def get_group(self, group_id: int) -> PlanGroupRecord:
        return self.labs_api.get_record_by_id(
            int(group_id),
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            model_class=PlanGroupRecord,
        )

    def update_group(self, group_id: int, **fields) -> PlanGroupRecord:
        group = self.get_group(group_id)
        data = dict(group.data)
        for key in (
            "name",
            "offered_to",
            "shared",
            "kind",
            "sampling_config",
            "status",
        ):
            if key in fields and fields[key] is not None:
                data[key] = fields[key]
        if "plan_ids" in fields and fields["plan_ids"] is not None:
            data["plan_ids"] = [int(p) for p in fields["plan_ids"]]
        if "arms" in fields and fields["arms"] is not None:
            data["arms"] = {str(k): v for k, v in fields["arms"].items()}
        record = self.labs_api.update_record(
            record_id=group.id,
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            data=data,
            current_record=group,
        )
        return PlanGroupRecord(record.to_api_dict())

    def add_plan_to_group(self, group_id: int, plan_id: int) -> PlanGroupRecord:
        """Add ``plan_id`` to the group's membership (idempotent)."""
        group = self.get_group(group_id)
        plan_id = int(plan_id)
        if plan_id in group.plan_ids:
            return group
        return self.update_group(group_id, plan_ids=[*group.plan_ids, plan_id])

    def remove_plan_from_group(self, group_id: int, plan_id: int) -> PlanGroupRecord:
        """Remove ``plan_id`` from the group, dropping any arm assignment for it."""
        group = self.get_group(group_id)
        plan_id = int(plan_id)
        new_ids = [p for p in group.plan_ids if p != plan_id]
        new_arms = {k: v for k, v in group.arms.items() if k != str(plan_id)}
        return self.update_group(group_id, plan_ids=new_ids, arms=new_arms)

    def delete_group(self, group_id: int) -> None:
        """Hard-delete a plan group record. Use sparingly. Reads it scoped to this
        program first and refuses if it's not ours (see :class:`RecordNotInProgramError`)."""
        if self.get_group(int(group_id)) is None:
            raise RecordNotInProgramError(
                f"group {group_id} is not in program {self.program_id}"
            )
        self.labs_api.delete_record(int(group_id))
