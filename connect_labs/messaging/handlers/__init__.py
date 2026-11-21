"""Handlers: who answers a thread -- an agent, a flow, a person, or nobody.

A thread names its handler by kind (`Thread.handler_kind`) and a reference within that kind
(`handler_ref`: the agent and skill, the flow's key, the human queue). Messaging calls the
handler at four moments, always through this module so a failing handler hands the thread
to a person instead of dropping the worker:

    start(thread, ctx)               the thread has just opened (its opening, if any, is sent)
    on_message(thread, msgs)         the worker wrote; `msgs` is the debounced batch, oldest first
    on_handoff_in(thread, from_, note)  the thread was handed to this handler
    stop(thread, reason)             the thread closed, or was handed away

A handler replies with `thread.send(text, author_kind, author_ref)`; it never talks to a
transport. Registering a kind replaces what was there, which is how the canopy bridge
installs the real `agent` handler over the stub labs ships:

    from connect_labs.messaging import handlers
    handlers.register("agent", MyAgentHandler())
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class Handler(ABC):
    kind: str = ""

    @abstractmethod
    def start(self, thread, ctx: dict) -> None: ...

    @abstractmethod
    def on_message(self, thread, msgs: list) -> None: ...

    def on_handoff_in(self, thread, from_: dict, note: str) -> None:
        """Default: nothing to do until the worker writes."""

    def stop(self, thread, reason: str) -> None:
        """Default: nothing to tidy up."""


_HANDLERS: dict[str, Handler] = {}


def joined(msgs: list) -> str:
    """A debounced batch of the worker's messages as one text, oldest first."""
    return "\n".join((m.text or "").strip() for m in msgs if (m.text or "").strip())


def register(kind: str, handler: Handler) -> Handler:
    """Install `handler` for `kind`, replacing any earlier one (e.g. the agent stub)."""
    handler.kind = kind
    _HANDLERS[kind] = handler
    return handler


def get(kind: str) -> Handler | None:
    return _HANDLERS.get(kind)


def registered() -> dict[str, Handler]:
    return dict(_HANDLERS)


def register_builtin() -> None:
    """The handlers labs ships. Never replaces one already registered (an app may install first)."""
    from connect_labs.messaging.handlers import builtin

    for kind, handler in builtin.HANDLERS.items():
        if kind not in _HANDLERS:
            register(kind, handler)


# ---------------------------------------------------------------------------
# Dispatch: every call goes through here, so a failing handler never drops the worker.
# ---------------------------------------------------------------------------


def _call(thread, method: str, *args) -> bool:
    handler = get(thread.handler_kind)
    if handler is None:
        logger.warning("messaging: no %r handler for thread %s", thread.handler_kind, thread.pk)
        if method in ("start", "on_message") and thread.handler_kind != "human":
            to_human(thread, f"No {thread.handler_kind} handler is installed on this labs server.")
        return False
    try:
        getattr(handler, method)(thread, *args)
        return True
    except Exception as e:  # noqa: BLE001 -- the worker is handed to a person, not dropped
        logger.exception("messaging: %s handler failed in %s for thread %s", thread.handler_kind, method, thread.pk)
        if method in ("start", "on_message", "on_handoff_in") and thread.handler_kind != "human":
            to_human(thread, f"The {thread.handler_kind} handler failed ({type(e).__name__}); a person should reply.")
        return False


def start(thread, ctx: dict | None = None) -> bool:
    return _call(thread, "start", ctx or {})


def on_message(thread, msgs: list) -> bool:
    return _call(thread, "on_message", msgs)


def stop(thread, reason: str) -> bool:
    if get(thread.handler_kind) is None:
        return False
    return _call(thread, "stop", reason)


def handoff(thread, *, to_kind: str, to_ref: str = "", note: str = "", by: str = "") -> None:
    """Hand `thread` to another handler: the old one stops, the new one is told why.

    `note` is kept on the thread for staff (never sent). Handing to `human` puts the thread at
    the top of the Inbox with `note` as the reason.
    """
    from connect_labs.messaging import service
    from connect_labs.messaging.models import AuthorKind, Thread

    from_ = {"kind": thread.handler_kind, "ref": thread.handler_ref}
    if from_["kind"] != to_kind:
        stop(thread, "handed off")
    thread.handler_kind = to_kind
    thread.handler_ref = to_ref
    fields = ["handler_kind", "handler_ref"]
    if to_kind == "human":
        thread.needs_attention = True
        thread.attention_reason = (note or "Handed to a person")[:300]
        fields += ["needs_attention", "attention_reason"]
    Thread.objects.filter(pk=thread.pk).update(**{f: getattr(thread, f) for f in fields})
    who = by or from_["kind"]
    service.add_note(
        thread,
        f"Handed from {from_['kind']}{':' + from_['ref'] if from_['ref'] else ''} to {to_kind}"
        f"{':' + to_ref if to_ref else ''}" + (f" -- {note}" if note else ""),
        author_kind=AuthorKind.SYSTEM,
        author_ref=who,
    )
    _call(thread, "on_handoff_in", from_, note)


def to_human(thread, reason: str, *, by: str = "") -> None:
    """Hand the thread to a person (the Inbox), with the reason they will see."""
    handoff(
        thread,
        to_kind="human",
        to_ref=thread.handler_ref if thread.handler_kind == "human" else "inbox",
        note=reason,
        by=by,
    )
