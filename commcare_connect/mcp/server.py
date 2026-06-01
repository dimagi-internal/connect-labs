"""The connect-labs FastMCP 3.x server.

This replaces the hand-rolled JSON-RPC dispatch in ``transport.py``. It does
NOT replace the curated tool layer: connect-labs ships ~60 explicit,
composed tools (each calling the labs service layer directly) registered via
``tool_registry.register``. Rewriting all of them as ``@mcp.tool`` functions
would be a large, error-prone change and would force their hand-crafted JSON
schemas through FastMCP's signature inference. Instead we *bridge* the
existing registry into FastMCP: for every registered ``Tool`` we mount a
``RegistryTool`` whose ``parameters`` is the existing ``input_schema`` verbatim
and whose ``run`` executes the EXACT same path the old transport did —
resolve the authenticated user, enforce the write rate limit, call the same
handler, strip private keys, and write an ``MCPAuditLog`` row.

Net effect:
  * tool NAMES, DESCRIPTIONS, and input SCHEMAS are byte-identical to before,
  * tool BEHAVIOR is the same (same handler, same service layer),
  * the write rate limit and per-call audit are preserved,
  * the only thing that changed is the protocol plumbing (FastMCP 3.x
    Streamable-HTTP instead of the hand-rolled POST view).

Auth — per-user Personal Access Token (unchanged token system):
  * ``CommCarePATVerifier`` wraps the existing ``MCPAccessToken.verify(raw)``
    and returns a FastMCP ``AccessToken`` carrying the user id in ``sub`` /
    ``user_id`` claims. Tools run AS that user (recovered via
    ``current_user()``). The token model, minting (``token_views.py``,
    ``mcp_create_token``), and consent flow are untouched.

The module exposes:
  * ``mcp``            — the FastMCP instance (auth attached)
  * ``build_http_app()`` — builds the Streamable-HTTP ASGI app, called once
    from ``config/asgi.py`` at mount time.
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from typing import Any

from asgiref.sync import sync_to_async
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools.tool import Tool, ToolResult

from commcare_connect.labs.integrations.connect.api_client import LabsAPIError

from .models import MCPAccessToken, MCPAuditLog
from .rate_limit import enforce_write_limit
from .tool_registry import _REGISTRY, MCPToolError
from .tool_registry import Tool as RegistryToolSpec

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "CommCare Connect Labs MCP server. Tools run as the authenticated user " "(per-user Personal Access Token)."
)


# ---------------------------------------------------------------------------
# Auth — per-user PAT verifier wrapping the existing MCPAccessToken model.
# ---------------------------------------------------------------------------

# Coarse scope advertised for PAT callers. PATs are full-user tokens (they
# act as the user); per-tool authorization happens inside each handler and
# the write rate limiter.
PAT_SCOPES = ["connect_labs:user"]


def _verify_pat_sync(raw: str):
    """Synchronous PAT lookup + touch. Returns the user or None.

    Wraps the UNCHANGED ``MCPAccessToken.verify`` + ``touch`` exactly as the
    old ``auth.authenticate_request`` did.
    """
    token = MCPAccessToken.verify(raw)
    if token is None:
        return None
    token.touch()
    return token.user


class CommCarePATVerifier(TokenVerifier):
    """Resolve a connect-labs Personal Access Token to an AccessToken.

    Mirrors the old ``auth.authenticate_request`` contract: a valid, active,
    non-expired ``MCPAccessToken`` resolves to its user; anything else
    (missing/garbage/revoked/expired) returns ``None``, which FastMCP turns
    into a 401.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        user = await sync_to_async(_verify_pat_sync, thread_sensitive=True)(token)
        if user is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(user.pk),
            scopes=PAT_SCOPES,
            claims={
                "sub": str(user.pk),
                "user_id": user.pk,
                "username": getattr(user, "username", "") or "",
                "auth_method": "pat",
            },
        )


def current_user():
    """Return the authenticated Django user, or None.

    Recovers the user id from the FastMCP access token (set by
    ``CommCarePATVerifier``) and loads the Django user. Used inside tool
    execution so handlers run AS the caller — the same ``user`` object the
    old transport passed straight through from ``authenticate_request``.
    """
    access = get_access_token()
    if access is None:
        return None
    claims = access.claims or {}
    uid = claims.get("user_id")
    if uid is None:
        uid = claims.get("sub")
    if uid is None:
        return None
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return None
    from commcare_connect.users.models import User

    try:
        return User.objects.get(pk=uid)
    except User.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# Audit — same MCPAuditLog write the old transport did, per tool call.
# ---------------------------------------------------------------------------


def _write_audit(
    user,
    tool_name: str,
    arguments: dict,
    *,
    success: bool,
    is_write: bool = False,
    error_code: str = "",
    version_before: int | None = None,
    version_after: int | None = None,
) -> None:
    """Best-effort audit write. Never raises — failure to log must not abort
    a tool call. Identical semantics to ``transport._log``."""
    try:
        MCPAuditLog.objects.create(
            user=user if (user and getattr(user, "is_authenticated", False)) else None,
            tool_name=tool_name,
            is_write=is_write,
            arguments=arguments if is_write else {},
            success=success,
            error_code=error_code or "",
            version_before=version_before,
            version_after=version_after,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write MCPAuditLog row (non-fatal)")


# ---------------------------------------------------------------------------
# Tool bridge — one RegistryTool per entry in the legacy _REGISTRY.
# ---------------------------------------------------------------------------


def _run_registry_tool(spec: RegistryToolSpec, arguments: dict) -> ToolResult:
    """Execute a legacy registry tool. Runs synchronously (in FastMCP's
    threadpool) so the existing sync handlers + ORM work unchanged.

    This is a faithful port of ``transport._handle_tools_call``: same user
    resolution, write-limit enforcement, audit writes, private-key stripping,
    and error wrapping. The only difference is the return shape — a FastMCP
    ``ToolResult`` instead of a JSON-RPC envelope (FastMCP builds the envelope).
    """
    user = current_user()

    if spec.is_write:
        try:
            enforce_write_limit(user)
        except MCPToolError as e:
            _write_audit(user, spec.name, arguments, success=False, error_code=e.code, is_write=True)
            raise ToolError(e.message) from e

    try:
        result = spec.handler(user=user, **arguments)
    except MCPToolError as e:
        _write_audit(user, spec.name, arguments, success=False, error_code=e.code, is_write=spec.is_write)
        raise ToolError(e.message) from e
    except Exception as e:  # noqa: BLE001
        _write_audit(user, spec.name, arguments, success=False, error_code="UPSTREAM_ERROR", is_write=spec.is_write)
        # Surface the full traceback inline — same diagnostic choice the old
        # transport made; faster to debug than digging through CloudWatch.
        request_id = uuid.uuid4().hex[:8]
        logger.exception("MCP tool error [tool=%s request_id=%s]", spec.name, request_id)
        detail = f"{type(e).__name__}: {e}"
        if isinstance(e, LabsAPIError):
            if e.status_code is not None:
                detail += f" (upstream_status={e.status_code})"
            if e.body is not None:
                detail += f" upstream_body={e.body}"
        raise ToolError(f"{detail}\n{traceback.format_exc()}") from e

    version_before = None
    version_after = None
    if isinstance(result, dict):
        version_before = result.get("_version_before")
        version_after = result.get("_version_after")
        # Strip private keys before returning to the caller.
        result = {k: v for k, v in result.items() if not k.startswith("_")}

    _write_audit(
        user,
        spec.name,
        arguments,
        success=True,
        is_write=spec.is_write,
        version_before=version_before,
        version_after=version_after,
    )

    structured = result if isinstance(result, dict) else {"value": result}
    # Mirror the old transport's content block (a JSON text blob) so existing
    # clients that read content[0].text keep working.
    return ToolResult(
        content=[{"type": "text", "text": json.dumps(result)}],
        structured_content=structured,
    )


class RegistryTool(Tool):
    """A FastMCP Tool backed by a legacy ``tool_registry`` entry.

    ``parameters`` is the registry tool's ``input_schema`` verbatim, so the
    advertised schema is byte-identical to the old ``tools/list`` output.
    ``run`` defers to ``_run_registry_tool`` in a threadpool (the legacy
    handlers are synchronous and do blocking ORM / HTTP work).
    """

    spec: RegistryToolSpec

    model_config = {"arbitrary_types_allowed": True}

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return await sync_to_async(_run_registry_tool, thread_sensitive=True)(self.spec, arguments)


def _build_registry_tools() -> list[RegistryTool]:
    # Importing the tools package runs every @register decorator, populating
    # _REGISTRY. (Also done in apps.MCPConfig.ready(), but be defensive in
    # case the server is built before app-ready in some contexts.)
    from . import tools  # noqa: F401

    built: list[RegistryTool] = []
    for spec in _REGISTRY.values():
        built.append(
            RegistryTool(
                name=spec.name,
                description=spec.description,
                parameters=spec.input_schema,
                spec=spec,
            )
        )
    return built


def _build_server() -> FastMCP:
    server = FastMCP(
        "connect_labs",
        instructions=SERVER_INSTRUCTIONS,
        auth=CommCarePATVerifier(),
    )
    for tool in _build_registry_tools():
        server.add_tool(tool)
    return server


mcp = _build_server()


def build_http_app():
    """Build the Streamable-HTTP ASGI app for mounting at /mcp/.

    ``path="/"`` because ``config/asgi.py`` mounts this app under the ``/mcp``
    prefix; the MCP endpoint then lives at exactly ``/mcp/`` — the same public
    URL the hand-rolled view served (see ``snippets.DEFAULT_SERVER_URL``).
    """
    return mcp.http_app(path="/", transport="streamable-http")
