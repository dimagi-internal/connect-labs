"""A tender's emails, drafted by supply and sent from labs, mark themselves sent.

Every supplier, person and address here is invented.
"""

from datetime import date, timedelta

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from connect_labs.communications import service
from connect_labs.communications.models import SentMessage
from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.solicitations.tests.factories import make_user
from connect_labs.supply_chain import communication_kinds
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commitment, Outreach
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10734


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture(autouse=True)
def as_system(monkeypatch, da):
    """The kind reaches supply as the person; here the programme's scope stands in for them."""
    monkeypatch.setattr(communication_kinds, "supply_access", lambda caller, program_id: da)
    monkeypatch.setattr(communication_kinds, "_is_programme", lambda caller, program_id: program_id == PROGRAM)


@pytest.fixture
def mail(settings, monkeypatch):
    settings.LABS_EMAIL_ENABLED = True
    queued = []

    def fake_send(
        subject, message, recipient_list, html_message=None, from_email=None, *, reply_to=None, cc=None, countdown=None
    ):
        queued.append({"subject": subject, "body": message, "to": recipient_list, "reply_to": reply_to})
        return object()

    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", fake_send)
    return queued


@pytest.fixture
def tender(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Kano round",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_open", tender_id=tender["id"])
    unsent = op(
        da,
        "supplier_create",
        data={"name": "Savanna Mills", "contacts": [{"name": "Bola", "email": "bola@savanna.example"}]},
    )
    silent = op(
        da,
        "supplier_create",
        data={"name": "Harmattan Foods", "contacts": [{"name": "Ada Bello", "email": "ada@harmattan.example"}]},
    )
    asked = (date.today() - timedelta(days=19)).isoformat()
    rows = {
        "unsent": op(da, "outreach_log", data={"tender_id": tender["id"], "supplier_id": unsent["id"]}),
        "silent": op(
            da, "outreach_log", data={"tender_id": tender["id"], "supplier_id": silent["id"], "sent_on": asked}
        ),
    }
    question = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "text": "Is the waiver confirmed?",
            "supplier_id": silent["id"],
            "tender_id": tender["id"],
            "raised_on": asked,
            "source": "supplier_reported",
        },
    )
    return {"tender": tender, "rows": rows, "question": question}


def browser_request(user):
    request = RequestFactory().post("/communications/1/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = user
    return request


def test_the_draft_holds_each_email_the_tender_owes(tender):
    sophie = make_user("sophie")
    draft = service.create_draft(sophie, "supply_chain.tender_emails", about_id=tender["tender"]["id"])
    by_label = {r.label: r for r in draft.recipients.all()}
    assert {"Quote request: Savanna Mills", "Reminder: Harmattan Foods", "Reply: Harmattan Foods"} <= set(by_label)
    reminder = by_label["Reminder: Harmattan Foods"]
    assert reminder.email == "ada@harmattan.example" and reminder.body and reminder.subject
    assert reminder.ref["outreach_id"] == tender["rows"]["silent"]["id"]


def test_sending_marks_each_one_sent_on_the_tender(tender, mail):
    sophie = make_user("sophie")
    draft = service.create_draft(sophie, "supply_chain.tender_emails", about_id=tender["tender"]["id"])
    draft.reply_to = "sophie@program.example"
    draft.save()
    service.send(browser_request(sophie), draft.pk)

    assert {m["to"][0] for m in mail} >= {"bola@savanna.example", "ada@harmattan.example"}
    today = date.today()
    assert Outreach.objects.get(pk=tender["rows"]["unsent"]["id"]).sent_on == today
    assert Outreach.objects.get(pk=tender["rows"]["silent"]["id"]).last_reminder_on == today
    question = Commitment.objects.get(pk=tender["question"]["id"])
    assert question.reply_sent_on == today and question.resolved_on == today
    effects = {m.recipient.label: m.effects for m in SentMessage.objects.filter(communication=draft)}
    assert effects["Quote request: Savanna Mills"]["outreach_sent_on"] == today.isoformat()


def test_only_the_programme_prepares_them(tender, monkeypatch):
    monkeypatch.setattr(communication_kinds, "_is_programme", lambda caller, program_id: False)
    with pytest.raises(PermissionDenied):
        service.create_draft(make_user("stranger"), "supply_chain.tender_emails", about_id=tender["tender"]["id"])
