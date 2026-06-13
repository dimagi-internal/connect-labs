"""The ``tasks`` ensurer: coaching Tasks from the manifest's ``coaching_arcs``.

For every opp whose manifest was stashed by ``opp_data`` (``ctx.ids["manifest:*"]``),
this ensurer walks the manifest's ``coaching_arcs`` and ensures one coaching
``Task`` per arc — the record that makes the PAR task scene show a REAL
conversation with REAL names instead of the canned-bot hack.

**What an arc carries that the task page reads:**

- ``arc.flw_id`` + the flagged persona's ``display_name`` -> the task's
  ``flw_name`` (the task hero header and tasks list render
  ``Task.flw_name == data["flw_name"] or data["username"]``),
- ``arc.transcript`` (a list of ``CoachingMessage(role, text, ts)``) -> the
  task's ``ocs_conversation`` field, which the Tasks UI renders as the
  "Coaching Conversation" panel. The arc's OWN messages are used verbatim
  (not a re-rendered OCS template), so the panel shows the exact bot/worker
  exchange the manifest authored.
- ``arc.follow_up_outcome_week`` -> whether the loop CLOSED. An arc with a
  follow-up outcome resolved (a ``closed_satisfactory`` task: status
  ``closed``, the "Coaching Conversation" panel persists after close — the
  PAR ``task_good_url`` scene). An arc with no outcome is still open (an
  ``investigating`` task: a partial, mid-flight exchange — the
  ``task_incomplete_url`` scene).

**Which run a coaching task lands on.** An arc's ``week_triggered`` is 1-based;
``week_idx = week_triggered - 1`` indexes ``ctx.weeks``. The run is the chc run
``weekly_runs`` stamped on ``ctx.ids[f"run:{opp}:{week}"]`` for that week — the
same run ``run_audits`` linked the arc's FLW audit to. The current-week
``in_progress`` run is skipped (the live manager flow creates its own tasks on
camera).

**Audit linkage.** If ``run_audits`` stashed an audit for this (run, flw) at
``ctx.ids[f"audit:{run_id}:{flw}"]``, it is passed as ``audit_session_id`` so the
task and its triggering image review are cross-linked (the task page's "view the
audit" affordance resolves).

**Creator name.** The task's creator is the network manager who took the action,
rendered as a real human name. The manifest has no separate manager entity, so —
mirroring ``weekly_runs``' flag attribution — a persona's ``display_name`` stands
in: the first persona that is NOT the arc's own FLW (a manager coaching a
different worker reads correctly), falling back to the first persona.

**Idempotency** is keyed on ``(workflow_run_id, flw_id, archetype)`` via
``TaskDataAccess.get_tasks_for_run``: a matching task is reused, a missing one is
created. Re-runs are therefore count-stable.

**Realized vars.** This ensurer does NOT emit the PAR ``task_good_url`` /
``task_incomplete_url`` drill-target vars. Selecting which (run, flw) task is the
"good" (closed-with-transcript) vs "incomplete" (open) drill target is a cross-opp
decision the rollup ensurer (Task 8) makes from the full PAR snapshot — the same
deferral ``run_audits`` uses. Instead this ensurer stashes
``ctx.ids[f"task:{run_id}:{flw}"] = task_id`` for every task it ensures, so the
rollup can pick the named targets from a complete map.

It reuses the shared synthetic kit's ``generate_task_from_archetype`` (which calls
``archetypes.build_task_data``) directly — no dependency on PAR-specific helpers in
``program_admin_demo.py``. (``_display_name_for`` / ``compose_task_title`` usage
mirrors ``weekly_runs`` / ``run_audits`` rather than importing the demo module.)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Mirrors weekly_runs / run_audits: labs-only opps short-circuit to the in-process
# backend, so the token is never sent anywhere.
_LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 (not a secret)

# Arc -> task archetype. A coaching arc that recorded a follow-up outcome resolved
# the flag (the "good" closed-with-transcript task the PAR good_url scene reads);
# one without is still mid-flight (the "incomplete" open task).
_CLOSED_ARCHETYPE = "closed_satisfactory"
_OPEN_ARCHETYPE = "investigating"


def _display_name_for(persona) -> str:
    """Real human name for a persona: its ``display_name`` or a title-cased id.

    Mirrors ``weekly_runs._display_name_for`` / ``run_audits._display_name_for`` so
    tasks, audits and runs agree on names.
    """
    if persona.display_name:
        return persona.display_name
    token = persona.id.split("_", 1)[0]
    return token[:1].upper() + token[1:] if token else persona.id


def _creator_name_for(manifest, arc_flw_id: str) -> str:
    """A real human creator (the coaching manager) for an arc's task.

    The manifest has no separate manager entity, so a persona's display name
    stands in — the first persona that is NOT the arc's own FLW (a manager
    coaching a different worker), falling back to the first persona, then to a
    generic label. Mirrors ``weekly_runs``' flag attribution convention.
    """
    for persona in manifest.flw_personas:
        if persona.id != arc_flw_id:
            return _display_name_for(persona)
    if manifest.flw_personas:
        return _display_name_for(manifest.flw_personas[0])
    return "Program Manager"


def _archetype_for_arc(arc) -> str:
    """``closed_satisfactory`` when the arc's loop closed, else ``investigating``."""
    return _CLOSED_ARCHETYPE if arc.follow_up_outcome_week is not None else _OPEN_ARCHETYPE


def _fill_flw_name(text: str, flw_name: str) -> str:
    """Substitute a ``{flw_name}`` placeholder in an arc message, brace-safely.

    Arc transcripts may write the worker's name inline OR use the same
    ``{flw_name}`` placeholder convention the OCS templates use. We honor the
    placeholder so an authored ``"Hi {flw_name}"`` renders the real name, but
    fall back to the literal text if it contains other ``{...}`` braces that
    ``str.format`` can't resolve (so a stray brace never raises).
    """
    if "{flw_name}" not in text:
        return text
    try:
        return text.format(flw_name=flw_name)
    except (KeyError, IndexError, ValueError):
        return text.replace("{flw_name}", flw_name)


def _transcript_from_arc(arc, flw_name: str) -> list[dict]:
    """The arc's own messages in the ``ocs_conversation`` shape the Tasks UI reads.

    Each ``CoachingMessage`` (``role`` in {bot, flw}, ``text``, ``ts``) maps 1:1 to
    the ``{role, text, ts}`` entry shape ``archetypes.build_task_data`` /
    ``ocs_templates.render_transcript`` produce — so the panel renders the
    manifest's authored exchange verbatim instead of a re-templated one. Any
    ``{flw_name}`` placeholder is filled with the persona's real display name,
    matching the OCS-template convention.
    """
    return [{"role": m.role, "text": _fill_flw_name(m.text, flw_name), "ts": m.ts.isoformat()} for m in arc.transcript]


def ensure_tasks(resource, ctx) -> dict:
    """Ensure one coaching Task per manifest ``coaching_arc``.

    ``resource`` is a :class:`~..env_manifest.TasksResource`; ``ctx`` is the run's
    :class:`~..engine.EnsureContext`. For each stashed ``manifest:<opp>``, for each
    ``coaching_arc``, ensures one ``Task`` on the arc's triggered week's run
    (skipping the current-week in_progress run), carrying the persona's real
    ``flw_name``, the arc's transcript, a real ``creator_name``, and a link to the
    arc FLW's audit when ``run_audits`` stashed one. Stashes
    ``ctx.ids[f"task:{run_id}:{flw}"] = task_id`` for the rollup ensurer's
    drill-target selection. Returns an empty realized map — the ``task_good_url`` /
    ``task_incomplete_url`` vars are deferred to the rollup ensurer.
    """
    from commcare_connect.labs.synthetic.walkthrough_kit import compose_task_title, generate_task_from_archetype
    from commcare_connect.tasks.data_access import TaskDataAccess

    weeks = ctx.weeks
    current_week = ctx.current_week

    manifest_keys = sorted(k for k in ctx.ids if isinstance(k, str) and k.startswith("manifest:"))

    tasks_ensured = 0
    tasks_reused = 0

    for key in manifest_keys:
        manifest = ctx.ids[key]
        opp_id = manifest.opportunity_id
        persona_by_id = {p.id: p for p in manifest.flw_personas}
        if not manifest.coaching_arcs:
            continue

        tda = TaskDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            for arc in manifest.coaching_arcs:
                persona = persona_by_id.get(arc.flw_id)
                if persona is None:
                    # The manifest validator rejects arcs referencing unknown
                    # flw_ids, so this is defensive only.
                    continue

                week_idx = arc.week_triggered - 1
                if week_idx < 0 or week_idx >= len(weeks):
                    # Arc targets a week outside the resolved completed window
                    # (e.g. only the live current week). No completed run to land
                    # the coaching task on.
                    continue
                monday_iso = weeks[week_idx]
                if monday_iso == current_week:
                    # The live manager-flow week: its tasks are created on camera.
                    continue

                run_id = ctx.ids.get(f"run:{opp_id}:{monday_iso}")
                if run_id is None:
                    raise KeyError(
                        f"tasks: no run stashed for opp {opp_id} week {monday_iso} "
                        "(weekly_runs must run before tasks)"
                    )

                flw_name = _display_name_for(persona)
                creator_name = _creator_name_for(manifest, arc.flw_id)
                archetype = _archetype_for_arc(arc)
                audit_session_id = ctx.ids.get(f"audit:{run_id}:{arc.flw_id}")

                existing = [
                    t
                    for t in tda.get_tasks_for_run(run_id)
                    if t.data.get("username") == arc.flw_id and t.data.get("synthetic_archetype") == archetype
                ]
                if existing:
                    task_id = existing[0].id
                    tasks_reused += 1
                else:
                    title = compose_task_title(flw_id=flw_name, reason=arc.target_behavior)
                    task_id = generate_task_from_archetype(
                        tda=tda,
                        opportunity_id=opp_id,
                        workflow_run_id=run_id,
                        audit_session_id=audit_session_id,
                        flw_id=arc.flw_id,
                        monday_iso=monday_iso,
                        title=title,
                        task_archetype=archetype,
                        creator_name=creator_name,
                        flw_name=flw_name,
                    )
                    # Overlay the arc's OWN transcript onto the task (the kit
                    # otherwise renders an OCS-template conversation) and tag the
                    # archetype for idempotency keying. The arc messages ARE the
                    # conversation the PAR scene scrolls through.
                    task = tda.get_task(task_id)
                    task.data["ocs_conversation"] = _transcript_from_arc(arc, flw_name)
                    task.data["synthetic_archetype"] = archetype
                    tda.save_task(task)
                    tasks_ensured += 1

                ctx.ids[f"task:{run_id}:{arc.flw_id}"] = task_id
        finally:
            tda.close()

    logger.info(
        "tasks: ensured %d new + reused %d existing coaching tasks",
        tasks_ensured,
        tasks_reused,
    )

    # PAR task_good_url / task_incomplete_url drill targets are selected by the
    # rollup ensurer from ctx.ids["task:*"]; nothing authoritative to emit here.
    return {}
