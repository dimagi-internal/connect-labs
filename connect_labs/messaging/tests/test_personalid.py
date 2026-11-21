"""Labs as a PersonalID message server, against a fake connect-id.

Every worker name and message here is invented.
"""

from __future__ import annotations

import httpx
import pytest

from connect_labs.messaging import service
from connect_labs.messaging.models import Address, DeliveryStatus, Message, Thread, TransportEvent
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.messaging.tests.fake_connectid import SECRET
from connect_labs.workers.models import WorkerEvent

pytestmark = pytest.mark.django_db


def _thread(worker, address, **kwargs):
    return service.open_thread(worker, address=address, direction="out", program_id=PROGRAM, **kwargs)


@pytest.fixture
def channel(fake_connectid, messaging_on, worker):
    fake_connectid.add_user(worker.username)
    return service.address_for(worker, PROGRAM, channel_name="Bednet Program")


# --- channels ---------------------------------------------------------------------------


def test_a_channel_is_made_once_per_worker_and_program(channel, fake_connectid, worker):
    assert channel.channel_id in fake_connectid.channels
    assert fake_connectid.channels[channel.channel_id]["source"] == f"labs-program-{PROGRAM}"
    assert fake_connectid.channels[channel.channel_id]["name"] == "Bednet Program"
    assert channel.consent is True and channel.key
    again = service.address_for(worker, PROGRAM)
    assert again.pk == channel.pk and len(fake_connectid.channels) == 1


def test_nothing_reaches_personalid_while_messaging_is_off(fake_connectid, worker, settings):
    settings.MESSAGING_ENABLED = False
    fake_connectid.add_user(worker.username)
    with pytest.raises(service.MessagingError) as e:
        service.address_for(worker, PROGRAM)
    assert e.value.code == "messaging_off"
    assert fake_connectid.calls == []


# --- sending ----------------------------------------------------------------------------


def test_a_message_is_sent_with_create_message_and_stored(channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    message = thread.send("Your nets arrive Tuesday. Details: https://example.org/nets.", "staff", "nurse.joy")
    message.refresh_from_db()
    assert message.delivery_status == DeliveryStatus.SENT
    sent = fake_connectid.messages[message.transport_message_id]
    assert sent["version_header"] == "2" and sent["parts"] == ["message"]
    assert ("POST", "/messaging/send_fcm/") not in fake_connectid.calls
    (shown,) = fake_connectid.texts_to(worker.username, channel.key)
    # the bare URL became a tappable Markdown link, and the first message carries the disclosure
    assert "[https://example.org/nets](https://example.org/nets)." in shown
    assert "AI" in shown and "STOP" in shown
    assert message.text == shown  # what is stored is what was sent


def test_only_the_first_message_on_a_channel_carries_the_disclosure(channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    thread.send("one", "staff")
    thread.send("two", "staff")
    first, second = fake_connectid.texts_to(worker.username, channel.key)
    assert "STOP" in first and second == "two"


def test_every_message_is_on_the_workers_timeline(channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    message = thread.send("hello", "flow", "welcome")
    event = WorkerEvent.objects.get(dedupe_key=f"messaging:message:{message.pk}")
    assert event.kind == "message_out" and event.source == "labs" and event.program_id == PROGRAM
    assert event.payload["author_kind"] == "flow" and "hello" not in str(event.payload)


def test_a_retry_after_a_lost_answer_counts_as_delivered(channel, fake_connectid, worker, run_now):
    """The first attempt arrived but its answer was lost: the retry carries the same id."""
    thread = _thread(worker, channel)
    message = thread.send("first try", "staff")
    Message.objects.filter(pk=message.pk).update(delivery_status=DeliveryStatus.QUEUED)
    assert service.deliver(message.pk) == "sent"  # MESSAGE_ID_ALREADY_EXISTS is success
    assert len(fake_connectid.messages) == 1


def test_personalid_down_is_retried_and_then_given_up(channel, fake_connectid, worker, monkeypatch):
    from connect_labs.messaging import tasks
    from connect_labs.messaging.transport import TransportUnavailable

    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: None)
    thread = _thread(worker, channel)
    message = thread.send("hello", "staff")
    fake_connectid.fail_next = [httpx.Response(503)]
    with pytest.raises(TransportUnavailable):
        service.deliver(message.pk)
    message.refresh_from_db()
    assert message.delivery_status == DeliveryStatus.QUEUED and message.attempts == 1
    fake_connectid.fail_next = [httpx.ConnectError("down")] * 3
    result = tasks.deliver_message.apply(args=[message.pk], retries=tasks.DELIVERY_RETRIES)
    assert result.get() == "failed"
    message.refresh_from_db()
    assert message.delivery_status == DeliveryStatus.FAILED and "could not be reached" in message.failure_reason
    thread.refresh_from_db()
    assert thread.needs_attention


def test_a_refused_consent_turns_the_channel_off(channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    fake_connectid.channels[channel.channel_id]["consent"] = False
    message = thread.send("hello", "staff")
    message.refresh_from_db()
    channel.refresh_from_db()
    thread.refresh_from_db()
    assert message.delivery_status == DeliveryStatus.FAILED
    assert channel.consent is False and not thread.is_open


def test_nothing_sends_on_a_channel_turned_off(channel, worker):
    thread = _thread(worker, channel)
    Address.objects.filter(pk=channel.pk).update(consent=False)
    with pytest.raises(service.MessagingError) as e:
        thread.send("hello", "staff")
    assert e.value.code == "not_reachable"


def test_nothing_sends_when_the_program_is_off(channel, worker):
    from connect_labs.scope_config.models import ScopeConfig

    ScopeConfig.objects.filter(scope_key=str(PROGRAM)).update(data={"enabled": False})
    thread = _thread(worker, channel)
    with pytest.raises(service.MessagingError) as e:
        thread.send("hello", "staff")
    assert e.value.code == "program_off"


def test_a_message_queued_before_messaging_was_turned_off_is_not_sent(
    channel, fake_connectid, worker, settings, monkeypatch
):
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: None)
    message = _thread(worker, channel).send("hello", "staff")
    settings.MESSAGING_ENABLED = False
    assert service.deliver(message.pk) == "failed"
    assert fake_connectid.messages == {}


def test_a_worker_cannot_be_impersonated_as_an_author(channel, worker):
    with pytest.raises(service.MessagingError):
        _thread(worker, channel).send("hello", "worker")


# --- the key endpoint -------------------------------------------------------------------


def test_the_key_goes_to_the_channels_own_worker(client, channel, fake_connectid, worker):
    token = fake_connectid.tokens and next(t for t, u in fake_connectid.tokens.items() if u == worker.username)
    response = client.post(
        "/messaging/personalid/key/", {"channel_id": channel.channel_id}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200 and response.json() == {"key": channel.key}
    channel.refresh_from_db()
    assert channel.key_served_at is not None


def test_the_username_matches_whatever_its_case(client, channel, fake_connectid, worker):
    fake_connectid.tokens["upper"] = worker.username.upper()
    response = client.post(
        "/messaging/personalid/key/", {"channel_id": channel.channel_id}, headers={"Authorization": "Bearer upper"}
    )
    assert response.status_code == 200


def test_someone_elses_channel_and_an_unknown_one_look_the_same(client, channel, fake_connectid):
    other = fake_connectid.add_user("someone_else")
    mine = client.post(
        "/messaging/personalid/key/", {"channel_id": channel.channel_id}, headers={"Authorization": f"Bearer {other}"}
    )
    unknown = client.post(
        "/messaging/personalid/key/", {"channel_id": "nope"}, headers={"Authorization": f"Bearer {other}"}
    )
    assert mine.status_code == unknown.status_code == 404
    assert "key" not in mine.json()


def test_a_token_personalid_refuses_gets_nothing(client, channel):
    response = client.post(
        "/messaging/personalid/key/", {"channel_id": channel.channel_id}, headers={"Authorization": "Bearer forged"}
    )
    assert response.status_code == 401


def test_a_key_request_needs_both_parts(client, channel):
    assert client.post("/messaging/personalid/key/", {"channel_id": channel.channel_id}).status_code == 400


# --- what connect-id posts --------------------------------------------------------------


def test_a_reply_is_stored_answered_200_and_lands_in_the_inbox(client, channel, fake_connectid, worker, run_now):
    response = fake_connectid.reply(client, channel.channel_id, channel.key, "Asante, I got them")
    assert response.status_code == 200 and response.content == b""
    message = Message.objects.get(direction="in")
    assert message.text == "Asante, I got them" and message.author_kind == "worker"
    thread = message.thread
    assert thread.handler_kind == "human" and thread.needs_attention and thread.direction == "in"
    assert WorkerEvent.objects.get(dedupe_key=f"messaging:message:{message.pk}").source == "personalid"
    # ciphertext only was kept of what connect-id posted
    event = TransportEvent.objects.get()
    assert event.processed_at and "Asante" not in str(event.payload)


def test_a_reply_joins_the_open_thread(client, channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    fake_connectid.reply(client, channel.channel_id, channel.key, "ok")
    assert Message.objects.get(direction="in").thread_id == thread.pk


def test_a_replayed_delivery_is_stored_once(client, channel, fake_connectid, worker, run_now):
    ids = ["7d0c4f7e-0000-4000-8000-000000000001"]
    fake_connectid.reply(client, channel.channel_id, channel.key, "once", message_ids=ids)
    fake_connectid.reply(client, channel.channel_id, channel.key, "once", message_ids=ids)
    # and an id repeated inside one batch
    fake_connectid.reply(client, channel.channel_id, channel.key, "a", "a", message_ids=[ids[0].replace("1", "2")] * 2)
    assert Message.objects.filter(direction="in").count() == 2


@pytest.mark.parametrize(
    "signature",
    ["", "bm90IGEgc2lnbmF0dXJl", None],
    ids=["missing", "wrong", "wrong-secret"],
)
def test_an_unsigned_or_missigned_post_is_refused(client, channel, signature, run_now):
    from connect_labs.messaging.tests.fake_connectid import FakeConnectID

    body = {"channel_id": channel.channel_id, "consent": False}
    if signature is None:
        response = FakeConnectID.signed_post(client, "/messaging/personalid/consent/", body, secret="not-" + SECRET)
    else:
        response = FakeConnectID.signed_post(client, "/messaging/personalid/consent/", body, signature=signature)
    assert response.status_code == 401
    assert TransportEvent.objects.count() == 0
    channel.refresh_from_db()
    assert channel.consent is True


def test_no_secret_configured_refuses_everything(client, channel, settings, fake_connectid):
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = ""
    response = fake_connectid.set_consent(client, channel.channel_id, False)
    assert response.status_code == 401


def test_turning_the_channel_off_closes_its_threads_and_blocks_sends(client, channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    assert fake_connectid.set_consent(client, channel.channel_id, False).status_code == 200
    channel.refresh_from_db()
    thread.refresh_from_db()
    assert channel.consent is False and channel.consent_history[-1]["source"] == "personalid"
    assert not thread.is_open and thread.close_reason.startswith("unsubscribed")
    assert WorkerEvent.objects.filter(worker=worker, kind="consent_changed").exists()
    with pytest.raises(service.MessagingError):
        _thread(worker, channel).send("hello", "staff")


def test_stop_stops_and_start_starts_again(client, channel, fake_connectid, worker, run_now):
    thread = _thread(worker, channel)
    fake_connectid.reply(client, channel.channel_id, channel.key, "STOP")
    channel.refresh_from_db()
    thread.refresh_from_db()
    assert channel.opted_out_at is not None and not thread.is_open
    fake_connectid.reply(client, channel.channel_id, channel.key, "start")
    channel.refresh_from_db()
    assert channel.can_send


def test_a_receipt_marks_our_message_delivered(client, channel, fake_connectid, worker, run_now):
    message = _thread(worker, channel).send("hello", "staff")
    fake_connectid.receipt(client, channel.channel_id, [message.transport_message_id])
    message.refresh_from_db()
    assert message.delivery_status == DeliveryStatus.DELIVERED and message.delivered_at.minute == 5


def test_a_post_for_a_channel_not_ours_is_answered_200_and_recorded(client, channel, fake_connectid, run_now):
    response = fake_connectid.signed_post(
        client, "/messaging/personalid/consent/", {"channel_id": "not-ours", "consent": False}
    )
    assert response.status_code == 200
    event = TransportEvent.objects.get()
    assert event.processed_at and "no channel" in event.error


def test_a_message_we_cannot_decrypt_is_recorded_not_lost(client, channel, fake_connectid, run_now):
    from connect_labs.messaging import crypto

    fake_connectid.reply(client, channel.channel_id, crypto.new_key(), "wrong key")
    event = TransportEvent.objects.get()
    assert "cannot decrypt" in event.error
    assert not Message.objects.filter(direction="in").exists()


def test_every_endpoint_is_404_while_messaging_is_off(client, channel, fake_connectid, settings):
    settings.MESSAGING_ENABLED = False
    for path in ("key", "delivery", "consent", "callback"):
        response = fake_connectid.signed_post(
            client, f"/messaging/personalid/{path}/", {"channel_id": channel.channel_id}
        )
        assert response.status_code == 404, path
    assert TransportEvent.objects.count() == 0


def test_the_delivery_answer_does_not_wait_for_the_work(client, channel, fake_connectid, monkeypatch):
    """connect-id never retries: the endpoint stores and answers; a broken queue loses nothing."""
    from connect_labs.messaging import tasks

    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())

    def broker_down(*a, **k):
        raise ConnectionError("broker down")

    monkeypatch.setattr(tasks.process_transport_event, "delay", broker_down)
    response = fake_connectid.reply(client, channel.channel_id, channel.key, "hello?")
    assert response.status_code == 200
    event = TransportEvent.objects.get()
    assert event.processed_at is None
    tasks.process_transport_event(event.pk)  # what the sweep does
    assert Message.objects.filter(direction="in").count() == 1


def test_thread_close_is_idempotent(channel, worker):
    thread = _thread(worker, channel)
    thread.close("done", by="nurse.joy")
    thread.close("again")
    thread.refresh_from_db()
    assert thread.close_reason == "done (by nurse.joy)"
    assert Thread.objects.get(pk=thread.pk).state == "closed"
