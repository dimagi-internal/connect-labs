"""Manager-flow demo endpoints — synthetic-only helpers that let a walkthrough
recorder drive a believable "manager doing the work live" scene without
requiring real OCS bot wiring on every synthetic opportunity.

Two endpoints, both scoped to an in_progress workflow run:

- POST /labs/workflow/api/run/<run_id>/manager-audit/
    Body: {opportunity_id, flw_id}
    Atomically creates a `completed_pass_clean` AuditSession (5/5 good-pool
    photos, overall_result=pass) AND a Decision linking it to the FLW for
    that run. Returns {audit_id, decision_id, redirect_url}.

- POST /labs/workflow/api/run/<run_id>/manager-coaching/
    Body: {opportunity_id, flw_id, task_id, reason_key, reason_label, prompt_text}
    Attaches a believable in-progress OCS coaching conversation onto an
    existing task (so the task page renders a real-looking transcript) AND
    creates a Decision linking task → FLW (carrying forward any audit ids
    from a prior manager-audit call so the row shows both).

Both endpoints mirror the synthetic generator's direct-labs_api write style
(see commcare_connect/labs/synthetic/program_admin_demo.py) so the records
they produce are indistinguishable from seeded ones.
"""

from __future__ import annotations

import json
import logging
import time

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _coaching_conversation(prompt_text: str) -> list[dict]:
    """A 4-message in-progress coaching transcript suitable for the task page.

    The structure mirrors what ``task.data["ocs_conversation"]`` looks like
    for seeded archetype tasks, so the task detail template renders the
    same way. The conversation deliberately stops mid-flow (no closing
    message) to convey "still open" — matching the demo narrative.
    """
    return [
        {"role": "bot", "text": prompt_text},
        {
            "role": "flw",
            "text": (
                "Thanks for reaching out. I usually wrap the tape near the elbow because "
                "it's easier to keep the arm steady there. Is that wrong?"
            ),
        },
        {
            "role": "bot",
            "text": (
                "Good catch — that's likely the source of the skew. The MUAC tape should sit "
                "at the midpoint between the shoulder tip and the elbow, on a relaxed arm hanging "
                "by the child's side. Measuring near the elbow gives you a wider reading because "
                "the muscle is thicker there. Could you try the midpoint position on your next 3 "
                "visits and let me know what readings you get?"
            ),
        },
        {
            "role": "flw",
            "text": "Okay, I'll try the midpoint and circle back after my next shift.",
        },
    ]


@csrf_exempt
@require_http_methods(["POST"])
def manager_audit_create_api(request: HttpRequest, run_id: int) -> JsonResponse:
    """Create a completed_pass_clean audit + linking decision for the
    manager-flow demo. See module docstring.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    opportunity_id = body.get("opportunity_id")
    flw_id = body.get("flw_id")
    if not opportunity_id or not flw_id:
        return JsonResponse({"error": "opportunity_id and flw_id are required"}, status=400)

    opportunity_id = int(opportunity_id)
    run_id = int(run_id)

    from commcare_connect.audit.data_access import AuditDataAccess
    from commcare_connect.decisions.data_access import DecisionsDataAccess
    from commcare_connect.labs.synthetic.archetypes import build_audit_data
    from commcare_connect.workflow.data_access import WorkflowDataAccess

    try:
        wda = WorkflowDataAccess(request=request, opportunity_id=opportunity_id)
        run = wda.get_run(run_id)
        if run is None:
            return JsonResponse({"error": f"Run {run_id} not found"}, status=404)
        if run.is_completed:
            return JsonResponse(
                {"error": "Run is completed; manager-flow endpoints are for in_progress runs only"},
                status=409,
            )
        monday_iso = run.data.get("period_start") or run.data.get("state", {}).get("period_start")
        if not monday_iso:
            return JsonResponse({"error": "Run is missing period_start"}, status=400)

        # Build the audit's data dict via the same archetype helper the seed
        # uses. visit_id_base = millisecond epoch so each manager click gets
        # a unique pool — running the recorder twice in a session shouldn't
        # collide visit_ids across audits.
        visit_id_base = int(time.time() * 1000) & 0x7FFFFFFF
        opp_name = run.data.get("opportunity_name") or ""  # cosmetic only
        audit_data = build_audit_data(
            archetype_name="completed_pass_clean",
            flw_id=flw_id,
            monday_iso=monday_iso,
            opportunity_id=opportunity_id,
            opportunity_name=opp_name,
            workflow_run_id=run_id,
            visit_id_base=visit_id_base,
        )

        ada = AuditDataAccess(request=request, opportunity_id=opportunity_id)
        audit_rec = ada.labs_api.create_record(
            experiment="audit",
            type="AuditSession",
            data=audit_data,
            labs_record_id=run_id,
            username=flw_id,
        )

        dda = DecisionsDataAccess(request=request, opportunity_id=opportunity_id)
        decision = dda.create_decision(
            workflow_run_id=run_id,
            opportunity_id=opportunity_id,
            flw_id=flw_id,
            decision_type="action_taken",
            reason_key="bad_muac_distribution",
            reason_label="Bad MUAC distribution",
            audit_session_ids=[audit_rec.id],
            task_ids=None,
            notes=None,
            decided_by=getattr(request.user, "username", None),
        )
    except Exception as exc:  # noqa: BLE001 — demo helper, log + 500
        logger.exception("manager_audit_create_api failed")
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(
        {
            "audit_id": audit_rec.id,
            "decision_id": decision.id,
            "redirect_url": f"/audit/{audit_rec.id}/?opportunity_id={opportunity_id}",
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
def manager_coaching_attach_api(request: HttpRequest, run_id: int) -> JsonResponse:
    """Attach a synthetic OCS coaching conversation onto an existing task and
    record a Decision linking task → FLW. See module docstring.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    required = ("opportunity_id", "flw_id", "task_id", "prompt_text")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return JsonResponse({"error": f"Missing required fields: {missing}"}, status=400)

    from commcare_connect.decisions.data_access import DecisionsDataAccess
    from commcare_connect.tasks.data_access import TaskDataAccess

    opportunity_id = int(body["opportunity_id"])
    flw_id = body["flw_id"]
    task_id = int(body["task_id"])
    prompt_text = body["prompt_text"]
    reason_key = body.get("reason_key") or "bad_muac_distribution"
    reason_label = body.get("reason_label") or "Bad MUAC distribution"

    try:
        tda = TaskDataAccess(request=request, opportunity_id=opportunity_id)
        task = tda.get_task(task_id)
        if task is None:
            return JsonResponse({"error": f"Task {task_id} not found"}, status=404)

        # Inject the synthetic OCS conversation directly onto the task data.
        # The task detail template reads task.data["ocs_conversation"]
        # (commcare_connect/tasks/views.py:249), so this is the only field
        # the FE needs to render the coaching transcript.
        updated_data = dict(task.data or {})
        updated_data["ocs_conversation"] = _coaching_conversation(prompt_text)
        updated_data["ocs_status"] = "in_progress"
        # Once the manager has fired the coaching prompt, the "Start AI
        # Coaching" panel on the task page should disappear.
        updated_data.pop("coaching_pending", None)

        # Persist via the raw labs_api (same pattern the synthetic seed uses)
        # — TaskDataAccess.save_task wraps a write of the whole record.
        task_with_update = task
        task_with_update.data = updated_data
        tda.save_task(task_with_update)

        dda = DecisionsDataAccess(request=request, opportunity_id=opportunity_id)
        prior = next(
            (d for d in dda.get_decisions_for_run(run_id) if d.flw_id == flw_id and d.audit_session_ids),
            None,
        )
        audit_ids = prior.audit_session_ids if prior else None
        decision = dda.create_decision(
            workflow_run_id=run_id,
            opportunity_id=opportunity_id,
            flw_id=flw_id,
            decision_type="action_taken",
            reason_key=reason_key,
            reason_label=reason_label,
            audit_session_ids=audit_ids,
            task_ids=[task_id],
            notes=None,
            decided_by=getattr(request.user, "username", None),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("manager_coaching_attach_api failed")
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"decision_id": decision.id, "task_id": task_id}, status=201)
