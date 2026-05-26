"""MCP tool: ``program_admin_demo_seed_v2`` — narrative-driven synthetic generator
for the Program Admin Report demo.

(Tool name retained for API stability; the implementation is the synthetic
data generator, not a one-shot DB seeder. Internal helpers + docs use
"generator" / "generate".)

Replaces the v1 ``program_admin_demo_seed`` with a much richer config that
drives a believable multi-opp story:

- Per-opp FLW roster with FLW archetypes
  (``solid`` / ``improver_*`` / ``suspended_*`` / ``new_hire``)
- Backdated weekly workflow_runs (each on its actual Monday)
- Audit + Task records generated via the named **audit_archetype** and
  **task_archetype** vocabulary in
  ``commcare_connect/labs/synthetic/archetypes.py``. Audits attach real
  MUAC stock images from the corpus (see ``docs/synthetic-data/audit-corpus.md``)
  so labs's bulk-assessment view renders thumbnails + pass/fail outcomes,
  not a blank "0 assessments" page.
- Optional "missed week" per opp so the NO-RUN cell variant shows up
- Repeatable: ``cleanup_first=True`` wipes prior workflow data for the opps
  so the demo can be re-generated without accumulating stale records

The renderer (program_admin_report.py) reads the same Decision contract; this
generator constructs records via direct LabsRecord writes so it can:
- Backdate ``completed_at`` (the higher-level ``complete_run`` helper uses
  wall-clock ``now``)
- Stamp Task close events at custom timestamps
- Attach archetype-appropriate image sets to AuditSession records

See docs/superpowers/specs/2026-05-25-program-admin-report-design.md.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Archetype semantics
# -----------------------------------------------------------------------------

ARCHETYPES = (
    "solid",
    "improver_closed_satisfactory",
    "improver_warned",
    "improver_in_progress",
    "suspended_repeat_offense",
    "suspended_fraudulent",
    "new_hire",
)

REASON_LABELS = {
    "bad_muac_distribution": "Bad MUAC distribution",
    "gender_skew": "Gender split off threshold",
    "misleading_photos": "Misleading MUAC photos (suspected fraud)",
    "repeated_failure": "Repeat failure on prior coaching",
}


def _connect_token(user):
    from ..connect_token import require_connect_token

    return require_connect_token(user)


def _monday_dt(monday_iso: str, hour: int = 9, minute: int = 0) -> dt.datetime:
    """Return a TZ-aware datetime at HH:MM UTC on the given ISO date."""
    d = dt.date.fromisoformat(monday_iso)
    return dt.datetime.combine(d, dt.time(hour, minute), tzinfo=dt.timezone.utc)


# -----------------------------------------------------------------------------
# Cleanup
# -----------------------------------------------------------------------------


def _cleanup_opportunity(wda, dda, tda, ada, opportunity_id: int):
    """Delete prior workflow_runs + decisions + tasks + audits tied to the opp's
    chc_nutrition_analysis (and program_admin_report) workflow defs.

    Idempotent. Logs counts deleted.
    """
    deleted = {"workflow_runs": 0, "decisions": 0, "tasks": 0, "audits": 0, "definitions": 0}

    # Find chc_nutrition workflow definitions for this opp
    defs = [
        d
        for d in wda.list_definitions()
        if d.opportunity_id == opportunity_id and d.template_type in ("chc_nutrition_analysis", "program_admin_report")
    ]

    run_ids: set[int] = set()
    for d in defs:
        for r in wda.list_runs(definition_id=d.id):
            if r.opportunity_id == opportunity_id:
                run_ids.add(r.id)

    # Delete decisions linked to these runs
    if run_ids:
        for d in dda.get_decisions_for_opportunity_or_all(opportunity_id):
            if d.workflow_run_id in run_ids:
                wda.labs_api.delete_records([d.id])
                deleted["decisions"] += 1

        # Delete tasks
        all_tasks = tda.get_tasks()
        for t in all_tasks:
            if t.workflow_run_id in run_ids:
                wda.labs_api.delete_records([t.id])
                deleted["tasks"] += 1

        # Delete audits (by labs_record_id == workflow_run_id linkage, and by data.workflow_run_id)
        all_audits = ada.list_records()
        for a in all_audits:
            wf_run_id = a.data.get("workflow_run_id") or a.labs_record_id
            if wf_run_id in run_ids:
                wda.labs_api.delete_records([a.id])
                deleted["audits"] += 1

        # Delete the workflow_runs themselves
        wda.labs_api.delete_records(list(run_ids))
        deleted["workflow_runs"] = len(run_ids)

    return deleted


# -----------------------------------------------------------------------------
# Per-archetype trajectory
# -----------------------------------------------------------------------------


def _decisions_for_flw_across_weeks(flw: dict, week_count: int) -> list[dict | None]:
    """Return a list of per-week "decision specs" (or None if FLW is not active that week).

    Each spec dict has keys understood by ``_apply_decision_spec``:
        - ``decision_type``: "no_issues" | "action_taken"
        - ``reason_key`` (action_taken only)
        - ``reason_label``
        - ``audit_archetype`` (str — key in AUDIT_ARCHETYPES), or None
        - ``task_archetype`` (str — key in TASK_ARCHETYPES), or None

    Returning None for a week means the FLW is not in the roster that week
    (new hires before they join, suspended FLWs after they're suspended).

    The per-FLW-archetype → (audit_archetype, task_archetype) mapping below
    is the single place to tune what an "improver_warned" or "suspended_repeat
    _offense" trajectory looks like in terms of concrete audit/task evidence.
    """
    archetype = flw["archetype"]
    out: list[dict | None] = [None] * week_count

    def flag(reason: str, audit_arche: str, task_arche: str) -> dict:
        return {
            "decision_type": "action_taken",
            "reason_key": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
            "audit_archetype": audit_arche,
            "task_archetype": task_arche,
        }

    no_issues = {"decision_type": "no_issues", "audit_archetype": None, "task_archetype": None}

    if archetype == "solid":
        for i in range(week_count):
            out[i] = no_issues

    elif archetype == "new_hire":
        joined = flw.get("joined_week", week_count - 1)
        for i in range(joined, week_count):
            out[i] = no_issues

    elif archetype in ("improver_closed_satisfactory", "improver_warned"):
        flag_week = flw.get("flag_week", 0)
        reason = flw.get("reason_key", "bad_muac_distribution")
        if archetype == "improver_closed_satisfactory":
            audit_arche, task_arche = "completed_pass_clean", "closed_satisfactory"
        else:
            audit_arche, task_arche = "completed_mixed_tape_usage", "closed_warned"
        for i in range(week_count):
            if i < flag_week:
                out[i] = no_issues
            elif i == flag_week:
                out[i] = flag(reason, audit_arche, task_arche)
            else:
                out[i] = no_issues

    elif archetype == "improver_in_progress":
        flag_week = flw.get("flag_week", 0)
        reason = flw.get("reason_key", "bad_muac_distribution")
        for i in range(week_count):
            if i < flag_week:
                out[i] = no_issues
            elif i == flag_week:
                out[i] = flag(reason, "in_review_partial", "investigating")
            else:
                out[i] = no_issues

    elif archetype in ("suspended_repeat_offense", "suspended_fraudulent"):
        first = flw.get("first_flag_week", 0)
        last = flw.get("second_flag_week", first + 2)
        is_fraud = archetype == "suspended_fraudulent"
        reason = flw.get(
            "reason_key",
            "misleading_photos" if is_fraud else "bad_muac_distribution",
        )
        # First flag: tape_usage / misleading audit (fail) + warned task.
        first_audit = "completed_fail_misleading" if is_fraud else "completed_fail_tape_usage"
        # Second flag: still failing → suspended task. Fraud variant uses the
        # fraud-framed coaching transcript; repeat-failure variant uses the
        # standard repeat-offense one.
        second_audit = first_audit
        suspension_task = "closed_suspended_fraud" if is_fraud else "closed_suspended"
        for i in range(week_count):
            if i < first:
                out[i] = no_issues
            elif i == first:
                out[i] = flag(reason, first_audit, "closed_warned")
            elif i < last:
                out[i] = no_issues
            elif i == last:
                out[i] = flag("repeated_failure", second_audit, suspension_task)
            else:
                out[i] = None  # removed from roster after suspension
    else:
        raise MCPToolError("INVALID_INPUT", f"Unknown archetype: {archetype!r}")

    return out


# -----------------------------------------------------------------------------
# Record builders (direct LabsRecord writes to bypass wall-clock now())
# -----------------------------------------------------------------------------


def _create_backdated_workflow_run(
    *,
    wda,
    definition_id: int,
    opportunity_id: int,
    monday_iso: str,
    flws_active: list[dict] | None = None,
    week_idx: int = 0,
) -> int:
    """Write a workflow_run record directly with status=completed +
    backdated completed_at, and (optionally) a CHC Nutrition pipeline
    snapshot with one row per active FLW.

    Without the pipeline snapshot, "Open the run" from the Program Admin
    Report lands on an empty "No data available" table — there's no real
    FLW visit data in the synthetic opp to back the live pipeline. The
    rows we synthesise here let the chc_nutrition table render properly,
    with per-FLW decision pills + state-aware action buttons.

    ``flws_active`` is a list of dicts (each at minimum has ``id``,
    ``archetype``, and the flag-week metadata used by
    ``_decisions_for_flw_across_weeks``). ``week_idx`` lets us infer
    whether the FLW was in a flagged state this week so the synthetic
    MUAC distribution matches the narrative.
    """
    from commcare_connect.labs.synthetic.archetypes import build_flw_pipeline_row

    completed_at = _monday_dt(monday_iso, hour=9, minute=0).isoformat()
    period_end = (dt.date.fromisoformat(monday_iso) + dt.timedelta(days=6)).isoformat()

    pipeline_rows: list[dict] = []
    if flws_active:
        for flw in flws_active:
            archetype = flw["archetype"]
            # Did this FLW get flagged this week? Use the trajectory builder
            # to derive the per-week decision spec.
            specs = _decisions_for_flw_across_weeks(flw, week_count=week_idx + 1)
            spec_this_week = specs[week_idx] if week_idx < len(specs) else None
            if spec_this_week is None:
                continue  # FLW not on roster this week
            flagged = spec_this_week.get("decision_type") == "action_taken"
            # Seed: stable per (opp, flw, week) so regenerations are deterministic.
            seed = hash((opportunity_id, flw["id"], week_idx)) & 0xFFFFFFFF
            pipeline_rows.append(
                build_flw_pipeline_row(
                    flw_id=flw["id"],
                    archetype=archetype,
                    flagged_this_week=flagged,
                    rng_seed=seed,
                )
            )

    snapshot_state = {"period_start": monday_iso, "period_end": period_end}
    # chc_nutrition_analysis reads `pipelines.data.rows` (not `pipelines.data`)
    # because the runtime pipelines dict wraps rows in a {rows: [...]} object.
    # Match that exact shape so the saved snapshot replay works the same way.
    snapshot_pipelines = {"data": {"rows": pipeline_rows}} if pipeline_rows else {}

    data = {
        "definition_id": definition_id,
        "opportunity_id": opportunity_id,
        "status": "completed",
        "completed_at": completed_at,
        "period_start": monday_iso,
        "period_end": period_end,
        "state": snapshot_state,
        # Snapshot consumed by chc_nutrition's `view.pipelines.data` reads.
        # Decisions are NOT in the snapshot — they're queried live via
        # view.decisionsFor() against the separate Decision LabsRecord rows.
        "snapshot": {"workers": [], "pipelines": snapshot_pipelines, "state": snapshot_state},
    }
    rec = wda.labs_api.create_record(
        experiment="workflow",
        type="workflow_run",
        data=data,
    )
    return rec.id


# Counter used to mint unique synthetic visit_ids per audit. Real visit_ids
# would be unique UserVisit row IDs; the synthetic generator has no live
# visits to point at, but BulkAssessmentView keys visit_images on visit_id,
# so we just need a unique int per audit.
_visit_id_counter = 9_000_000


def _next_visit_id() -> int:
    global _visit_id_counter
    _visit_id_counter += 1
    return _visit_id_counter


def _generate_audit(
    *,
    ada,
    opportunity_id: int,
    opportunity_name: str,
    workflow_run_id: int,
    flw_id: str,
    monday_iso: str,
    audit_archetype: str,
) -> int:
    """Generate an AuditSession record from a named audit archetype.

    Returns the audit id. The archetype controls status / overall_result /
    image set (real blob_ids backed by the MUAC stock corpus); see
    ``commcare_connect/labs/synthetic/archetypes.py``.
    """
    from commcare_connect.labs.synthetic.archetypes import build_audit_data

    data = build_audit_data(
        archetype_name=audit_archetype,
        flw_id=flw_id,
        monday_iso=monday_iso,
        opportunity_id=opportunity_id,
        opportunity_name=opportunity_name,
        workflow_run_id=workflow_run_id,
        visit_id_base=_next_visit_id(),
    )
    rec = ada.labs_api.create_record(
        experiment="audit",
        type="AuditSession",
        data=data,
        labs_record_id=workflow_run_id,
        username=flw_id,
    )
    return rec.id


def _generate_task(
    *,
    tda,
    opportunity_id: int,
    workflow_run_id: int,
    audit_session_id: int | None,
    flw_id: str,
    monday_iso: str,
    title: str,
    task_archetype: str,
    creator_name: str,
) -> int:
    """Generate a Task record from a named task archetype.

    Returns the task id. The archetype controls status / official_action /
    close timing; see ``commcare_connect/labs/synthetic/archetypes.py``.
    """
    from commcare_connect.labs.synthetic.archetypes import build_task_data

    data = build_task_data(
        archetype_name=task_archetype,
        flw_id=flw_id,
        monday_iso=monday_iso,
        opportunity_id=opportunity_id,
        workflow_run_id=workflow_run_id,
        audit_session_id=audit_session_id,
        title=title,
        creator_name=creator_name,
    )
    rec = tda.labs_api.create_record(
        experiment="tasks",
        type="Task",
        data=data,
        username=flw_id,
    )
    return rec.id


def _apply_decision_spec(
    *,
    dda,
    tda,
    ada,
    spec: dict,
    workflow_run_id: int,
    opportunity_id: int,
    opportunity_name: str,
    flw_id: str,
    monday_iso: str,
    creator_name: str,
):
    """Materialize one (run, flw) decision spec into records.

    If the spec is an ``action_taken`` and specifies an ``audit_archetype``
    and/or ``task_archetype``, we generate those records (using the named
    audit + task archetypes in ``commcare_connect/labs/synthetic/archetypes.py``)
    and link them from the Decision."""
    decision_type = spec["decision_type"]
    audit_ids: list[int] = []
    task_ids: list[int] = []

    if decision_type == "action_taken":
        audit_archetype = spec.get("audit_archetype")
        task_archetype = spec.get("task_archetype")
        spawned_audit_id: int | None = None
        if audit_archetype:
            spawned_audit_id = _generate_audit(
                ada=ada,
                opportunity_id=opportunity_id,
                opportunity_name=opportunity_name,
                workflow_run_id=workflow_run_id,
                flw_id=flw_id,
                monday_iso=monday_iso,
                audit_archetype=audit_archetype,
            )
            audit_ids.append(spawned_audit_id)
        if task_archetype:
            task_title = f"[{spec.get('reason_label', spec.get('reason_key', 'Action'))}] {flw_id}"
            task_ids.append(
                _generate_task(
                    tda=tda,
                    opportunity_id=opportunity_id,
                    workflow_run_id=workflow_run_id,
                    audit_session_id=spawned_audit_id,
                    flw_id=flw_id,
                    monday_iso=monday_iso,
                    title=task_title,
                    task_archetype=task_archetype,
                    creator_name=creator_name,
                )
            )

    decided_at = _monday_dt(monday_iso, hour=11, minute=0).isoformat()
    dda.create_decision(
        workflow_run_id=workflow_run_id,
        opportunity_id=opportunity_id,
        flw_id=flw_id,
        decision_type=decision_type,
        reason_key=spec.get("reason_key"),
        reason_label=spec.get("reason_label"),
        audit_session_ids=audit_ids,
        task_ids=task_ids,
        decided_at=decided_at,
        decided_by=creator_name,
        notes=f"Synthetic week {monday_iso}",
    )


# -----------------------------------------------------------------------------
# Audit DAO helper (minimal — list records for opp scope)
# -----------------------------------------------------------------------------


def _list_audits(ada):
    """Helper to list all AuditSession records the access layer can see."""
    from commcare_connect.audit.models import AuditSessionRecord

    return ada.labs_api.get_records(
        experiment="audit",
        type="AuditSession",
        model_class=AuditSessionRecord,
    )


def _list_decisions_for_opp(dda, opportunity_id: int):
    """All Decision records visible at the opp scope."""
    from commcare_connect.decisions.models import DecisionRecord

    return dda.labs_api.get_records(
        experiment="decisions",
        type="Decision",
        model_class=DecisionRecord,
    )


# -----------------------------------------------------------------------------
# Tool registration
# -----------------------------------------------------------------------------


@register(
    name="program_admin_demo_seed_v2",
    description=(
        "Narrative-driven synthetic generator for the program-admin-report demo. "
        "Builds 4 weekly chc_nutrition saved runs per opp with backdated "
        "completed_at, applies per-FLW archetype trajectories (solid / "
        "improver_* / suspended_* / new_hire), generates AuditSession + Task "
        "records from named **audit_archetype** + **task_archetype** vocabularies "
        "(see commcare_connect/labs/synthetic/archetypes.py), and creates a "
        "final program_admin_report run watching all opps. Audits attach real "
        "MUAC stock images so the bulk-assessment view renders thumbnails. "
        "Pass cleanup_first=true to wipe prior runs/decisions/tasks/audits for "
        "the opps before regenerating (idempotent). Tool name retained as "
        "*_seed_v2 for API stability."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "cleanup_first": {"type": "boolean", "default": True},
            "weeks": {
                "type": "array",
                "items": {"type": "string", "description": "ISO Monday date"},
                "minItems": 1,
            },
            "opps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "opportunity_id": {"type": "integer"},
                        "label": {"type": "string"},
                        "network_manager": {"type": "string"},
                        "missed_week_idxs": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "default": [],
                        },
                        "flws": {
                            "type": "array",
                            "items": {"type": "object"},  # validated dynamically
                            "minItems": 1,
                        },
                    },
                    "required": ["opportunity_id", "label", "network_manager", "flws"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
        },
        "required": ["weeks", "opps"],
        "additionalProperties": False,
    },
    is_write=True,
)
def program_admin_demo_seed_v2(
    user,
    *,
    weeks: list[str],
    opps: list[dict],
    cleanup_first: bool = True,
) -> dict[str, Any]:
    from commcare_connect.audit.data_access import AuditDataAccess
    from commcare_connect.decisions.data_access import DecisionsDataAccess
    from commcare_connect.tasks.data_access import TaskDataAccess
    from commcare_connect.workflow.data_access import WorkflowDataAccess
    from commcare_connect.workflow.templates import create_workflow_from_template

    token = _connect_token(user)
    week_count = len(weeks)

    # Patch DecisionsDataAccess + AuditDataAccess with the helpers we need
    DecisionsDataAccess.get_decisions_for_opportunity_or_all = lambda self, opp_id: _list_decisions_for_opp(
        self, opp_id
    )
    AuditDataAccess.list_records = lambda self: _list_audits(self)

    summary: dict[str, Any] = {"opportunities": [], "program_admin_report": None}
    watched_sources: list[dict] = []

    for opp_cfg in opps:
        opp_id = opp_cfg["opportunity_id"]
        flws = opp_cfg["flws"]
        missed = set(opp_cfg.get("missed_week_idxs", []))
        nm = opp_cfg["network_manager"]

        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=token)
        dda = DecisionsDataAccess(opportunity_id=opp_id, access_token=token)
        tda = TaskDataAccess(opportunity_id=opp_id, access_token=token)
        ada = AuditDataAccess(opportunity_id=opp_id, access_token=token)
        try:
            cleanup_counts = None
            if cleanup_first:
                cleanup_counts = _cleanup_opportunity(wda, dda, tda, ada, opp_id)

            # Find or create chc_nutrition definition
            existing_defs = [
                d
                for d in wda.list_definitions()
                if d.opportunity_id == opp_id and d.template_type == "chc_nutrition_analysis"
            ]
            if existing_defs:
                definition = existing_defs[0]
            else:
                definition, _, _ = create_workflow_from_template(wda, template_key="chc_nutrition_analysis")

            watched_sources.append({"opportunity_id": opp_id, "workflow_definition_id": definition.id})

            # Precompute per-FLW trajectory across the window
            per_flw_specs = {f["id"]: _decisions_for_flw_across_weeks(f, week_count) for f in flws}

            week_summaries = []
            for week_idx, monday_iso in enumerate(weeks):
                if week_idx in missed:
                    week_summaries.append({"week": monday_iso, "ran": False})
                    continue

                run_id = _create_backdated_workflow_run(
                    wda=wda,
                    definition_id=definition.id,
                    opportunity_id=opp_id,
                    monday_iso=monday_iso,
                    flws_active=flws,
                    week_idx=week_idx,
                )
                decisions_made = 0
                tasks_spawned = 0
                audits_spawned = 0
                active_flw_count = 0
                for flw in flws:
                    spec = per_flw_specs[flw["id"]][week_idx]
                    if spec is None:
                        continue  # not in roster this week
                    active_flw_count += 1
                    if spec.get("task_archetype"):
                        tasks_spawned += 1
                    if spec.get("audit_archetype"):
                        audits_spawned += 1
                    _apply_decision_spec(
                        dda=dda,
                        tda=tda,
                        ada=ada,
                        spec=spec,
                        workflow_run_id=run_id,
                        opportunity_id=opp_id,
                        opportunity_name=opp_cfg["label"],
                        flw_id=flw["id"],
                        monday_iso=monday_iso,
                        creator_name=nm,
                    )
                    decisions_made += 1

                week_summaries.append(
                    {
                        "week": monday_iso,
                        "ran": True,
                        "run_id": run_id,
                        "decisions": decisions_made,
                        "tasks_spawned": tasks_spawned,
                        "audits_spawned": audits_spawned,
                        "active_flws": active_flw_count,
                    }
                )

            summary["opportunities"].append(
                {
                    "opportunity_id": opp_id,
                    "label": opp_cfg["label"],
                    "network_manager": nm,
                    "workflow_definition_id": definition.id,
                    "cleanup_counts": cleanup_counts,
                    "weeks": week_summaries,
                }
            )
        finally:
            wda.close()
            dda.close()
            tda.close()
            ada.close()

    # Create / find program_admin_report definition watching all opps
    primary_opp_id = opps[0]["opportunity_id"]
    par_wda = WorkflowDataAccess(opportunity_id=primary_opp_id, access_token=token)
    try:
        existing_par_defs = [
            d
            for d in par_wda.list_definitions()
            if d.opportunity_id == primary_opp_id and d.template_type == "program_admin_report"
        ]
        if existing_par_defs:
            par_def = existing_par_defs[0]
            # Update config to reflect current watched_sources
            updated = {**par_def.data}
            updated.setdefault("config", {})
            updated["config"]["watched_sources"] = watched_sources
            par_wda.update_definition(definition_id=par_def.id, data=updated)
            par_def = par_wda.get_definition(par_def.id)
        else:
            par_def, _, _ = create_workflow_from_template(
                par_wda,
                template_key="program_admin_report",
                opportunity_ids=[s["opportunity_id"] for s in watched_sources],
            )
            updated = {**par_def.data}
            updated.setdefault("config", {})
            updated["config"]["watched_sources"] = watched_sources
            par_wda.update_definition(definition_id=par_def.id, data=updated)
            par_def = par_wda.get_definition(par_def.id)

        # Backdated PAR run — completed at the last week's Monday + 1d
        last_monday = weeks[-1]
        par_completed_at = (_monday_dt(last_monday) + dt.timedelta(days=1)).isoformat()
        window_start = weeks[0]
        # End window at today+1 so the filter actually catches the seeded runs
        # (their completed_at is the historical Monday but we want a wide
        # filter for safety — the seeder backdates so old data lands in window).
        window_end = (dt.date.today() + dt.timedelta(days=1)).isoformat()

        # Build the rollup snapshot via the template hook
        from commcare_connect.workflow.templates.program_admin_report import build_snapshot

        snapshot = build_snapshot(
            pipelines={},
            state={
                "window_start": window_start,
                "window_end": window_end,
                "watched_sources": watched_sources,
                "weeks": weeks,
            },
            opportunity_id=primary_opp_id,
            workers=[],
            opportunity_ids=[s["opportunity_id"] for s in watched_sources],
            definition_id=par_def.id,
            access_token=token,
        )
        # Inject the expected-weeks list + display labels into the snapshot
        # so render code can render columns for each canonical Monday.
        if "state" in snapshot:
            snapshot["state"]["expected_weeks"] = weeks
            snapshot["state"]["display_window_start"] = weeks[0]
            snapshot["state"]["display_window_end"] = (
                dt.date.fromisoformat(weeks[-1]) + dt.timedelta(days=6)
            ).isoformat()
            # Also store NM / label per source so render code can render them
            label_by_opp = {o["opportunity_id"]: o for o in opps}
            for src in snapshot["state"].get("watched_summary", []):
                meta = label_by_opp.get(src["opportunity_id"], {})
                src["label"] = meta.get("label", f"Opp #{src['opportunity_id']}")
                src["network_manager"] = meta.get("network_manager", "")
                src["flw_count"] = len(meta.get("flws", []))
                src["missed_week_idxs"] = meta.get("missed_week_idxs", [])

        # Write the PAR run directly with backdated completed_at
        run_data = {
            "definition_id": par_def.id,
            "opportunity_id": primary_opp_id,
            "status": "completed",
            "completed_at": par_completed_at,
            "period_start": window_start,
            "period_end": (dt.date.fromisoformat(weeks[-1]) + dt.timedelta(days=6)).isoformat(),
            "state": {
                "window_start": window_start,
                "window_end": window_end,
                "watched_sources": watched_sources,
                "weeks": weeks,
            },
            "snapshot": snapshot,
        }
        par_rec = par_wda.labs_api.create_record(
            experiment="workflow",
            type="workflow_run",
            data=run_data,
        )

        summary["program_admin_report"] = {
            "definition_id": par_def.id,
            "run_id": par_rec.id,
            "window_start": window_start,
            "window_end": window_end,
            "watched_sources_count": len(watched_sources),
            "snapshot_summary_count": len(snapshot.get("state", {}).get("watched_summary", [])),
            "snapshot_total_runs": sum(
                len(src.get("runs", [])) for src in snapshot.get("state", {}).get("watched_summary", [])
            ),
            "report_url": f"/labs/workflow/{par_def.id}/run/?run_id={par_rec.id}",
        }
    finally:
        par_wda.close()

    return summary
