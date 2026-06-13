"""Generate synthetic task and OCS transcript records from manifest."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .manifest import CoachingArc, TaskSpec, Timeline
from .ocs_templates import render_transcript


def _week_start(timeline: Timeline, week: int) -> dt.datetime:
    day_offset = (week - 1) * 7
    d = timeline.start_date + dt.timedelta(days=day_offset)
    return dt.datetime.combine(d, dt.time(9, 0))


def _build_from_task_spec(spec: TaskSpec, timeline: Timeline, persona_names: dict[str, str]) -> dict[str, Any]:
    created_at = _week_start(timeline, spec.created_week) + dt.timedelta(hours=2)
    completed_at = None
    if spec.status == "completed":
        completed_at = (created_at + dt.timedelta(days=3, hours=4)).isoformat()

    record: dict[str, Any] = {
        "assigned_to": spec.flw_id,
        "title": spec.title,
        "priority": spec.priority,
        "status": spec.status,
        "created_at": created_at.isoformat(),
        "synthetic": True,
    }
    if completed_at:
        record["completed_at"] = completed_at

    if spec.ocs_persona:
        flw_name = persona_names.get(spec.flw_id, spec.flw_id)
        record["ocs_conversation"] = render_transcript(
            template_key=spec.ocs_persona,
            flw_name=flw_name,
            base_timestamp=created_at + dt.timedelta(hours=1),
        )

    return record


def _build_from_coaching_arc(arc: CoachingArc, timeline: Timeline, persona_names: dict[str, str]) -> dict[str, Any]:
    created_at = _week_start(timeline, arc.week_triggered) + dt.timedelta(hours=3)
    flw_name = persona_names.get(arc.flw_id, arc.flw_id)

    if arc.transcript:
        conversation = [{"role": m.role, "text": m.text, "ts": m.ts.isoformat()} for m in arc.transcript]
    else:
        conversation = render_transcript(
            template_key=arc.persona, flw_name=flw_name, base_timestamp=created_at + dt.timedelta(hours=1)
        )

    # Match the schema TasksDataAccess.create_task writes so synthetic tasks
    # show up in the Tasks UI alongside human-created ones. The synthetic flag
    # + ocs_conversation field carry the demo-specific payload; the Tasks UI
    # ignores unknown fields, leaving room for a custom OCS-transcript renderer.
    return {
        "title": f"Coaching: {arc.target_behavior}",
        "description": f"Synthetic coaching task generated for {flw_name}.",
        "priority": "medium",
        "status": "completed",
        "username": arc.flw_id,
        "flw_name": flw_name,
        "user_id": None,
        "assigned_to_type": "self",
        "assigned_to_name": "Synthetic Coach",
        "audit_session_id": None,
        "workflow_run_id": None,
        "resolution_details": {},
        "events": [
            {
                "event_type": "created",
                "actor": "Synthetic Coach",
                "description": f"Coaching task created targeting: {arc.target_behavior}",
                "timestamp": created_at.isoformat(),
            },
            {
                "event_type": "resolved",
                "actor": "Synthetic Coach",
                "description": "Coaching arc completed.",
                "timestamp": (created_at + dt.timedelta(days=3)).isoformat(),
            },
        ],
        "ocs_conversation": conversation,
        "synthetic": True,
    }


def build_task_records(
    *,
    opportunity_id: int,
    tasks: list[TaskSpec],
    coaching_arcs: list[CoachingArc],
    timeline: Timeline,
    persona_names: dict[str, str],
) -> list[dict[str, Any]]:
    records = []
    for spec in tasks:
        records.append(_build_from_task_spec(spec, timeline, persona_names))
    for arc in coaching_arcs:
        records.append(_build_from_coaching_arc(arc, timeline, persona_names))
    return records
