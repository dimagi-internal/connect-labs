"""Communications: anyone allowed prepares a draft; only a person, in labs, sends it.

Every name and address here is invented.
"""

from __future__ import annotations

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse

from connect_labs.communications import kinds, service
from connect_labs.communications.models import Communication, SentMessage
from connect_labs.mcp.tool_registry import MCPToolError, get_tool, list_tools
from connect_labs.solicitations.tests.factories import grant, make_org, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def mail(settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    queued = []

    def fake_send(
        subject,
        message,
        recipient_list,
        html_message=None,
        from_email=None,
        *,
        reply_to=None,
        cc=None,
        countdown=None,
        sent_message_id=None,
    ):
        queued.append({"subject": subject, "body": message, "to": recipient_list, "reply_to": reply_to, "cc": cc})
        if sent_message_id:  # what the mail task does once the mail service takes it
            from connect_labs.communications.service import delivered

            delivered(sent_message_id)
        return object()

    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", fake_send)
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    return queued


@pytest.fixture
def world():
    org = make_org("partner-org", name="Partner Org")
    writer = make_user("writer")
    grant(writer, org)
    return {"org": org, "writer": writer, "outsider": make_user("outsider")}


def browser_request(user, **meta):
    request = RequestFactory().post("/communications/1/", **meta)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    return request


def draft_for(world, **overrides):
    tool = get_tool("communications_draft_create").handler
    args = {
        "kind": "general",
        "params": {"owner_org": "partner-org"},
        "subject": "Hello {org_name}",
        "body": "Dear {first_name},\n\nA note for {name}.",
        "recipients": [{"email": "amina@clinic.example", "name": "Amina Bello", "org": "partner-org"}],
        **overrides,
    }
    return tool(user=world["writer"], **args)


def test_an_agent_prepares_a_draft_and_gets_its_url(world):
    out = draft_for(world)
    assert out["status"] == "draft" and out["prepared_via"] == "mcp"
    assert out["url"].endswith(reverse("communications:detail", args=[out["id"]]))
    assert out["preview"]["subject"] == "Hello Partner Org"
    assert out["preview"]["body"].startswith("Dear Amina,")
    assert out["reply_to"] == "writer@example.org"


def test_no_mcp_tool_sends():
    names = [t["name"] for t in list_tools() if t["name"].startswith("communications_")]
    assert names and not any("send" in name for name in names)


def test_only_members_write_for_an_organisation(world):
    tool = get_tool("communications_draft_create").handler
    with pytest.raises(MCPToolError):
        tool(user=world["outsider"], kind="general", params={"owner_org": "partner-org"})
    draft = draft_for(world)
    with pytest.raises(MCPToolError):
        get_tool("communications_get").handler(user=world["outsider"], id=draft["id"])


def test_a_general_message_links_a_record_only_its_writer_may_read(world):
    from connect_labs.solicitations.tests.factories import make_call

    out = draft_for(world, about_ref=f"labs.labsorg:{world['org'].pk}")
    assert Communication.objects.get(pk=out["id"]).about == world["org"]
    hidden = make_call(make_org("owner-org"), title="Unlisted call", listed=False)
    with pytest.raises(MCPToolError) as missing:
        draft_for(world, about_ref="solicitations.solicitation:999999")
    with pytest.raises(MCPToolError) as unreadable:
        draft_for(world, about_ref=f"solicitations.solicitation:{hidden.pk}")
    # One answer for missing and unreadable: naming a record reveals nothing about it.
    assert str(missing.value).replace("999999", "N") == str(unreadable.value).replace(str(hidden.pk), "N")


def test_any_other_reference_is_kept_as_written_and_listed_by_it(world):
    out = draft_for(world, about_ref="workflow.run:812")
    communication = Communication.objects.get(pk=out["id"])
    assert communication.about_type is None and communication.about_id == "workflow.run:812"
    assert out["about"] == {"ref": "workflow.run:812"}
    listed = get_tool("communications_list").handler(user=world["writer"], about_ref="workflow.run:812")
    assert [c["id"] for c in listed["communications"]] == [out["id"]]


def test_bad_addresses_and_links_are_refused_not_crashed_on(world, client):
    with pytest.raises(MCPToolError):
        draft_for(world, recipients=[{"email": "a@clinic.example", "cc": ["not-an-address"]}])
    with pytest.raises(MCPToolError):
        draft_for(world, params={"owner_org": "partner-org", "program_id": "abc"})
    client.force_login(world["writer"])
    assert client.get(reverse("communications:outbox") + "?kind=nope&about=1").status_code == 404
    assert (
        client.get(reverse("communications:outbox") + "?kind=supply_chain.tender_emails&about=abc").status_code == 404
    )


def test_a_long_title_never_overflows_the_record(world, mail):
    out = draft_for(world, subject="{org_name} " + "x" * 295)
    service.send(browser_request(world["writer"]), out["id"])
    assert len(SentMessage.objects.get(communication_id=out["id"]).subject) == 300


def test_a_writers_own_drafts_stay_listed_however_many_others_there_are(world):
    from connect_labs.communications.models import Communication as C

    other = make_org("busy-org")
    C.objects.bulk_create([C(kind="general", owner_org=other, title=f"theirs {i}") for i in range(450)])
    out = draft_for(world)
    older = C.objects.get(pk=out["id"])
    C.objects.filter(pk=older.pk).update(updated_at=older.updated_at.replace(year=2020))
    listed = get_tool("communications_list").handler(user=world["writer"])
    assert out["id"] in [c["id"] for c in listed["communications"]]


def test_a_person_sends_and_each_recipient_gets_their_own_copy(world, mail):
    out = draft_for(
        world,
        recipients=[
            {
                "email": "amina@clinic.example",
                "name": "Amina Bello",
                "org": "partner-org",
                "cc": ["boss@clinic.example"],
            },
            {"email": "musa@clinic.example", "name": "Musa Ali", "org": "partner-org"},
        ],
        reply_to="pm@partner.example",
    )
    result = service.send(browser_request(world["writer"]), out["id"])
    assert result["queued"] == 2
    assert [m["to"] for m in mail] == [["amina@clinic.example"], ["musa@clinic.example"]]
    assert mail[0]["body"].startswith("Dear Amina,") and mail[1]["body"].startswith("Dear Musa,")
    assert mail[0]["cc"] == ["boss@clinic.example"] and mail[0]["reply_to"] == ["pm@partner.example"]
    rows = list(SentMessage.objects.filter(communication_id=out["id"]).order_by("pk"))
    assert [r.to_email for r in rows] == ["amina@clinic.example", "musa@clinic.example"]
    assert rows[0].sent_by == world["writer"] and rows[0].subject == "Hello Partner Org"


def test_a_token_never_sends(world, mail):
    out = draft_for(world)
    with pytest.raises(PermissionDenied):
        service.send(browser_request(world["writer"], HTTP_AUTHORIZATION="Bearer labs_pat_x"), out["id"])
    assert mail == []


def test_a_message_goes_once(world, mail):
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    with pytest.raises(ValidationError):
        service.send(browser_request(world["writer"]), out["id"])
    assert len(mail) == 1


def test_a_draft_with_nobody_or_no_address_is_not_sent(world, mail):
    out = draft_for(world, recipients=[{"name": "No Address", "org": "partner-org"}])
    with pytest.raises(ValidationError) as e:
        service.send(browser_request(world["writer"]), out["id"])
    assert "No email address" in str(e.value)
    assert mail == []


def test_an_agent_can_change_a_draft_but_not_a_sent_one(world, mail):
    out = draft_for(world)
    update = get_tool("communications_draft_update").handler
    changed = update(user=world["writer"], id=out["id"], subject="Changed")
    assert changed["subject"] == "Changed"
    service.send(browser_request(world["writer"]), out["id"])
    with pytest.raises(MCPToolError):
        update(user=world["writer"], id=out["id"], subject="Again")


def test_the_page_sends_and_shows_what_went(client, world, mail):
    out = draft_for(world)
    client.force_login(world["writer"])
    page = client.get(reverse("communications:detail", args=[out["id"]])).content.decode()
    assert 'data-testid="send"' in page and "Dear Amina," in page
    recipient = Communication.objects.get(pk=out["id"]).recipients.get()
    response = client.post(
        reverse("communications:detail", args=[out["id"]]),
        {
            "action": "send",
            "subject": "Hello {org_name}",
            "body": "Hi {first_name}",
            "reply_to": "pm@partner.example",
            "include": [recipient.pk],
            f"email_{recipient.pk}": "amina@clinic.example",
        },
    )
    assert response.status_code == 302
    assert mail[0]["body"] == "Hi Amina"
    page = client.get(reverse("communications:detail", args=[out["id"]])).content.decode()
    assert 'data-testid="sent-messages"' in page and "amina@clinic.example" in page
    outbox = client.get(reverse("communications:outbox")).content.decode()
    sent_section = outbox.split('data-testid="sent"')[1]
    assert reverse("communications:detail", args=[out["id"]]) in sent_section


def test_an_outsider_cannot_open_a_draft(client, world):
    out = draft_for(world)
    client.force_login(world["outsider"])
    assert client.get(reverse("communications:detail", args=[out["id"]])).status_code == 404


def test_a_failed_follow_up_step_is_recorded_without_losing_the_send(world, mail, monkeypatch):
    out = draft_for(world)

    def boom(*args, **kwargs):
        raise RuntimeError("outreach row gone")

    monkeypatch.setattr(kinds.get("general"), "after_sent", boom)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    assert row.delivery == "delivered" and row.followup == "failed" and "outreach row gone" in row.followup_error


def test_placeholders_fill_in_and_unknown_braces_stay():
    assert kinds.render("Hi {first_name}, {other}", {"first_name": "Ada"}) == "Hi Ada, {other}"


def test_what_sending_changes_waits_for_the_mail_service(world, settings, monkeypatch):
    """Queued is not sent: the kind's follow-up runs only when delivery succeeds, never on failure."""
    settings.LABS_EMAIL_ENABLED = True
    handed = []
    monkeypatch.setattr(
        "connect_labs.utils.email.send_labs_email",
        lambda *a, sent_message_id=None, **k: handed.append(sent_message_id),
    )
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    calls = []
    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: calls.append(1) or {"done": True})
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    assert row.delivery == "queued" and calls == [] and handed == [row.pk]
    service.not_delivered(row.pk, failed=True)
    row.refresh_from_db()
    assert row.delivery == "failed" and calls == []


def test_what_sending_changes_runs_once_the_mail_service_takes_it(world, settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", lambda *a, **k: object())
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: {"done": True})
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    service.delivered(row.pk)
    row.refresh_from_db()
    assert row.delivery == "delivered" and row.effects["done"]


def test_another_organisations_message_about_a_record_is_not_shown(world, mail):
    other = make_org("other-org", name="Other Org")
    other_writer = make_user("other-writer")
    grant(other_writer, other)
    tool = get_tool("communications_draft_create").handler
    theirs = tool(
        user=other_writer,
        kind="general",
        params={"owner_org": "other-org"},
        about_ref=f"labs.labsorg:{world['org'].pk}",
        subject="Private to Other Org",
        body="x",
        recipients=[{"email": "someone@other.example", "name": "Someone"}],
    )
    service.send(browser_request(other_writer), theirs["id"])
    ours = draft_for(world, about_ref=f"labs.labsorg:{world['org'].pk}")
    service.send(browser_request(world["writer"]), ours["id"])
    seen = {m.subject for m in service.sent_about(world["writer"], world["org"])}
    assert "Hello Partner Org" in seen and "Private to Other Org" not in seen


def test_a_delivery_report_applies_once_and_a_late_failure_never_undoes_it(world, settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", lambda *a, **k: object())
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    calls = []
    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: calls.append(1) or {})
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    service.delivered(row.pk)
    service.delivered(row.pk)  # the task ran twice
    service.not_delivered(row.pk, failed=True)
    row.refresh_from_db()
    assert calls == [1] and row.delivery == "delivered"


def test_references_are_whole_and_case_does_not_matter(world):
    with pytest.raises(MCPToolError):
        draft_for(world, about_ref="workflow.run:" + "9" * 80)
    out = draft_for(world, about_ref=f"LABS.LabsOrg:{world['org'].pk}")
    assert Communication.objects.get(pk=out["id"]).about == world["org"]


def test_an_automatic_notice_reaches_its_final_state(world, settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    monkeypatch.setattr(
        "connect_labs.utils.email.send_labs_email",
        lambda *a, sent_message_id=None, **k: service.delivered(sent_message_id),
    )
    assert service.send_automatic(
        "solicitations.notice", about=world["org"], to=["a@x.example", "b@x.example"], subject="s", body="b"
    )
    rows = SentMessage.objects.filter(kind="solicitations.notice")
    assert sorted(r.to_email for r in rows) == ["a@x.example", "b@x.example"]
    assert {r.delivery for r in rows} == {"delivered"}


def test_a_success_after_a_failure_wins_and_still_follows_up_once(world, settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", lambda *a, **k: object())
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    calls = []
    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: calls.append(1) or {})
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    service.not_delivered(row.pk, failed=True)
    service.delivered(row.pk)
    service.delivered(row.pk)
    row.refresh_from_db()
    assert row.delivery == "delivered" and calls == [1]


def test_no_messages_for_a_zero_limit(world):
    assert service.sent_about(world["writer"], world["org"], limit=0) == []


# --- settling what the mail path left open ------------------------------------------


@pytest.fixture
def queued_only(settings, monkeypatch):
    """Mail on, handed to the queue, and no report back (yet)."""
    settings.LABS_EMAIL_ENABLED = True
    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", lambda *a, **k: object())
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())


def test_check_delivery_marks_a_message_with_no_word_back_and_a_person_settles_it(
    client, world, queued_only, monkeypatch
):
    from datetime import timedelta

    from django.utils import timezone

    calls = []
    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: calls.append(1) or {})
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    assert service.reconcile([row])["unknown"] == 0  # too soon to say
    SentMessage.objects.filter(pk=row.pk).update(sent_at=timezone.now() - timedelta(hours=3))

    client.force_login(world["writer"])
    detail = reverse("communications:detail", args=[out["id"]])
    assert 'data-testid="check-delivery"' in client.get(detail).content.decode()
    client.post(detail, {"action": "check_delivery"})
    row.refresh_from_db()
    assert row.delivery == "unknown" and calls == []
    page = client.get(detail).content.decode()
    assert "Outcome unknown" in page and 'data-testid="confirm-delivered"' in page
    client.post(detail, {"action": "confirm_delivered", "message": row.pk})
    row.refresh_from_db()
    assert row.delivery == "delivered" and row.followup == "done" and calls == [1]


def test_check_delivery_retries_a_failed_follow_up_now_and_never_resends(client, world, queued_only, monkeypatch):
    sends = []
    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", lambda *a, **k: sends.append(1) or object())

    def down(*a, **k):
        raise RuntimeError("supply is down")

    monkeypatch.setattr(kinds.get("general"), "after_sent", down)
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    service.delivered(row.pk)
    row.refresh_from_db()
    assert row.followup == "failed" and "supply is down" in row.followup_error

    monkeypatch.setattr(kinds.get("general"), "after_sent", lambda *a, **k: {"marked": True})
    client.force_login(world["writer"])
    client.post(reverse("communications:detail", args=[out["id"]]), {"action": "check_delivery"})
    row.refresh_from_db()
    assert row.followup == "done" and row.effects["marked"] and row.followup_attempts == 2
    assert sends == [1]  # the email went once


def test_check_delivery_on_a_records_page_covers_what_was_sent_about_it(client, world, queued_only):
    from datetime import timedelta

    from django.utils import timezone

    from connect_labs.solicitations.local_models import SOURCE_DIRECTORY
    from connect_labs.solicitations.tests.factories import make_call
    from connect_labs.supply_chain.runner import dimagi_org

    reviewer = make_user("reviewer", email="reviewer@dimagi.com")
    round_ = make_call(dimagi_org(), source=SOURCE_DIRECTORY, slug="eoi", evaluation_criteria=[])
    notice = service.record_automatic(
        "solicitations.notice", about=round_, to=["a@x.example"], subject="s", body="b", queued=True
    )[0]
    SentMessage.objects.filter(pk=notice.pk).update(sent_at=timezone.now() - timedelta(hours=3))
    url = f"{reverse('communications:outbox')}?kind=solicitations.round_applicants&about={round_.pk}"
    client.force_login(reviewer)
    assert 'data-testid="check-delivery"' in client.get(url).content.decode()
    client.post(url, {"action": "check_delivery"})
    notice.refresh_from_db()
    assert notice.delivery == "unknown"
    client.force_login(world["outsider"])
    assert client.post(url, {"action": "check_delivery"}).status_code == 404


def test_nothing_reconciles_on_a_timer():
    from django_celery_beat.models import PeriodicTask

    from connect_labs.communications import tasks

    assert not hasattr(tasks, "reconcile_communications")
    assert not PeriodicTask.objects.filter(name="communications_reconcile").exists()


def test_only_someone_who_may_send_settles_a_message(client, world, queued_only):
    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    SentMessage.objects.filter(pk=row.pk).update(delivery="unknown")
    client.force_login(world["outsider"])
    response = client.post(
        reverse("communications:detail", args=[out["id"]]), {"action": "confirm_delivered", "message": row.pk}
    )
    assert response.status_code == 404
    row.refresh_from_db()
    assert row.delivery == "unknown"


def test_the_mail_task_reports_through_its_own_retried_task(settings, monkeypatch):
    from unittest.mock import patch

    from connect_labs.utils import tasks as mail_tasks

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    with patch("connect_labs.communications.tasks.record_delivery.delay") as report:
        mail_tasks.send_mail_async(subject="s", message="b", recipient_list=["a@x.example"], sent_message_id=42)
    report.assert_called_once_with(42, True, False)


def test_a_worker_without_mail_holds_a_recorded_message_for_a_later_one(settings):
    from unittest.mock import patch

    from celery.exceptions import Retry

    from connect_labs.utils import tasks as mail_tasks

    settings.EMAIL_BACKEND = "connect_labs.utils.email.NotConfiguredEmailBackend"
    with patch("connect_labs.communications.tasks.record_delivery.delay") as report, pytest.raises(Retry):
        mail_tasks.send_mail_async(subject="s", message="b", recipient_list=["a@x.example"], sent_message_id=42)
    report.assert_not_called()
    # Not a recorded message: as before, nothing to hold, nothing retried.
    assert mail_tasks.send_mail_async(subject="s", message="b", recipient_list=["a@x.example"]) == 0


def test_delivered_and_its_pending_follow_up_are_recorded_together(world, queued_only, monkeypatch):
    def stop(*a, **k):
        raise SystemExit  # the worker stops right after the delivery claim

    out = draft_for(world)
    service.send(browser_request(world["writer"]), out["id"])
    row = SentMessage.objects.get(communication_id=out["id"])
    monkeypatch.setattr(service, "run_followup", stop)
    with pytest.raises(SystemExit):
        service.delivered(row.pk)
    row.refresh_from_db()
    assert row.delivery == "delivered" and row.followup == "pending"  # reconcile will pick it up
