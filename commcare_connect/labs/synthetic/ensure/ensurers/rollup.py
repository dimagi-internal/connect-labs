"""The ``rollup`` ensurer: the cross-opp PAR run + drill-target selection.

This is the LAST, integrative ensurer. By the time it runs, the earlier
ensurers have stashed everything it needs on ``ctx.ids``:

- ``weekly_runs`` materialized per-opp weekly ``chc_nutrition_analysis`` runs and
  recorded the watched (opp, workflow_definition_id) list at
  ``ctx.ids["chc_watched_sources"]`` plus every run id at
  ``ctx.ids[f"run:{opp}:{week_start}"]``,
- ``run_audits`` stashed ``ctx.ids[f"audit:{run_id}:{flw}"]`` for each run-linked
  completable audit,
- ``tasks`` stashed ``ctx.ids[f"task:{run_id}:{flw}"]`` for each coaching task.

This ensurer does two things:

1. **Build the cross-opp ``program_admin_report`` saved run.** It ensures a PAR
   workflow DEFINITION watching ``resource.opportunity_ids`` (reused across
   re-seeds, render_code refreshed from the template source), computes the
   window-scoped rollup via the template's own
   ``compute_program_admin_rollup``, wraps it in the snapshot shape the runner's
   ``view`` helper reads (``schema_version`` + ``state``), and writes a
   ``completed`` run carrying the state keys the report template's
   ``snapshot_inputs`` manifest declares (``watched_summary``, ``window_start``,
   ``window_end``, ``watched_sources``, ``weeks``, ``expected_weeks``,
   ``display_window_start``/``display_window_end``). This is a direct port of the
   PAR-run portion of ``program_admin_demo.program_admin_demo_seed`` — the helpers
   (``monday_dt``/``week_end_iso``) come from the shared kit; the PAR-specific glue
   is re-implemented here rather than imported from the demo module.

2. **Select + emit the drill-target ``${...}`` vars** the earlier ensurers
   deferred. Instead of walking the live PAR snapshot (the old
   ``regenerate.py`` / ``_lib.discovery.find_drill_targets`` path), we select the
   targets straight from ``ctx.ids`` — we already know every (opp, week, flw)
   that has a run + audit + task, and the task's status tells us whether the
   coaching loop closed (the ``good`` cluster) or is still open (the
   ``incomplete`` cluster). That is cleaner and avoids a second HTTP round-trip
   through the snapshot API. The selected target VAR NAMES match the spec /
   ``regenerate.py`` contract exactly.

**Idempotency.** The PAR run is keyed on ``(primary opp, program_admin_report
template, sorted watched-opp set)`` — an existing matching completed run is
reused and its snapshot/state refreshed in place rather than duplicated. Drill
selection is deterministic (sorted), so re-runs emit a stable map.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors the sibling ensurers: labs-only opps short-circuit to the in-process
# local-records backend, so the token is never sent anywhere.
_LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 (not a secret)

_PAR_TEMPLATE_TYPE = "program_admin_report"


def _display_name_for(persona) -> str:
    """Real human name for a persona: its ``display_name`` or a title-cased id.

    Mirrors ``weekly_runs`` / ``run_audits`` / ``tasks`` so every record agrees on
    names (re-implemented locally rather than importing the demo module).
    """
    if persona.display_name:
        return persona.display_name
    token = persona.id.split("_", 1)[0]
    return token[:1].upper() + token[1:] if token else persona.id


# ---------------------------------------------------------------------- #
# PAR definition + run idempotency
# ---------------------------------------------------------------------- #


def _ensure_par_definition(wda, watched_sources: list[dict]):
    """Return the primary opp's PAR definition, creating it if absent.

    Reuses an existing def across re-seeds (so ids don't churn), keeps its
    ``config.watched_sources`` in sync with the current watched set, and refreshes
    its render_code from the template source — the same upgrade-on-reseed behavior
    ``program_admin_demo`` has.
    """
    from commcare_connect.workflow.templates import create_workflow_from_template, get_template

    existing = [
        d
        for d in wda.list_definitions()
        if d.opportunity_id == wda.opportunity_id and d.template_type == _PAR_TEMPLATE_TYPE
    ]
    if existing:
        definition = existing[0]
    else:
        definition, _, _ = create_workflow_from_template(
            wda,
            template_key=_PAR_TEMPLATE_TYPE,
            opportunity_ids=[s["opportunity_id"] for s in watched_sources],
        )

    # Keep config.watched_sources current (multi-opp PAR aggregates this set).
    updated = {**definition.data}
    updated.setdefault("config", {})
    updated["config"]["watched_sources"] = watched_sources
    wda.update_definition(definition_id=definition.id, data=updated)
    definition = wda.get_definition(definition.id)

    template = get_template(_PAR_TEMPLATE_TYPE)
    component_code = template.get("render_code") if template else None
    if component_code:
        current = wda.get_render_code(definition.id)
        if not (current and current.data.get("component_code") == component_code):
            next_version = (current.data.get("version") if current else 0) + 1
            wda.save_render_code(definition_id=definition.id, component_code=component_code, version=next_version)
    return definition


def _existing_par_run(wda, definition_id: int, watched_opp_ids: list[int]):
    """Return the existing completed PAR run for this def + watched set, or None.

    Keyed on the def + the sorted watched-opp set so a re-run reuses the run it
    created last time instead of stacking duplicates.
    """
    want = sorted(watched_opp_ids)
    for r in wda.list_runs(definition_id=definition_id):
        if r.opportunity_id != wda.opportunity_id:
            continue
        if r.data.get("status") != "completed":
            continue
        run_sources = r.data.get("state", {}).get("watched_sources", []) or []
        run_opp_ids = sorted(s.get("opportunity_id") for s in run_sources)
        if run_opp_ids == want:
            return r
    return None


def _build_snapshot(
    *,
    token: str,
    weeks: list[str],
    window_start: str,
    window_end: str,
    watched_sources: list[dict],
    manifests_by_opp: dict[int, Any],
) -> dict:
    """Compute the PAR rollup + wrap it in the runner's snapshot shape.

    Ports ``program_admin_demo``'s snapshot assembly: call the template's
    ``compute_program_admin_rollup`` to get the state-shaped rollup, then stamp
    the grid-driving keys (``expected_weeks`` / ``display_window_*``) and per-source
    display metadata (``label`` / ``network_manager`` / ``flw_count`` /
    ``missed_week_idxs``) the render reads. Display metadata is sourced from each
    opp's stashed manifest rather than an inline opp dict.
    """
    from commcare_connect.labs.synthetic.walkthrough_kit import week_end_iso
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
    snapshot = {"schema_version": 2, "state": rollup}
    state = snapshot["state"]
    state["expected_weeks"] = weeks
    state["display_window_start"] = weeks[0]
    state["display_window_end"] = week_end_iso(weeks[-1])

    for src in state.get("watched_summary", []):
        opp_id = src.get("opportunity_id")
        manifest = manifests_by_opp.get(opp_id)
        if manifest is not None:
            src["label"] = manifest.opportunity_name
            # The manifest has no separate manager entity; mirror the flag/task
            # attribution convention (first persona stands in as the manager).
            src["network_manager"] = _display_name_for(manifest.flw_personas[0]) if manifest.flw_personas else ""
            src["flw_count"] = len(manifest.flw_personas)
        else:
            src.setdefault("label", f"Opp #{opp_id}")
            src.setdefault("network_manager", "")
            src.setdefault("flw_count", 0)
        src.setdefault("missed_week_idxs", [])
    return snapshot


# ---------------------------------------------------------------------- #
# Drill-target selection from ctx.ids
# ---------------------------------------------------------------------- #


def _run_coords(ctx) -> dict[int, tuple[int, int, str]]:
    """Map ``run_id`` -> ``(opp_id, week_idx, monday_iso)`` from ``ctx.ids``.

    Inverts the ``run:{opp}:{monday}`` keys ``weekly_runs`` stamped so a
    drill-target's run id resolves back to its grid coordinates (opp + week
    index). Only completed-week runs (those in ``ctx.weeks``) get a week index;
    the current in-progress run is excluded (it carries no audits/tasks).
    """
    week_idx_by_monday = {monday: idx for idx, monday in enumerate(ctx.weeks)}
    out: dict[int, tuple[int, int, str]] = {}
    for key, run_id in ctx.ids.items():
        if not (isinstance(key, str) and key.startswith("run:")):
            continue
        _, opp_str, monday = key.split(":", 2)
        if monday not in week_idx_by_monday:
            continue  # current in-progress week — no drill targets live there
        out[run_id] = (int(opp_str), week_idx_by_monday[monday], monday)
    return out


def _select_drill_targets(ctx) -> tuple[dict | None, dict | None]:
    """Select the ``good`` + ``incomplete`` drill clusters straight from ``ctx.ids``.

    A drill cluster is a (opp, week, flw) that has BOTH a run-linked audit and a
    coaching task. We classify by the task's status (the same signal
    ``_lib.discovery`` reads off the snapshot):

    - ``good`` — the coaching loop CLOSED (task ``status == "closed"``): the
      satisfying "image reviewed + flag resolved" drill (PAR's Northern complete
      week),
    - ``incomplete`` — the loop is still OPEN (task ``status == "investigating"``):
      the "manager left it mid-flight" drill (PAR's Southern).

    Both are walked in a deterministic (sorted) order so re-runs pick the same
    targets. Each returned cluster carries the keys the realized map needs:
    ``opp_id``, ``week_idx``, ``run_id``, ``audit_id``, ``task_id``, ``flw_id``,
    ``opp_label``. Returns ``(good, incomplete)``; either may be ``None`` if no
    matching cluster exists.
    """
    from commcare_connect.tasks.data_access import TaskDataAccess

    coords = _run_coords(ctx)

    manifests_by_opp = {
        m.opportunity_id: m for k, m in ctx.ids.items() if isinstance(k, str) and k.startswith("manifest:")
    }

    def _opp_label(opp_id: int) -> str:
        m = manifests_by_opp.get(opp_id)
        if m is None:
            return f"Opp #{opp_id}"
        # The grid short-label is the first token of the opp name (e.g.
        # "Northern" from "Northern Region Nutrition") — matches discovery's
        # ``opp_label.split()[0]``.
        name = m.opportunity_name or f"Opp #{opp_id}"
        return name.split()[0] if name.split() else name

    # Build (run_id, flw) candidates that have BOTH an audit and a task.
    candidates: list[tuple[int, str, int, int]] = []  # (run_id, flw, audit_id, task_id)
    for key, task_id in sorted(ctx.ids.items(), key=lambda kv: str(kv[0])):
        if not (isinstance(key, str) and key.startswith("task:")):
            continue
        _, run_str, flw = key.split(":", 2)
        run_id = int(run_str)
        audit_id = ctx.ids.get(f"audit:{run_id}:{flw}")
        if audit_id is None or run_id not in coords:
            continue
        candidates.append((run_id, flw, audit_id, task_id))

    # Cache task status per opp (one DAO per opp, get_tasks_for_run is per-run).
    status_cache: dict[int, str | None] = {}
    daos: dict[int, Any] = {}
    try:

        def _task_status(opp_id: int, run_id: int, task_id: int) -> str | None:
            if task_id in status_cache:
                return status_cache[task_id]
            tda = daos.get(opp_id)
            if tda is None:
                tda = TaskDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
                daos[opp_id] = tda
            status = None
            for t in tda.get_tasks_for_run(run_id):
                if t.id == task_id:
                    status = t.status
                    break
            status_cache[task_id] = status
            return status

        # Classify by WEEK (run), not by individual cluster. The PAR report renders
        # a week's status from ALL its flagged workers: "All resolved" only when
        # every flag's task is closed, "N open" when any task is still
        # investigating. The drill targets MUST agree with that rendering, or the
        # walkthrough waits for a run the grid never marks resolved (the scene-8
        # bug: an old per-cluster pick grabbed the first week containing ANY closed
        # task, even a week that also had open work, so good_run_id pointed at a
        # week the grid showed as "N open").
        #
        #   good       = a week where EVERY flagged cluster is resolved (closed
        #                task) AND none is open -> the grid's "All resolved" week.
        #   incomplete = a week with open work (>=1 investigating task), in a
        #                DIFFERENT opp than good, so the two drills land on the
        #                resolved opp + the still-open opp (Northern + Southern),
        #                never two cells of the same opp.
        from collections import defaultdict

        by_run: dict[int, list[dict]] = defaultdict(list)
        for run_id, flw, audit_id, task_id in candidates:
            opp_id, week_idx, _monday = coords[run_id]
            by_run[run_id].append(
                {
                    "opp_id": opp_id,
                    "opp_label": _opp_label(opp_id),
                    "week_idx": week_idx,
                    "run_id": run_id,
                    "audit_id": audit_id,
                    "task_id": task_id,
                    "flw_id": flw,
                    "status": _task_status(opp_id, run_id, task_id),
                }
            )

        def _clean(c: dict) -> dict:
            return {k: v for k, v in c.items() if k != "status"}

        good: dict | None = None
        open_runs: list[dict] = []  # (one representative open cluster per open week)
        for run_id in sorted(by_run):
            clusters = by_run[run_id]
            resolved = [c for c in clusters if c["status"] == "closed"]
            still_open = [c for c in clusters if c["status"] == "investigating"]
            if still_open:
                open_runs.append(still_open[0])
            elif resolved and good is None:
                good = _clean(resolved[0])  # fully-resolved week -> grid "All resolved"

        # incomplete: the first open week in a different opp than good (fall back to
        # any open week if good is None or no other-opp open week exists).
        incomplete: dict | None = None
        good_opp = good["opp_id"] if good else None
        for c in open_runs:
            if c["opp_id"] != good_opp:
                incomplete = _clean(c)
                break
        if incomplete is None and open_runs:
            incomplete = _clean(open_runs[0])
        return good, incomplete
    finally:
        for tda in daos.values():
            tda.close()


# ---------------------------------------------------------------------- #
# URL builders (path-relative; the walkthrough spec carries base_url)
# ---------------------------------------------------------------------- #


def _run_path(definition_id: int, run_id: int, opp_id: int) -> str:
    return f"/labs/workflow/{definition_id}/run/?run_id={run_id}&opportunity_id={opp_id}"


def _audit_path(audit_id: int, opp_id: int) -> str:
    return f"/audit/{audit_id}/?opportunity_id={opp_id}"


def _task_path(task_id: int, opp_id: int) -> str:
    return f"/tasks/{task_id}/edit/?opportunity_id={opp_id}"


# ---------------------------------------------------------------------- #
# Ensurer entrypoint
# ---------------------------------------------------------------------- #


def ensure_rollup(resource, ctx) -> dict:
    """Build the cross-opp PAR run + emit the deferred drill-target vars.

    ``resource`` is a :class:`~..env_manifest.RollupResource`; ``ctx`` is the run's
    :class:`~..engine.EnsureContext`. Returns the full realized map of PAR + drill
    vars the walkthrough spec interpolates (``par_run_id`` / ``par_def_id`` /
    ``par_url`` / ``good_*`` / ``incomplete_*`` / ``flagged_flw_*`` plus the
    path-relative drill URLs).
    """
    from commcare_connect.labs.synthetic.walkthrough_kit import monday_dt, week_end_iso
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    realized: dict[str, Any] = {}
    weeks = ctx.weeks
    current_week = ctx.current_week
    if not weeks:
        raise ValueError("rollup: no completed weeks resolved — cannot build a PAR window")

    watched_sources = ctx.ids.get("chc_watched_sources")
    if not watched_sources:
        raise KeyError("rollup: ctx.ids['chc_watched_sources'] is missing (weekly_runs must run before rollup)")

    manifests_by_opp = {
        m.opportunity_id: m for k, m in ctx.ids.items() if isinstance(k, str) and k.startswith("manifest:")
    }

    primary_opp_id = resource.opportunity_ids[0]
    watched_opp_ids = [s["opportunity_id"] for s in watched_sources]

    # ---- window (same shaping program_admin_demo uses) ----
    window_start = weeks[0]
    # End window at today+1 so the rollup's run filter catches seeded runs even
    # when their completed_at is the historical Monday.
    window_end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    if current_week:
        par_completed_at = monday_dt(current_week, hour=8).isoformat()
    else:
        par_completed_at = (monday_dt(weeks[-1]) + dt.timedelta(days=1)).isoformat()

    wda = WorkflowDataAccess(opportunity_id=primary_opp_id, access_token=_LABS_ONLY_TOKEN)
    try:
        definition = _ensure_par_definition(wda, watched_sources)
        par_def_id = definition.id

        snapshot = _build_snapshot(
            token=_LABS_ONLY_TOKEN,
            weeks=weeks,
            window_start=window_start,
            window_end=window_end,
            watched_sources=watched_sources,
            manifests_by_opp=manifests_by_opp,
        )

        run_data = {
            "definition_id": par_def_id,
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

        existing = _existing_par_run(wda, par_def_id, watched_opp_ids)
        if existing is not None:
            # Refresh the snapshot/state in place (re-seed should re-roll up the
            # latest watched data) without minting a duplicate run.
            merged = {**existing.data, **run_data}
            wda.labs_api.update_record(
                record_id=existing.id,
                experiment="workflow",
                type="workflow_run",
                data=merged,
                current_record=existing,
            )
            par_run_id = existing.id
        else:
            par_rec = wda.labs_api.create_record(
                experiment="workflow",
                type="workflow_run",
                data=run_data,
            )
            par_run_id = par_rec.id
    finally:
        wda.close()

    par_url = _run_path(par_def_id, par_run_id, primary_opp_id)
    realized.update(
        {
            "par_def_id": par_def_id,
            "par_run_id": par_run_id,
            "par_url": par_url,
        }
    )
    # NOTE: ``workflow_def_id`` (the PRIMARY chc def, per the regenerate.py
    # contract) and ``opp_id`` / ``wk4_run_id`` / ``wk4_url`` are owned + emitted
    # by the ``weekly_runs`` ensurer — we do NOT re-emit (and must not clobber)
    # them here. The PAR definition id is exposed as ``par_def_id`` only.

    # ---- drill targets (selected straight from ctx.ids) ----
    good, incomplete = _select_drill_targets(ctx)
    if good:
        realized.update(
            {
                "good_opp_id": good["opp_id"],
                "good_opp_label": good["opp_label"],
                "good_week_idx": good["week_idx"],
                "good_run_id": good["run_id"],
                "good_audit_id": good["audit_id"],
                "good_task_id": good["task_id"],
                "flagged_flw_good": good["flw_id"],
                "chc_good_url": _run_path(
                    _watched_def_id(watched_sources, good["opp_id"]),
                    good["run_id"],
                    good["opp_id"],
                ),
                "audit_good_url": _audit_path(good["audit_id"], good["opp_id"]),
                "task_good_url": _task_path(good["task_id"], good["opp_id"]),
            }
        )
    else:
        logger.warning("rollup: no 'good' (closed task + audit) drill cluster found in ctx.ids")
    if incomplete:
        realized.update(
            {
                "incomplete_opp_id": incomplete["opp_id"],
                "incomplete_opp_label": incomplete["opp_label"],
                "incomplete_week_idx": incomplete["week_idx"],
                "incomplete_run_id": incomplete["run_id"],
                "incomplete_audit_id": incomplete["audit_id"],
                "incomplete_task_id": incomplete["task_id"],
                "flagged_flw_incomplete": incomplete["flw_id"],
                "audit_incomplete_url": _audit_path(incomplete["audit_id"], incomplete["opp_id"]),
                "task_incomplete_url": _task_path(incomplete["task_id"], incomplete["opp_id"]),
            }
        )
    else:
        logger.warning("rollup: no 'incomplete' (investigating task + audit) drill cluster found in ctx.ids")

    # ---- flagged_flw_manager: the FLW the live manager flow audits/coaches ----
    # That's the FLW flagged on the in-progress current week (week index ==
    # len(weeks)) of the primary opp. Derived from that opp's manifest anomalies
    # rather than a hardcoded username.
    manager_flw = _manager_flagged_flw(manifests_by_opp.get(primary_opp_id), len(weeks))
    if manager_flw:
        realized["flagged_flw_manager"] = manager_flw

    return realized


def _watched_def_id(watched_sources: list[dict], opp_id: int) -> int | None:
    """The chc workflow_definition_id for ``opp_id`` from the watched-source list."""
    for src in watched_sources:
        if src.get("opportunity_id") == opp_id:
            return src.get("workflow_definition_id")
    return None


def _manager_flagged_flw(manifest, current_week_idx: int) -> str | None:
    """The FLW flagged on the in-progress current week (the live manager-flow FLW).

    Mirrors ``regenerate._derive_manager_flagged_flw``, but sourced from the
    manifest's anomalies (the env's source of which flw flags which week) instead
    of an inline config roster: return the first FLW an anomaly concentrates on
    for ``current_week_idx`` (the in-progress week). Returns ``None`` when the
    manifest has no anomaly on that week.
    """
    if manifest is None:
        return None
    for anomaly in manifest.anomalies:
        weeks = set(anomaly.weeks or [])
        if anomaly.week is not None:
            weeks.add(anomaly.week)
        if current_week_idx in weeks and anomaly.flw_ids:
            return anomaly.flw_ids[0]
    return None
