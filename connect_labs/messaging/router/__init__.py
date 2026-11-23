"""The router: which conversations start, and where a Connect Worker's message goes.

**Outbound** (labs begins): eligibility, then caps, then approval, then the thread opens and its
opening is sent. **Inbound** (the worker writes): their open thread's handler; else the program's
first matching inbound playbook; else the Inbox.

One module per question, each with its own docstring:

    eligibility.py   who, and whether: membership, the playbook's rule, the channel, caps, quiet hours
    starting.py      a person's preview and commit, a confirmed start, an automatic one, a dry run
    inbound.py       which thread a message joins, exits and handoffs, the debounced handler
    triggers.py      state events, schedules, and idle threads

Everything else imports from here (``from connect_labs.messaging import router``; ``router.preview``),
never from the modules, so the split can move without touching a caller.
"""

from __future__ import annotations

from connect_labs.messaging import service

from .eligibility import Candidate, eligibility, handler_ref, quiet_until
from .inbound import after_inbound, handle_due, handoff_to, schedule_handling, thread_for_inbound
from .starting import (
    CONFIRM_MAX_AGE_SECONDS,
    MAX_WORKERS,
    commit,
    describe_start,
    dry_run,
    preview,
    start_automatic,
    start_confirmed,
)
from .triggers import EVENT_MAX_AGE, close_idle, due_schedules, on_worker_event, run_schedules, scan_worker_events

__all__ = [
    "CONFIRM_MAX_AGE_SECONDS",
    "EVENT_MAX_AGE",
    "MAX_WORKERS",
    "Candidate",
    "after_inbound",
    "close_idle",
    "commit",
    "describe_start",
    "dry_run",
    "due_schedules",
    "eligibility",
    "handle_due",
    "handler_ref",
    "handoff_to",
    "install",
    "on_worker_event",
    "preview",
    "quiet_until",
    "run_schedules",
    "scan_worker_events",
    "schedule_handling",
    "start_automatic",
    "start_confirmed",
    "thread_for_inbound",
]


def install() -> None:
    """Route the worker's messages through the router (`service.record_inbound` calls these)."""
    service.INBOUND_THREAD = thread_for_inbound
    service.AFTER_INBOUND = after_inbound
