"""The Inbox: staff see their programs' conversations with Connect Workers, reply, take over and hand back.

Every name here is invented.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.access import scopes
from connect_labs.messaging import handlers, inbox, service
from connect_labs.messaging.handlers import Handler
from connect_labs.messaging.models import DeliveryStatus, Message, Thread
from connect_labs.messaging.tests.conftest import PROGRAM

pytestmark = pytest.mark.django_db

OTHER_PROGRAM = 777


@pytest.fixture
def people(django_user_model, monkeypatch):
    staff = django_user_model.objects.create_user(username="nurse.joy", password="x")
    outsider = django_user_model.objects.create_user(username="outsider", password="x")
    opp_only = django_user_model.objects.create_user(username="opp.manager", password="x")
    held = {
        "nurse.joy": scopes.Holdings(frozenset(), frozenset(), frozenset({PROGRAM})),
        "outsider": scopes.Holdings(frozenset(), frozenset(), frozenset({OTHER_PROGRAM})),
        "opp.manager": scopes.Holdings(frozenset(), frozenset({9001}), frozenset()),
    }

    def holdings(caller):
        user = scopes._user_of(caller)
        return held[user.username]

    monkeypatch.setattr(scopes, "holdings", holdings)
    return {"staff": staff, "outsider": outsider, "opp_only": opp_only}


@pytest.fixture
def channel(fake_connectid, messaging_on, worker):
    fake_connectid.add_user(worker.username)
    return service.address_for(worker, PROGRAM, channel_name="Bednet Program")


class Recorder(Handler):
    def __init__(self):
        self.calls = []

    def start(self, thread, ctx):
        self.calls.append(("start", ctx))

    def on_message(self, thread, msgs):
        self.calls.append(("on_message", [m.text for m in msgs]))

    def on_handoff_in(self, thread, from_, note):
        self.calls.append(("handoff_in", from_, note))

    def stop(self, thread, reason):
        self.calls.append(("stop", reason))


@pytest.fixture
def recorder():
    previous = handlers.get("agent")
    rec = handlers.register("agent", Recorder())
    yield rec
    if previous is not None:
        handlers.register("agent", previous)
    else:
        handlers._HANDLERS.pop("agent", None)


def _agent_thread(worker, channel, **kw):
    return service.open_thread(
        worker, address=channel, direction="out", program_id=PROGRAM, handler_kind="agent", handler_ref="coach", **kw
    )


# --- access ------------------------------------------------------------------------------


def test_only_people_holding_the_program_see_its_threads(people, channel, worker, client):
    thread = _agent_thread(worker, channel)
    assert list(inbox.threads(people["staff"])) == [thread]
    assert list(inbox.threads(people["outsider"])) == []
    client.force_login(people["outsider"])
    assert client.get(f"/labs/messaging/thread/{thread.pk}/").status_code == 404
    response = client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "reply", "text": "hi"})
    assert response.status_code == 404
    assert not Message.objects.filter(author_kind="staff").exists()


def test_holding_the_opportunity_is_enough(people, channel, worker):
    thread = service.open_thread(worker, address=channel, direction="out", program_id=PROGRAM, opportunity_id=9001)
    assert list(inbox.threads(people["opp_only"])) == [thread]


def test_unknown_holdings_deny_and_say_so(people, channel, worker, client, monkeypatch):
    def unknowable(caller):
        raise scopes.ScopesUnavailable(scopes.UNKNOWABLE)

    monkeypatch.setattr(scopes, "holdings", unknowable)
    _agent_thread(worker, channel)
    client.force_login(people["staff"])
    response = client.get("/labs/messaging/")
    assert response.status_code == 200 and b"could not find out" in response.content


def test_the_inbox_needs_a_login(client):
    assert client.get("/labs/messaging/").status_code == 302


# --- the pages ---------------------------------------------------------------------------


def test_the_inbox_lists_waiting_threads_first_and_filters(people, channel, worker, client, run_now):
    quiet = _agent_thread(worker, channel, playbook_key="learn-stalled")
    waiting = service.open_thread(
        worker, address=channel, direction="in", program_id=PROGRAM, needs_attention=True, attention_reason="wrote in"
    )
    client.force_login(people["staff"])
    page = client.get("/labs/messaging/")
    rows = [row["thread"] for row in page.context["rows"]]
    assert rows == [waiting, quiet]
    assert b"wrote in" in page.content
    only = client.get("/labs/messaging/", {"playbook": "learn-stalled"}).context["rows"]
    assert [r["thread"] for r in only] == [quiet]
    attention = client.get("/labs/messaging/", {"attention": "1"}).context["rows"]
    assert [r["thread"] for r in attention] == [waiting]


def test_the_thread_page_shows_the_transcript_and_never_the_phone(people, channel, worker, client, run_now):
    worker.phone = "+2348000000001"
    worker.save()
    thread = _agent_thread(worker, channel)
    thread.send("Hello Amina", "agent", "coach")
    client.force_login(people["staff"])
    page = client.get(f"/labs/messaging/thread/{thread.pk}/")
    assert page.status_code == 200
    assert b"Hello Amina" in page.content and b"+2348000000001" not in page.content


# --- what staff do ------------------------------------------------------------------------


def test_a_staff_reply_is_sent_as_staff_and_takes_the_thread_over(people, channel, worker, recorder, client, run_now):
    thread = _agent_thread(worker, channel)
    client.force_login(people["staff"])
    response = client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "reply", "text": "I'll call you"})
    assert response.status_code == 302
    message = Message.objects.get(author_kind="staff")
    assert message.author_ref == "nurse.joy" and message.delivery_status == DeliveryStatus.SENT
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and thread.taken_over_by == "nurse.joy"
    assert thread.previous_handler == {"kind": "agent", "ref": "coach"}
    assert ("stop", "handed off") in recorder.calls


def test_hand_back_restores_the_handler_and_tells_it(people, channel, worker, recorder, run_now):
    thread = _agent_thread(worker, channel)
    inbox.take_over(people["staff"], thread.pk)
    thread = inbox.hand_back(people["staff"], thread.pk)
    assert thread.handler_kind == "agent" and thread.handler_ref == "coach" and not thread.taken_over_by
    assert recorder.calls[-1][0] == "handoff_in" and "Handed back by nurse.joy" in recorder.calls[-1][2]
    notes = Message.objects.filter(thread=thread, delivery_status=DeliveryStatus.NOTE)
    assert notes.count() == 2  # taken over, handed back; never sent to the worker


def test_nothing_to_hand_back_is_refused(people, channel, worker):
    thread = service.open_thread(worker, address=channel, direction="in", program_id=PROGRAM)
    with pytest.raises(service.MessagingError) as e:
        inbox.hand_back(people["staff"], thread.pk)
    assert e.value.code == "nothing_to_hand_back"


def test_close_from_the_inbox_stops_the_handler(people, channel, worker, recorder, client):
    thread = _agent_thread(worker, channel)
    client.force_login(people["staff"])
    client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "close"})
    thread.refresh_from_db()
    assert not thread.is_open and "nurse.joy" in thread.close_reason
    assert recorder.calls[-1][0] == "stop"


def test_close_from_the_inbox_keeps_the_reason_a_note_and_an_audit_row(people, channel, worker, recorder, client):
    from connect_labs.audit_trail.models import AuditEvent

    thread = _agent_thread(worker, channel)
    client.force_login(people["staff"])
    page = client.get(f"/labs/messaging/thread/{thread.pk}/")
    assert b'data-testid="close"' in page.content and b'data-testid="close-reason"' in page.content
    client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "close", "reason": "test run over"})
    thread.refresh_from_db()
    assert not thread.is_open and thread.close_reason == "test run over (by nurse.joy)"
    note = Message.objects.get(thread=thread, delivery_status=DeliveryStatus.NOTE)
    assert note.text == "Closed by nurse.joy in the Inbox: test run over" and note.author_ref == "nurse.joy"
    event = AuditEvent.objects.filter(resource_type="messaging_thread", action="update").get()
    assert event.resource_id == str(thread.pk) and event.metadata["change"] == "closed"
    # The worker's channel is untouched (closing is not a STOP).
    channel.refresh_from_db()
    assert channel.can_send
    # A closed thread offers no actions.
    assert b'data-testid="close"' not in client.get(f"/labs/messaging/thread/{thread.pk}/").content


def test_an_outsider_cannot_close(people, channel, worker, client):
    thread = _agent_thread(worker, channel)
    client.force_login(people["outsider"])
    response = client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "close"})
    assert response.status_code == 404
    with pytest.raises(inbox.access.NoAccess):
        inbox.close(people["outsider"], thread.pk)
    thread.refresh_from_db()
    assert thread.is_open


def test_a_reply_to_a_closed_thread_is_refused_with_a_reason(people, channel, worker, client):
    thread = _agent_thread(worker, channel)
    thread.close("done")
    client.force_login(people["staff"])
    response = client.post(f"/labs/messaging/thread/{thread.pk}/", {"action": "reply", "text": "hi"}, follow=True)
    assert b"closed" in response.content
    assert not Message.objects.filter(author_kind="staff").exists()


def test_mark_handled_clears_attention(people, channel, worker):
    thread = service.open_thread(worker, address=channel, direction="in", program_id=PROGRAM, needs_attention=True)
    assert not inbox.mark_handled(people["staff"], thread.pk).needs_attention


# --- the built-in handlers ------------------------------------------------------------------


def test_a_worker_writing_in_lands_with_a_person(client, channel, fake_connectid, run_now):
    fake_connectid.reply(client, channel.channel_id, channel.key, "Habari, my phone broke")
    thread = Thread.objects.get()
    assert thread.handler_kind == "human" and thread.needs_attention
    assert thread.attention_reason == "The Connect Worker wrote in"


def test_a_reply_to_a_none_thread_is_handed_to_a_person(client, channel, fake_connectid, worker, handle_now):
    thread = service.open_thread(worker, address=channel, direction="out", program_id=PROGRAM, handler_kind="none")
    fake_connectid.reply(client, channel.channel_id, channel.key, "ok")
    handle_now()
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and thread.needs_attention
    assert "expects no reply" in thread.attention_reason


def test_a_failing_handler_hands_the_worker_to_a_person(client, channel, fake_connectid, worker, handle_now):
    class Broken(Recorder):
        def on_message(self, thread, msgs):
            raise RuntimeError("boom")

    previous = handlers.get("flow")
    handlers.register("flow", Broken())
    try:
        thread = service.open_thread(worker, address=channel, direction="out", program_id=PROGRAM, handler_kind="flow")
        fake_connectid.reply(client, channel.channel_id, channel.key, "hello?")
        handle_now()
    finally:
        if previous is not None:
            handlers.register("flow", previous)
        else:
            handlers._HANDLERS.pop("flow", None)
    thread.refresh_from_db()
    assert thread.handler_kind == "human" and "failed" in thread.attention_reason


def test_the_builtin_handlers_do_not_replace_an_installed_one(recorder):
    handlers.register_builtin()
    assert handlers.get("agent") is recorder
