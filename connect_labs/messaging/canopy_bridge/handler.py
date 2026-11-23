"""The agent handler: a thread answered by a canopy agent, on the workers surface of labs' host site.

Installed over labs' stub in ``AppConfig.ready`` (``handlers.register("agent", ...)``). For each
thread it keeps one canopy contact session per agent (``CanopySession``), and for each batch of
the worker's messages:

1. opens the session if it has none yet (S1), registering the worker with canopy first (S2), with
   the skill as session metadata (``x_skill``, ``x_thread``, ``x_program``, ``x_playbook``);
2. builds and stores the Worker Brief (``brief.py``) and sends the worker's words with the brief
   as signed context (S3), channel ``personalid``, as the agent the playbook names;
3. records the turn (``PendingTurn``) and waits: canopy pushes the reply to the webhook (S5,
   ``webhook.py``) or labs polls for it (``tasks.poll_turn``), whichever is configured -- and
   always polls, later, as a safety net. ``deliver`` sends it to the worker once.

A failure, a refusal or a turn that takes too long hands the thread to a person with the reason
(``handlers.to_human``). One refusal is waited out instead: canopy's per-person send limit (6 a
minute, 200 a day: ``429 rate_limited``). The worker's words stay waiting in labs and are sent again
a little over a minute later (``kickoff``), at most ``RATE_LIMIT_RETRIES`` times; still refused (the
daily limit), a person picks the thread up and is told why.

Handing the thread to another agent ends this session; the next one opens seeded with the handoff
note, and continuity comes from labs' record, not canopy's.
"""

from __future__ import annotations

import functools
import hashlib
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from connect_labs.messaging import handlers
from connect_labs.messaging.canopy_bridge import brief as briefs
from connect_labs.messaging.canopy_bridge import conf, sdk
from connect_labs.messaging.canopy_bridge.models import CanopySession, PendingTurn, ReplyDelivery, TurnStatus
from connect_labs.messaging.handlers import Handler
from connect_labs.messaging.models import Direction, Message, Thread

logger = logging.getLogger(__name__)

NOT_CONFIGURED = (
    "Canopy agents are not configured on this labs server (the workers surface of its canopy site), so a person picks "
    "this conversation up."
)
#: The first poll, when canopy does not push replies; then doubling, at most POLL_MAX apart.
POLL_FIRST = 3
POLL_MAX = 60
#: With the webhook on, the poll is only a safety net.
POLL_SAFETY_NET = 120
#: After a handoff to another agent, how long to let the previous agent's last answer land before the
#: new agent picks up a message the worker is still waiting on.
KICKOFF_AFTER = 45
#: canopy's per-contact send limit is per minute (and per day): wait just past a minute, a few times.
RATE_LIMIT_RETRY_SECONDS = 65
RATE_LIMIT_RETRIES = 2


def _relay(config):
    return sdk.RelayClient(config)


@functools.lru_cache(maxsize=2)
def _cached_relay(config):
    return _relay(config)


def relay_client():
    """The process's relay client for the workers surface (tests replace it)."""
    return _cached_relay(conf.require_config())


def agent_and_skill(thread: Thread) -> tuple[str, str]:
    agent, _, skill = (thread.handler_ref or "").partition(":")
    return agent.strip(), skill.strip()


def current_session(thread: Thread) -> CanopySession | None:
    return CanopySession.objects.filter(thread=thread, ended_at__isnull=True).order_by("-created_at", "-id").first()


def end_sessions(thread: Thread, reason: str) -> int:
    return CanopySession.objects.filter(thread=thread, ended_at__isnull=True).update(
        ended_at=timezone.now(), end_reason=reason[:200]
    )


def _session_for(thread: Thread) -> CanopySession:
    """This thread's open session for its current agent, opened on canopy if it is not yet."""
    from connect_labs.messaging.canopy_bridge import profile_push

    agent, skill = agent_and_skill(thread)
    if not agent:
        raise ValueError("the thread's agent handler names no agent")
    session = current_session(thread)
    if session is not None and (session.agent != agent or session.skill != skill):
        end_sessions(thread, f"handed to {agent}:{skill}")
        session = None
    if session is None:
        session = CanopySession.objects.create(thread=thread, agent=agent, skill=skill, sub=thread.worker.canopy_sub)
    if session.session_id:
        return session
    profile_push.ensure_pushed(thread.worker)
    metadata = {
        "x_skill": skill,
        "x_thread": str(thread.pk),
        "x_program": "" if thread.program_id is None else str(thread.program_id),
        "x_playbook": thread.playbook_key or "",
    }
    session_id = relay_client().open_session(
        session.sub,
        agent_slug=agent,
        title=f"Connect Worker conversation {thread.pk} ({thread.playbook_key or 'messaging'})",
        metadata=metadata,
        channel=conf.CHANNEL,
    )
    session.session_id = str(session_id)[:64]
    session.opened_at = timezone.now()
    session.save(update_fields=["session_id", "opened_at"])
    return session


def _schedule_poll(pending: PendingTurn, countdown: int) -> None:
    from connect_labs.messaging.canopy_bridge import tasks

    when = timezone.now() + timedelta(seconds=countdown)
    PendingTurn.objects.filter(pk=pending.pk).update(next_poll_at=when)
    pending_id = pending.pk

    def enqueue():
        try:
            tasks.poll_turn.apply_async(args=[pending_id], countdown=countdown)
        except Exception:  # noqa: BLE001 -- the sweep polls any turn left due
            logger.warning("canopy bridge: could not queue a poll of turn %s", pending_id, exc_info=True)

    transaction.on_commit(enqueue)


def relay_turn(thread: Thread, text: str, msgs: list) -> PendingTurn:
    """Send the worker's ``text`` (from ``msgs``) to the thread's agent with the Worker Brief."""
    session = _session_for(thread)
    brief = briefs.build(thread, inbound_text=text, session_note=session.seed_note)
    briefs.store(brief, thread)
    client_id = f"labs-message-{msgs[-1].pk}" if msgs else None
    answer = relay_client().send(
        session.session_id,
        session.sub,
        text,
        context={"format": "markdown", "body": brief.body},
        client_id=client_id,
    )
    turn_id = str((answer or {}).get("turn_id") or "")
    if not turn_id:
        raise RuntimeError("canopy accepted the message but started no turn")
    try:
        pending = PendingTurn.objects.create(
            session=session, thread=thread, turn_id=turn_id[:64], brief_hash=brief.hash
        )
    except IntegrityError:  # a retried send collapsed onto the first turn (client_id)
        pending = PendingTurn.objects.get(turn_id=turn_id[:64])
    if msgs:
        Message.objects.filter(pk__in=[m.pk for m in msgs]).update(
            canopy_turn_id=turn_id[:64], canopy_session_id=session.session_id, brief_hash=brief.hash
        )
    _schedule_poll(pending, POLL_SAFETY_NET if conf.webhook_on() else POLL_FIRST)
    return pending


def _to_human(thread: Thread, reason: str) -> None:
    thread.refresh_from_db()
    if thread.is_open and thread.handler_kind == "agent":
        handlers.to_human(thread, reason, by="canopy bridge")


def _refused(thread: Thread, exc, *, attempt: int) -> str:
    """canopy refused the send. Over the per-person limit, try again after a minute (the words
    wait in labs), up to RATE_LIMIT_RETRIES times; anything else, or still limited, goes to a person."""
    if getattr(exc, "code", "") == sdk.RATE_LIMITED and attempt < RATE_LIMIT_RETRIES:
        retry_later(thread, attempt=attempt + 1, countdown=RATE_LIMIT_RETRY_SECONDS)
        return "rate_limited"
    _to_human(thread, _refusal_reason(exc))
    return "refused"


def retry_later(thread: Thread, *, attempt: int, countdown: int) -> None:
    """Send what the worker is waiting on again in ``countdown`` seconds (``tasks.kickoff``)."""
    from connect_labs.messaging.canopy_bridge import tasks

    thread_id = thread.pk

    def enqueue():
        try:
            tasks.kickoff.apply_async(args=[thread_id], kwargs={"attempt": attempt}, countdown=countdown)
        except Exception:  # noqa: BLE001 -- the worker's next message sends it anyway
            logger.warning("canopy bridge: could not queue a retry of thread %s", thread_id, exc_info=True)
            _to_human(thread, "The agent could not be reached (rate_limited) and no retry could be queued.")

    transaction.on_commit(enqueue)


def _refusal_reason(exc) -> str:
    code = getattr(exc, "code", "") or ""
    if code == sdk.RATE_LIMITED:
        return (
            "Canopy refused this Connect Worker's messages to the agent as over its per-person limit "
            f"(rate_limited, still after {RATE_LIMIT_RETRIES} retries a minute apart: probably the daily "
            "limit); a person should reply."
        )
    detail = str(exc)[:160]
    return f"The agent could not be reached ({code or type(exc).__name__}: {detail}); a person should reply."


class CanopyAgentHandler(Handler):
    """A canopy agent answers the thread (``handler_ref`` = ``<agent>:<skill>``)."""

    def start(self, thread, ctx: dict) -> None:
        # Outbound: labs has already sent the opening, verbatim; the agent answers when the worker
        # does. Inbound: the worker's first message follows, debounced, to on_message.
        if not conf.enabled():
            handlers.to_human(thread, NOT_CONFIGURED)

    def on_message(self, thread, msgs: list) -> None:
        if not conf.enabled():
            handlers.to_human(thread, NOT_CONFIGURED)
            return
        msgs = _with_unsent(thread, msgs)
        text = handlers.joined(msgs)
        if not text:
            return
        from connect_labs.messaging import config, service

        limit = config.max_turns_for(thread)
        if limit is not None and service.automated_turns(thread) >= limit:
            # Its answer could not be sent anyway: do not spend an agent turn on it.
            handlers.to_human(
                thread, f"This conversation has reached its playbook's limit of {limit} automated messages."
            )
            return
        try:
            relay_turn(thread, text, msgs)
        except sdk.RelayError as exc:
            logger.warning("canopy bridge: relay refused for thread %s: %s", thread.pk, exc)
            _refused(thread, exc, attempt=0)

    def on_handoff_in(self, thread, from_: dict, note: str) -> None:
        """Handed to this agent (from a person, a flow or another agent): a new session, seeded."""
        agent, skill = agent_and_skill(thread)
        end_sessions(thread, f"handed to {agent}:{skill}")
        CanopySession.objects.create(
            thread=thread,
            agent=agent,
            skill=skill,
            sub=thread.worker.canopy_sub,
            seed_note=(note or "")[:600],
            handed_from=f"{from_.get('kind', '')}:{from_.get('ref', '')}"[:200],
        )
        if not conf.enabled():
            handlers.to_human(thread, NOT_CONFIGURED)
            return
        from connect_labs.messaging.canopy_bridge import tasks

        thread_id = thread.pk

        def enqueue():
            try:
                tasks.kickoff.apply_async(args=[thread_id], countdown=KICKOFF_AFTER)
            except Exception:  # noqa: BLE001 -- the worker's next message starts it anyway
                logger.warning("canopy bridge: could not queue the kickoff of thread %s", thread_id, exc_info=True)

        transaction.on_commit(enqueue)

    def stop(self, thread, reason: str) -> None:
        end_sessions(thread, reason or "stopped")


# ---------------------------------------------------------------------------
# Delivering the agent's answer (the webhook and the poll both come here)
# ---------------------------------------------------------------------------


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode()).hexdigest()


def deliver(pending: PendingTurn, *, seq: int, text: str, ended: bool, error: str = "", via: str) -> str:
    """Send one piece of the agent's answer to the worker, exactly once per (turn, seq).

    Returns ``delivered``, ``duplicate``, ``empty`` or why it was not sent."""
    from connect_labs.messaging.service import MessagingError

    text = (text or "").strip()
    with transaction.atomic():
        locked = PendingTurn.objects.select_for_update().select_related("session", "thread").get(pk=pending.pk)
        # (turn, seq) is canopy's idempotency key: a retry of a piece already handled is answered
        # as done, never sent twice. A turn the poll delivered was delivered whole (all its prose).
        if ReplyDelivery.objects.filter(turn=locked, seq=seq).exists():
            return "duplicate"
        if via != "poll" and ReplyDelivery.objects.filter(turn=locked, via="poll").exists():
            return "duplicate"
        if locked.status in (TurnStatus.FAILED, TurnStatus.TIMED_OUT):
            # A person already has the thread; an answer that comes after that is not sent.
            return "too_late"
        delivery = ReplyDelivery.objects.create(
            turn=locked, seq=seq, via=via, text_hash=_text_hash(text) if text else "", ended=ended, error=error[:300]
        )
        if ended:
            locked.status = TurnStatus.DELIVERED if not error else TurnStatus.FAILED
            locked.finished_at = timezone.now()
            locked.detail = error[:300]
            locked.save(update_fields=["status", "finished_at", "detail"])
    thread, session = locked.thread, locked.session
    outcome = "empty"
    if text:
        try:
            message = thread.send(
                text,
                "agent",
                f"{session.agent}:{session.skill}" if session.skill else session.agent,
                canopy_turn_id=locked.turn_id,
                canopy_session_id=session.session_id,
                brief_hash=locked.brief_hash,
            )
            ReplyDelivery.objects.filter(pk=delivery.pk).update(message=message)
            outcome = "delivered"
        except MessagingError as exc:
            outcome = exc.code
            logger.info("canopy bridge: reply on thread %s not sent (%s)", thread.pk, exc.code)
            if exc.code in ("turn_limit", "invalid", "no_channel"):
                _to_human(thread, f"The agent's reply was not sent: {exc.public_message}")
    if error and ended:
        _to_human(thread, f"The agent's turn ended with an error ({error[:160]}); a person should reply.")
    if ended:
        run_after(locked)
    return outcome


def run_after(pending: PendingTurn) -> None:
    """What the agent asked to happen once its answer was out (close the thread, hand it off)."""
    from connect_labs.messaging import router

    actions = list(pending.after or [])
    if not actions:
        return
    PendingTurn.objects.filter(pk=pending.pk).update(after=[])
    thread = Thread.objects.get(pk=pending.thread_id)
    for action in actions:
        if not thread.is_open:
            break
        try:
            if action.get("action") == "close":
                thread.close(action.get("reason") or "closed by the agent", by=action.get("by") or "agent")
            elif action.get("action") == "handoff":
                router.handoff_to(thread, action["to"], note=action.get("note") or "", by=action.get("by") or "agent")
        except Exception:  # noqa: BLE001 -- one failed action never stops the rest
            logger.exception("canopy bridge: %s after turn %s failed", action.get("action"), pending.turn_id)
        thread.refresh_from_db()


def fail(pending: PendingTurn, *, status: str, reason: str) -> None:
    """The turn will not be answered: a person takes the thread."""
    updated = PendingTurn.objects.filter(pk=pending.pk, status=TurnStatus.PENDING).update(
        status=status, finished_at=timezone.now(), detail=reason[:300]
    )
    if updated:
        _to_human(Thread.objects.get(pk=pending.thread_id), reason)
        pending.refresh_from_db()
        run_after(pending)


# ---------------------------------------------------------------------------
# The polling fallback
# ---------------------------------------------------------------------------

TERMINAL = frozenset({"done", "failed", "lost", "missed", "cancelled"})


def _answer_from_messages(pending: PendingTurn, rows: list[dict]) -> tuple[str, int]:
    """The agent's prose of this turn: assistant rows after what labs last read, minus any the
    webhook already delivered. Returns (text, highest index seen)."""
    session = pending.session
    delivered = set(
        ReplyDelivery.objects.filter(turn__session=session).exclude(text_hash="").values_list("text_hash", flat=True)
    )
    texts, high = [], session.last_index
    for row in sorted(rows, key=lambda r: int(r.get("turn_index") or 0)):
        index = int(row.get("turn_index") or 0)
        high = max(high, index)
        if index <= session.last_index or row.get("role") != "assistant":
            continue
        text = (row.get("plaintext") or "").strip()
        if text and _text_hash(text) not in delivered:
            texts.append(text)
    return "\n\n".join(texts), high


def poll(pending: PendingTurn, *, now=None) -> str:
    """Ask canopy how the turn went; deliver its answer once it is done. Returns what happened."""
    now = now or timezone.now()
    pending.refresh_from_db()
    if pending.status != TurnStatus.PENDING:
        return pending.status
    if ReplyDelivery.objects.filter(turn=pending).exists():
        # The webhook is delivering this turn; wait for its last piece, but not forever.
        if now - pending.sent_at > timedelta(seconds=conf.turn_timeout_seconds()):
            fail(
                pending,
                status=TurnStatus.TIMED_OUT,
                reason="The agent's answer never finished; a person should reply.",
            )
            return TurnStatus.TIMED_OUT
        return "webhook"
    relay = relay_client()
    session = pending.session
    turn = relay.turn(pending.turn_id, session.sub) or {}
    status = str(turn.get("status") or "")
    if status not in TERMINAL:
        if now - pending.sent_at > timedelta(seconds=conf.turn_timeout_seconds()):
            minutes = conf.turn_timeout_seconds() // 60
            fail(
                pending,
                status=TurnStatus.TIMED_OUT,
                reason=f"The agent did not answer within {minutes} minutes; a person should reply.",
            )
            return TurnStatus.TIMED_OUT
        return "running"
    if status != "done":
        fail(pending, status=TurnStatus.FAILED, reason=f"The agent's turn ended {status}; a person should reply.")
        return TurnStatus.FAILED
    rows = relay.messages(session.session_id, session.sub)
    text, high = _answer_from_messages(pending, rows)
    CanopySession.objects.filter(pk=session.pk, last_index__lt=high).update(last_index=high)
    deliver(pending, seq=0, text=text, ended=True, via="poll")
    return "delivered" if text else "empty"


def next_poll_in(polls: int) -> int:
    return min(POLL_MAX, POLL_FIRST * 2 ** max(0, polls))


def _waiting(thread: Thread):
    """The worker's messages since the thread's last outbound message (a note is not one)."""
    last_out = (
        Message.objects.filter(thread=thread, direction=Direction.OUTBOUND)
        .exclude(delivery_status="note")
        .order_by("-created_at", "-id")
        .first()
    )
    waiting = Message.objects.filter(thread=thread, direction=Direction.INBOUND)
    if last_out is not None:
        waiting = waiting.filter(created_at__gt=last_out.created_at)
    return waiting.order_by("created_at", "id")


def _with_unsent(thread: Thread, msgs: list) -> list:
    """``msgs`` plus anything the worker wrote since the last answer that never reached canopy
    (a send refused ``rate_limited`` and still waiting for its retry), oldest first. Without it a new
    message during the wait would start a turn, and the retry would find "a turn in flight" and drop
    the earlier words."""
    earlier = [m for m in _waiting(thread).filter(canopy_turn_id="") if m.pk not in {x.pk for x in msgs}]
    if not earlier:
        return msgs
    return sorted([*earlier, *msgs], key=lambda m: (m.created_at, m.pk))


def kickoff(thread: Thread, *, attempt: int = 0) -> str:
    """After a handoff, or after canopy's per-person limit: if the worker is still waiting on an
    answer, send what they wrote. ``attempt`` counts the rate-limit retries so far."""
    thread.refresh_from_db()
    if not thread.is_open or thread.handler_kind != "agent" or not conf.enabled():
        return "not ours"
    if PendingTurn.objects.filter(thread=thread, status=TurnStatus.PENDING).exists():
        return "a turn is in flight"
    msgs = list(_waiting(thread))
    if not msgs:
        return "nothing waiting"
    try:
        relay_turn(thread, handlers.joined(msgs), msgs)
    except sdk.RelayError as exc:
        logger.warning("canopy bridge: relay refused for thread %s: %s", thread.pk, exc)
        return _refused(thread, exc, attempt=attempt)
    return "sent"
