"""Starting an OCS conversation for a task — the one implementation.

Two callers: the task page's "start AI conversation" endpoint
(``views.task_initiate_ai``, with the browser session's OCS token), and a workflow
action run in the background (``workflow/actions.py``, with the user's stored
``UserOCSToken``).
"""

from __future__ import annotations

import logging

from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess
from connect_labs.workflow import coach_briefing

logger = logging.getLogger(__name__)

#: The bot OCSBotsListAPIView offers on a synthetic opportunity: no OCS account is
#: involved, and a canned coaching conversation is attached instead.
SYNTHETIC_BOT = "synthetic-muac-coaching"


def start_ai_session(
    user,
    data_access,
    task,
    *,
    ocs=None,
    identifier: str,
    experiment: str,
    prompt_text: str,
    platform: str = "commcare_connect",
    start_new_session: bool = False,
    on_behalf_of: str | None = None,
    coach_image: dict | None = None,
    participant_data: dict | None = None,
) -> dict:
    """Start (or attach) the conversation as ``user`` and record it on ``task``,
    which is saved.

    ``ocs`` is the caller's ``OCSDataAccess`` (a browser request's, or one built for
    the user with no request); when omitted one is made for ``user`` from their
    stored token. Returns ``{"session_id", "status", "message"}``. Raises
    ``OCSAPIError`` when OCS refuses; the caller decides what the person sees.

    ``on_behalf_of`` marks a QA redirect: ``identifier`` is then a staff member's own
    ConnectID username, receiving the conversation meant for that worker. It is
    recorded on the session (``qa_recipient`` / ``on_behalf_of``), and on a synthetic
    opportunity the conversation is REAL -- the point is to QA the actual bot.

    ``coach_image`` (``{"url", "caption"}``, from ``workflow/coach_image.attachment``)
    puts a picture of the worker's figures in the session state as
    ``coach_image_url`` / ``coach_image_caption`` -- only beside a briefing, which is
    what it pictures.

    ``participant_data`` is merged by OCS into the participant's record for THIS bot when the
    conversation starts, before the opening message is sent. A dashboard uses it to reset what
    the bot tracks per task (e.g. ``{"chatbot_task_status": "not_started"}``): OCS writes the
    opening message outside the bot's pipeline, so without it the record keeps the PREVIOUS
    task's status until the worker first replies. Ignored on a synthetic opportunity.
    """
    from connect_labs.labs.synthetic.manager_flow_views import _coaching_conversation
    from connect_labs.labs.synthetic.registry import get_synthetic_opp

    actor_name = user.get_display_name()
    session_params = {
        "identifier": identifier,
        "experiment": experiment,
        "platform": platform,
        "prompt_text": prompt_text,
    }
    qa_redirect = {"qa_recipient": identifier, "on_behalf_of": on_behalf_of} if on_behalf_of else {}
    session_params.update(qa_redirect)

    # Synthetic-opp short circuit: skip the real OCS call and attach a canned
    # coaching transcript directly onto the task — keeps the manager-flow demo
    # self-contained without requiring a real OCS account / experiment.
    if experiment == SYNTHETIC_BOT or (not qa_redirect and get_synthetic_opp(int(task.opportunity_id)) is not None):
        updated_data = dict(task.data or {})
        updated_data["ocs_conversation"] = _coaching_conversation(
            prompt_text, flw_name=task.flw_name or task.username or "there"
        )
        updated_data["ocs_status"] = "in_progress"
        updated_data.pop("coaching_pending", None)
        task.data = updated_data
        data_access.save_task(task)
        data_access.add_ai_session(
            task,
            actor=actor_name,
            session_params=session_params,
            session_id="synthetic-coaching-session",
            status="completed",
        )
        return {
            "session_id": "synthetic-coaching-session",
            "status": "completed",
            "message": "Synthetic coaching conversation started.",
        }

    # Session data links the OCS session back to Connect.
    session_data = {
        "task_id": str(task.id),
        "opportunity_id": str(task.opportunity_id),
        "username": task.task_username,
        "created_by": getattr(user, "username", "unknown"),
        **qa_redirect,
    }
    # A coaching briefing is NOT sent as `prompt_text`: OCS runs that through a
    # generic "write a reminder" LLM call outside the bot's pipeline, so the worker
    # was shown the raw briefing (OCS session f931d8ea-..., 2026-10-07). Instead the
    # worker gets a fixed opening VERBATIM (`message_text`) and the briefing goes into
    # session state, where the coaching bot's own prompt reads
    # `{session_state.coach_briefing}`. Any other prompt keeps the `prompt_text` path.
    if coach_briefing.is_briefing(prompt_text):
        session_data["coach_briefing"] = prompt_text
        if coach_image and coach_image.get("url"):
            session_data["coach_image_url"] = coach_image["url"]
            session_data["coach_image_caption"] = coach_image.get("caption") or ""
        message = {"message_text": coach_briefing.opening_message(prompt_text)}
    else:
        message = {"prompt_text": prompt_text}
    ocs_client = ocs if ocs is not None else OCSDataAccess(user=user)
    try:
        result = ocs_client.trigger_bot(
            identifier=identifier,
            platform=platform,
            experiment_id=experiment,
            start_new_session=start_new_session,
            session_data=session_data,
            participant_data=participant_data or None,
            **message,
        )
    finally:
        if ocs is None:
            ocs_client.close()

    # Minimal diagnostics (no participant data).
    logger.info(
        "trigger_bot response for task %s: status=%s keys=%s",
        task.id,
        result.get("status") if isinstance(result, dict) else type(result).__name__,
        list(result.keys()) if isinstance(result, dict) else None,
    )

    session_id = None
    status = "pending"
    if isinstance(result, dict):
        session = result.get("session")
        session_id = (
            (session.get("id") if isinstance(session, dict) else None) or result.get("session_id") or result.get("id")
        )
        if session_id:
            session_id = str(session_id)
            status = "completed"
            logger.info("Session linked immediately from trigger_bot: %s", session_id)
        else:
            logger.warning("trigger_bot response has no session_id. Keys: %s", list(result.keys()))

    task.add_ai_session(actor=actor_name, session_params=session_params, session_id=session_id, status=status)
    data_access.save_task(task)
    return {"session_id": session_id, "status": status, "message": "AI conversation initiated."}
