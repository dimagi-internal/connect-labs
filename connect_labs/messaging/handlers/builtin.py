"""The handlers labs ships, by kind: a person (the Inbox), nobody, a flow, and the agent stub.

The canopy bridge replaces the `agent` stub with the real handler (`handlers.register("agent", ...)`).
"""

from __future__ import annotations

from connect_labs.messaging.handlers import Handler

AGENT_PENDING = (
    "Agent conversations are not connected yet on this labs server (the canopy bridge is pending), "
    "so a person picks this conversation up."
)


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
        if thread.needs_attention and thread.attention_reason:
            return  # already waiting for a person; keep the reason they will read
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


class AgentStubHandler(Handler):
    """Stands in for the canopy agent handler until it is installed: a person takes the thread."""

    def start(self, thread, ctx: dict) -> None:
        from connect_labs.messaging import handlers

        handlers.to_human(thread, AGENT_PENDING)

    def on_message(self, thread, msgs: list) -> None:
        from connect_labs.messaging import handlers

        handlers.to_human(thread, AGENT_PENDING)

    def on_handoff_in(self, thread, from_: dict, note: str) -> None:
        from connect_labs.messaging import handlers

        handlers.to_human(thread, AGENT_PENDING)


def _flow_handler():
    from connect_labs.messaging.flows import FlowHandler

    return FlowHandler()


HANDLERS: dict = {
    "human": HumanHandler(),
    "none": NoneHandler(),
    "agent": AgentStubHandler(),
    "flow": _flow_handler(),
}
