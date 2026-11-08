"""An organisation's applications: listing them, and updating one while its call is open.

An imported EOI round's submission has no labs respondent -- it came from a
Google Form -- so the organisation it was matched to is who may come back to it:
any member reads it, and while the round is open picks where it would work.
Its answers stay the form's. A call made in labs is amended whole.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from connect_labs.communications.models import Communication, SentMessage
from connect_labs.marketplace.models import OrgInvite
from connect_labs.solicitations import access, communication_kinds, service_areas
from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import SOURCE_DIRECTORY, SolicitationResponse
from connect_labs.solicitations.tests.factories import (
    grant,
    make_call,
    make_org,
    make_response,
    make_review,
    make_user,
)
from connect_labs.supply_chain.runner import dimagi_org

pytestmark = pytest.mark.django_db


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


SHAPES = {"Kano": _square(8, 11, 9, 12), "Lagos": _square(3, 6, 4, 7)}


def area(name, level=1):
    return {"name": name, "level": level, "source": "labs", "country": "NGA", "ref": {"boundary_id": name}}


@pytest.fixture(autouse=True)
def boundaries(monkeypatch):
    monkeypatch.setattr(service_areas, "_resolve_geometry", lambda desc: SHAPES.get(desc["name"]))


@pytest.fixture
def world():
    firm = make_org("firm-org", name="Firm Org")
    applicant = make_user("applicant")  # filled in the form; a member of the firm
    colleague = make_user("colleague")  # never touched the form; also a member
    grant(applicant, firm)
    grant(colleague, firm, role="member")
    reviewer = make_user("reviewer", email="reviewer@dimagi.com")
    round_ = make_call(
        dimagi_org(),
        title="Community health EOI",
        source=SOURCE_DIRECTORY,
        slug="chp-eoi",
        questions=[{"id": "q1", "text": "Where do you work today?", "type": "textarea"}],
        evaluation_criteria=[],
        application_deadline=timezone.localdate() + timedelta(days=30),
    )
    imported = make_response(
        round_,
        None,
        llo_entity=firm,
        responses={"q1": "Kano and Jigawa."},
        submitted_by_email="lead@firm.example",
    )
    owner_org = make_org("owner-org")
    call = make_call(owner_org, application_deadline=timezone.localdate() + timedelta(days=30))
    sent = make_response(call, applicant, llo_entity=firm)
    return {
        "firm": firm,
        "applicant": applicant,
        "colleague": colleague,
        "outsider": make_user("outsider"),
        "reviewer": reviewer,
        "round": round_,
        "imported": imported,
        "call": call,
        "sent": sent,
    }


def da(user):
    return SolicitationsDataAccess(user=user)


def login(client, user):
    client.force_login(user)
    return client


# --- who reads and amends -------------------------------------------------------


def test_a_member_reads_and_amends_what_their_organisation_sent(world):
    for response in (world["imported"], world["sent"]):
        assert access.can_read_response(world["colleague"], response)
        assert access.can_amend_response(world["colleague"], response)
        assert not access.can_read_response(world["outsider"], response)
        assert not access.can_amend_response(world["outsider"], response)


def test_a_colleagues_draft_stays_theirs(world):
    world["sent"].status = "draft"
    world["sent"].save()
    assert not access.can_read_response(world["colleague"], world["sent"])
    assert not access.can_amend_response(world["applicant"], world["sent"])  # finished on the respond page


def test_nothing_is_amended_once_the_call_closes_or_is_decided(world):
    world["round"].application_deadline = timezone.localdate() - timedelta(days=1)
    world["round"].save()
    world["imported"].refresh_from_db()
    assert access.can_read_response(world["colleague"], world["imported"])
    assert not access.can_amend_response(world["colleague"], world["imported"])
    world["sent"].status = "awarded"
    world["sent"].save()
    assert not access.can_amend_response(world["applicant"], world["sent"])


def test_an_applicant_never_reviews_their_own_organisations_application(world):
    assert not access.can_review(world["colleague"], world["imported"])


# --- the list -------------------------------------------------------------------


def test_the_list_is_everything_from_the_callers_side(world):
    other = make_org("other-org")
    stranger = make_user("stranger")
    grant(stranger, other)
    make_response(world["round"], None, llo_entity=other)
    make_response(world["call"], world["colleague"], status="draft", llo_entity=world["firm"])
    make_response(world["call"], stranger, status="draft", llo_entity=world["firm"], org_name="Firm")

    colleague = {r.pk for r in da(world["colleague"]).get_my_applications()}
    assert world["imported"].pk in colleague and world["sent"].pk in colleague
    assert len(colleague) == 3  # plus their own draft; never the stranger's, never the other org's
    assert da(world["outsider"]).get_my_applications() == []


def test_the_page_splits_open_from_closed_and_offers_the_update(client, world):
    closed = make_call(
        make_org("old-owner"), title="Old call", application_deadline=timezone.localdate() - timedelta(days=5)
    )
    make_response(closed, world["applicant"], llo_entity=world["firm"])
    body = login(client, world["colleague"]).get(reverse("solicitations:applications")).content.decode()
    open_part, closed_part = body.split('data-testid="closed-applications"')
    assert "Community health EOI" in open_part and world["call"].title in open_part
    assert reverse("solicitations:response_update", args=[world["imported"].pk]) in open_part
    assert "Update areas" in open_part
    assert "Old call" in closed_part and "Update" not in closed_part
    assert "No areas picked yet" in open_part


def test_the_page_needs_a_sign_in(client):
    assert client.get(reverse("solicitations:applications")).status_code == 302


# --- amending -------------------------------------------------------------------


def test_a_round_takes_areas_and_nothing_else(world):
    response = da(world["colleague"]).amend_response(world["imported"].pk, {"service_areas": [area("Kano")]})
    assert [a["name"] for a in response.service_areas] == ["Kano"]
    response.refresh_from_db()
    assert response.responses == {"q1": "Kano and Jigawa."} and response.status == "submitted"
    with pytest.raises(ValidationError):
        da(world["colleague"]).amend_response(world["imported"].pk, {"responses": {"q1": "Lagos."}})


def test_a_call_takes_answers_and_areas(world):
    da(world["colleague"]).amend_response(
        world["sent"].pk, {"responses": {"q1": "Twelve years.", "q2": "20"}, "service_areas": [area("Lagos")]}
    )
    world["sent"].refresh_from_db()
    assert world["sent"].responses["q1"] == "Twelve years."
    assert world["sent"].service_areas[0]["name"] == "Lagos"


def test_an_outsider_cannot_amend(world):
    with pytest.raises(SolicitationResponse.DoesNotExist):
        da(world["outsider"]).amend_response(world["imported"].pk, {"service_areas": [area("Kano")]})


def test_the_owner_cannot_amend_an_application(world):
    with pytest.raises(PermissionDenied):
        da(world["reviewer"]).amend_response(world["imported"].pk, {"service_areas": [area("Kano")]})


def test_updating_a_rounds_areas_on_the_page(client, world):
    login(client, world["colleague"])
    page = reverse("solicitations:response_update", args=[world["imported"].pk])
    body = client.get(page).content.decode()
    assert 'data-testid="update-areas"' in body and "Kano and Jigawa." in body
    response = client.post(page, {"service_areas": json.dumps([area("Kano")])})
    assert response.status_code == 302 and response.url.startswith(reverse("solicitations:applications"))
    world["imported"].refresh_from_db()
    assert world["imported"].service_areas[0]["name"] == "Kano"
    listing = client.get(response.url).content.decode()
    assert (
        "No areas picked yet"
        not in listing.split('data-testid="closed-applications"')[0]
        .split("Community health EOI")[1]
        .split('data-testid="application"')[0]
    )


def test_a_closed_round_says_so(client, world):
    world["round"].application_deadline = timezone.localdate() - timedelta(days=1)
    world["round"].save()
    response = login(client, world["colleague"]).get(
        reverse("solicitations:response_update", args=[world["imported"].pk])
    )
    assert response.status_code == 403 and "has closed" in response.content.decode()


def test_an_outsider_gets_a_404(client, world):
    response = login(client, world["outsider"]).get(
        reverse("solicitations:response_update", args=[world["imported"].pk])
    )
    assert response.status_code == 404


def test_an_applicant_reads_their_round_response_without_its_reviews(client, world):
    make_review(world["imported"], world["reviewer"], notes="Private reviewer note.")
    body = (
        login(client, world["colleague"])
        .get(reverse("solicitations:response_detail", args=[world["imported"].pk]))
        .content.decode()
    )
    assert "Kano and Jigawa." in body
    assert "Private reviewer note." not in body
    assert reverse("solicitations:response_update", args=[world["imported"].pk]) in body


def test_the_reviewer_sees_the_picked_areas(client, world):
    da(world["colleague"]).amend_response(world["imported"].pk, {"service_areas": [area("Kano")]})
    body = (
        login(client, world["reviewer"])
        .get(reverse("solicitations:response_detail", args=[world["imported"].pk]))
        .content.decode()
    )
    assert 'data-testid="round-response-areas"' in body and "Kano" in body


# --- writing to applicants (communications, kind solicitations.round_applicants) ---


@pytest.fixture
def mail(settings, monkeypatch):
    """Outbound mail switched on, with every queued message captured instead of sent."""
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
        queued.append({"subject": subject, "body": message, "to": recipient_list, "reply_to": reply_to})
        if sent_message_id:  # what the mail task does once the mail service takes it
            from connect_labs.communications.service import delivered

            delivered(sent_message_id)
        return object()

    monkeypatch.setattr("connect_labs.utils.email.send_labs_email", fake_send)
    monkeypatch.setattr("django.db.transaction.on_commit", lambda fn, using=None, robust=False: fn())
    return queued


ROUND_KIND = "solicitations.round_applicants"
MESSAGE = "Dear {first_name},\n\nPlease pick your areas for {call_title}."


def outbox_url(world):
    return f"{reverse('communications:outbox')}?kind={ROUND_KIND}&about={world['round'].pk}"


def start_draft(client, world, user):
    response = login(client, user).post(outbox_url(world))
    assert response.status_code == 302, response
    return Communication.objects.get(pk=int(response.url.rstrip("/").split("/")[-1]))


def test_the_round_page_links_its_reviewers_to_its_messages(client, world):
    page = login(client, world["reviewer"]).get(reverse("marketplace:round", args=["chp-eoi"])).content.decode()
    contact = reverse("solicitations:contact_applicants", args=[world["round"].public_id])
    assert contact in page
    assert client.get(contact).url == outbox_url(world)
    assert login(client, world["colleague"]).get(outbox_url(world)).status_code == 404


def test_a_draft_addresses_each_person_at_each_applicant(client, world):
    make_response(world["round"], None, org_name="Nobody We Know")
    world["imported"].submitted_by_name = "Amina Bello, Musa Ali"
    world["imported"].submitted_by_email = "amina@firm.example, musa@firm.example"
    world["imported"].save()
    draft = start_draft(client, world, world["reviewer"])
    assert [(r.name, r.email, r.org) for r in draft.recipients.all()] == [
        ("Amina Bello", "amina@firm.example", world["firm"]),
        ("Musa Ali", "musa@firm.example", world["firm"]),
    ]
    assert draft.reply_to == "reviewer@dimagi.com" and "{link}" in draft.body
    assert draft.context["unmatched"] == ["Nobody We Know"]
    body = client.get(reverse("communications:detail", args=[draft.pk])).content.decode()
    assert "amina@firm.example" in body and 'data-testid="preview"' in body
    assert "Dear Amina," in body and "[invitation link]" in body


def test_sending_writes_each_person_their_own_link_with_the_reply_to(client, world, mail):
    world["imported"].submitted_by_name = "Amina Bello, Musa Ali"
    world["imported"].submitted_by_email = "amina@firm.example, musa@firm.example"
    world["imported"].save()
    draft = start_draft(client, world, world["reviewer"])
    ids = [str(r.pk) for r in draft.recipients.all()]
    response = client.post(
        reverse("communications:detail", args=[draft.pk]),
        {"action": "send", "subject": "Areas", "body": MESSAGE, "reply_to": "pm@partner.example", "include": ids},
    )
    assert response.status_code == 302
    assert [m["to"] for m in mail] == [["amina@firm.example"], ["musa@firm.example"]]
    assert all(m["reply_to"] == ["pm@partner.example"] for m in mail)
    assert mail[0]["body"].startswith("Dear Amina,") and mail[1]["body"].startswith("Dear Musa,")
    links = [m["body"].split("\n")[-1] for m in mail]  # {link} not placed: added at the end
    assert all("/solicitations/join/" in link for link in links) and links[0] != links[1]

    sent = list(SentMessage.objects.filter(communication=draft).order_by("pk"))
    assert [m.to_email for m in sent] == ["amina@firm.example", "musa@firm.example"]
    assert all("[invitation link]" in m.body and "/solicitations/join/" not in m.body for m in sent)
    assert sent[0].sent_by == world["reviewer"] and sent[0].effects["invite_id"]
    draft.refresh_from_db()
    assert draft.status == "sent"
    # The round's correspondence lists them.
    assert "musa@firm.example" in client.get(outbox_url(world)).content.decode()


def test_only_the_chosen_are_written_to(client, world, mail):
    other = make_org("other-org", name="Other Org")
    make_response(world["round"], None, llo_entity=other, submitted_by_email="x@other.example")
    draft = start_draft(client, world, world["reviewer"])
    firm = draft.recipients.get(org=world["firm"])
    client.post(
        reverse("communications:detail", args=[draft.pk]),
        {"action": "send", "subject": "s", "body": MESSAGE, "reply_to": "pm@partner.example", "include": [firm.pk]},
    )
    assert [m["to"] for m in mail] == [["lead@firm.example"]]


def test_nothing_is_sent_or_invited_when_mail_is_off(client, world, settings):
    settings.LABS_EMAIL_ENABLED = False
    draft = start_draft(client, world, world["reviewer"])
    response = client.post(
        reverse("communications:detail", args=[draft.pk]),
        {
            "action": "send",
            "subject": "s",
            "body": MESSAGE,
            "reply_to": "pm@partner.example",
            "include": [r.pk for r in draft.recipients.all()],
        },
    )
    assert response.status_code == 400 and "not set up" in response.content.decode()
    assert not SentMessage.objects.filter(communication=draft).exists()
    assert not OrgInvite.objects.exists()
    draft.refresh_from_db()
    assert draft.status == "draft"


def test_a_closed_round_cannot_be_written_to(client, world, mail):
    draft = start_draft(client, world, world["reviewer"])
    world["round"].application_deadline = timezone.localdate() - timedelta(days=1)
    world["round"].save()
    response = client.post(
        reverse("communications:detail", args=[draft.pk]),
        {"action": "send", "include": [r.pk for r in draft.recipients.all()]},
    )
    assert response.status_code == 400 and "closed" in response.content.decode()
    assert mail == []


def test_an_invitation_goes_only_to_an_address_an_applicant_gave(client, world, mail):
    from django.core.exceptions import ValidationError as DjangoValidationError

    from connect_labs.communications import service as comms

    draft = start_draft(client, world, world["reviewer"])
    recipient = draft.recipients.get()
    with pytest.raises(DjangoValidationError):
        comms.update_draft(
            world["reviewer"], draft.pk, {"recipients": [{"email": "me@elsewhere.example", "org": "firm-org"}]}
        )
    with pytest.raises(DjangoValidationError):
        comms.update_draft(world["reviewer"], draft.pk, {"emails": {recipient.pk: "me@elsewhere.example"}})
    # Even a recipient changed underneath (a stale or forged row) is checked again at send.
    recipient.email = "me@elsewhere.example"
    recipient.save()
    response = client.post(
        reverse("communications:detail", args=[draft.pk]), {"action": "send", "include": [recipient.pk]}
    )
    assert response.status_code == 400 and "be invited from here" in response.content.decode()
    assert mail == [] and not OrgInvite.objects.exists()


def test_a_call_made_in_labs_is_not_offered_it_since_it_is_scored_blind(world):
    owner = make_user("call-owner")
    grant(owner, world["call"].owner_org)
    kind = communication_kinds.RoundApplicantsKind()
    assert kind.resolve(world["call"].pk) is None
    assert not kind.can_send(access.caller_for(owner), world["call"])


# --- labs' own notices are recorded -----------------------------------------------


def test_a_submission_receipt_is_recorded_against_the_call(world, settings):
    settings.LABS_EMAIL_ENABLED = False
    da(world["colleague"]).create_response(
        world["call"].public_id, llo_entity_id="firm-org", data={"responses": {"q1": "x"}}
    )
    notices = SentMessage.objects.filter(kind="solicitations.notice", about_id=str(world["call"].pk))
    assert {m.to_email for m in notices} == {"colleague@example.org", "calls@owner.example"}
    assert all(m.delivery == "not_sent" and m.sent_by is None for m in notices)


# --- accepting the invitation ---------------------------------------------------


def test_accepting_a_link_joins_the_organisation_and_lands_on_its_applications(client, world, mail):
    draft = start_draft(client, world, world["reviewer"])
    client.post(
        reverse("communications:detail", args=[draft.pk]),
        {
            "action": "send",
            "subject": "s",
            "body": MESSAGE,
            "reply_to": "pm@partner.example",
            "include": [r.pk for r in draft.recipients.all()],
        },
    )
    link = mail[0]["body"].split("\n")[-1]
    path = link[link.index("/solicitations/") :]
    newcomer = make_user("newcomer")
    login(client, newcomer)
    opened = client.get(path)
    assert opened.status_code == 302 and opened.url == reverse("solicitations:join")
    assert "Act for Firm Org" in client.get(opened.url).content.decode()
    accepted = client.post(reverse("solicitations:join"))
    assert accepted.status_code == 302 and accepted.url == reverse("solicitations:applications")
    assert access.can_amend_response(newcomer, world["imported"])
    # The link works once.
    assert client.get(path).status_code == 404


def test_a_made_up_link_is_refused(client, world):
    assert client.get(reverse("solicitations:join_link", args=["not-a-real-token"])).status_code == 404
