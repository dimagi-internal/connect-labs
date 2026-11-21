"""The four endpoints connect-id calls on labs as a message server.

    POST /messaging/personalid/key/       the worker's phone asks for a channel's key
    POST /messaging/personalid/delivery/  the worker wrote to us        (HMAC-signed by connect-id)
    POST /messaging/personalid/consent/   the worker turned a channel on or off  (HMAC-signed)
    POST /messaging/personalid/callback/  the worker's phone received our messages (HMAC-signed)

connect-id signs what it posts with our shared secret: ``X-MAC-DIGEST`` is
base64(HMAC-SHA256(secret, raw body)) (connect-id ``messaging/tasks.py`` ``mac_digest``).
It never retries a delivery, and treats anything but exactly 200 as not delivered, so the
signed endpoints store what came (`TransportEvent`, ciphertext only), queue the work and
answer 200 at once.

The key endpoint is called by the phone, not connect-id: form field ``channel_id`` and the
worker's PersonalID bearer token. We ask PersonalID whose token it is (``/o/userinfo/``)
and hand the key over only if that is the channel's worker (``sub``, case-insensitively).
A channel that is unknown and one that belongs to someone else get the same 404.

While ``MESSAGING_ENABLED`` is off every endpoint answers 404, as if it did not exist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from connect_labs.messaging.models import Address, TransportEvent, TransportEventKind, TransportKind

logger = logging.getLogger(__name__)

MAC_HEADER = "X-MAC-DIGEST"
#: Key requests per client address per minute. Each one makes us call PersonalID, so an
#: unauthenticated caller must not be able to make us do it at request rate.
KEY_REQUESTS_PER_MINUTE = 30


def _require_enabled():
    if not getattr(settings, "MESSAGING_ENABLED", False):
        raise Http404()


def mac_digest(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode()


def _signed(request) -> bool:
    """Whether the body carries connect-id's signature with our secret. The secret never leaves this frame."""
    given = request.headers.get(MAC_HEADER) or ""
    secret = getattr(settings, "PERSONALID_MESSAGING_CLIENT_SECRET", "") or ""
    if not (given and secret):
        logger.warning("messaging: %s refused: %s", request.path, "no secret configured" if not secret else "unsigned")
        return False
    if not hmac.compare_digest(mac_digest(secret, request.body), given):
        logger.warning("messaging: %s refused: signature does not match", request.path)
        return False
    return True


def _client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")


def _rate_limited(request) -> bool:
    key = f"messaging-key-ip:{_client_ip(request)}:{timezone.now().strftime('%Y%m%d%H%M')}"
    cache.add(key, 0, 120)
    try:
        return cache.incr(key) > KEY_REQUESTS_PER_MINUTE
    except ValueError:
        return False


@csrf_exempt
@require_POST
def key(request):
    _require_enabled()
    if _rate_limited(request):
        return JsonResponse({"error": "too many requests"}, status=429)
    authorization = request.headers.get("Authorization") or ""
    channel_id = (request.POST.get("channel_id") or "").strip()
    if not (authorization and channel_id):
        return JsonResponse({"error": "channel_id and Authorization are required"}, status=400)

    from connect_labs.messaging.transport.personalid import userinfo_sub

    # Who is asking, before we say anything about the channel.
    sub = userinfo_sub(authorization)
    if sub is None:
        return JsonResponse({"error": "PersonalID did not accept this token"}, status=401)
    address = (
        Address.objects.select_related("worker")
        .filter(channel_id=channel_id, transport=TransportKind.PERSONALID)
        .first()
    )
    if address is None or address.worker.username.lower() != sub.lower():
        logger.info("messaging: key refused for channel %s", channel_id)
        return JsonResponse({"error": "unknown channel"}, status=404)
    if not address.key:
        from connect_labs.messaging import crypto

        address.key = crypto.new_key()
    address.key_served_at = timezone.now()
    address.save(update_fields=["key", "key_served_at", "updated_at"])
    return JsonResponse({"key": address.key})


def _store(request, kind: str):
    """Verify, keep what came, queue the work, answer exactly 200."""
    _require_enabled()
    if not _signed(request):
        return HttpResponse("Invalid signature", status=401)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse("Not JSON", status=400)
    if not isinstance(payload, dict):
        return HttpResponse("Not a JSON object", status=400)
    channel_id = str(payload.get("channel_id") or "")[:64]
    if kind == TransportEventKind.DELIVERY:
        # Ciphertext and ids only: the keys stay with the address, the text is decrypted in the task.
        keep = ("message_id", "ciphertext", "tag", "nonce", "timestamp")
        payload = {
            "channel_id": channel_id,
            "messages": [
                {k: m.get(k) for k in keep if k in m} for m in payload.get("messages") or [] if isinstance(m, dict)
            ][:200],
        }
    from connect_labs.messaging.tasks import enqueue_event

    with transaction.atomic():
        event = TransportEvent.objects.create(kind=kind, channel_id=channel_id, payload=payload)
        transaction.on_commit(lambda: enqueue_event(event.pk))
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def delivery(request):
    return _store(request, TransportEventKind.DELIVERY)


@csrf_exempt
@require_POST
def consent(request):
    return _store(request, TransportEventKind.CONSENT)


@csrf_exempt
@require_POST
def callback(request):
    return _store(request, TransportEventKind.CALLBACK)
