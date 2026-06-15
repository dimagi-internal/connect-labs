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
        into (optional) Audit + (optional) Task records. Flags are not
        created here: per-opp report render code computes them at render
        time on live runs (view.ensureAutoFlags), and the per-demo
        orchestrator seeds the equivalent Flag records for COMPLETED runs
        (see program_admin_demo._seed_auto_flags_for_run).

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


def compose_task_title(*, flw_id: str, reason: str | None) -> str:
    """The ONE task-title grammar the synthetic generator uses.

    ``Coach isha_n — gender split off threshold`` — a sentence a manager
    would actually type. The previous dev-style ``[Gender split off
    threshold] isha_n`` bracket form read as a debug artifact on the task
    page and in the PAR drill panels. The reason label keeps its internal
    casing (acronyms like MUAC survive) but is decapitalized so the title
    reads as one phrase.
    """
    label = (reason or "follow-up").strip()
    label = label[:1].lower() + label[1:]
    return f"Coach {flw_id} — {label}"


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


# Floor for visit-id bases minted at RECORD time (live manager-flow audits).
# Kept inside the same 8-digit synthetic namespace as VisitIdSequence
# (9_000_001, 9_000_002, …) but in a disjoint sub-range so a live audit can
# never collide with a seeded one.
LIVE_VISIT_ID_FLOOR = 9_500_000
_LIVE_VISIT_ID_SPAN = 400_000


def live_visit_id_base(now_ts: float | None = None) -> int:
    """Visit-id base for audits created LIVE during a walkthrough recording.

    Seeded audits draw bases from ``VisitIdSequence`` so their photo cards
    render visit ids like ``#90000010``. Live manager-flow audits used to
    derive the base from a millisecond epoch, which produced 11-digit ids —
    the same bulk-assessment UI then displayed two visibly different id
    grammars between seeded and live audits. This keeps live ids in the
    same 8-digit ``9X XXX XXX`` shape (bases 9_500_000..9_899_999 →
    visit ids 95_000_000..98_999_999), time-derived for uniqueness across
    recorder reruns.
    """
    import time as _time

    ts = int(now_ts if now_ts is not None else _time.time())
    return LIVE_VISIT_ID_FLOOR + ts % _LIVE_VISIT_ID_SPAN


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
    flw_name: str | None = None,
) -> int:
    """Generate an AuditSession record from a named audit archetype.

    Returns the audit id. The archetype controls status / overall_result /
    image set (real blob_ids backed by the MUAC stock corpus); see
    ``commcare_connect/labs/synthetic/archetypes.py``. ``flw_name`` is the
    worker's real display name, stamped onto the audit data + visit cards.
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
        flw_name=flw_name,
    )
    rec = ada.labs_api.create_record(
        experiment="audit",
        type="AuditSession",
        data=data,
        labs_record_id=workflow_run_id,
        username=flw_id,
    )
    return rec.id


def update_audit_from_archetype(
    *,
    ada,
    audit_id: int,
    opportunity_id: int,
    opportunity_name: str,
    workflow_run_id: int,
    flw_id: str,
    monday_iso: str,
    audit_archetype: str,
    visit_id: int,
    flw_name: str | None = None,
) -> int:
    """Rebuild an EXISTING AuditSession to a named archetype (upsert).

    Mirror of :func:`generate_audit_from_archetype` for the reconcile path: when
    an FLW's coaching arc has since resolved (or moved to a different audit
    shape) but the existing seeded audit still carries the old status, this
    rebuilds its ``data`` in place via ``update_record`` so the record matches
    the arc's current state. Keeps the same record id (and its
    ``workflow_run_id`` linkage) so downstream consumers and idempotency keys are
    stable. Returns the audit id.
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
        flw_name=flw_name,
    )
    ada.labs_api.update_record(
        record_id=audit_id,
        experiment="audit",
        type="AuditSession",
        data=data,
        labs_record_id=workflow_run_id,
        username=flw_id,
    )
    return audit_id


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
    reason_key: str | None = None,
    flw_name: str | None = None,
) -> int:
    """Generate a Task record from a named task archetype.

    ``reason_key`` (e.g. ``gender_skew``) selects a reason-specific coaching
    conversation variant when one exists, so the transcript talks about the
    same issue the task's flag asserts. ``flw_name`` is the worker's real
    display name, written to the task's ``flw_name`` field — the task hero
    header and tasks list read it instead of the raw username. Returns the
    task id. See ``commcare_connect/labs/synthetic/archetypes.py`` for the
    archetype catalog.
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
        reason_key=reason_key,
        flw_name=flw_name,
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
# and tasks. Flags are not created here: live runs derive them at render
# time via view.ensureAutoFlags, and the per-demo orchestrator seeds them
# for completed runs (program_admin_demo._seed_auto_flags_for_run). This
# module only produces the artifacts the manager would have created as
# actions in response.
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
    flw_name: str | None = None,
) -> None:
    """Materialize one (run, flw) action spec into Audit + Task records.

    ``spec`` dict keys (all optional — empty spec is a no-op):
        - ``audit_archetype``: name of an audit archetype from archetypes.py
        - ``task_archetype``: name of a task archetype from archetypes.py
        - ``reason_label``: human label composed into the task title
        - ``reason_key``: selects the reason-matched coaching conversation
          variant (see ``generator/ocs_templates.py``) so a gender-split
          task never closes on a photo-framing transcript

    ``flw_name`` is the worker's real human display name; it's stamped onto
    the Audit + Task records so the task hero header / audit cards / PAR
    drill render a real name instead of the raw ``flw_id`` username.
    Defaults to ``flw_id``. ``creator_name`` is the (already human-readable)
    name of the manager who took the action — used for task authorship and
    the title is composed from the worker's display name.
    """
    audit_archetype = spec.get("audit_archetype")
    task_archetype = spec.get("task_archetype")
    spawned_audit_id: int | None = None
    flw_name = flw_name or flw_id

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
            flw_name=flw_name,
        )

    if task_archetype:
        task_title = compose_task_title(flw_id=flw_name, reason=spec.get("reason_label") or spec.get("reason_key"))
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
            reason_key=spec.get("reason_key"),
            flw_name=flw_name,
        )
