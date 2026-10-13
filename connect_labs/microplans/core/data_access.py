"""Data access for microplans — wraps LabsRecordAPIClient.

Persists program-scoped plans + plan groups as LabsRecords (experiment=<program_id>).
No Django models; reads/writes go to the production LabsRecord API via
BaseDataAccess.labs_api.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.core.exceptions import PermissionDenied
from django.http import Http404

from connect_labs.microplans.core import plan as plan_lib
from connect_labs.microplans.core.models import TYPE_PLAN, TYPE_PLAN_GROUP, PlanGroupRecord, PlanRecord
from connect_labs.workflow.data_access import BaseDataAccess

# Bump when the `microplan_plan` `data` shape changes, so readers can branch on
# schema_version instead of guessing (cheap migration insurance).
# v3 added the editable `microplan_plan` record (planning-phase work areas + audit).
# v4 made plans program-scoped (program_id, opportunity_id, status) + plan groups.
SCHEMA_VERSION = 4


def _barriers_if_needed(grouping: dict | None, input_areas, allow_remote: bool):
    """Barrier lines for grouping, but only when the barrier-aware strategy is chosen
    (else None → no fetch). Best-effort: any failure degrades to plain grouping."""
    if not isinstance(grouping, dict) or grouping.get("strategy") != "barrier_aware":
        return None
    try:
        from connect_labs.microplans.core.barriers import barriers_for_areas

        return barriers_for_areas(input_areas or [], allow_remote=allow_remote)
    except Exception:  # noqa: BLE001
        return None


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


class ProgramAccessDenied(Http404):
    """The caller may not use this labs-only program (``microplans.access``).

    A 404 rather than a 403, like every other labs-only refusal: it must not
    confirm the program exists. Subclassing ``Http404`` means a view that lets it
    escape still answers 404 instead of 500."""


class WorkspaceDeleteForbidden(PermissionDenied):
    """Only a Program Manager may hard-delete in a workspace program.

    Network Managers archive instead (a Program Manager can restore an archived
    plan; nobody can restore a deleted one). A 403, deliberately: the action is
    refused, not the record hidden. See ``ProgramPlanDataAccess._guard_delete``."""


def _caller(kwargs):
    """The user this data access acts for, if the caller named one.

    Web views pass ``request``, MCP tools pass ``user``. Celery tasks, seeders and
    tests pass neither: they act on ids a view or tool already checked when it
    enqueued them, so there is no one to check here."""
    if kwargs.get("user") is not None:
        return kwargs["user"]
    request = kwargs.get("request")
    return getattr(request, "user", None) if request is not None else None


class ProgramPlanDataAccess(BaseDataAccess):
    """Program-scoped CRUD for microplans + plan groups.

    Plans and groups are LabsRecords scoped by experiment=<program_id> (the same
    convention solicitations use), so a program owns a portfolio of candidate
    plans. opportunity_id on a plan is a late binding set only at Deploy.
    """

    def __init__(self, program_id, *, team: str | None = None, **kwargs):
        """``team`` is what a caller-less construction (a Celery task a view
        enqueued) stamps on new records in a workspace program -- the view worked
        it out for its caller with ``team_for_new_records``. Ignored otherwise."""
        program_id = int(program_id)
        # A labs-only program has no Connect membership check behind it (see
        # below), so the caller's access to it is checked here, before anything
        # is read. A real program returns None without a query.
        caller = _caller(kwargs)
        if caller is not None:
            from connect_labs.microplans.access import record_denial, scope_denied_reason

            denied = scope_denied_reason(caller, program_id=program_id)
            if denied:
                record_denial(reason=denied, program_id=program_id, user=caller)
                raise ProgramAccessDenied(denied)
        # A workspace program (labs.workspaces) scopes plans and groups by team and
        # keeps hard deletes for Program Managers. `workspace` is None -- and every
        # method below behaves exactly as it did -- for any other program; it costs
        # no query for a real one.
        from connect_labs.labs import workspaces

        self.workspace = workspaces.workspace_for_program(program_id)
        self.viewer = workspaces.role_in(caller, self.workspace) if self.workspace is not None else None
        self.caller = caller
        self._default_team = (team or "").strip()
        # Labs-only program: a synthetic opp surfaced in user_programs by
        # labs.context._merge_labs_only_opps as a program id in the reserved range
        # (>= LABS_ONLY_OPP_ID_FLOOR / 10_000; = the backing opp's id). Carry that
        # backing opp id so the LabsRecord API client short-circuits to the labs DB
        # (no prod round-trip, no membership check) — exactly like a synthetic
        # opportunity. Real programs (PKs below the floor) return False here and
        # are untouched, still hitting prod.
        if not kwargs.get("opportunity_id"):
            from connect_labs.labs.synthetic.local_records_backend import is_labs_only_opportunity_id

            if is_labs_only_opportunity_id(program_id):
                kwargs["opportunity_id"] = program_id
        super().__init__(program_id=program_id, **kwargs)

    @property
    def _experiment(self) -> str:
        return str(self.program_id)

    # ---- workspace roles (labs.workspaces) ----
    #
    # In a workspace program a plan or group carries ``data["team"]``: the Connect
    # org slug of the team that made it. A Network Manager reads and changes only
    # their teams' records; a Program Manager, everything. A record with no team
    # (made before the program was a workspace) is a Program Manager's. Outside a
    # workspace none of this applies and no ``team`` is written.

    @property
    def restricted_to_teams(self) -> bool:
        """Whether this caller sees only their own teams' records."""
        return self.viewer is not None and not self.viewer.is_program_manager

    @property
    def can_delete(self) -> bool:
        """Whether this caller may hard-delete here (everyone, outside a workspace)."""
        return self.workspace is None or (self.viewer is not None and self.viewer.is_program_manager)

    def _visible(self, record) -> bool:
        if record is None or not self.restricted_to_teams:
            return True
        return (record.data or {}).get("team") in self.viewer.teams

    def team_for_new_records(self, requested: str | None = None) -> str | None:
        """The team a new plan or group made by this caller belongs to; None outside a workspace.

        A Network Manager's is one of their teams -- the one asked for, else their
        first. A Program Manager's is whichever they ask for ("" = Program
        Managers only). A caller-less construction stamps what its view decided."""
        if self.workspace is None:
            return None
        requested = (requested or "").strip()[:200]
        if self.restricted_to_teams:
            return requested if requested in self.viewer.teams else self.viewer.teams[0]
        if self.viewer is not None:
            return requested
        return self._default_team

    def _require_visible_plans(self, plan_ids) -> None:
        """A Network Manager may not put another team's plan into a group."""
        if not self.restricted_to_teams or not plan_ids:
            return
        visible = {p.id for p in self.list_plans()}
        hidden = [int(p) for p in plan_ids if int(p) not in visible]
        if hidden:
            raise RecordNotInProgramError(f"plan(s) {hidden} are not in program {self.program_id}")

    def _load_plan(self, plan_id: int) -> PlanRecord:
        """``get_plan`` for a change: in a workspace, a plan the caller cannot see is
        refused rather than handed back as None (outside one, unchanged)."""
        plan = self.get_plan(plan_id)
        if plan is None and self.workspace is not None:
            raise RecordNotInProgramError(f"plan {plan_id} is not in program {self.program_id}")
        return plan

    def _load_group(self, group_id: int) -> PlanGroupRecord:
        group = self.get_group(group_id)
        if group is None and self.workspace is not None:
            raise RecordNotInProgramError(f"group {group_id} is not in program {self.program_id}")
        return group

    def _guard_delete(self, kind: str, record_id: int) -> None:
        """Refuse a hard delete in a workspace program to anyone but a Program
        Manager -- one check for the web views and the MCP tools alike, logged on
        the audit trail. Caller-less code is refused too: nothing legitimate
        deletes a workspace's records in the background."""
        if self.can_delete:
            return
        from connect_labs.audit_trail.models import Action, Outcome
        from connect_labs.audit_trail.service import record

        record(
            Action.ACCESS_DENIED,
            resource_type=kind,
            resource_id=record_id,
            program_id=self.program_id,
            labs_only=True,
            outcome=Outcome.FAILURE,
            status_code=403,
            metadata={"reason": "workspace delete requires a Program Manager"},
            user=self.caller,
        )
        raise WorkspaceDeleteForbidden("Only a Program Manager can delete in this program. Archive it instead.")

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
        stats: list | None = None,
        area_targets: dict | None = None,
        coverage_config: dict | None = None,
        coverage_stats: list | None = None,
        run_meta: dict | None = None,
        team: str | None = None,
    ) -> PlanRecord:
        """Create a Draft plan in the program from a generated frame (one work area
        per cluster/pin). ``input_areas`` is the original draw/admin/pin payload
        (stored so footprints overlay can reuse the cached fetch geometry).
        ``grouping`` is the Phase-1 strategy/params for cell→group bucketing
        (defaults to BFS adjacency — Connect-GIS parity).

        ``coverage_config``/``coverage_stats`` (coverage mode) persist the exact
        ``CoverageConfig`` used and the frame-level metrics the preview UI shows
        (fetched/after_filters/retained_buildings/per-ward breakdown, …) — so a
        created coverage plan records what params produced it and its stats without
        a footprint re-fetch (the coverage analogue of ``sampling_stats``).
        ``run_meta`` is a small provenance block (run_id/actor/created_at/…) so a
        batch of plans created together is groupable + reproducible.

        ``lga``/``state`` are the administrative labels Connect's work-area importer
        requires non-empty (see ``microplans/CONNECT_IMPORT_CONTRACT.md``); stored at
        creation so the Connect-import CSV export populates them without the caller
        re-supplying them. ``lga`` falls back to ``region`` when blank."""
        # Barrier-aware grouping needs road/river lines. Read them CACHE-ONLY here —
        # create runs in the web request and a cold Overture fetch would block it; the
        # cache was warmed on the (Celery) coverage preview that precedes create. Cold
        # cache → group_work_areas falls back to plain clustering.
        barriers = _barriers_if_needed(grouping, input_areas, allow_remote=False)
        work_areas = plan_lib.materialize_work_areas(mode, pins, hulls, grouping=grouping, barriers=barriers)
        data = {
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
            # Per-area populations by source ({area_id: {worldpop_u5: …, …}}), captured
            # from the picked boundaries so the review-page per-area visit table can
            # auto-fill expected-visit targets from a chosen population source (#15).
            # Keyed on the unique area_id (not ward name) so same-named areas keep
            # their own populations.
            "area_populations": {
                str(a.get("area_id")): a["populations"]
                for a in (input_areas or [])
                if a.get("area_id") and isinstance(a.get("populations"), dict)
            },
            "grouping": dict(grouping or {}),
            "status_log": [],
            # Optimistic-concurrency counter; bumped on every _save_plan.
            "revision": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        # Persist the sampling overlay (selected-PSU hulls + per-arm stats) so the
        # review page can redraw the surveyed settlements + Sample details on load —
        # the same keys regenerate_plan writes, so a created plan and a reopened plan
        # render identically. (Pins aren't stored: the work areas ARE the pins.)
        if mode == "sampling":
            if hulls is not None:
                data["psu_hulls"] = hulls
            if stats is not None:
                data["sampling_stats"] = stats
        # Coverage analogue: persist the config used + the frame stats the preview UI
        # shows, so a created plan records exactly what params produced it (needed to
        # compare parameter-tuning runs — regenerating stats means re-fetching roofs).
        if mode == "coverage":
            if coverage_config is not None:
                data["coverage_config"] = dict(coverage_config)
            if coverage_stats is not None:
                data["coverage_stats"] = coverage_stats
        if run_meta:
            data["run_meta"] = dict(run_meta)
        stamp = self.team_for_new_records(team)
        if stamp is not None:
            data["team"] = stamp
        # Per-ward expected-visit targets set in the setup planning table (the U5-for-
        # calc column). Stored + spread across each ward's retained buildings at
        # creation so visits are populated up front — the same math as the later
        # edit path (set_area_targets / apply_plan_edits). Keyed by the ward label the
        # frame attributed to each work area (ward name, or "Area N" for drawn shapes).
        clean_targets = {}
        for k, v in (area_targets or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                clean_targets[str(k)] = fv
        if clean_targets and mode == "coverage":
            data["area_targets"] = clean_targets
            plan_lib.recompute_area_visits(work_areas, clean_targets)
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            data=data,
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
        from connect_labs.microplans.core import assignment as assignment_lib

        plan = self._load_plan(plan_id)
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
                plan_lib.apply_action(w, "reassign", {"opportunity_access": new_workers[w["id"]]}, actor)
        data["work_areas"] = work_areas
        data["assignment"] = assignment
        return self._save_plan(plan, data, base_revision)

    def regroup_plan(self, plan_id: int, grouping: dict, actor: str, base_revision: int | None = None) -> PlanRecord:
        """Re-apply grouping (cells → work_area_group) to an existing plan.

        Phase 1 of the two-phase pipeline. Snapshots each cell's old group, runs
        the strategy, and then routes through ``apply_action("regroup", ...)`` so
        per-cell audits are appended consistently with manual regrouping.
        """
        from connect_labs.microplans.core import grouping as grouping_lib

        plan = self._load_plan(plan_id)
        data = dict(plan.data)
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        cfg = grouping_lib.GroupingConfig.from_payload(grouping)
        # Apply to ACTIVE cells; excluded ones keep their old group.
        active = [w for w in work_areas if w.get("status") != plan_lib.STATUS_EXCLUDED]
        # Regroup runs on the Celery worker, so a barrier fetch here CAN go remote
        # (cold Overture read + cache) — this is the main path for barrier-aware.
        barriers = _barriers_if_needed(grouping, data.get("input_areas"), allow_remote=True)
        # Snapshot old groups, then run the strategy + restore them so apply_action
        # can compute a real before→after diff and emit the audit.
        old_groups = {w["id"]: w.get("work_area_group", "") for w in active}
        grouping_lib.group_work_areas(active, cfg, barriers=barriers)
        new_groups = {w["id"]: w.get("work_area_group", "") for w in active}
        for w in active:
            w["work_area_group"] = old_groups[w["id"]]
            plan_lib.apply_action(w, "regroup", {"work_area_group": new_groups[w["id"]]}, actor)
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
        area_targets: dict | None = None,
        coverage_config: dict | None = None,
        coverage_stats: list | None = None,
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
        plan = self._load_plan(plan_id)
        data = dict(plan.data)
        # Regenerate runs on the Celery worker → barrier fetch may go remote.
        barriers = _barriers_if_needed(grouping, input_areas, allow_remote=True)
        work_areas = plan_lib.materialize_work_areas(mode, pins, hulls, grouping=grouping, barriers=barriers)
        data["work_areas"] = work_areas
        data["input_areas"] = list(input_areas or [])
        data["mode"] = mode
        data["grouping"] = dict(grouping or {})
        data["assignment"] = {}  # destructive reset — no CHWs carried over
        # Per-ward expected-visit targets from the setup planning table; re-spread over
        # the fresh work areas (destructive reset otherwise — same shape as create_plan).
        clean_targets = {}
        for k, v in (area_targets or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                clean_targets[str(k)] = fv
        data["area_targets"] = clean_targets
        if clean_targets and mode == "coverage":
            plan_lib.recompute_area_visits(work_areas, clean_targets)
        # Persist the selected-PSU hulls (sampling only) so the saved plan can show the
        # surveyed settlements on a map after creation, without re-fetching footprints.
        if mode == "sampling" and hulls is not None:
            data["psu_hulls"] = hulls
        if stats is not None:
            # Per-arm sampling summary (incl. PSU/building balance stats) for
            # cross-arm comparability; never shared/pushed to Connect.
            data["sampling_stats"] = stats
        # Coverage: re-capture the config + frame stats on a destructive regen so a
        # re-gridded plan records the params that produced its new layout (parity
        # with create_plan). Leave a prior capture untouched when not re-supplied.
        if mode == "coverage":
            if coverage_config is not None:
                data["coverage_config"] = dict(coverage_config)
            if coverage_stats is not None:
                data["coverage_stats"] = coverage_stats
        return self._save_plan(plan, data, base_revision)

    def list_plans(self) -> list[PlanRecord]:
        plans = self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN,
            program_id=self.program_id,
            model_class=PlanRecord,
        )
        return [p for p in plans if self._visible(p)] if self.restricted_to_teams else plans

    def get_plan(self, plan_id: int) -> PlanRecord:
        """The plan, or None -- also None for another team's plan in a workspace."""
        plan = self.labs_api.get_record_by_id(
            int(plan_id),
            experiment=self._experiment,
            type=TYPE_PLAN,
            model_class=PlanRecord,
        )
        return plan if self._visible(plan) else None

    def _save_plan(self, plan: PlanRecord, data: dict, base_revision: int | None = None) -> PlanRecord:
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
        plan = self._load_plan(plan_id)
        data = dict(plan.data)
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        for wa_id in wa_ids:
            wa = plan_lib.find(work_areas, wa_id)
            if wa is None:
                raise ValueError(f"work area {wa_id!r} not in plan {plan_id}")
            plan_lib.apply_action(wa, action, params, actor)
        # Exclusions change the retained-buildings denominator, so re-spread the
        # per-area expected-visit targets across what's left (#9).
        if action in ("exclude", "unexclude") and data.get("area_targets"):
            plan_lib.recompute_area_visits(work_areas, data["area_targets"])
        data["work_areas"] = work_areas
        return self._save_plan(plan, data, base_revision)

    def set_area_targets(
        self,
        plan_id: int,
        targets: dict,
        actor: str,
        base_revision: int | None = None,
    ) -> PlanRecord:
        """Store per-area expected-visit targets {ward: total_expected_visits} on the
        plan and re-spread them across each area's retained buildings (#9/#15)."""
        plan = self._load_plan(plan_id)
        data = dict(plan.data)
        clean = {}
        for k, v in (targets or {}).items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                clean[str(k)] = fv
        data["area_targets"] = clean
        work_areas = [dict(w) for w in data.get("work_areas", [])]
        plan_lib.recompute_area_visits(work_areas, clean)
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
        plan = self._load_plan(plan_id)
        data = dict(plan.data)
        plan_lib.transition_plan(data, to, actor, opportunity_id=opportunity_id)
        return self._save_plan(plan, data, base_revision)

    def delete_plan(self, plan_id: int) -> None:
        """Hard-delete a plan record. Use sparingly — Archive (status transition) is
        the safer default for normal lifecycle. This is for wiping sample data.

        Reads the plan scoped to this program first (``get_plan`` sends program_id,
        so the prod GET only returns it if it's in this program and the caller is a
        member). Refuses if it's not ours — never deletes by raw id. See
        :class:`RecordNotInProgramError`. In a workspace program only a Program
        Manager may (:class:`WorkspaceDeleteForbidden`)."""
        self._guard_delete(TYPE_PLAN, int(plan_id))
        if self.get_plan(int(plan_id)) is None:
            raise RecordNotInProgramError(f"plan {plan_id} is not in program {self.program_id}")
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
        team: str | None = None,
    ) -> PlanGroupRecord:
        """Create a plan group. ``kind="study"`` + ``arms`` make it a controlled
        study (arm assignment is labs-side — never written onto plans)."""
        self._require_visible_plans(plan_ids)
        data = {
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
        }
        stamp = self.team_for_new_records(team)
        if stamp is not None:
            data["team"] = stamp
        record = self.labs_api.create_record(
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            data=data,
        )
        return PlanGroupRecord(record.to_api_dict())

    def list_groups(self) -> list[PlanGroupRecord]:
        groups = self.labs_api.get_records(
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            program_id=self.program_id,
            model_class=PlanGroupRecord,
        )
        return [g for g in groups if self._visible(g)] if self.restricted_to_teams else groups

    def get_group(self, group_id: int) -> PlanGroupRecord:
        """The group, or None -- also None for another team's group in a workspace."""
        group = self.labs_api.get_record_by_id(
            int(group_id),
            experiment=self._experiment,
            type=TYPE_PLAN_GROUP,
            model_class=PlanGroupRecord,
        )
        return group if self._visible(group) else None

    def update_group(self, group_id: int, **fields) -> PlanGroupRecord:
        """``team`` moves a workspace group to another team -- a Program Manager's
        call only; it is ignored for anyone else and outside a workspace."""
        group = self._load_group(group_id)
        if fields.get("plan_ids") is not None:
            # Only what is being ADDED must be the caller's to add.
            self._require_visible_plans({int(p) for p in fields["plan_ids"]} - set(group.plan_ids))
        data = dict(group.data)
        is_pm = self.viewer is not None and self.viewer.is_program_manager
        if fields.get("team") is not None and self.workspace is not None and is_pm:
            data["team"] = str(fields["team"]).strip()[:200]
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
        group = self._load_group(group_id)
        plan_id = int(plan_id)
        self._require_visible_plans([plan_id])
        if plan_id in group.plan_ids:
            return group
        return self.update_group(group_id, plan_ids=[*group.plan_ids, plan_id])

    def remove_plan_from_group(self, group_id: int, plan_id: int) -> PlanGroupRecord:
        """Remove ``plan_id`` from the group, dropping any arm assignment for it."""
        group = self._load_group(group_id)
        plan_id = int(plan_id)
        new_ids = [p for p in group.plan_ids if p != plan_id]
        new_arms = {k: v for k, v in group.arms.items() if k != str(plan_id)}
        return self.update_group(group_id, plan_ids=new_ids, arms=new_arms)

    def delete_group(self, group_id: int) -> None:
        """Hard-delete a plan group record. Use sparingly. Reads it scoped to this
        program first and refuses if it's not ours (see :class:`RecordNotInProgramError`).
        In a workspace program only a Program Manager may (:class:`WorkspaceDeleteForbidden`)."""
        self._guard_delete(TYPE_PLAN_GROUP, int(group_id))
        if self.get_group(int(group_id)) is None:
            raise RecordNotInProgramError(f"group {group_id} is not in program {self.program_id}")
        self.labs_api.delete_record(int(group_id))
