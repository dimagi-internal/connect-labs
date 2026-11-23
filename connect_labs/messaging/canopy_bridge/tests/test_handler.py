"""The agent handler, the reply webhook and the polling fallback, against a fake canopy and a fake
connect-id: a Connect Worker writes, an agent answers through canopy, the answer reaches the phone.

Every name and message here is invented.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.messaging import handlers, playbooks, router, service
from connect_labs.messaging.canopy_bridge import conf, handler, sdk, tasks, webhook
from connect_labs.messaging.canopy_bridge.handler import CanopyAgentHandler
from connect_labs.messaging.canopy_bridge.models import (
    CanopySession,
    PendingTurn,
    ReplyDelivery,
    TurnStatus,
    WorkerBrief,
)
from connect_labs.messaging.models import Address, Direction, Message, Thread
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.workers.models import WorkerMembership, WorkerState

pytestmark = pytest.mark.django_db

WEBHOOK = "https://labs.connect.dimagi.com/messaging/canopy/replies/"


def _set(key, **playbook):
    return playbooks.set_playbook(scopes.SYSTEM, PROGRAM, key, {"version": 1, **playbook})


@pytest.fixture
def agent_handler():
    """The canopy bridge's handler (an earlier test may have swapped in a recorder)."""
    previous = handlers.get("agent")
    handlers.register("agent", CanopyAgentHandler())
    yield
    handlers.register("agent", previous)


@pytest.fixture
def world(bridge_on, fake_connectid, worker, run_now, monkeypatch, agent_handler):
    fake_connectid.add_user(worker.username)
    membership = WorkerMembership.objects.create(
        worker=worker, opportunity_id=9001, program_id=PROGRAM, opportunity_name="Bednets North"
    )
    WorkerState.objects.create(membership=membership, stage="learning")
    service.address_for(worker, PROGRAM, channel_name="Bednet Program")
    _set(
        "learn-support",
        direction="inbound",
        handler={"kind": "agent", "agent": "connect-coach", "skill": "learn-support"},
        tools=["worker_profile", "thread_close"],
        description="Help with Learn",
    )
    relay = sdk.RelayClient(conf.require_config(), transport=bridge_on.transport, mint=bridge_on.mint)
    monkeypatch.setattr(handler, "relay_client", lambda: relay)
    monkeypatch.setattr(webhook, "client_keys", bridge_on.client_keys)
    polls = []
    monkeypatch.setattr(
        tasks.poll_turn, "apply_async", lambda args, countdown=None: polls.append((args[0], countdown))
    )
    kickoffs, kickoff_calls = [], []

    def queue_kickoff(args, kwargs=None, countdown=None):
        kickoffs.append(args[0])
        kickoff_calls.append((args[0], (kwargs or {}).get("attempt", 0), countdown))

    monkeypatch.setattr(tasks.kickoff, "apply_async", queue_kickoff)
    bridge_on.polls, bridge_on.kickoffs, bridge_on.kickoff_calls = polls, kickoffs, kickoff_calls
    return worker


def _write(client, fake_connectid, worker, *texts):
    address = Address.objects.get(worker=worker, purpose="program")
    return fake_connectid.reply(client, address.channel_id, address.key, *texts)


def _agent_messages(thread):
    return list(Message.objects.filter(thread=thread, author_kind="agent").order_by("created_at", "id"))


def _converse(client, fake_connectid, world, handle_now, *texts):
    _write(client, fake_connectid, world, *texts)
    handle_now()
    return Thread.objects.get(worker=world)


def test_the_workers_words_go_to_the_agent_with_the_brief(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "How do I finish", "module 3?")
    send = bridge_on.sends[-1]
    assert send["text"] == "How do I finish\nmodule 3?"
    assert send["sub"] == "personalid:amina123"
    assert "# Worker Brief" in send["context"]["body"] and "Amina Bello" in send["context"]["body"]
    session = CanopySession.objects.get(thread=thread)
    canopy_session = bridge_on.sessions[session.session_id]
    assert canopy_session["agent"] == "connect-coach"
    assert canopy_session["metadata"] == {
        "x_skill": "learn-support",
        "x_thread": str(thread.pk),
        "x_program": str(PROGRAM),
        "x_playbook": "learn-support",
    }
    assert bridge_on.minted[-1][sdk.CHANNEL_CLAIM] == "personalid"
    # Canopy knew the worker before their first session (S2).
    assert "personalid:amina123" in bridge_on.contacts
    pending = PendingTurn.objects.get(thread=thread)
    assert pending.turn_id == send["turn"]
    assert WorkerBrief.objects.filter(hash=pending.brief_hash).exists()
    inbound = Message.objects.filter(thread=thread, direction="in")
    assert set(inbound.values_list("canopy_turn_id", flat=True)) == {send["turn"]}
    assert set(inbound.values_list("brief_hash", flat=True)) == {pending.brief_hash}
    # No webhook configured: labs polls, soon.
    assert bridge_on.polls == [(pending.pk, handler.POLL_FIRST)]


def test_the_poll_delivers_the_answer_once(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "Is my payment coming?")
    pending = PendingTurn.objects.get(thread=thread)
    assert handler.poll(pending) == "running"
    bridge_on.finish(pending.turn_id, "Yes, it was sent on Monday.")
    assert handler.poll(pending) == "delivered"
    [reply] = _agent_messages(thread)
    assert reply.text.startswith("Yes, it was sent on Monday.")
    assert reply.author_ref == "connect-coach:learn-support"
    assert (reply.canopy_turn_id, reply.brief_hash) == (pending.turn_id, pending.brief_hash)
    assert reply.canopy_session_id == pending.session.session_id
    assert handler.poll(pending) == "delivered"
    assert len(_agent_messages(thread)) == 1
    key = Address.objects.get(worker=world).key
    assert any("sent on Monday" in t for t in fake_connectid.texts_to(world.username, key))


def test_the_webhook_delivers_each_piece_once(client, world, fake_connectid, bridge_on, handle_now, settings):
    settings.CANOPY_MESSAGING_REPLY_WEBHOOK = True
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    pending = PendingTurn.objects.get(thread=thread)
    assert bridge_on.polls == [(pending.pk, handler.POLL_SAFETY_NET)]

    def post(**extra):
        token = bridge_on.reply_token(
            WEBHOOK,
            sub="personalid:amina123",
            session=pending.session.session_id,
            turn=pending.turn_id,
            seq=0,
            text="Hello Amina!",
            ended=True,
            **extra,
        )
        return token, client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")

    token, response = post()
    assert response.status_code == 200 and response.json() == {"status": "delivered"}
    # canopy signs every delivery attempt afresh (a new jti): a retry of the same (turn, seq) is
    # the idempotency key, answered 200 so canopy stops retrying, and never sent twice.
    _token, retry = post()
    assert retry.status_code == 200 and retry.json() == {"status": "duplicate"}
    # The very same token again is a replay: its jti is spent in the shared store (SDK 0.34).
    replay = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
    assert replay.status_code == 401 and replay.json() == {"error": "replayed"}
    assert _agent_messages(thread)[-1].text.startswith("Hello Amina!")
    assert len(_agent_messages(thread)) == 1
    pending.refresh_from_db()
    assert pending.status == "delivered"
    # The safety-net poll finds nothing left to do.
    assert handler.poll(pending) == "delivered"


@pytest.mark.parametrize(
    "change, status",
    [
        ({"key_other": True}, 401),
        ({"audience": "https://elsewhere.test/hook/"}, 401),
        ({"sub": "personalid:somebody-else"}, 409),
        ({"turn": "not-a-turn-of-ours"}, 404),
        # The SDK refuses a reply for another host site itself (ReplyRefused wrong_site, 0.34).
        ({"site": "ace-web"}, 401),
    ],
)
def test_the_webhook_refuses_what_canopy_did_not_sign_for_this_turn(
    client, world, fake_connectid, bridge_on, handle_now, change, status
):
    from canopy_sdk.keys import generate_private_key

    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    pending = PendingTurn.objects.get(thread=thread)
    kwargs = {
        "sub": change.get("sub", "personalid:amina123"),
        "session": pending.session.session_id,
        "turn": change.get("turn", pending.turn_id),
        "text": "Injected",
    }
    if change.get("key_other"):
        kwargs["key"] = generate_private_key("EdDSA")
    if change.get("site"):
        kwargs["site"] = change["site"]
    token = bridge_on.reply_token(change.get("audience", WEBHOOK), **kwargs)
    response = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
    assert response.status_code == status
    assert _agent_messages(thread) == []
    assert not ReplyDelivery.objects.exists()


def test_the_webhook_is_404_while_the_bridge_is_off(client, settings):
    settings.MESSAGING_ENABLED = False
    assert client.post("/messaging/canopy/replies/", data="x", content_type="application/jwt").status_code == 404


def test_a_turn_that_fails_or_never_ends_goes_to_a_person(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "first")
    pending = PendingTurn.objects.get(thread=thread)
    bridge_on.finish(pending.turn_id, "", status="failed")
    assert handler.poll(pending) == "failed"
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and thread.needs_attention
    assert "ended failed" in thread.attention_reason


def test_a_turn_past_its_timeout_goes_to_a_person(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "first")
    pending = PendingTurn.objects.get(thread=thread)
    later = timezone.now() + timedelta(seconds=conf.turn_timeout_seconds() + 5)
    assert handler.poll(pending, now=later) == "timed_out"
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and "did not answer within" in thread.attention_reason
    # An answer that arrives after that is not sent: a person has the thread.
    bridge_on.finish(pending.turn_id, "late answer")
    assert handler.deliver(pending, seq=0, text="late answer", ended=True, via="webhook") == "too_late"
    assert _agent_messages(thread) == []


def test_the_poll_task_backs_off_and_the_sweep_picks_up_lost_polls(
    client, world, fake_connectid, bridge_on, handle_now
):
    thread = _converse(client, fake_connectid, world, handle_now, "first")
    pending = PendingTurn.objects.get(thread=thread)
    bridge_on.polls.clear()
    assert tasks.poll_turn(pending.pk) == "running"
    assert tasks.poll_turn(pending.pk) == "running"
    assert [c for _, c in bridge_on.polls] == [handler.next_poll_in(1), handler.next_poll_in(2)]
    assert handler.next_poll_in(10) == handler.POLL_MAX
    PendingTurn.objects.filter(pk=pending.pk).update(next_poll_at=timezone.now() - timedelta(minutes=5))
    queued = []
    from connect_labs.messaging.canopy_bridge import tasks as bridge_tasks

    bridge_tasks.poll_turn.delay = lambda pk: queued.append(pk)
    try:
        assert bridge_tasks.poll_stale() == 1 and queued == [pending.pk]
    finally:
        del bridge_tasks.poll_turn.delay


def test_a_refused_send_hands_the_thread_to_a_person(client, world, fake_connectid, bridge_on, handle_now):
    bridge_on.fail_next["send"] = (403, "agent_unavailable: this agent does not serve the site's contacts")
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and "agent_unavailable" in thread.attention_reason
    assert bridge_on.kickoff_calls == [] and not PendingTurn.objects.exists()


def test_a_send_over_canopys_per_person_limit_waits_a_minute_and_is_sent_again(
    client, world, fake_connectid, bridge_on, handle_now
):
    bridge_on.fail_next["send"] = (429, "rate_limited: too many messages (6 a minute); wait a minute and send again")
    thread = _converse(client, fake_connectid, world, handle_now, "hello", "are you there?")
    thread.refresh_from_db()
    # Not a person's yet: the words wait in labs and are sent again a little over a minute later.
    assert thread.handler_kind == "agent" and not PendingTurn.objects.filter(thread=thread).exists()
    assert bridge_on.kickoff_calls == [(thread.pk, 1, handler.RATE_LIMIT_RETRY_SECONDS)]
    assert tasks.kickoff.run(thread.pk, attempt=1) == "sent"
    sent = bridge_on.sends[-1]
    assert "hello" in sent["text"] and "are you there?" in sent["text"]
    assert PendingTurn.objects.filter(thread=thread, status="pending").count() == 1


def test_a_message_written_during_the_wait_carries_the_refused_words_with_it(
    client, world, fake_connectid, bridge_on, handle_now
):
    bridge_on.fail_next["send"] = (429, "rate_limited: too many messages (6 a minute); wait a minute and send again")
    thread = _converse(client, fake_connectid, world, handle_now, "first words")
    assert not bridge_on.sends
    _converse(client, fake_connectid, world, handle_now, "second words")
    sent = bridge_on.sends[-1]["text"]
    assert sent.index("first words") < sent.index("second words")
    inbound = Message.objects.filter(thread=thread, direction=Direction.INBOUND)
    assert inbound.count() == 2 and not inbound.filter(canopy_turn_id="").exists()
    # The queued retry then finds the words sent and a turn in flight: nothing is lost or sent twice.
    assert handler.kickoff(Thread.objects.get(pk=thread.pk), attempt=1) == "a turn is in flight"


def test_still_over_the_limit_after_the_retries_goes_to_a_person_with_the_reason(
    client, world, fake_connectid, bridge_on, handle_now
):
    bridge_on.fail_next["send"] = (429, "rate_limited: too many messages today (200 a day); try again tomorrow")
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    for attempt in range(1, handler.RATE_LIMIT_RETRIES + 1):
        assert bridge_on.kickoff_calls[-1] == (thread.pk, attempt, handler.RATE_LIMIT_RETRY_SECONDS)
        bridge_on.fail_next["send"] = (429, "rate_limited: too many messages today (200 a day); try again tomorrow")
        outcome = handler.kickoff(Thread.objects.get(pk=thread.pk), attempt=attempt)
        assert outcome == ("rate_limited" if attempt < handler.RATE_LIMIT_RETRIES else "refused")
    assert len(bridge_on.kickoff_calls) == handler.RATE_LIMIT_RETRIES
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and thread.needs_attention
    assert "rate_limited" in thread.attention_reason and "per-person limit" in thread.attention_reason
    assert not PendingTurn.objects.exists()


def test_a_streamed_turn_ends_with_an_empty_post_and_each_piece_is_sent_once(
    client, world, fake_connectid, bridge_on, handle_now, settings
):
    settings.CANOPY_MESSAGING_REPLY_WEBHOOK = True
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    pending = PendingTurn.objects.get(thread=thread)

    def post(seq, text, ended, error=""):
        token = bridge_on.reply_token(
            WEBHOOK,
            sub="personalid:amina123",
            session=pending.session.session_id,
            turn=pending.turn_id,
            seq=seq,
            text=text,
            ended=ended,
            error=error,
        )
        return client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt").json()

    assert post(0, "Hello Amina!", False) == {"status": "delivered"}
    assert post(1, "Your next visit is Friday.", False) == {"status": "delivered"}
    pending.refresh_from_db()
    assert pending.status == "pending"
    # In stream mode the last post (ended) carries no text.
    assert post(2, "", True) == {"status": "empty"}
    assert post(1, "Your next visit is Friday.", False) == {"status": "duplicate"}
    pending.refresh_from_db()
    assert pending.status == "delivered"
    assert [m.text.split("\n")[0] for m in _agent_messages(thread)] == ["Hello Amina!", "Your next visit is Friday."]
    thread.refresh_from_db()
    assert thread.handler_kind == "agent"


def test_a_turn_that_ends_with_an_error_goes_to_a_person(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    pending = PendingTurn.objects.get(thread=thread)
    token = bridge_on.reply_token(
        WEBHOOK,
        sub="personalid:amina123",
        session=pending.session.session_id,
        turn=pending.turn_id,
        seq=0,
        text="",
        ended=True,
        error="cancelled",
    )
    response = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
    assert response.json() == {"status": "empty"}
    pending.refresh_from_db()
    assert (pending.status, pending.detail) == ("failed", "cancelled")
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and thread.needs_attention
    assert _agent_messages(thread) == []


def test_a_push_after_the_poll_delivered_the_turn_is_not_sent_again(
    client, world, fake_connectid, bridge_on, handle_now
):
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    pending = PendingTurn.objects.get(thread=thread)
    bridge_on.finish(pending.turn_id, "Hi there.")
    assert handler.poll(pending) == "delivered"
    for seq in (0, 1):
        token = bridge_on.reply_token(
            WEBHOOK,
            sub="personalid:amina123",
            session=pending.session.session_id,
            turn=pending.turn_id,
            seq=seq,
            text="Hi there.",
            ended=False,
        )
        response = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
        assert response.json() == {"status": "duplicate"}
    assert len(_agent_messages(thread)) == 1


def test_the_turn_limit_hands_the_thread_to_a_person(client, world, fake_connectid, bridge_on, handle_now):
    _set(
        "learn-support",
        direction="inbound",
        handler={"kind": "agent", "agent": "connect-coach", "skill": "learn-support"},
        caps={"max_turns": 1},
    )
    thread = _converse(client, fake_connectid, world, handle_now, "one")
    pending = PendingTurn.objects.get(thread=thread)
    bridge_on.finish(pending.turn_id, "Answer one")
    handler.poll(pending)
    _write(client, fake_connectid, world, "two")
    handle_now()
    thread.refresh_from_db()
    assert len(_agent_messages(thread)) == 1
    assert thread.handler_kind == "human" and "limit" in thread.attention_reason
    # The agent was not asked: its answer could not have been sent.
    assert len(bridge_on.sends) == 1


def test_an_answer_over_the_turn_limit_is_not_sent(client, world, fake_connectid, bridge_on, handle_now):
    _set(
        "learn-support",
        direction="inbound",
        handler={"kind": "agent", "agent": "connect-coach", "skill": "learn-support"},
        caps={"max_turns": 1},
    )
    thread = _converse(client, fake_connectid, world, handle_now, "one")
    pending = PendingTurn.objects.get(thread=thread)
    thread.send("A flow said something", "flow", "flow:x")
    bridge_on.finish(pending.turn_id, "Answer one")
    assert handler.poll(pending) == "delivered"
    thread.refresh_from_db()
    assert _agent_messages(thread) == []
    assert thread.handler_kind == "human" and "limit" in thread.attention_reason


def test_off_means_a_person_answers(client, world, fake_connectid, bridge_on, handle_now, monkeypatch):
    from connect_labs.messaging.canopy_bridge import conf

    monkeypatch.setattr(conf, "workspace", lambda: "")
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    assert thread.handler_kind == "human" and "not configured" in thread.attention_reason
    assert bridge_on.sends == []


def test_a_handoff_to_another_agent_opens_a_new_session_seeded_with_the_note(
    client, world, fake_connectid, bridge_on, handle_now
):
    _set(
        "payments",
        direction="inbound",
        priority=500,
        handler={"kind": "agent", "agent": "connect-helper", "skill": "payments"},
    )
    thread = _converse(client, fake_connectid, world, handle_now, "Where is my money?")
    first = CanopySession.objects.get(thread=thread)
    router.handoff_to(thread, "payments", note="Asked about a missing payment", by="agent:connect-coach")
    first.refresh_from_db()
    assert first.ended_at is not None
    new = CanopySession.objects.get(thread=thread, ended_at__isnull=True)
    assert (new.agent, new.skill, new.session_id) == ("connect-helper", "payments", "")
    assert new.seed_note == "Asked about a missing payment"
    assert bridge_on.kickoffs == [thread.pk]
    # The worker is still waiting (the first agent had not answered): the new agent picks it up.
    PendingTurn.objects.update(status="delivered")
    assert handler.kickoff(Thread.objects.get(pk=thread.pk)) == "sent"
    send = bridge_on.sends[-1]
    assert send["session"] == CanopySession.objects.get(pk=new.pk).session_id
    assert bridge_on.sessions[send["session"]]["agent"] == "connect-helper"
    assert "Asked about a missing payment" in send["context"]["body"]
    assert send["text"] == "Where is my money?"


def test_kickoff_does_nothing_when_the_worker_was_answered(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "hi")
    pending = PendingTurn.objects.get(thread=thread)
    bridge_on.finish(pending.turn_id, "Hello!")
    handler.poll(pending)
    assert handler.kickoff(thread) == "nothing waiting"


def test_closing_the_thread_ends_the_session(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "hi")
    thread.close("done", by="staff")
    assert CanopySession.objects.get(thread=thread).end_reason == "done"


def test_actions_the_agent_asked_for_run_after_its_answer(client, world, fake_connectid, bridge_on, handle_now):
    thread = _converse(client, fake_connectid, world, handle_now, "thanks, that's all")
    pending = PendingTurn.objects.get(thread=thread)
    PendingTurn.objects.filter(pk=pending.pk).update(after=[{"action": "close", "reason": "resolved", "by": "agent"}])
    bridge_on.finish(pending.turn_id, "Goodbye!")
    handler.poll(pending)
    thread.refresh_from_db()
    assert _agent_messages(thread)[-1].text.startswith("Goodbye!")
    assert not thread.is_open and thread.close_reason.startswith("resolved")


def test_a_turn_that_never_answers_drops_the_close_it_asked_for(client, world, fake_connectid, bridge_on, handle_now):
    """The person who takes a timed-out thread must find it open, not closed under them."""
    thread = _converse(client, fake_connectid, world, handle_now, "thanks, that's all")
    pending = PendingTurn.objects.get(thread=thread)
    PendingTurn.objects.filter(pk=pending.pk).update(after=[{"action": "close", "reason": "resolved", "by": "agent"}])
    handler.fail(pending, status=TurnStatus.TIMED_OUT, reason="The agent took too long; a person should reply.")
    thread.refresh_from_db()
    assert thread.is_open and thread.handler_kind == "human"
    assert PendingTurn.objects.get(pk=pending.pk).after == []


def test_the_real_relay_client_is_made_once_from_the_real_config(bridge_on):
    """``HostConfig`` cannot be hashed (it holds a dict), so the relay is cached by the config's
    identity. Every other test replaces ``relay_client``; this one builds the real one."""
    handler._relay_cache = None
    first = handler.relay_client()
    assert isinstance(first, sdk.RelayClient)
    assert handler.relay_client() is first


def test_a_poll_and_a_send_from_another_process_name_the_person_themselves(
    client, world, fake_connectid, bridge_on, handle_now, monkeypatch
):
    """The session is opened in one Celery process and polled (and written to) in another, whose
    relay client has never minted for this worker. Each call names the session's channel and agent."""
    thread = _converse(client, fake_connectid, world, handle_now, "Is my payment coming?")
    pending = PendingTurn.objects.get(thread=thread)
    fresh = sdk.RelayClient(conf.require_config(), transport=bridge_on.transport, mint=bridge_on.mint)
    monkeypatch.setattr(handler, "relay_client", lambda: fresh)
    assert handler.poll(pending) == "running"
    bridge_on.finish(pending.turn_id, "Yes, it was sent on Monday.")
    assert handler.poll(pending) == "delivered"
    other = sdk.RelayClient(conf.require_config(), transport=bridge_on.transport, mint=bridge_on.mint)
    monkeypatch.setattr(handler, "relay_client", lambda: other)
    _write(client, fake_connectid, world, "Thank you")
    handle_now()
    assert bridge_on.sends[-1]["text"] == "Thank you"
