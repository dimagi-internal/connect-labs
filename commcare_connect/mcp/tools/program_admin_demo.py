"""MCP tool that seeds a complete program-admin-report demo dataset.

End-to-end: creates the workflow definitions (chc_nutrition_analysis +
program_admin_report) if they don't already exist for the configured opps,
creates one completed workflow_run per (opp, week), populates each run with
Decision records (per spec §3) + Task records for action_taken decisions,
then creates a single program_admin_report run covering the full window.

Intended for one-off demo seeding from a Claude Code session. It uses the
caller's Connect OAuth token (same path as ``synthetic_generate_from_manifest``)
so the writes are scoped to opps the caller has access to.

Inputs:
- ``opportunity_ids``: list of opp ids to seed (the watched workflows).
- ``weeks``: list of ISO dates, one per CHC nutrition saved run.
- ``target_flw_id``: the FLW id that gets the "interesting" action decisions
  + spawned task each week. Other FLWs get a default no_issues decision.
- ``other_flw_ids``: list of FLW ids that get no_issues decisions per run.
- ``reason_keys_per_week``: optional list of reason_keys (one per week) to
  use for the target FLW's action decision. Defaults to a rotation through
  ``bad_muac_distribution`` and ``gender_skew``.

Returns a manifest of created records (counts + ids) so the caller can verify.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

REASON_KEYS_DEFAULT = ["bad_muac_distribution", "gender_skew", "bad_muac_distribution"]
REASON_LABELS = {
    "bad_muac_distribution": "Bad MUAC distribution",
    "gender_skew": "Gender split off threshold",
}


def _connect_token(user):
    from ..connect_token import require_connect_token

    return require_connect_token(user)


def _ensure_chc_nutrition_definition(*, wda, opp_id: int):
    """Find or create a chc_nutrition_analysis workflow definition for ``opp_id``.

    Returns the WorkflowDefinitionRecord.
    """
    from commcare_connect.workflow.templates import create_workflow_from_template

    existing = [
        d for d in wda.list_definitions() if d.opportunity_id == opp_id and d.template_type == "chc_nutrition_analysis"
    ]
    if existing:
        return existing[0]

    definition, _render_code, _pipeline = create_workflow_from_template(
        wda,
        template_key="chc_nutrition_analysis",
    )
    return definition


def _ensure_program_admin_report_definition(*, wda, primary_opp_id: int, watched_sources: list[dict]):
    """Find or create a program_admin_report workflow definition.

    ``watched_sources`` is the list of {opportunity_id, workflow_definition_id}
    pairs the report covers.
    """
    from commcare_connect.workflow.templates import create_workflow_from_template

    existing = [
        d
        for d in wda.list_definitions()
        if d.opportunity_id == primary_opp_id and d.template_type == "program_admin_report"
    ]
    if existing:
        return existing[0]

    definition, _render_code, _pipeline = create_workflow_from_template(
        wda,
        template_key="program_admin_report",
        opportunity_ids=[s["opportunity_id"] for s in watched_sources],
    )

    # Inject watched_sources into the config so build_snapshot knows what to read.
    updated_data = {**definition.data}
    updated_data.setdefault("config", {})
    updated_data["config"]["watched_sources"] = watched_sources
    wda.update_definition(definition_id=definition.id, data=updated_data)
    return wda.get_definition(definition.id)


def _seed_run(
    *,
    wda,
    dda,
    tda,
    definition_id: int,
    opportunity_id: int,
    week_iso: str,
    target_flw_id: str,
    other_flw_ids: list[str],
    reason_key: str,
    user_name: str,
):
    """Create one chc_nutrition saved run + its decisions + tasks for one Monday."""
    period_start = week_iso
    period_end = (dt.date.fromisoformat(week_iso) + dt.timedelta(days=6)).isoformat()

    run = wda.create_run(
        definition_id=definition_id,
        opportunity_id=opportunity_id,
        period_start=period_start,
        period_end=period_end,
        initial_state={"period_start": period_start, "period_end": period_end},
    )
    run_id = run.id

    decision_ids = []
    task_ids = []

    # Target FLW — action_taken with task
    task = tda.create_task(
        username=target_flw_id,
        opportunity_id=opportunity_id,
        title=f"[{REASON_LABELS[reason_key]}] Coaching review needed",
        description=f"Auto-spawned from week {week_iso} chc_nutrition run.",
        priority="high",
        creator_name=user_name,
        workflow_run_id=run_id,
        status="investigating",
    )
    task_ids.append(task.id)

    target_decision = dda.create_decision(
        workflow_run_id=run_id,
        opportunity_id=opportunity_id,
        flw_id=target_flw_id,
        decision_type="action_taken",
        reason_key=reason_key,
        reason_label=REASON_LABELS[reason_key],
        task_ids=[task.id],
        notes=f"Demo seed week {week_iso}",
        decided_by=user_name,
    )
    decision_ids.append(target_decision.id)

    # Other FLWs — no_issues
    for flw in other_flw_ids:
        d = dda.create_decision(
            workflow_run_id=run_id,
            opportunity_id=opportunity_id,
            flw_id=flw,
            decision_type="no_issues",
            decided_by=user_name,
        )
        decision_ids.append(d.id)

    # Mark run completed with a minimal snapshot (chc_nutrition's default
    # snapshot manifest captures workers + pipelines — for demo we pass an
    # empty dict; the framework's hook would normally fill it, but the seeder
    # doesn't have the render context).
    wda.complete_run(run_id=run_id, snapshot={"workers": [], "pipelines": {}, "state": {}})

    return {
        "run_id": run_id,
        "week": week_iso,
        "decisions_created": len(decision_ids),
        "tasks_created": len(task_ids),
    }


@register(
    name="program_admin_demo_seed",
    description=(
        "Seed a complete program-admin-report demo: workflow definitions, "
        "weekly chc_nutrition saved runs, Decision + Task records per FLW, "
        "and a program_admin_report run covering the full window. One-shot, "
        "idempotent on the workflow_definition side (it reuses an existing "
        "one if the opp already has a chc_nutrition_analysis workflow)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
            },
            "weeks": {
                "type": "array",
                "items": {"type": "string", "description": "ISO date for the Monday of each week"},
                "minItems": 1,
            },
            "target_flw_id": {"type": "string"},
            "other_flw_ids": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
            "reason_keys_per_week": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
            },
        },
        "required": ["opportunity_ids", "weeks", "target_flw_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def program_admin_demo_seed(
    user,
    *,
    opportunity_ids: list[int],
    weeks: list[str],
    target_flw_id: str,
    other_flw_ids: list[str] | None = None,
    reason_keys_per_week: list[str] | None = None,
) -> dict[str, Any]:
    from commcare_connect.decisions.data_access import DecisionsDataAccess
    from commcare_connect.tasks.data_access import TaskDataAccess
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    other_flw_ids = other_flw_ids or []
    if reason_keys_per_week and len(reason_keys_per_week) != len(weeks):
        raise MCPToolError(
            "INVALID_INPUT",
            "reason_keys_per_week, when provided, must have the same length as weeks",
        )
    reason_keys_per_week = reason_keys_per_week or [
        REASON_KEYS_DEFAULT[i % len(REASON_KEYS_DEFAULT)] for i in range(len(weeks))
    ]

    token = _connect_token(user)
    user_name = getattr(user, "username", "demo_seeder")

    summary: dict[str, Any] = {"opportunities": [], "program_admin_report": None}

    # Per-opp: seed chc_nutrition workflow + N weekly runs
    watched_sources: list[dict] = []
    for opp_id in opportunity_ids:
        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=token)
        dda = DecisionsDataAccess(opportunity_id=opp_id, access_token=token)
        tda = TaskDataAccess(opportunity_id=opp_id, access_token=token)
        try:
            definition = _ensure_chc_nutrition_definition(wda=wda, opp_id=opp_id)
            watched_sources.append({"opportunity_id": opp_id, "workflow_definition_id": definition.id})

            run_summaries = []
            for week_iso, reason_key in zip(weeks, reason_keys_per_week):
                run_summaries.append(
                    _seed_run(
                        wda=wda,
                        dda=dda,
                        tda=tda,
                        definition_id=definition.id,
                        opportunity_id=opp_id,
                        week_iso=week_iso,
                        target_flw_id=target_flw_id,
                        other_flw_ids=other_flw_ids,
                        reason_key=reason_key,
                        user_name=user_name,
                    )
                )

            summary["opportunities"].append(
                {
                    "opportunity_id": opp_id,
                    "workflow_definition_id": definition.id,
                    "runs": run_summaries,
                }
            )
        finally:
            wda.close()
            dda.close()
            tda.close()

    # One program_admin_report on the first opp as the "primary"
    primary_opp_id = opportunity_ids[0]
    par_wda = WorkflowDataAccess(opportunity_id=primary_opp_id, access_token=token)
    try:
        par_def = _ensure_program_admin_report_definition(
            wda=par_wda,
            primary_opp_id=primary_opp_id,
            watched_sources=watched_sources,
        )
        # The watched runs' `completed_at` is whenever the seeder ran
        # (today), not the historical `period_start`. The window has to
        # span [first seed time, now+1d] so the filter actually catches
        # them; otherwise the rollup is silently empty. We expose the
        # configured `weeks[0]` → `weeks[-1]+6d` as display dates in the
        # state so the render header shows the intent, but the filter
        # window covers today.
        display_window_start = weeks[0]
        display_window_end = (dt.date.fromisoformat(weeks[-1]) + dt.timedelta(days=6)).isoformat()
        today = dt.date.today()
        window_start = display_window_start
        window_end = (today + dt.timedelta(days=1)).isoformat()
        par_run = par_wda.create_run(
            definition_id=par_def.id,
            opportunity_id=primary_opp_id,
            period_start=window_start,
            period_end=window_end,
            initial_state={
                "window_start": window_start,
                "window_end": window_end,
                "watched_sources": watched_sources,
            },
        )

        # Build the snapshot manually by invoking the template's hook
        from commcare_connect.workflow.templates.program_admin_report import build_snapshot

        snapshot = build_snapshot(
            pipelines={},
            state={
                "window_start": window_start,
                "window_end": window_end,
                "watched_sources": watched_sources,
            },
            opportunity_id=primary_opp_id,
            workers=[],
            opportunity_ids=opportunity_ids,
            definition_id=par_def.id,
            access_token=token,
        )
        par_wda.complete_run(run_id=par_run.id, snapshot=snapshot)
        summary["program_admin_report"] = {
            "definition_id": par_def.id,
            "run_id": par_run.id,
            "window_start": window_start,
            "window_end": window_end,
            "watched_sources_count": len(watched_sources),
            "snapshot_summary_count": len(snapshot.get("watched_summary", [])),
        }
    finally:
        par_wda.close()

    return summary
