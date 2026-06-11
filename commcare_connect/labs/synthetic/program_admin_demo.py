"""Synthetic data generator for the Program Admin Report demo.

Lives in ``labs/synthetic/`` next to ``archetypes.py`` and
``manager_flow_views.py`` because that's where the rest of the synthetic
infrastructure lives. The MCP-callable wrapper at
``commcare_connect/mcp/tools/program_admin_demo.py`` is a 30-line shim
that just imports + registers ``program_admin_demo_seed``.

What this generator produces, per ``demo_config.json``:

- A ``chc_nutrition_analysis`` workflow definition per opportunity,
  reused on re-runs.
- One backdated workflow_run per (opp × week) — last week of the
  first opp can be left ``in_progress`` for manager-flow walkthroughs.
- Audit + Task records per (run × FLW) materialized from the FLW's
  archetype trajectory (``_actions_for_flw_across_weeks``). Flags are
  NOT seeded — the chc_nutrition render code derives them from the
  pipeline data at render time and persists them via
  view.ensureAutoFlags.
- A ``program_admin_report`` rollup run watching all the chc runs.

Generic primitives (cleanup, backdated-run writer, archetype-driven
audit + task creation, action-spec application) live in
``walkthrough_kit``; this module keeps only what's specific to the
PAR/CHC-nutrition story.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# Archetype + reason vocabulary (PAR/CHC-nutrition-specific)
# ---------------------------------------------------------------------- #


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


# ---------------------------------------------------------------------- #
# Per-archetype weekly trajectory
# ---------------------------------------------------------------------- #


def _actions_for_flw_across_weeks(flw: dict, week_count: int) -> list[dict | None]:
    """Return a list of per-week action specs (or None when the FLW isn't
    on the roster that week — pre-hire for new_hire, post-suspension for
    suspended_*).

    The FLW-archetype → (audit_archetype, task_archetype) mapping below
    is the single place to tune what an "improver_warned" or "suspended_
    repeat_offense" trajectory looks like in terms of concrete audit/task
    evidence. Flags themselves are not seeded here — the chc_nutrition
    render code derives them from the pipeline data at render time and
    persists them via view.ensureAutoFlags.
    """
    archetype = flw["archetype"]
    out: list[dict | None] = [None] * week_count

    def action(reason: str, audit_arche: str, task_arche: str) -> dict:
        return {
            "reason_key": reason,
            "reason_label": REASON_LABELS.get(reason, reason),
            "audit_archetype": audit_arche,
            "task_archetype": task_arche,
        }

    no_issues: dict = {"audit_archetype": None, "task_archetype": None}

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
                out[i] = action(reason, audit_arche, task_arche)
            else:
                out[i] = no_issues

    elif archetype == "improver_in_progress":
        flag_week = flw.get("flag_week", 0)
        reason = flw.get("reason_key", "bad_muac_distribution")
        for i in range(week_count):
            if i < flag_week:
                out[i] = no_issues
            elif i == flag_week:
                out[i] = action(reason, "in_review_partial", "investigating")
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
        first_audit = "completed_fail_misleading" if is_fraud else "completed_fail_tape_usage"
        second_audit = first_audit
        suspension_task = "closed_suspended_fraud" if is_fraud else "closed_suspended"
        for i in range(week_count):
            if i < first:
                out[i] = no_issues
            elif i == first:
                out[i] = action(reason, first_audit, "closed_warned")
            elif i < last:
                out[i] = no_issues
            elif i == last:
                out[i] = action("repeated_failure", second_audit, suspension_task)
            else:
                out[i] = None  # removed from roster after suspension
    else:
        from commcare_connect.mcp.tool_registry import MCPToolError

        raise MCPToolError("INVALID_INPUT", f"Unknown archetype: {archetype!r}")

    return out


# ---------------------------------------------------------------------- #
# CHC-Nutrition pipeline snapshot builder
# ---------------------------------------------------------------------- #


def _build_chc_pipeline_rows(opp_id: int, flws: list[dict], week_idx: int) -> list[dict]:
    """Compute the chc_nutrition pipeline rows for one week.

    Reading order:
      - For each FLW, look up its trajectory's spec for ``week_idx``.
      - If None → skip (not on roster this week).
      - Derive ``flagged_this_week`` + ``kpi_issue`` from the spec.
      - Build the row via ``build_flw_pipeline_row`` (handles MUAC bins,
        gender split, jitter, etc).
    """
    from commcare_connect.labs.synthetic.archetypes import build_flw_pipeline_row

    rows: list[dict] = []
    for flw in flws:
        specs = _actions_for_flw_across_weeks(flw, week_count=week_idx + 1)
        spec = specs[week_idx] if week_idx < len(specs) else None
        if spec is None:
            continue
        flagged = bool(spec.get("audit_archetype") or spec.get("task_archetype"))
        kpi_issue: str | None = None
        if flagged:
            reason = spec.get("reason_key") or ""
            if reason == "gender_skew":
                kpi_issue = "gender"
            elif reason in ("bad_muac_distribution", "misleading_photos", "repeated_failure"):
                kpi_issue = "muac"
        seed = hash((opp_id, flw["id"], week_idx)) & 0xFFFFFFFF
        rows.append(
            build_flw_pipeline_row(
                flw_id=flw["id"],
                archetype=flw["archetype"],
                flagged_this_week=flagged,
                rng_seed=seed,
                kpi_issue=kpi_issue,
            )
        )
    return rows


def _chc_pipeline_snapshot(opp_id: int, flws: list[dict], week_idx: int) -> dict:
    """Wrap chc_nutrition rows in the snapshot shape its render code reads.

    The runtime pipelines dict wraps rows in ``{rows: [...]}``; the saved
    snapshot must match exactly so chc_nutrition's snapshot fallback
    (``instance.snapshot.pipelines``) replays the same way as a live run.
    """
    rows = _build_chc_pipeline_rows(opp_id, flws, week_idx)
    return {"data": {"rows": rows}} if rows else {}


# ---------------------------------------------------------------------- #
# Connect token helper
# ---------------------------------------------------------------------- #


def _connect_token(user):
    from commcare_connect.mcp.connect_token import require_connect_token

    return require_connect_token(user)


def _refresh_render_code(wda, definition, template_key: str) -> bool:
    """Replace `definition`'s render_code with the current template source.

    Returns True if the render_code was written (or the version bumped),
    False if the def already has the latest code byte-for-byte.

    Why this exists: when a synthetic seed re-runs against opps that
    already have a workflow definition (the cleanup step does NOT delete
    definitions, so `existing_defs[0]` re-uses them), the def carries
    forward whatever render_code was captured at original creation
    time. Post-deploy template edits land in
    ``commcare_connect/workflow/templates/<name>.py`` source, but the
    runner reads the LabsRecord-stored render_code that was set when the
    def was first written — so re-seeding alone never refreshes the JSX.

    We used to fix this manually after every deploy by calling the MCP's
    ``workflow_sync_from_template_file`` / ``workflow_update_render_code``
    per opportunity. Doing the same thing in-line as part of the seed
    means re-seeding IS the upgrade path: post-deploy you re-seed and
    every def comes back with the current render code.

    save_render_code is an upsert + repoint, so unconditional calls are
    cheap and safe even when nothing changed.
    """
    from commcare_connect.workflow.templates import get_template

    template = get_template(template_key)
    if not template:
        return False
    component_code = template.get("render_code")
    if not component_code:
        return False

    existing = wda.get_render_code(definition.id)
    if existing and existing.data.get("component_code") == component_code:
        return False  # Already up to date.

    next_version = (existing.data.get("version") if existing else 0) + 1
    wda.save_render_code(definition_id=definition.id, component_code=component_code, version=next_version)
    return True


# ---------------------------------------------------------------------- #
# Orchestrator
# ---------------------------------------------------------------------- #


def program_admin_demo_seed(
    user,
    *,
    weeks: list[str],
    opps: list[dict],
    cleanup_first: bool = True,
) -> dict[str, Any]:
    """Seed the full PAR demo: per-opp chc_nutrition runs + a PAR rollup.

    Inputs:
      ``weeks`` — list of ISO Monday dates, one per chc_nutrition run.
      ``opps`` — list of opp configs, each with:
          opportunity_id, label, network_manager, flws (list of FLW dicts
          with id + archetype + per-archetype params), and optionally
          missed_week_idxs + in_progress_last_week.
      ``cleanup_first`` — wipe prior runs/flags/tasks/audits for the
          opps before re-seeding. Default True.
    """
    # Defensive: some MCP clients double-encode list args as JSON strings
    # when their cached schema doesn't know the property is an array. Parse
    # back to native lists so the rest of the seed body works uniformly.
    # Handle three shapes: full string ("[...]"), list of strings
    # (["{...}", "{...}"]), and dict items in either list level.
    import json as _json

    def _maybe_load(v):
        return _json.loads(v) if isinstance(v, str) else v

    if isinstance(weeks, str):
        weeks = _json.loads(weeks)
    if isinstance(opps, str):
        opps = _json.loads(opps)
    opps = [_maybe_load(o) for o in opps]
    for o in opps:
        if isinstance(o, dict) and isinstance(o.get("flws"), str):
            o["flws"] = _json.loads(o["flws"])
        if isinstance(o, dict) and isinstance(o.get("flws"), list):
            o["flws"] = [_maybe_load(f) for f in o["flws"]]
    from commcare_connect.audit.data_access import AuditDataAccess
    from commcare_connect.flags.data_access import FlagsDataAccess
    from commcare_connect.labs.synthetic.walkthrough_kit import (
        VisitIdSequence,
        apply_action_spec,
        cleanup_opportunity_workflows,
        create_backdated_workflow_run,
        monday_dt,
        week_end_iso,
    )
    from commcare_connect.tasks.data_access import TaskDataAccess
    from commcare_connect.workflow.data_access import WorkflowDataAccess
    from commcare_connect.workflow.templates import create_workflow_from_template

    token = _connect_token(user)
    week_count = len(weeks)
    visit_id_seq = VisitIdSequence()

    summary: dict[str, Any] = {"opportunities": [], "program_admin_report": None}
    watched_sources: list[dict] = []

    for opp_cfg in opps:
        opp_id = opp_cfg["opportunity_id"]
        flws = opp_cfg["flws"]
        missed = set(opp_cfg.get("missed_week_idxs", []))
        in_progress_last_week = bool(opp_cfg.get("in_progress_last_week", False))
        nm = opp_cfg["network_manager"]

        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=token)
        fda = FlagsDataAccess(opportunity_id=opp_id, access_token=token)
        tda = TaskDataAccess(opportunity_id=opp_id, access_token=token)
        ada = AuditDataAccess(opportunity_id=opp_id, access_token=token)
        try:
            cleanup_counts = None
            if cleanup_first:
                cleanup_counts = cleanup_opportunity_workflows(
                    wda=wda,
                    fda=fda,
                    tda=tda,
                    ada=ada,
                    opportunity_id=opp_id,
                    template_types=["chc_nutrition_analysis", "program_admin_report"],
                )

            existing_defs = [
                d
                for d in wda.list_definitions()
                if d.opportunity_id == opp_id and d.template_type == "chc_nutrition_analysis"
            ]
            if existing_defs:
                definition = existing_defs[0]
            else:
                definition, _, _ = create_workflow_from_template(wda, template_key="chc_nutrition_analysis")
            # Refresh the def's render_code to whatever the current template
            # source has. Without this, an existing def carries forward the
            # render_code captured at creation time and never picks up
            # post-deploy template updates — which means re-seeding after a
            # template change still serves stale JSX (the manual
            # workflow_sync_from_template_file dance we kept doing). save_
            # render_code is an upsert + repoint, so this is safe to call
            # unconditionally.
            _refresh_render_code(wda, definition, "chc_nutrition_analysis")

            watched_sources.append({"opportunity_id": opp_id, "workflow_definition_id": definition.id})

            per_flw_specs = {f["id"]: _actions_for_flw_across_weeks(f, week_count) for f in flws}

            week_summaries: list[dict] = []
            last_week_idx = week_count - 1
            for week_idx, monday_iso in enumerate(weeks):
                if week_idx in missed:
                    week_summaries.append({"week": monday_iso, "ran": False})
                    continue

                # Manager-demo: leave the LAST week's run as in_progress with
                # no audits/tasks generated; the walkthrough recorder
                # writes those live so viewers see the flow.
                is_in_progress_week = in_progress_last_week and week_idx == last_week_idx
                pipelines_snapshot = _chc_pipeline_snapshot(opp_id, flws, week_idx)
                run_id = create_backdated_workflow_run(
                    wda=wda,
                    definition_id=definition.id,
                    opportunity_id=opp_id,
                    monday_iso=monday_iso,
                    in_progress=is_in_progress_week,
                    pipelines=pipelines_snapshot,
                )

                actions_taken = 0
                tasks_spawned = 0
                audits_spawned = 0
                active_flw_count = 0
                for flw in flws:
                    spec = per_flw_specs[flw["id"]][week_idx]
                    if spec is None:
                        continue
                    active_flw_count += 1
                    if is_in_progress_week:
                        continue
                    if spec.get("task_archetype"):
                        tasks_spawned += 1
                    if spec.get("audit_archetype"):
                        audits_spawned += 1
                    if not spec.get("task_archetype") and not spec.get("audit_archetype"):
                        continue
                    apply_action_spec(
                        tda=tda,
                        ada=ada,
                        spec=spec,
                        workflow_run_id=run_id,
                        opportunity_id=opp_id,
                        opportunity_name=opp_cfg["label"],
                        flw_id=flw["id"],
                        monday_iso=monday_iso,
                        creator_name=nm,
                        visit_id_seq=visit_id_seq,
                    )
                    actions_taken += 1

                week_summaries.append(
                    {
                        "week": monday_iso,
                        "ran": True,
                        "run_id": run_id,
                        "in_progress": is_in_progress_week,
                        "actions": actions_taken,
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
            fda.close()
            tda.close()
            ada.close()

    # ---------------- Program Admin Report rollup ----------------
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
        # Same render_code refresh as the per-opp chc_nutrition def — a
        # PAR def that survives across re-seeds needs to pick up
        # post-deploy template changes too.
        _refresh_render_code(par_wda, par_def, "program_admin_report")

        last_monday = weeks[-1]
        par_completed_at = (monday_dt(last_monday) + dt.timedelta(days=1)).isoformat()
        window_start = weeks[0]
        # End window at today+1 so the filter catches the seeded runs even
        # if completed_at is the historical Monday.
        window_end = (dt.date.today() + dt.timedelta(days=1)).isoformat()

        from commcare_connect.workflow.templates.program_admin_report import compute_program_admin_rollup

        rollup = compute_program_admin_rollup(
            state={
                "window_start": window_start,
                "window_end": window_end,
                "watched_sources": watched_sources,
                "weeks": weeks,
            },
            access_token=token,
        )
        # Wrap in the snapshot shape (schema_version + state) the runner's
        # view helper reads — the same shape the declarative manifest now
        # produces at a real conclude.
        snapshot = {"schema_version": 2, "state": rollup}
        if "state" in snapshot:
            snapshot["state"]["expected_weeks"] = weeks
            snapshot["state"]["display_window_start"] = weeks[0]
            snapshot["state"]["display_window_end"] = week_end_iso(weeks[-1])
            label_by_opp = {o["opportunity_id"]: o for o in opps}
            for src in snapshot["state"].get("watched_summary", []):
                meta = label_by_opp.get(src["opportunity_id"], {})
                src["label"] = meta.get("label", f"Opp #{src['opportunity_id']}")
                src["network_manager"] = meta.get("network_manager", "")
                src["flw_count"] = len(meta.get("flws", []))
                src["missed_week_idxs"] = meta.get("missed_week_idxs", [])

        run_data = {
            "definition_id": par_def.id,
            "opportunity_id": primary_opp_id,
            "status": "completed",
            "completed_at": par_completed_at,
            "period_start": window_start,
            "period_end": week_end_iso(weeks[-1]),
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
