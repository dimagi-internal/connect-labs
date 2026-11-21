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
from connect_labs.messaging.canopy_bridge.models import CanopySession, PendingTurn, ReplyDelivery, WorkerBrief
from connect_labs.messaging.models import Address, Message, Thread
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
    kickoffs = []
    monkeypatch.setattr(tasks.kickoff, "apply_async", lambda args, countdown=None: kickoffs.append(args[0]))
    bridge_on.polls, bridge_on.kickoffs = polls, kickoffs
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
    token = bridge_on.reply_token(
        WEBHOOK,
        sub="personalid:amina123",
        session=pending.session.session_id,
        turn=pending.turn_id,
        seq=0,
        text="Hello Amina!",
        ended=True,
    )
    response = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
    assert response.status_code == 200 and response.json() == {"status": "delivered"}
    retry = client.post("/messaging/canopy/replies/", data=token, content_type="application/jwt")
    assert retry.json() == {"status": "duplicate"}
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
        ({"site": "connect-labs"}, 409),
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
    bridge_on.fail_next["send"] = (429, "rate_limited: too many")
    thread = _converse(client, fake_connectid, world, handle_now, "hello")
    assert thread.handler_kind == "human" and "rate_limited" in thread.attention_reason
    assert not PendingTurn.objects.exists()


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


def test_off_means_a_person_answers(client, world, fake_connectid, bridge_on, handle_now, settings):
    settings.CANOPY_MESSAGING_WORKSPACE = ""
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
