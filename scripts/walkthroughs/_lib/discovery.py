"""PAR snapshot walker — discover drill targets from a completed PAR run.

The PAR snapshot's ``watched_summary`` lists each watched opportunity's
weekly runs and, per-run, an ``flw_rows`` list with ``{flw_id, flags,
audits, tasks}`` for every FLW that produced any of those three artifacts
during the run window. Recording scripts need to find two specific drill
targets in there:

  - A "good run" — completed audit + closed task (the satisfying drill).
  - An "incomplete run" — in_review audit + investigating task (the
    "manager left it open" drill).

The previous recorders each had their own copy of this walker (one in
``record_drill_through.py``, another in ``capture_walkthrough.py``)
and they were starting to drift. This is the canonical version.

This walker used to read a per-run ``decisions`` list keyed by
decision_type/audit_outcomes/task_outcomes (a row joining audits +
tasks together via a Decision record). That join went away in PR #281
when Decisions became Flags — audits and tasks now carry
``workflow_run_id`` directly and the snapshot groups them by ``flw_id``
into ``flw_rows`` independently. The walker still produces the same
``(opp, run, audit, task, flw)`` tuples; it just sources them from
the per-FLW group instead of a row-shaped join.

Note: PAR snapshots only include *completed* runs — in_progress runs
must be discovered separately (e.g. via the ``WK4_RUN_ID`` env var or
``.run_ids.json`` written by regenerate.py).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any


def _resolve_run_week_idx(run: dict, expected_weeks: list[str], missed: set[int]) -> int | None:
    """Return the week index this run belongs to, or None if it falls
    outside the expected weekly windows.

    Match by ``completed_at[:10]`` being within ``monday..monday+6d``.
    """
    completed = (run.get("completed_at") or "")[:10]
    if not completed:
        return None
    for idx, monday in enumerate(expected_weeks):
        if idx in missed:
            continue
        try:
            end = (dt.date.fromisoformat(monday) + dt.timedelta(days=6)).isoformat()
        except ValueError:
            continue
        if monday <= completed <= end:
            return idx
    return None


def find_drill_targets(
    api_get: Callable[[str], Any],
    par_run_id: int,
    *,
    labs_base_url: str,
    primary_opp_id: int,
) -> dict:
    """Walk the PAR snapshot and return drill targets.

    ``api_get`` is a callable that hits a URL and returns an object with
    ``.json()`` (typically ``playwright_page.request.get``).

    Returns::

        {
          "par_run_id": int,
          "good": {opp_id, opp_label, wf_def_id, week_idx, run_id, audit_id, task_id, flw_id} | None,
          "incomplete": {...} | None,
          "expected_weeks": [...],
        }

    "Good" picks a closed_satisfactory task first, then closed_warned as
    fallback (warned has fail thumbnails — less clean visually). "Incomplete"
    is the first in_review/in_progress audit with an investigating task.

    Raises ``RuntimeError`` if neither pair can be found — the recorders
    are useless without at least one drill target.
    """
    snap_resp = api_get(
        f"{labs_base_url}/labs/workflow/api/run/{par_run_id}/snapshot/" f"?opportunity_id={primary_opp_id}"
    ).json()
    state = snap_resp.get("snapshot", {}).get("state", {})
    summary = state.get("watched_summary", []) or []
    expected_weeks = state.get("expected_weeks", []) or []

    satisfactory: dict | None = None
    warned: dict | None = None
    incomplete: dict | None = None

    for src in summary:
        opp_label = src.get("label", "")
        opp_short = opp_label.split()[0] if opp_label else "Opp"
        missed = set(src.get("missed_week_idxs", []) or [])
        for run in src.get("runs", []):
            week_idx = _resolve_run_week_idx(run, expected_weeks, missed)
            if week_idx is None:
                continue
            # flw_rows is the post-flags-rename shape: one entry per
            # FLW that produced any audit/task/flag in this run. We pick
            # the first audit + first task for each FLW; the snapshot
            # builder orders them by recency.
            for fr in run.get("flw_rows", []) or []:
                audits = fr.get("audits", []) or []
                tasks = fr.get("tasks", []) or []
                if not audits or not tasks:
                    continue
                a, t = audits[0], tasks[0]
                target = {
                    "opp_id": src.get("opportunity_id"),
                    "opp_label": opp_short,
                    "wf_def_id": src.get("workflow_definition_id"),
                    "week_idx": week_idx,
                    "run_id": run["id"],
                    "audit_id": a["id"],
                    "task_id": t["id"],
                    "flw_id": fr.get("flw_id"),
                }
                a_status = a.get("status")
                t_status = t.get("status")
                if a_status == "completed" and t_status == "closed":
                    if satisfactory is None and t.get("official_action") == "satisfactory":
                        satisfactory = target
                    elif warned is None and t.get("official_action") == "warned":
                        warned = target
                elif incomplete is None and a_status in ("in_review", "in_progress") and t_status == "investigating":
                    incomplete = target

    good = satisfactory or warned
    if not good or not incomplete:
        raise RuntimeError(
            f"could not find a good + incomplete pair in PAR run {par_run_id} "
            f"(good={good!r}, incomplete={incomplete!r})"
        )
    return {
        "par_run_id": par_run_id,
        "good": good,
        "incomplete": incomplete,
        "expected_weeks": expected_weeks,
    }
