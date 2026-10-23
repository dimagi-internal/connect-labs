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


def verify_view(headers, access_token: str | None, *, uri: str, subject: str) -> CallView:
    """Verify ``Canopy-View`` on this request's ``headers`` for the ``resources/read`` of
    ``uri`` as ``subject`` (the labs user the delegated token serves), bound to that very
    request. Never raises."""
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
                rpc=("resources/read", {"uri": uri}),
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
