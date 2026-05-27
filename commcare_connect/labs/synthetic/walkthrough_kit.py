"""Generic synthetic-data primitives for walkthrough/demo recordings.

Lives next to ``archetypes.py`` (vocabulary) + ``manager_flow_views.py``
(per-FLW pipeline rows). Every reusable building block for "seed a
multi-week, multi-opp story into labs prod" goes here so the next
walkthrough doesn't reinvent them.

What's generic vs walkthrough-specific:

  - Generic (this module):
      * ``monday_dt``, ``VisitIdSequence`` — primitives.
      * ``cleanup_opportunity_workflows`` — parameterized by template type.
      * ``create_backdated_workflow_run`` — writes a workflow_run with
        backdated completed_at; takes a pre-built snapshot dict.
      * ``generate_audit_from_archetype`` / ``generate_task_from_archetype``
        — thin wrappers over ``archetypes.build_audit_data`` /
        ``build_task_data``.
      * ``apply_action_spec`` — materialize one (run, flw) action spec
        into (optional) Audit + (optional) Task records. Flags are NOT
        seeded by the synthetic flow: per-opp report render code computes
        them at render time from the pipeline data and persists them via
        view.ensureAutoFlags.

  - Walkthrough-specific (in the per-demo orchestrator):
      * The archetype trajectory ("solid does no_issues every week,
        suspended_repeat_offense trips twice then disappears").
      * The shape of the pipeline snapshot
        (``{"data": {"rows": [...]}}`` for chc_nutrition, something
        else for the next demo).
      * The workflow definition discovery / creation
        (template_key="chc_nutrition_analysis" vs other templates).

A new walkthrough generator should import from this module and supply
its own trajectory builder + pipeline-snapshot shape; it should not need
to touch any of the low-level LabsRecord write code.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

# ---------------------------------------------------------------------- #
# Primitives
# ---------------------------------------------------------------------- #


def monday_dt(monday_iso: str, *, hour: int = 9, minute: int = 0) -> dt.datetime:
    """Return a TZ-aware datetime at HH:MM UTC on the given ISO date."""
    d = dt.date.fromisoformat(monday_iso)
    return dt.datetime.combine(d, dt.time(hour, minute), tzinfo=dt.timezone.utc)


def week_end_iso(monday_iso: str, *, days: int = 6) -> str:
    """Return the ISO date for ``monday + days`` (default the following Sunday)."""
    return (dt.date.fromisoformat(monday_iso) + dt.timedelta(days=days)).isoformat()


class VisitIdSequence:
    """Per-orchestrator monotonic counter for synthetic visit_ids.

    Real ``visit_id`` values reference rows in CommCare HQ's UserVisit
    table; the synthetic generator has no live visits, but the bulk-
    assessment view keys ``visit_images`` by visit_id, so each audit needs
    a unique int. ``start=9_000_000`` keeps synthetic ids well clear of any
    real visits.
    """

    def __init__(self, start: int = 9_000_000) -> None:
        self._next = start

    def next(self) -> int:
        self._next += 1
        return self._next


# ---------------------------------------------------------------------- #
# Cleanup
# ---------------------------------------------------------------------- #


def cleanup_opportunity_workflows(
    *,
    wda,
    fda,
    tda,
    ada,
    opportunity_id: int,
    template_types: list[str],
) -> dict[str, int]:
    """Delete prior workflow_runs + flags + tasks + audits tied to an opp's
    workflow definitions whose ``template_type`` is in ``template_types``.

    Idempotent. Returns counts deleted (workflow_runs, flags, tasks,
    audits, definitions). The "definitions" entry is always 0 — we
    intentionally keep the WorkflowDefinition records so subsequent
    regenerations reuse them rather than churning IDs.
    """
    from commcare_connect.audit.models import AuditSessionRecord
    from commcare_connect.flags.models import FlagRecord

    deleted = {
        "workflow_runs": 0,
        "flags": 0,
        "tasks": 0,
        "audits": 0,
        "definitions": 0,
    }

    defs = [
        d for d in wda.list_definitions() if d.opportunity_id == opportunity_id and d.template_type in template_types
    ]

    run_ids: set[int] = set()
    for d in defs:
        for r in wda.list_runs(definition_id=d.id):
            if r.opportunity_id == opportunity_id:
                run_ids.add(r.id)

    if not run_ids:
        return deleted

    # Flags
    flags = fda.labs_api.get_records(
        experiment="flags",
        type="Flag",
        model_class=FlagRecord,
    )
    for f in flags:
        if f.workflow_run_id in run_ids:
            wda.labs_api.delete_records([f.id])
            deleted["flags"] += 1

    # Tasks
    for t in tda.get_tasks():
        if t.workflow_run_id in run_ids:
            wda.labs_api.delete_records([t.id])
            deleted["tasks"] += 1

    # Audits — link is either via labs_record_id == workflow_run_id or via
    # data["workflow_run_id"] depending on how the audit was created.
    audits = ada.labs_api.get_records(
        experiment="audit",
        type="AuditSession",
        model_class=AuditSessionRecord,
    )
    for a in audits:
        wf_run_id = a.data.get("workflow_run_id") or a.labs_record_id
        if wf_run_id in run_ids:
            wda.labs_api.delete_records([a.id])
            deleted["audits"] += 1

    # Runs last (so cascading writes are safe)
    wda.labs_api.delete_records(list(run_ids))
    deleted["workflow_runs"] = len(run_ids)

    return deleted


# ---------------------------------------------------------------------- #
# Backdated workflow run writer
# ---------------------------------------------------------------------- #


def create_backdated_workflow_run(
    *,
    wda,
    definition_id: int,
    opportunity_id: int,
    monday_iso: str,
    in_progress: bool = False,
    pipelines: dict | None = None,
    workers: list | None = None,
    state_extra: dict | None = None,
) -> int:
    """Write a workflow_run record directly with a backdated ``completed_at``.

    ``in_progress=True`` skips ``completed_at`` and sets status to
    ``"in_progress"`` — used for walkthroughs that record the manager
    finishing the run live during the demo.

    ``pipelines`` should be the per-template pipeline-data shape (e.g.
    ``{"data": {"rows": [...]}}`` for chc_nutrition). Pass ``None`` to
    skip the snapshot entirely; the recorder's render code typically
    falls back to live pipelines in that case.

    Returns the new run id.
    """
    completed_at = monday_dt(monday_iso).isoformat()
    period_start = monday_iso
    period_end = week_end_iso(monday_iso)

    state: dict[str, Any] = {"period_start": period_start, "period_end": period_end}
    if state_extra:
        state.update(state_extra)

    snapshot = {
        "workers": list(workers or []),
        "pipelines": dict(pipelines or {}),
        "state": dict(state),
    }

    data: dict[str, Any] = {
        "definition_id": definition_id,
        "opportunity_id": opportunity_id,
        "period_start": period_start,
        "period_end": period_end,
        "state": state,
        "snapshot": snapshot,
    }
    if in_progress:
        data["status"] = "in_progress"
    else:
        data["status"] = "completed"
        data["completed_at"] = completed_at

    rec = wda.labs_api.create_record(
        experiment="workflow",
        type="workflow_run",
        data=data,
    )
    return rec.id


# ---------------------------------------------------------------------- #
# Audit + Task generation from named archetypes
# ---------------------------------------------------------------------- #


def generate_audit_from_archetype(
    *,
    ada,
    opportunity_id: int,
    opportunity_name: str,
    workflow_run_id: int,
    flw_id: str,
    monday_iso: str,
    audit_archetype: str,
    visit_id: int,
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
        visit_id_base=visit_id,
    )
    rec = ada.labs_api.create_record(
        experiment="audit",
        type="AuditSession",
        data=data,
        labs_record_id=workflow_run_id,
        username=flw_id,
    )
    return rec.id


def generate_task_from_archetype(
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

    Returns the task id. See ``commcare_connect/labs/synthetic/archetypes.py``
    for the archetype catalog.
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


# ---------------------------------------------------------------------- #
# Action spec application — synthetic generator for per-(run, flw) audits
# and tasks. Flags are not seeded here; per-opp report render code
# computes them from the pipeline data at render time via
# view.ensureAutoFlags. The synthetic flow only needs to produce the
# artifacts that the manager would have created as actions in response.
# ---------------------------------------------------------------------- #


def apply_action_spec(
    *,
    tda,
    ada,
    spec: dict,
    workflow_run_id: int,
    opportunity_id: int,
    opportunity_name: str,
    flw_id: str,
    monday_iso: str,
    creator_name: str,
    visit_id_seq: VisitIdSequence,
) -> None:
    """Materialize one (run, flw) action spec into Audit + Task records.

    ``spec`` dict keys (all optional — empty spec is a no-op):
        - ``audit_archetype``: name of an audit archetype from archetypes.py
        - ``task_archetype``: name of a task archetype from archetypes.py
        - ``reason_label`` / ``reason_key``: only used as the task title hint
    """
    audit_archetype = spec.get("audit_archetype")
    task_archetype = spec.get("task_archetype")
    spawned_audit_id: int | None = None

    if audit_archetype:
        spawned_audit_id = generate_audit_from_archetype(
            ada=ada,
            opportunity_id=opportunity_id,
            opportunity_name=opportunity_name,
            workflow_run_id=workflow_run_id,
            flw_id=flw_id,
            monday_iso=monday_iso,
            audit_archetype=audit_archetype,
            visit_id=visit_id_seq.next(),
        )

    if task_archetype:
        task_title = f"[{spec.get('reason_label', spec.get('reason_key', 'Action'))}] {flw_id}"
        generate_task_from_archetype(
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
