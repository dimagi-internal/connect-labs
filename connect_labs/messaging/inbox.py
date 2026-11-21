"""What staff do in the Inbox: reply, take over, hand back, mark handled, close.

Each takes the person acting (a Django user) and checks they may see the thread
(`access.require`) before anything happens, so the web pages and the MCP tools act alike.
A staff reply is sent with `author_kind=staff` through the same `service.send` as every
other message, with all its rails.
"""

from __future__ import annotations

from django.db.models import Count, Max, Q

from connect_labs.messaging import access, handlers, service
from connect_labs.messaging.models import AuthorKind, HandlerKind, Thread, ThreadState


def _who(user) -> str:
    return getattr(user, "username", "") or "staff"


def _locked_thread(caller, thread_id: int) -> Thread:
    caller = access.caller_for(caller)
    thread = Thread.objects.select_related("worker", "address").filter(pk=thread_id).first()
    if thread is None:
        raise access.NoAccess("no such conversation")
    access.require(caller, thread)
    return thread


def get_thread(caller, thread_id: int) -> Thread:
    return _locked_thread(caller, thread_id)


def take_over(caller, thread_id: int) -> Thread:
    """A person answers from now on. The handler that was answering is remembered for `hand_back`."""
    thread = _locked_thread(caller, thread_id)
    user = access.caller_for(caller).user
    if not thread.is_open:
        raise service.MessagingError("thread_closed", "This conversation is closed.")
    if thread.handler_kind != HandlerKind.HUMAN:
        previous = {"kind": thread.handler_kind, "ref": thread.handler_ref}
        handlers.handoff(
            thread, to_kind=HandlerKind.HUMAN, to_ref="inbox", note=f"Taken over by {_who(user)}", by=_who(user)
        )
        Thread.objects.filter(pk=thread.pk).update(previous_handler=previous)
    # The person taking it over is on it: it no longer waits for someone.
    Thread.objects.filter(pk=thread.pk).update(taken_over_by=_who(user), needs_attention=False, attention_reason="")
    thread.refresh_from_db()
    return thread


def hand_back(caller, thread_id: int) -> Thread:
    """Give the thread back to the handler a person took it from."""
    thread = _locked_thread(caller, thread_id)
    user = access.caller_for(caller).user
    previous = thread.previous_handler or {}
    if not previous.get("kind"):
        raise service.MessagingError("nothing_to_hand_back", "This conversation was not taken over from a handler.")
    if not thread.is_open:
        raise service.MessagingError("thread_closed", "This conversation is closed.")
    Thread.objects.filter(pk=thread.pk).update(
        taken_over_by="", previous_handler={}, needs_attention=False, attention_reason=""
    )
    thread.refresh_from_db()
    handlers.handoff(
        thread,
        to_kind=previous["kind"],
        to_ref=previous.get("ref", ""),
        note=f"Handed back by {_who(user)}",
        by=_who(user),
    )
    thread.refresh_from_db()
    return thread


def reply(caller, thread_id: int, text: str):
    """Send `text` to the worker as this person. Replying to a thread a handler runs takes it over."""
    thread = _locked_thread(caller, thread_id)
    user = access.caller_for(caller).user
    if thread.is_open and thread.handler_kind != HandlerKind.HUMAN:
        thread = take_over(caller, thread_id)
    message = service.send(thread, text, author_kind=AuthorKind.STAFF, author_ref=_who(user))
    Thread.objects.filter(pk=thread.pk).update(needs_attention=False, attention_reason="")
    return message


def mark_handled(caller, thread_id: int) -> Thread:
    thread = _locked_thread(caller, thread_id)
    Thread.objects.filter(pk=thread.pk).update(needs_attention=False, attention_reason="")
    thread.refresh_from_db()
    return thread


def close(caller, thread_id: int, reason: str = "") -> Thread:
    thread = _locked_thread(caller, thread_id)
    user = access.caller_for(caller).user
    service.close_thread(thread, reason or "closed in the Inbox", by=_who(user))
    thread.refresh_from_db()
    return thread


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

FILTERS = ("program", "playbook", "handler", "state", "attention", "worker")


def threads(caller, *, program=None, playbook=None, handler=None, state=None, attention=None, worker=None):
    """The threads `caller` may see, newest activity first, narrowed by the Inbox's filters.

    `state` is open (default), closed or all; `attention` true shows only threads waiting for
    a person.
    """
    qs = access.visible_threads(access.caller_for(caller)).select_related("worker")
    if program not in (None, ""):
        qs = qs.filter(program_id=int(program))
    if playbook not in (None, ""):
        qs = qs.filter(playbook_key=playbook)
    if handler not in (None, ""):
        qs = qs.filter(handler_kind=handler)
    state = state or ThreadState.OPEN
    if state != "all":
        qs = qs.filter(state=state)
    if attention in (True, "1", "true", "on"):
        qs = qs.filter(needs_attention=True)
    if worker not in (None, ""):
        qs = qs.filter(Q(worker__username__iexact=worker) | Q(worker__name__icontains=worker))
    return qs.annotate(n_messages=Count("messages"), last_at=Max("messages__created_at")).order_by(
        "-needs_attention", "-last_message_at", "-id"
    )


def facets(caller) -> dict:
    """The programs, playbooks and handlers present in what `caller` may see (for the filter menus)."""
    qs = access.visible_threads(access.caller_for(caller))
    return {
        "programs": sorted(p for p in qs.values_list("program_id", flat=True).distinct() if p is not None),
        "playbooks": sorted(p for p in qs.values_list("playbook_key", flat=True).distinct() if p),
        "handlers": [h for h in HandlerKind.values],
        "attention": qs.filter(state=ThreadState.OPEN, needs_attention=True).count(),
    }
