"""Flows: fixed, simple conversations labs runs itself -- no agent, no person until one is needed.

A flow writes the opening of an outbound thread from the facts its opener passes (`ctx`), when
the playbook gives no opening of its own. A reply to a flow goes to a person: a flow never
improvises. A one-way flow (a notice, a code) closes its thread once the message is queued,
so a reply starts a fresh conversation that the program's inbound playbooks route.

    welcome         a first hello on the program's channel            waits for a reply
    verify_code     a one-time code, on a dedicated security channel   one way; needs ctx code
    stock_low       a Connect Worker's stock is running low            one way; needs ctx item
    task_due        a task is due                                      one way; needs ctx task
    payment_notice  a payment was made                                 one way; needs ctx amount
"""

from __future__ import annotations

from dataclasses import dataclass, field

from connect_labs.messaging.handlers import Handler


class FlowError(ValueError):
    """A flow cannot be opened with what it was given (a missing fact)."""


@dataclass(frozen=True)
class Flow:
    key: str
    label: str
    text: str
    one_way: bool = True
    required: tuple[str, ...] = ()
    defaults: dict = field(default_factory=dict)
    #: "program" (the worker's channel for the program) or "security" (a channel for codes only)
    purpose: str = "program"

    def opening(self, values: dict) -> str:
        from connect_labs.messaging.playbooks import fill

        missing = [k for k in self.required if values.get(k) in (None, "")]
        if missing:
            raise FlowError(f"the {self.key} flow needs {', '.join(missing)}")
        return fill(self.text, {**self.defaults, **values})

    def on_message(self, thread, msgs: list) -> None:
        from connect_labs.messaging import handlers

        handlers.to_human(thread, f"The Connect Worker replied to {self.label.lower()}.")


FLOWS: dict[str, Flow] = {
    flow.key: flow
    for flow in (
        Flow(
            key="welcome",
            label="Welcome",
            text=(
                "Hello {first_name}, welcome to {program}! This is where {program} will message you about your "
                "work on Connect. You can reply here at any time."
            ),
            one_way=False,
        ),
        Flow(
            key="verify_code",
            label="A verification code",
            text="Your {program} code is {code}. It expires in {minutes} minutes. Do not share it with anyone.",
            required=("code",),
            defaults={"minutes": 10},
            purpose="security",
        ),
        Flow(
            key="stock_low",
            label="A low-stock notice",
            text="Hello {first_name}, your stock of {item} is running low{days_left_text}. {action}",
            required=("item",),
            defaults={"days_left_text": "", "action": "Please reply if you need a resupply."},
        ),
        Flow(
            key="task_due",
            label="A task reminder",
            text='Hello {first_name}, your task "{task}" is due {due}. {action}',
            required=("task",),
            defaults={"due": "soon", "action": "You can find it under Tasks in your Connect app."},
        ),
        Flow(
            key="payment_notice",
            label="A payment notice",
            text="Hello {first_name}, a payment of {amount} for your work on {program} was made{on_date}. {action}",
            required=("amount",),
            defaults={"on_date": "", "action": "Reply here if anything looks wrong."},
        ),
    )
}


def prepare(values: dict) -> dict:
    """Derived wording a flow's text uses (e.g. "in 3 days" from days_left)."""
    out = dict(values)
    if out.get("days_left") not in (None, "") and "days_left_text" not in out:
        days = out["days_left"]
        out["days_left_text"] = f" (about {days} day{'s' if str(days) != '1' else ''} left)"
    if out.get("date") and "on_date" not in out:
        out["on_date"] = f" on {out['date']}"
    return out


class FlowHandler(Handler):
    """Runs the thread's flow (`handler_ref` is its key). The opening is sent by the router."""

    def start(self, thread, ctx: dict) -> None:
        return None

    def on_message(self, thread, msgs: list) -> None:
        flow = FLOWS.get(thread.handler_ref)
        if flow is None:
            from connect_labs.messaging import handlers

            handlers.to_human(thread, f"No flow {thread.handler_ref!r} on this labs server.")
            return
        flow.on_message(thread, msgs)
