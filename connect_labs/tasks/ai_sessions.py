"""Starting an OCS conversation for a task — the one implementation.

Two callers: the task page's "start AI conversation" endpoint
(``views.task_initiate_ai``, with the browser session's OCS token), and a workflow
action run in the background (``workflow/actions.py``, with the user's stored
``UserOCSToken``).
"""

from __future__ import annotations

import logging

from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess

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
) -> dict:
    """Start (or attach) the conversation as ``user`` and record it on ``task``,
    which is saved.

    ``ocs`` is the caller's ``OCSDataAccess`` (a browser request's, or one built for
    the user with no request); when omitted one is made for ``user`` from their
    stored token. Returns ``{"session_id", "status", "message"}``. Raises
    ``OCSAPIError`` when OCS refuses; the caller decides what the person sees.
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

    # Synthetic-opp short circuit: skip the real OCS call and attach a canned
    # coaching transcript directly onto the task — keeps the manager-flow demo
    # self-contained without requiring a real OCS account / experiment.
    if experiment == SYNTHETIC_BOT or get_synthetic_opp(int(task.opportunity_id)) is not None:
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
    }
    ocs_client = ocs if ocs is not None else OCSDataAccess(user=user)
    try:
        result = ocs_client.trigger_bot(
            identifier=identifier,
            platform=platform,
            experiment_id=experiment,
            prompt_text=prompt_text,
            start_new_session=start_new_session,
            session_data=session_data,
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
