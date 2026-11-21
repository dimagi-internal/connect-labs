"""The handlers labs ships, by kind: a person (the Inbox) and nobody.

`flow` and `agent` arrive with the router. The canopy bridge replaces `agent` with the real
one (`handlers.register("agent", ...)`).
"""

from __future__ import annotations

from connect_labs.messaging.handlers import Handler


def _waiting(thread, reason: str) -> None:
    from connect_labs.messaging.models import Thread

    Thread.objects.filter(pk=thread.pk).update(needs_attention=True, attention_reason=reason[:300])
    thread.needs_attention, thread.attention_reason = True, reason[:300]


class HumanHandler(Handler):
    """A person answers, in the Inbox. A worker's message puts the thread at the top of it."""

    def start(self, thread, ctx: dict) -> None:
        # An outbound thread a person handles waits for the worker; one a worker began waits for us.
        if thread.direction == "in":
            _waiting(thread, ctx.get("reason") or "The Connect Worker wrote in")

    def on_message(self, thread, msgs: list) -> None:
        n = len(msgs)
        _waiting(thread, "The Connect Worker replied" + (f" ({n} messages)" if n > 1 else ""))

    def on_handoff_in(self, thread, from_: dict, note: str) -> None:
        _waiting(thread, note or f"Handed over from {from_.get('kind') or 'a handler'}")


class NoneHandler(Handler):
    """Nobody answers: a one-way message. If the worker writes back anyway, a person sees it."""

    def start(self, thread, ctx: dict) -> None:
        return None

    def on_message(self, thread, msgs: list) -> None:
        from connect_labs.messaging import handlers

        handlers.to_human(thread, "The Connect Worker replied to a message that expects no reply.")


HANDLERS: dict = {"human": HumanHandler(), "none": NoneHandler()}
