"""The ``connect-messaging`` site's MCP resource: the worker tools, bound to one subject (S4).

``POST {LABS_PUBLIC_URL}/messaging/canopy/mcp/`` -- a stateless MCP endpoint (Streamable HTTP,
JSON responses) that canopy's gateway calls in SUBJECT MODE for an agent's turn with a Connect
Worker. It accepts nothing but canopy's subject statement:

    Authorization: CanopySubject <jws>    typ canopy-subject+jwt, signed by canopy's client key
    DPoP: <proof>                          for this request, bound to the statement's cnf.jkt

and, for every request, before anything else:

1. verifies the statement (``sdk.verify_subject``: signature against canopy's client metadata,
   ``iss``, ``aud`` = this resource, lifetime, DPoP binding, single use, ``rpc`` = this very
   JSON-RPC request) and that its ``site`` is this site;
2. resolves ``sub`` (``personalid:<username>``) to the ConnectWorker, and ``session`` to the thread
   labs relays to that session -- the session must be this worker's and this agent's;
3. lists and runs only the tools that thread's playbook grants (``tools``).

These calls are not a labs user: they never reach labs' main MCP (``/mcp/``) or any user-scoped
tool, and every tool takes its worker and thread from the subject, never from its arguments.
Every ``tools/call`` is audited in ``MCPAuditLog`` with the subject, the thread and canopy's turn.
"""

from __future__ import annotations

import json
import logging

from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from connect_labs.audit_trail.context import audit_context
from connect_labs.messaging import playbooks
from connect_labs.messaging.canopy_bridge import conf, sdk, tools, webhook
from connect_labs.messaging.canopy_bridge.models import CanopySession

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
MAX_BODY = 64 * 1024
INSTRUCTIONS = (
    "Connect Labs' record of the ONE Connect Worker you are talking to, in this program. Labs is the system of "
    "record: read more here rather than guessing. Every tool is about this worker and this conversation only."
)


class Refused(Exception):
    def __init__(self, code: str, message: str, status: int = 403):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _rpc_error(rpc_id, code: int, message: str, data: dict | None = None, status: int = 200, headers=None):
    body = {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    if data:
        body["error"]["data"] = data
    response = JsonResponse(body, status=status)
    for k, v in (headers or {}).items():
        response[k] = v
    return response


def _result(rpc_id, result: dict):
    return JsonResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def bind(subject):
    """(worker, thread, session) for a verified subject, or ``Refused``."""
    from connect_labs.workers.models import ConnectWorker

    if subject.site != conf.site_name():
        raise Refused("wrong_site", "this statement is for another site")
    username = ConnectWorker.username_from_sub(subject.sub)
    worker = ConnectWorker.objects.filter(username=username, erased_at__isnull=True).first() if username else None
    if worker is None:
        raise Refused("unknown_subject", "labs holds no Connect Worker for this subject")
    session = (
        CanopySession.objects.select_related("thread", "thread__worker")
        .filter(session_id=subject.session)
        .exclude(session_id="")
        .first()
    )
    if session is None or session.thread.worker_id != worker.pk or session.sub != subject.sub:
        raise Refused("wrong_session", "this conversation is not one labs relays for this Connect Worker")
    if session.agent != subject.agent:
        raise Refused("wrong_agent", "this conversation belongs to another agent")
    return worker, session.thread, session


def _context(worker, thread, subject) -> tuple[tools.Ctx, list[str]]:
    playbook = playbooks.for_thread(thread) or {}
    granted = [t for t in (playbook.get("tools") or []) if t in tools.TOOLS]
    programs = frozenset({thread.program_id}) if thread.program_id is not None else frozenset()
    ctx = tools.Ctx(
        worker=worker,
        thread=thread,
        subject=subject,
        playbook=playbook,
        programs=programs,
        other_programs=bool((playbook.get("context") or {}).get("other_programs")),
    )
    return ctx, granted


def _audit(ctx: tools.Ctx, name: str, arguments: dict, *, success: bool, error_code: str = "", is_write=False):
    from connect_labs.mcp.models import MCPAuditLog

    subject = ctx.subject
    try:
        MCPAuditLog.objects.create(
            user=None,
            tool_name=name[:100],
            is_write=is_write,
            arguments=arguments if is_write else {},
            success=success,
            error_code=error_code[:50],
            client_id=(conf.require_config().canopy_client_id or "")[:255],
            actor=subject.agent[:100],
            execution={
                "mode": "subject",
                "site": subject.site,
                "subject": subject.sub,
                "agent": subject.agent,
                "turn": subject.turn,
                "session": subject.session,
                "capability": subject.capability,
                "thread_id": ctx.thread.pk,
            },
            request_id=f"canopy-subject:{subject.turn}"[:64],
            program_id=ctx.thread.program_id,
        )
    except Exception:  # noqa: BLE001 -- failing to log must not fail the call
        logger.exception("canopy bridge: could not write the MCP audit row")


def _tool_list(granted: list[str]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
            "annotations": {"readOnlyHint": not t.is_write},
        }
        for t in (tools.TOOLS[name] for name in granted)
    ]


def _call(ctx: tools.Ctx, granted: list[str], params: dict) -> dict:
    import jsonschema

    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    spec = tools.TOOLS.get(name)
    if spec is None or name not in granted:
        _audit(ctx, name or "(none)", {}, success=False, error_code="PERMISSION_DENIED")
        return _error_result("not_allowed", f"{name or 'That tool'} is not available in this conversation.")
    if not isinstance(arguments, dict):
        _audit(ctx, name, {}, success=False, error_code="INVALID_SCHEMA", is_write=spec.is_write)
        return _error_result("invalid", "arguments must be an object")
    try:
        jsonschema.validate(arguments, spec.input_schema)
    except jsonschema.ValidationError as exc:
        _audit(ctx, name, arguments, success=False, error_code="INVALID_SCHEMA", is_write=spec.is_write)
        return _error_result("invalid", f"Invalid arguments for {name}: {exc.message}")
    with audit_context(source="mcp", request_id=f"canopy-subject:{ctx.subject.turn}"[:64], path=f"mcp:{name}"):
        try:
            result = spec.handler(ctx, **arguments)
        except tools.ToolRefused as exc:
            _audit(ctx, name, arguments, success=False, error_code=exc.code.upper()[:50], is_write=spec.is_write)
            return _error_result(exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 -- the agent gets a reason, never a traceback
            logger.exception("canopy bridge: worker tool %s failed", name)
            _audit(ctx, name, arguments, success=False, error_code="UPSTREAM_ERROR", is_write=spec.is_write)
            return _error_result("error", f"{name} failed ({type(exc).__name__}).")
    _audit(ctx, name, arguments, success=True, is_write=spec.is_write)
    return {"content": [{"type": "text", "text": json.dumps(result, default=str)}], "structuredContent": result}


def _error_result(code: str, message: str) -> dict:
    return {"isError": True, "content": [{"type": "text", "text": f"{code}: {message}"}]}


@csrf_exempt
@require_http_methods(["POST", "GET", "DELETE"])
def endpoint(request):
    if not conf.enabled():
        raise Http404("canopy agents are not configured here")
    if request.method != "POST":
        # Stateless: no server-sent stream and no session to delete.
        return HttpResponse(status=405, headers={"Allow": "POST"})
    if len(request.body) > MAX_BODY:
        return _rpc_error(None, -32600, "request too large", status=413)
    try:
        message = json.loads(request.body or b"")
    except ValueError:
        return _rpc_error(None, -32700, "parse error", status=400)
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return _rpc_error(None, -32600, "one JSON-RPC request per POST", status=400)
    rpc_id = message.get("id")
    method = message["method"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    try:
        subject = sdk.verify_subject(
            request,
            config=conf.require_config(),
            client_keys=webhook.client_keys(),
            replay_store=_replay_store(),
            method="POST",
            rpc=(method, params),
        )
    except sdk.SubjectRefused as exc:
        logger.info("canopy bridge: subject refused (%s): %s", exc.code, exc.message)
        return _rpc_error(
            rpc_id,
            -32001,
            "unauthorized",
            {"code": exc.code},
            status=401,
            headers={"WWW-Authenticate": f'{sdk.SUBJECT_AUTH_SCHEME} error="{exc.code}"'},
        )
    try:
        worker, thread, _session = bind(subject)
    except Refused as exc:
        logger.warning("canopy bridge: subject %s refused (%s)", subject.sub, exc.code)
        return _rpc_error(rpc_id, -32003, exc.message, {"code": exc.code}, status=exc.status)
    ctx, granted = _context(worker, thread, subject)
    if rpc_id is None:  # a notification (e.g. notifications/initialized)
        return HttpResponse(status=202)
    if method == "initialize":
        return _result(
            rpc_id,
            {
                "protocolVersion": str(params.get("protocolVersion") or PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": conf.site_name(), "version": "1"},
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(rpc_id, {})
    if method == "tools/list":
        return _result(rpc_id, {"tools": _tool_list(granted)})
    if method == "tools/call":
        return _result(rpc_id, _call(ctx, granted, params))
    return _rpc_error(rpc_id, -32601, f"method {method} is not served here")


def _replay_store():
    from canopy_sdk.django.stores import DjangoJtiStore

    return DjangoJtiStore()
