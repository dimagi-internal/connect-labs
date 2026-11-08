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
        subject, message, recipient_list, html_message=None, from_email=None, *, reply_to=None, cc=None, countdown=None
    ):
        queued.append({"subject": subject, "body": message, "to": recipient_list, "reply_to": reply_to, "cc": cc})
        return object()

    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", fake_send)
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


def test_a_general_message_may_be_about_any_record(world):
    out = draft_for(world, about_ref=f"labs.labsorg:{world['org'].pk}")
    communication = Communication.objects.get(pk=out["id"])
    assert communication.about == world["org"]
    with pytest.raises(MCPToolError):
        draft_for(world, about_ref="nothing.here:1")


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
    assert row.delivery == "queued" and "outreach row gone" in row.effects["after_sent_error"]


def test_placeholders_fill_in_and_unknown_braces_stay():
    assert kinds.render("Hi {first_name}, {other}", {"first_name": "Ada"}) == "Hi Ada, {other}"
