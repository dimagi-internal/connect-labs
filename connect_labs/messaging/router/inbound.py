"""Inbound: where a Connect Worker's message goes once it is stored.

The thread: their open thread on that channel; else the first of the program's inbound
playbooks (lowest `priority`) whose eligibility the worker meets; else the Inbox.

Then, before the handler sees the message: the playbook's exit keywords close the thread, and
its keyword handoffs move it. The handler itself is debounced per thread
(`MESSAGING_DEBOUNCE_SECONDS`): it is called once with every message that arrived while the
worker was still writing.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from connect_labs.messaging import config, handlers, playbooks, service
from connect_labs.messaging.models import Address, Direction, HandlerKind, Thread

from .rules import eligibility, handler_ref

logger = logging.getLogger(__name__)


def thread_for_inbound(address: Address, text: str) -> tuple[Thread, bool]:
    """The thread a worker's message belongs to: open one, an inbound playbook's, or the Inbox's."""
    thread = service.open_threads_on(address).order_by("-opened_at", "-id").first()
    if thread is not None:
        return thread, False
    worker, program_id = address.worker, address.program_id
    if program_id is not None and service.enabled() and config.program_enabled(program_id):
        for key, playbook in playbooks.inbound_for(program_id):
            reason, snapshot, opp = eligibility(playbook, worker, program_id)
            if reason:
                continue
            variant = playbooks.assign_variant(playbook, key, worker.username) or {}
            handler = variant.get("handler") or playbook["handler"]
            return (
                service.open_thread(
                    worker,
                    address=address,
                    direction=Direction.INBOUND,
                    program_id=program_id,
                    opportunity_id=opp,
                    handler_kind=handler["kind"],
                    handler_ref=handler_ref(handler),
                    playbook_key=key,
                    playbook_version=playbook["version"],
                    variant=variant.get("key", ""),
                    started_by="worker",
                    state_snapshot=snapshot,
                ),
                True,
            )
    return (
        service.open_thread(
            worker,
            address=address,
            direction=Direction.INBOUND,
            program_id=program_id,
            handler_kind=HandlerKind.HUMAN,
            handler_ref="inbox",
            playbook_key="",
            started_by="worker",
        ),
        True,
    )


def _matches(text: str, keywords) -> bool:
    words = {w.strip().lower() for w in keywords or []}
    said = (text or "").strip().lower().rstrip(".!")
    return said in words


def after_inbound(thread: Thread, message, is_new: bool) -> None:
    """What happens once a worker's message is stored: exits, handoffs, then the (debounced) handler."""
    playbook = playbooks.for_thread(thread) or {}
    if is_new:
        handlers.start(thread, {"reason": "The Connect Worker wrote in", "playbook": playbook})
        thread.refresh_from_db()
        if not thread.is_open:
            return
    if _matches(message.text, (playbook.get("exit") or {}).get("keywords")):
        service.close_thread(thread, "the Connect Worker ended it", by="exit keyword")
        return
    for rule in playbook.get("handoffs") or []:
        if _matches(message.text, rule["when"].get("keywords")):
            handoff_to(
                thread, rule["to"], note=rule.get("note") or f"The Connect Worker said {message.text.strip()!r}"
            )
            thread.refresh_from_db()
            break
    schedule_handling(thread)


def handoff_to(thread: Thread, target: str, *, note: str = "", by: str = "") -> None:
    """Hand a thread to a person ("human") or to another of its program's playbooks."""
    if target == "human":
        handlers.to_human(thread, note or "Handed to a person", by=by)
        return
    playbook = playbooks.get(thread.program_id, target)
    if playbook is None or not playbooks.is_enabled(playbook):
        handlers.to_human(thread, f"{note} (no playbook {target!r} to hand to)".strip(), by=by)
        return
    handler = playbook["handler"]
    Thread.objects.filter(pk=thread.pk).update(playbook_key=target, playbook_version=playbook["version"])
    thread.playbook_key, thread.playbook_version = target, playbook["version"]
    handlers.handoff(thread, to_kind=handler["kind"], to_ref=handler_ref(handler), note=note, by=by)


def schedule_handling(thread: Thread) -> None:
    """(Re)start the debounce: the handler sees this thread's new messages once the worker pauses."""
    from connect_labs.messaging import tasks

    delay = int(getattr(settings, "MESSAGING_DEBOUNCE_SECONDS", 8))
    due = timezone.now() + dt.timedelta(seconds=delay)
    Thread.objects.filter(pk=thread.pk).update(inbound_due_at=due)
    thread_id = thread.pk

    def enqueue():
        try:
            tasks.handle_inbound.apply_async(args=[thread_id], countdown=delay)
        except Exception:  # noqa: BLE001 -- the sweep handles any thread left due
            logger.warning("messaging: could not queue handling of thread %s", thread_id, exc_info=True)

    transaction.on_commit(enqueue)


def handle_due(thread_id: int, now: dt.datetime | None = None) -> str:
    """Give the handler every message the worker sent since it last looked, once they have paused."""
    from connect_labs.messaging.models import Message

    now = now or timezone.now()
    with transaction.atomic():
        thread = Thread.objects.select_for_update().filter(pk=thread_id).first()
        if thread is None or thread.inbound_due_at is None:
            return "nothing due"
        # A later message moved the time on; the task queued for it will do the work.
        if thread.inbound_due_at > now + dt.timedelta(milliseconds=500):
            return "not yet"
        pending = list(
            Message.objects.filter(thread=thread, direction=Direction.INBOUND, handled_at__isnull=True).order_by(
                "created_at", "id"
            )
        )
        Message.objects.filter(pk__in=[m.pk for m in pending]).update(handled_at=now)
        thread.inbound_due_at = None
        thread.save(update_fields=["inbound_due_at"])
    if not pending or not thread.is_open:
        return "nothing to handle"
    handlers.on_message(thread, pending)
    return f"handled {len(pending)}"
