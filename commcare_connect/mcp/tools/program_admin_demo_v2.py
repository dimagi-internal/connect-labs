"""MCP tool: program_admin_demo_seed_v2 — narrative-driven demo seeder.

Replaces the v1 ``program_admin_demo_seed`` with a much richer config that
drives a believable multi-opp story:

- Per-opp FLW roster with archetypes (solid / improver_* / suspended_* / new_hire)
- Backdated weekly workflow_runs (each on its actual Monday)
- Audit + Task records spawned with realistic open/closed states
- Optional "missed week" per opp so the NO-RUN cell variant shows up
- Repeatable: ``cleanup_first=True`` wipes prior workflow data for the opps
  so the demo can be re-run without accumulating stale records

The renderer (program_admin_report.py) reads the same Decision contract; this
tool just constructs the records via direct LabsRecord writes so it can:
- Backdate ``completed_at`` (the higher-level ``complete_run`` helper uses
  wall-clock ``now``)
- Stamp Task close events at custom timestamps
- Set arbitrary AuditSession status/outcome counts

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
        - ``spawn_task`` (bool)
        - ``task_outcome`` ("open" | "warned" | "satisfactory" | "suspended")
        - ``task_close_delay_days`` (int, only for closed tasks)
        - ``spawn_audit`` (bool)
        - ``audit_outcome`` ("in_review" | "completed_pass" | "completed_fail" | "completed_mixed")

    Returning None for a week means the FLW is not in the roster that week
    (new hires before they join, suspended FLWs after they're suspended).
    """
    archetype = flw["archetype"]
    out: list[dict | None] = [None] * week_count

    if archetype == "solid":
        for i in range(week_count):
            out[i] = {"decision_type": "no_issues"}

    elif archetype == "new_hire":
        joined = flw.get("joined_week", week_count - 1)
        for i in range(joined, week_count):
            out[i] = {"decision_type": "no_issues"}

    elif archetype in ("improver_closed_satisfactory", "improver_warned"):
        flag_week = flw.get("flag_week", 0)
        reason = flw.get("reason_key", "bad_muac_distribution")
        outcome = "satisfactory" if archetype == "improver_closed_satisfactory" else "warned"
        for i in range(week_count):
            if i < flag_week:
                out[i] = {"decision_type": "no_issues"}
            elif i == flag_week:
                out[i] = {
                    "decision_type": "action_taken",
                    "reason_key": reason,
                    "reason_label": REASON_LABELS.get(reason, reason),
                    "spawn_task": True,
                    "task_outcome": outcome,
                    "task_close_delay_days": 6,
                    "spawn_audit": True,
                    "audit_outcome": "completed_pass" if outcome == "satisfactory" else "completed_mixed",
                }
            else:
                out[i] = {"decision_type": "no_issues"}

    elif archetype == "improver_in_progress":
        flag_week = flw.get("flag_week", 0)
        reason = flw.get("reason_key", "bad_muac_distribution")
        for i in range(week_count):
            if i < flag_week:
                out[i] = {"decision_type": "no_issues"}
            elif i == flag_week:
                out[i] = {
                    "decision_type": "action_taken",
                    "reason_key": reason,
                    "reason_label": REASON_LABELS.get(reason, reason),
                    "spawn_task": True,
                    "task_outcome": "open",
                    "spawn_audit": True,
                    "audit_outcome": "in_review",
                }
            else:
                out[i] = {"decision_type": "no_issues"}

    elif archetype in ("suspended_repeat_offense", "suspended_fraudulent"):
        first = flw.get("first_flag_week", 0)
        last = flw.get("second_flag_week", first + 2)
        reason = flw.get(
            "reason_key",
            "misleading_photos" if archetype == "suspended_fraudulent" else "bad_muac_distribution",
        )
        for i in range(week_count):
            if i < first:
                out[i] = {"decision_type": "no_issues"}
            elif i == first:
                out[i] = {
                    "decision_type": "action_taken",
                    "reason_key": reason,
                    "reason_label": REASON_LABELS.get(reason, reason),
                    "spawn_task": True,
                    "task_outcome": "warned",
                    "task_close_delay_days": 5,
                    "spawn_audit": True,
                    "audit_outcome": "completed_fail",
                }
            elif i < last:
                out[i] = {"decision_type": "no_issues"}
            elif i == last:
                out[i] = {
                    "decision_type": "action_taken",
                    "reason_key": "repeated_failure",
                    "reason_label": REASON_LABELS["repeated_failure"],
                    "spawn_task": True,
                    "task_outcome": "suspended",
                    "task_close_delay_days": 2,
                    "spawn_audit": True,
                    "audit_outcome": "completed_fail",
                }
            else:
                out[i] = None  # removed from roster after suspension
    else:
        raise MCPToolError("INVALID_INPUT", f"Unknown archetype: {archetype!r}")

    return out


# -----------------------------------------------------------------------------
# Record builders (direct LabsRecord writes to bypass wall-clock now())
# -----------------------------------------------------------------------------


def _create_backdated_workflow_run(*, wda, definition_id: int, opportunity_id: int, monday_iso: str) -> int:
    """Write a workflow_run record directly with status=completed +
    backdated completed_at. Returns the new record id."""
    completed_at = _monday_dt(monday_iso, hour=9, minute=0).isoformat()
    period_end = (dt.date.fromisoformat(monday_iso) + dt.timedelta(days=6)).isoformat()
    data = {
        "definition_id": definition_id,
        "opportunity_id": opportunity_id,
        "status": "completed",
        "completed_at": completed_at,
        "period_start": monday_iso,
        "period_end": period_end,
        "state": {"period_start": monday_iso, "period_end": period_end},
        # Minimal snapshot so useRunView can render — chc_nutrition pulls
        # decisions live via view.decisionsFor anyway.
        "snapshot": {"workers": [], "pipelines": {}, "state": {"period_start": monday_iso, "period_end": period_end}},
    }
    rec = wda.labs_api.create_record(
        experiment="workflow",
        type="workflow_run",
        data=data,
    )
    return rec.id


def _create_audit(
    *, ada, opportunity_id: int, workflow_run_id: int, flw_id: str, monday_iso: str, outcome: str
) -> int:
    """Spawn an AuditSession record with the given outcome. Returns the audit id."""
    created_at = _monday_dt(monday_iso, hour=10, minute=0).isoformat()
    status = "in_review" if outcome == "in_review" else "completed"
    # Image counts depend on outcome: pass/fail/pending breakdowns
    if outcome == "completed_pass":
        image_results = {"pass": 5, "fail": 0, "pending": 0}
    elif outcome == "completed_fail":
        image_results = {"pass": 1, "fail": 4, "pending": 0}
    elif outcome == "completed_mixed":
        image_results = {"pass": 3, "fail": 2, "pending": 0}
    else:  # in_review
        image_results = {"pass": 2, "fail": 1, "pending": 2}

    data = {
        "title": f"MUAC audit for {flw_id}",
        "tag": "demo_seed",
        "status": status,
        "workflow_run_id": workflow_run_id,
        "opportunity_id": opportunity_id,
        "image_results": image_results,
        "created_at": created_at,
    }
    rec = ada.labs_api.create_record(
        experiment="audit",
        type="AuditSession",
        data=data,
        labs_record_id=workflow_run_id,
        username=flw_id,
    )
    return rec.id


def _create_task(
    *,
    tda,
    opportunity_id: int,
    workflow_run_id: int,
    flw_id: str,
    monday_iso: str,
    title: str,
    outcome: str,
    close_delay_days: int = 0,
    creator_name: str = "demo_seeder",
) -> int:
    """Spawn a Task record with backdated created/closed events."""
    created_at = _monday_dt(monday_iso, hour=10, minute=15)
    events = [
        {
            "event_type": "created",
            "actor": creator_name,
            "description": f"Task created by {creator_name}",
            "timestamp": created_at.isoformat(),
        }
    ]
    status = "investigating"
    resolution_details: dict = {}
    if outcome != "open":
        closed_at = created_at + dt.timedelta(days=close_delay_days, hours=4)
        status = "closed"
        resolution_details = {
            "official_action": outcome,
            "resolution_note": f"Closed by {creator_name} — {outcome}",
        }
        events.append(
            {
                "event_type": "closed",
                "actor": creator_name,
                "description": f"Closed: {outcome}",
                "timestamp": closed_at.isoformat(),
            }
        )

    data = {
        "title": title,
        "description": "Auto-spawned by program_admin_demo_seed_v2.",
        "priority": "high",
        "status": status,
        "username": flw_id,
        "flw_name": flw_id,
        "user_id": None,
        "opportunity_id": opportunity_id,
        "assigned_to_type": "self",
        "assigned_to_name": creator_name,
        "audit_session_id": None,
        "workflow_run_id": workflow_run_id,
        "resolution_details": resolution_details,
        "events": events,
    }
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
    flw_id: str,
    monday_iso: str,
    creator_name: str,
):
    """Materialize one (run, flw) decision spec into records.
    Creates audit + task + decision as appropriate."""
    decision_type = spec["decision_type"]
    audit_ids: list[int] = []
    task_ids: list[int] = []

    if decision_type == "action_taken":
        if spec.get("spawn_audit"):
            audit_ids.append(
                _create_audit(
                    ada=ada,
                    opportunity_id=opportunity_id,
                    workflow_run_id=workflow_run_id,
                    flw_id=flw_id,
                    monday_iso=monday_iso,
                    outcome=spec.get("audit_outcome", "in_review"),
                )
            )
        if spec.get("spawn_task"):
            task_title = f"[{spec.get('reason_label', spec.get('reason_key', 'Action'))}] {flw_id}"
            task_ids.append(
                _create_task(
                    tda=tda,
                    opportunity_id=opportunity_id,
                    workflow_run_id=workflow_run_id,
                    flw_id=flw_id,
                    monday_iso=monday_iso,
                    title=task_title,
                    outcome=spec.get("task_outcome", "open"),
                    close_delay_days=spec.get("task_close_delay_days", 0),
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
        notes=f"Demo seed week {monday_iso}",
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
        "Narrative-driven demo seeder. Builds 4 weekly chc_nutrition saved runs "
        "per opp with backdated completed_at, applies per-FLW archetype "
        "trajectories (solid / improver_* / suspended_* / new_hire), spawns "
        "AuditSession + Task records with realistic open/closed outcomes, "
        "and creates a final program_admin_report run watching all opps. "
        "Pass cleanup_first=true to wipe prior runs/decisions/tasks/audits "
        "for the opps before re-seeding (idempotent). See module docstring."
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
                    if spec.get("spawn_task"):
                        tasks_spawned += 1
                    if spec.get("spawn_audit"):
                        audits_spawned += 1
                    _apply_decision_spec(
                        dda=dda,
                        tda=tda,
                        ada=ada,
                        spec=spec,
                        workflow_run_id=run_id,
                        opportunity_id=opp_id,
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
