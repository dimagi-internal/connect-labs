"""Manager-flow demo endpoints — synthetic-only helpers that let a walkthrough
recorder drive a believable "manager doing the work live" scene without
requiring real OCS bot wiring on every synthetic opportunity.

Two endpoints, both scoped to an in_progress workflow run:

- POST /labs/workflow/api/run/<run_id>/manager-audit/
    Body: {opportunity_id, flw_id, filter?}
    Atomically creates a `pending_all_clean` AuditSession (5 good-pool
    photos, all UNREVIEWED) so the walkthrough can film the manager passing
    each one. Returns {audit_id, redirect_url}. The audit carries
    ``labs_record_id = workflow_run_id`` so the program-admin rollup can
    find it by run.

- POST /labs/workflow/api/run/<run_id>/manager-coaching/
    Body: {opportunity_id, flw_id, task_id, prompt_text}
    Attaches a believable in-progress OCS coaching conversation onto an
    existing task (so the task page renders a real-looking transcript).
    Returns {task_id}. The task already carries workflow_run_id from when
    it was created via /tasks/api/single-create/.

Neither endpoint creates Flag records — flags are findings derived from
data by the per-opp report's render code, not side-effects of an action.
"""

from __future__ import annotations

import json
import logging
import time

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _coaching_conversation(prompt_text: str, flw_name: str = "there") -> list[dict]:
    """An in-progress coaching transcript suitable for the task page.

    Structure:
      1. A ``system`` entry holding the manager's *instruction* to the
         assistant (the "Prompt Instructions" the manager typed). This
         is rendered as a distinct setup banner — NOT as the assistant's
         first chat message — so viewers see "here's what the assistant
         was told to do" separately from the conversation it then had.
      2. The assistant's own opening message (generated from, but not
         echoing, the instruction).
      3. The worker's reply, the assistant's coaching, and the worker's
         acknowledgement.

    The conversation deliberately stops mid-flow (no closing message) to
    convey "still open" — matching the demo narrative. It's written to be
    coherent with the cherry-picking flag (low SAM/MAM = only visiting
    easier, better-nourished households), which is what the post-PR-281
    flag direction means.
    """
    return [
        {"role": "system", "text": prompt_text},
        {
            "role": "bot",
            "text": (
                "Hi " + flw_name + "! Your supervisor asked me to check in about this week's "
                "visits. The screening numbers came in lower than we'd usually expect for your "
                "area — almost no children flagged as malnourished. Can you tell me a bit about "
                "which households you were able to reach this week?"
            ),
        },
        {
            "role": "flw",
            "text": (
                "Mostly the ones close to the health post and along the main road — they're "
                "quickest to get to and the families are usually expecting me."
            ),
        },
        {
            "role": "bot",
            "text": (
                "That's a sensible way to cover a lot of visits, but those closer households tend "
                "to be better off — the children most at risk of malnutrition are often in the "
                "harder-to-reach homes further out. If we only see the easy ones, we can miss the "
                "kids who most need screening. Could you plan next week's route to include a few "
                "of the further households you'd normally skip?"
            ),
        },
        {
            "role": "flw",
            "text": (
                "Okay, that's fair. I'll map out the homes on the far side and include them in " "next week's visits."
            ),
        },
    ]


@csrf_exempt
@require_http_methods(["POST"])
def manager_audit_create_api(request: HttpRequest, run_id: int) -> JsonResponse:
    """Create a fresh (all-pending, clean-pool) audit for the manager-flow demo.

    Uses the ``pending_all_clean`` archetype so the audit lands with 5
    unreviewed clean photos — the walkthrough then films the manager
    passing each one on camera and the audit resolves to an all-pass.
    (It used to seed ``completed_pass_clean``, which arrived already
    reviewed, leaving nothing for the manager to actually do.)

    The audit is linked back to the run via ``labs_record_id = run_id`` so
    the program-admin rollup can find it. No Flag is created here — flags
    are findings, not action side-effects.
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
            archetype_name="pending_all_clean",
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
    except Exception as exc:  # noqa: BLE001 — demo helper, log + 500
        logger.exception("manager_audit_create_api failed")
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse(
        {
            "audit_id": audit_rec.id,
            "redirect_url": f"/audit/{audit_rec.id}/?opportunity_id={opportunity_id}",
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
def manager_coaching_attach_api(request: HttpRequest, run_id: int) -> JsonResponse:
    """Attach a synthetic OCS coaching conversation onto an existing task.

    No Flag or Decision side-effect — the task already exists and carries
    workflow_run_id from when it was created via /tasks/api/single-create/.
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    required = ("opportunity_id", "flw_id", "task_id", "prompt_text")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return JsonResponse({"error": f"Missing required fields: {missing}"}, status=400)

    from commcare_connect.tasks.data_access import TaskDataAccess

    opportunity_id = int(body["opportunity_id"])
    task_id = int(body["task_id"])
    prompt_text = body["prompt_text"]

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
        updated_data["ocs_conversation"] = _coaching_conversation(
            prompt_text, flw_name=task.flw_name or task.username or "there"
        )
        updated_data["ocs_status"] = "in_progress"
        # Once the manager has fired the coaching prompt, the "Start AI
        # Coaching" panel on the task page should disappear.
        updated_data.pop("coaching_pending", None)

        # Persist via the raw labs_api (same pattern the synthetic seed uses)
        # — TaskDataAccess.save_task wraps a write of the whole record.
        task.data = updated_data
        tda.save_task(task)
    except Exception as exc:  # noqa: BLE001
        logger.exception("manager_coaching_attach_api failed")
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"task_id": task_id}, status=201)
