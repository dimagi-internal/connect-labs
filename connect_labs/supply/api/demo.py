"""Put the demo world back, programmatically, on any instance.

Every OES walkthrough is state-mutating by design — a reviewer records a
qualification, a buyer awards two lots, the award writes a contract — so a
second take has to find the world as the first one did. `award_lot` refuses a lot
that already has an award, which means take two does not merely look different,
it fails.

Locally that is ``manage.py seed_supply_demo --reset``. On a deployed instance
there is no shell, and the documented alternative was an interactive
``aws ecs execute-command`` — an SSO token and a human — which a render loop
cannot do between takes. So prod renders were effectively single-shot.

**Authenticated with a labs MCP Personal Access Token.** Not a bespoke secret:
PATs already exist, are self-service at ``/labs/mcp/tokens/``, and are already
how every other programmatic labs operation authenticates. An earlier draft of
this endpoint invented its own ``SUPPLY_DEMO_RESEED_TOKEN`` env var, reasoning
that the reseed deletes the ``supply_*`` tables so a DB-stored credential could
not authenticate its own destruction. That is true of supply's own ``ApiToken``
— and false of an MCP PAT, which lives in the ``mcp`` app, outside everything
this touches. The env var bought nothing and cost a deploy-time provisioning
step on every instance that wanted the feature.

**It can also set the demo password.** Putting the world into a known state
includes the credential you will sign in with: the seeder already rotates every
persona's password on every run, so a caller that can reseed can already choose
it. Passing ``password`` means a render needs no pre-shared secret at all — it
reseeds, then signs in with what it just set.
"""
import contextlib
import json
import os
import threading

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .. import audit

# One reseed at a time. Two concurrent `--reset` runs interleave a delete with
# the other's inserts and leave a half-built world that reads as a product bug —
# and a render loop firing per-take is exactly the caller that would race.
_LOCK = threading.Lock()

PASSWORD_ENV = "SUPPLY_DEMO_PASSWORD"


@contextlib.contextmanager
def _demo_password(value):
    """Seed with *value* as the persona password, then restore the environment.

    ``demo_password()`` reads the env var at call time precisely so a deployed
    instance can rotate it, which makes a scoped override the least invasive way
    to let a caller choose one — no parameter threaded through five seeder
    functions. Safe because the reseed is single-flight.
    """
    if not value:
        yield
        return
    previous = os.environ.get(PASSWORD_ENV)
    os.environ[PASSWORD_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(PASSWORD_ENV, None)
        else:
            os.environ[PASSWORD_ENV] = previous


def reseed_demo_world(password=None):
    """Rebuild the demo world. The one implementation both callers share."""
    from ..demo import seed_demo_world

    with _demo_password(password):
        return seed_demo_world(reset=True)


@csrf_exempt
def reseed(request):
    """POST /supply/api/demo/reseed/ — bearer a labs MCP PAT.

    Returns the seeder's own summary so the caller can assert the world it is
    about to film rather than trusting a 200.
    """
    # Reuse the MCP server's verifier: same tokens, same revocation, same
    # last-used tracking, nothing new to provision.
    from connect_labs.mcp.auth import authenticate_request

    user, failure = authenticate_request(request)
    if failure is not None:
        return failure

    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)

    # The common call carries no options at all — `curl -X POST` with no body,
    # which arrives with whatever content type the client felt like. Only insist
    # on parseable JSON when the caller actually claims to be sending JSON, so a
    # malformed password payload is still an error rather than silently ignored.
    body: dict = {}
    if (request.content_type or "").startswith("application/json") and request.body:
        try:
            body = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "body must be valid JSON"}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({"error": "body must be a JSON object"}, status=400)
    password = (body.get("password") or "").strip() or None

    if not _LOCK.acquire(blocking=False):
        # 409, not a queue: the caller wants a known world NOW, and waiting
        # behind another reset would hand it one built to someone else's clock.
        return JsonResponse({"error": "a reseed is already running", "retry": True}, status=409)
    try:
        summary = reseed_demo_world(password=password)
    finally:
        _LOCK.release()

    # Wiping and rebuilding the demo world is worth a line in the audit log even
    # when it is routine — it explains why every id moved.
    try:
        request.user = user
        audit.log_action(request, "demo.reseed", "DemoWorld", 0, summary)
    except Exception:  # noqa: BLE001 — the reseed happened; logging must not undo it
        pass

    return JsonResponse(
        {
            "ok": True,
            "reseeded": True,
            "password_set": bool(password),
            "summary": summary,
        }
    )
