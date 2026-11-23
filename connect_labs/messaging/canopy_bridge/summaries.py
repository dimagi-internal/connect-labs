"""What a thread came to, when nobody said: the fallback summary.

An agent records a thread's outcome with ``thread_record_outcome`` / ``thread_close``; a person can
too. A thread that closes (or times out, ``router.close_idle``) with no summary covering its last
message gets one from labs, so the next conversation's brief (``brief.py``, Recent history) is never
blind to it. Author ``system`` / ``labs-job``.

Deterministic and extractive, by design: labs' LLM helpers (``connect_labs/ai``) are workflow
agents, and a worker's messages should not go to a model just to be shortened. The summary says
who said how much, when, why it closed, and quotes the first and last thing the Connect Worker
said and the last reply, never anything restricted or the worker's phone.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.utils import timezone

from connect_labs.messaging.canopy_bridge.brief import _clip, _Scrubber
from connect_labs.messaging.models import AuthorKind, DeliveryStatus, Direction, Message, Thread, ThreadState
from connect_labs.workers.models import Visibility

logger = logging.getLogger(__name__)

AUTHOR_REF = "labs-job"
QUOTE = 160
#: Only threads closed this recently are looked at (older ones were summarised, or never will be).
LOOKBACK = dt.timedelta(days=30)


def _messages(thread):
    return (
        Message.objects.filter(thread=thread)
        .exclude(delivery_status=DeliveryStatus.NOTE)
        .exclude(visibility=Visibility.RESTRICTED)
        .order_by("created_at", "id")
    )


def needs_summary(thread: Thread) -> bool:
    """Closed, has messages, and no summary was written since the Connect Worker last wrote.

    Measured from the worker's last message, not the thread's: an agent records its summary
    (`thread_record_outcome`, `thread_close`) during the turn whose answer goes out after it,
    and that answer is no reason to write a second, extractive summary over the agent's own.
    """
    if thread.state != ThreadState.CLOSED:
        return False
    messages = _messages(thread)
    last = messages.filter(direction=Direction.INBOUND).last() or messages.last()
    if last is None:
        return False
    return not thread.summaries.filter(created_at__gte=last.created_at).exists()


def extractive(thread: Thread) -> str:
    messages = list(_messages(thread))
    scrub = _Scrubber(thread.worker.phone)
    inbound = [m for m in messages if m.direction == Direction.INBOUND]
    outbound = [m for m in messages if m.direction == Direction.OUTBOUND]
    first, last = messages[0].created_at.date(), messages[-1].created_at.date()
    span = first.isoformat() if first == last else f"{first.isoformat()} to {last.isoformat()}"
    parts = [
        f"{len(messages)} messages ({len(inbound)} from the Connect Worker, {len(outbound)} to them), {span}"
        + (f", playbook {thread.playbook_key}" if thread.playbook_key else "")
        + "."
    ]
    if thread.close_reason:
        parts.append(f"Closed: {thread.close_reason}.")
    if inbound:
        parts.append(f'The Connect Worker first wrote: "{_clip(scrub(inbound[0].text), QUOTE)}"')
        if len(inbound) > 1:
            parts.append(f'Last from them: "{_clip(scrub(inbound[-1].text), QUOTE)}"')
    else:
        parts.append("The Connect Worker did not reply.")
    if outbound:
        who = outbound[-1].author_ref or outbound[-1].author_kind
        parts.append(f'Last to them ({who}): "{_clip(scrub(outbound[-1].text), QUOTE)}"')
    return " ".join(parts)


def outcome_for(thread: Thread) -> str:
    reason = (thread.close_reason or "").lower()
    if reason.startswith("no messages for"):
        return "timed_out"
    if reason.startswith("unsubscribed"):
        return "unsubscribed"
    return "closed"


def summarize(thread: Thread) -> bool:
    from connect_labs.messaging import service

    if not needs_summary(thread):
        return False
    service.record_summary(
        thread, extractive(thread), author_kind=AuthorKind.SYSTEM, author_ref=AUTHOR_REF, outcome=outcome_for(thread)
    )
    return True


def summarize_closed(*, now=None, limit: int = 200) -> int:
    """Write the fallback summary for every recently closed thread that has none. Returns how many."""
    now = now or timezone.now()
    written = 0
    threads = (
        Thread.objects.filter(state=ThreadState.CLOSED, closed_at__gte=now - LOOKBACK)
        .select_related("worker")
        .order_by("closed_at", "id")
    )
    for thread in threads.iterator():
        if written >= limit:
            break
        try:
            written += summarize(thread)
        except Exception:  # noqa: BLE001 -- one thread's failure never stops the rest
            logger.exception("canopy bridge: fallback summary of thread %s failed", thread.pk)
    return written
