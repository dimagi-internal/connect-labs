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

from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import marketplace as tools
from connect_labs.solicitations import access, service_areas
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


# --- the invitation -------------------------------------------------------------


def test_the_owner_makes_one_link_per_applicant_organisation(world):
    make_response(world["round"], None, org_name="Nobody We Know")
    out = tools.marketplace_round_invites_create(world["reviewer"], "chp-eoi")
    assert [i["org_slug"] for i in out["invites"]] == ["firm-org"]
    invite = out["invites"][0]
    assert invite["contact"]["emails"] == ["lead@firm.example"]
    assert invite["response_ids"] == [world["imported"].pk]
    assert invite["connect_linked"] is False
    assert "/solicitations/join/" in invite["invite_url"]
    assert [u["org_name"] for u in out["unmatched"]] == ["Nobody We Know"]


def test_nobody_else_makes_links_and_never_for_a_closed_round(world):
    with pytest.raises(MCPToolError):
        tools.marketplace_round_invites_create(world["colleague"], "chp-eoi")
    world["round"].application_deadline = timezone.localdate() - timedelta(days=1)
    world["round"].save()
    with pytest.raises(MCPToolError):
        tools.marketplace_round_invites_create(world["reviewer"], "chp-eoi")


def test_accepting_a_link_joins_the_organisation_and_lands_on_its_applications(client, world):
    newcomer = make_user("newcomer")
    link = tools.marketplace_round_invites_create(world["reviewer"], "chp-eoi")["invites"][0]["invite_url"]
    path = link[link.index("/solicitations/") :]
    login(client, newcomer)
    opened = client.get(path)
    assert opened.status_code == 302 and opened.url == reverse("solicitations:join")
    page = client.get(opened.url).content.decode()
    assert "Act for Firm Org" in page
    accepted = client.post(reverse("solicitations:join"))
    assert accepted.status_code == 302 and accepted.url == reverse("solicitations:applications")
    assert access.can_amend_response(newcomer, world["imported"])
    # The link works once.
    assert client.get(path).status_code == 404


def test_a_made_up_link_is_refused(client, world):
    assert client.get(reverse("solicitations:join_link", args=["not-a-real-token"])).status_code == 404
