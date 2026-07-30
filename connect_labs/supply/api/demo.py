"""Put the demo world back, over HTTP.

Every OES walkthrough is state-mutating by design — a reviewer records a
qualification, a buyer awards two lots, the award writes a contract — so a
second take has to find the world as the first one did. `award_lot` refuses a lot
that already has an award, which means take two does not merely look different,
it fails.

Locally the render's ``setup:`` block runs ``manage.py seed_supply_demo --reset``
and that is the end of it. Against the deployed site there is no shell, so the
documented path was ``aws ecs execute-command`` — an interactive session, an SSO
token, and a human. That is not something a render loop can do between takes, so
in practice prod renders were single-shot.

This is the same reseed as a POST.

**Why a token from the environment and not an ApiToken row.** The reseed deletes
the ``supply_*`` tables, and ``ApiToken`` lives in them. A DB-stored credential
cannot authenticate its own destruction: it would be valid for the request that
erases it and absent for the next one. The secret has to outlive the data it
resets, so it comes from the environment.

**Disabled unless configured.** With no ``SUPPLY_DEMO_RESEED_TOKEN`` set, this
404s exactly as though the route did not exist — `/supply/` has open registration
on a public host, and an endpoint that empties it should not even advertise
itself, let alone be one guessed header away.
"""
import os
import threading

from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt

from .. import audit

TOKEN_ENV = "SUPPLY_DEMO_RESEED_TOKEN"

# One reseed at a time. Two concurrent `--reset` runs interleave a delete with
# the other's inserts and leave a half-built world that reads as a product bug —
# and a render loop firing per-take is exactly the caller that would race.
_LOCK = threading.Lock()


def _presented_token(request):
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("X-Reseed-Token") or "").strip()


@csrf_exempt
def reseed(request):
    """Reseed the demo world. POST, bearer-token authenticated.

    Returns the seeder's own summary so the caller can assert the world it is
    about to film, rather than trusting a 200.
    """
    expected = (os.environ.get(TOKEN_ENV) or "").strip()
    if not expected:
        # Not configured — indistinguishable from "no such route".
        return JsonResponse({"error": "not found"}, status=404)

    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    presented = _presented_token(request)
    if not presented or not constant_time_compare(presented, expected):
        return JsonResponse({"error": "invalid reseed token"}, status=403)

    if not _LOCK.acquire(blocking=False):
        # 409, not a queue: the caller wants a known world NOW, and waiting
        # behind another reset would hand it one built to someone else's clock.
        return JsonResponse({"error": "a reseed is already running", "retry": True}, status=409)
    try:
        summary = _run_reseed()
    finally:
        _LOCK.release()

    # Wiping and rebuilding the demo world is worth a line in the audit log even
    # when it is routine — it explains why every id moved.
    try:
        audit.log_action(request, "demo.reseed", "DemoWorld", 0, summary)
    except Exception:  # noqa: BLE001 — the reseed happened; logging must not undo it
        pass

    return JsonResponse({"ok": True, "reseeded": True, "summary": summary})


def _run_reseed():
    from ..demo import seed_demo_world

    return seed_demo_world(reset=True)
