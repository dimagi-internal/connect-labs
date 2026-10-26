"""Canopy's ``Canopy-Execution`` statement on a delegated call: verified once, kept for the call.

When canopy acts for a visitor, its gateway adds a signed statement to every request
(canopy-web spec ``2026-10-22-sensitive-data-agents-design`` §6): which agent is calling,
whether its capability is set to handle sensitive data (`data: sensitive`, and who set it), on
which runner and its flags in force, in a conversation with which requirements. The canopy SDK verifies it
(``canopy_sdk.django.execution.verify_execution``, dimagi-canopy >= 0.13): signature,
issuer, audience, a 60-second life, a single-use ``jti``, and binding to the DPoP key
and access token of THIS request.

Single-use is why it is verified here, once, at the start of the tool call, and the
result kept in a context variable for everything the call does
(``labs/canopy_panel.py:call_sensitive_data_clearance`` reads it): a second verification
of the same statement is a replay and would be refused.

Fails closed: no statement, a statement that does not verify, or an SDK too old to
verify one all leave the call without a verified execution, and sensitive data stays
out of reach. ``Canopy-Actor`` still only NAMES the agent and is never trusted.

**The person's own View is stated too, never inferred.** canopy sends ``Canopy-View``
(``canopy_sdk.django.execution.verify_view``, dimagi-canopy >= 0.20) on a View's
``resources/read`` -- only when the principal behind it is a person acting as themselves,
in a conversation they are in -- bound to the person, the resource, the conversation, the
DPoP key and the token. ``view_for_this_request`` verifies it. A delegated call with NO
statement is neither an agent labs cleared nor the person: it is refused.

A View's ``tools/call`` is the person's click only on the same positive word: a delegated
call to a tool only a View may make (app-only, or a commit only the person may make) needs
a ``Canopy-View`` for THAT ``tools/call`` -- its ``uri`` the View's own ``ui://`` resource --
verified once per call (``view_for_this_call``) and kept for it (``current_view``). Hiding
the tool from the agent is canopy's rule; labs does not take it on trust.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallExecution:
    """What this call's statement said, verified -- or why there is none."""

    execution: object | None = None  # canopy_sdk.host.Execution
    refused: str = ""

    def audit(self) -> dict:
        """What the audit log records about who executed the call (never the claims wholesale)."""
        if self.execution is None:
            return {"refused": self.refused} if self.refused else {}
        e = self.execution
        decl = e.declaration or {}
        return {
            "agent": e.agent,
            "declaration": (
                {k: decl.get(k) for k in ("data", "capability", "declared_by", "declared_at")} if decl else None
            ),
            "runner": {"id": e.runner.get("id"), "name": e.runner.get("name"), "flags": sorted(e.runner_flags)},
            "runner_data_mode": (e.runner_posture or {}).get("data_mode"),
            "requirements": list(e.requirements),
            "turn": e.turn,
            "session": e.session,
        }


_CURRENT: ContextVar[CallExecution | None] = ContextVar("labs_canopy_execution", default=None)


def supported() -> bool:
    """Whether the installed canopy SDK can verify execution statements (dimagi-canopy >= 0.13)."""
    try:
        from canopy_sdk.django import execution  # noqa: F401
    except ImportError:
        return False
    return True


def verify(headers, access_token: str | None, rpc=None) -> CallExecution:
    """Verify the statement on this request's ``headers``, for the JSON-RPC request ``rpc``
    (``(method, params)``: the statement must have been made for exactly it, dimagi-canopy
    >= 0.20). Never raises."""
    try:
        from canopy_sdk.django.execution import ExecutionRefused, verify_execution
    except ImportError:
        return CallExecution(refused="sdk_unsupported")
    try:
        return CallExecution(execution=verify_execution(dict(headers or {}), access_token=access_token, rpc=rpc))
    except ExecutionRefused as e:
        return CallExecution(refused=e.code)
    except Exception:  # noqa: BLE001 -- anything unexpected fails closed, never a 500
        logger.warning("Canopy-Execution could not be verified", exc_info=True)
        return CallExecution(refused="verify_error")


def current() -> CallExecution | None:
    """This call's verified statement (or refusal), or None outside a delegated call."""
    return _CURRENT.get()


@contextmanager
def bound(call: CallExecution | None):
    """Keep ``call`` as this call's execution for the duration of the block."""
    token = _CURRENT.set(call)
    try:
        yield call
    finally:
        _CURRENT.reset(token)


def for_this_request(access_token, rpc=None) -> CallExecution:
    """Verify the statement on the MCP request in flight (FastMCP's request context), for
    the JSON-RPC request ``rpc`` it must be bound to."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 -- no request context
        headers = {}
    raw = getattr(access_token, "token", None)
    return verify(headers, raw, rpc)


@dataclass(frozen=True)
class CallView:
    """This request's verified ``Canopy-View`` -- the person's own View -- or why there is none."""

    view: object | None = None  # canopy_sdk.host.View
    refused: str = ""

    def audit(self) -> dict:
        return self.view.audit() if self.view is not None else {"refused": self.refused}


def verify_view(headers, access_token: str | None, *, uri: str, subject: str, rpc=None) -> CallView:
    """Verify ``Canopy-View`` on this request's ``headers`` for the ``resources/read`` of
    ``uri`` as ``subject`` (the labs user the delegated token serves), bound to that very
    request -- or, with ``rpc`` (``(method, params)``), to that JSON-RPC request instead
    (a View's ``tools/call``, ``uri`` then the View's own resource). Never raises."""
    try:
        from canopy_sdk.django.execution import ViewRefused
        from canopy_sdk.django.execution import verify_view as sdk_verify_view
    except ImportError:
        return CallView(refused="sdk_unsupported")
    try:
        return CallView(
            view=sdk_verify_view(
                dict(headers or {}),
                uri=uri,
                subject=subject,
                access_token=access_token,
                rpc=rpc if rpc is not None else ("resources/read", {"uri": uri}),
            )
        )
    except ViewRefused as e:
        return CallView(refused=e.code)
    except Exception:  # noqa: BLE001 -- anything unexpected fails closed, never a 500
        logger.warning("Canopy-View could not be verified", exc_info=True)
        return CallView(refused="verify_error")


def view_for_this_request(access_token, *, uri: str, subject: str) -> CallView:
    """Verify ``Canopy-View`` on the MCP request in flight (FastMCP's request context)."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 -- no request context
        headers = {}
    return verify_view(headers, getattr(access_token, "token", None), uri=uri, subject=subject)


def subject_of(access_token) -> str:
    """The labs user a delegated token serves, as canopy names them in a statement (the
    grant's subject: the user's id)."""
    claims = getattr(access_token, "claims", None) or {}
    uid = claims.get("user_id")
    if uid is None:
        uid = claims.get("sub")
    return "" if uid is None else str(uid)


_CURRENT_VIEW: ContextVar[CallView | None] = ContextVar("labs_canopy_view", default=None)


def view_for_this_call(access_token, *, uri: str, tool_name: str, arguments: dict) -> CallView:
    """Verify ``Canopy-View`` on the MCP request in flight for THIS ``tools/call`` (this tool,
    these arguments) from the View at ``uri``, as the person the token serves."""
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers(include_all=True)
    except Exception:  # noqa: BLE001 -- no request context
        headers = {}
    return verify_view(
        headers,
        getattr(access_token, "token", None),
        uri=uri,
        subject=subject_of(access_token),
        rpc=("tools/call", {"name": tool_name, "arguments": dict(arguments or {})}),
    )


def current_view() -> CallView | None:
    """This call's verified ``Canopy-View`` (or why there is none); None when it was not asked."""
    return _CURRENT_VIEW.get()


def is_persons_view() -> bool:
    """Whether canopy stated, verifiably, that this call is the person's own View."""
    view = _CURRENT_VIEW.get()
    return view is not None and view.view is not None


@contextmanager
def bound_view(view: CallView | None):
    """Keep ``view`` as this call's View statement for the duration of the block."""
    token = _CURRENT_VIEW.set(view)
    try:
        yield view
    finally:
        _CURRENT_VIEW.reset(token)
