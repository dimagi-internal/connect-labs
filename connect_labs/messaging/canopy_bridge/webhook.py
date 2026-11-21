"""The reply webhook (S5): canopy pushes an agent's answer here, signed by canopy's client key.

``POST {LABS_PUBLIC_URL}{CANOPY_MESSAGING_REPLY_WEBHOOK_PATH}``, ``Content-Type: application/jwt``,
the body a compact JWS (``typ: canopy-reply+jwt``, ``aud`` = this URL) naming the contact, the
site, the session, the turn, ``seq``, ``text`` and ``ended``. Labs believes it only when it
verifies against the key canopy's client metadata names, and only for a turn labs itself sent in
that session for that person. Each (turn, seq) is delivered to the worker once: canopy's retry of
one labs already delivered is answered 200 ``duplicate``.

Answers: 200 (``delivered``, ``duplicate``, ``empty``, ...), 401 (not canopy's word), 404 (off, or
a turn labs does not know -- canopy retries, which covers the moment between canopy starting the
turn and labs recording it), 409 (a reply about another person or conversation).
"""

from __future__ import annotations

import logging

from django.http import Http404, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from connect_labs.audit_trail.context import audit_context
from connect_labs.messaging.canopy_bridge import conf, handler, sdk
from connect_labs.messaging.canopy_bridge.models import PendingTurn

logger = logging.getLogger(__name__)

MAX_BODY = 128 * 1024


def client_keys():
    """canopy's keys, from its client metadata, cached in Django's cache (tests replace this)."""
    return conf.client_keys()


@csrf_exempt
@require_POST
def replies(request):
    if not conf.enabled():
        raise Http404("canopy agents are not configured here")
    if len(request.body) > MAX_BODY:
        return JsonResponse({"error": "too_large"}, status=413)
    token = request.body.decode("ascii", errors="replace").strip()
    try:
        # aud = HostConfig.reply_url (CANOPY_MESSAGING_REPLY_WEBHOOK_PATH under LABS_PUBLIC_URL); the
        # jti is spent in the shared table, so a replay to another worker is refused too.
        reply = sdk.verify_reply(
            token, config=conf.require_config(), client_keys=client_keys(), replay_store=conf.replay_store()
        )
    except sdk.ReplyRefused as exc:
        logger.warning("canopy bridge: reply refused (%s): %s", exc.code, exc.message)
        return JsonResponse({"error": exc.code}, status=401)
    pending = PendingTurn.objects.select_related("session").filter(turn_id=reply.turn).first()
    if pending is None:
        return JsonResponse({"error": "unknown_turn"}, status=404)
    if pending.session.session_id != reply.session or pending.session.sub != reply.sub:
        logger.warning("canopy bridge: reply for turn %s names another person or session", reply.turn)
        return JsonResponse({"error": "mismatch"}, status=409)
    with audit_context(source="system", request_id=f"canopy-reply:{reply.turn}:{reply.seq}"):
        outcome = handler.deliver(
            pending, seq=reply.seq, text=reply.text, ended=reply.ended, error=reply.error, via="webhook"
        )
    return JsonResponse({"status": outcome})
