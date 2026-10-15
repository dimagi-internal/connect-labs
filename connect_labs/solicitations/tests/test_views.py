"""The solicitations pages, against the labs database.

Every page asks `SolicitationsDataAccess` who may do what; these tests pin what
each kind of viewer gets: anonymous, an outsider, a respondent, an owner (a
member of the owning organisation) and staff.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.solicitations.local_models import SolicitationResponse
from connect_labs.solicitations.tests.factories import grant, make_call, make_org, make_response, make_user

pytestmark = pytest.mark.django_db


def login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token",
        "expires_at": timezone.now().timestamp() + 3600,
        "organization_data": {},
    }
    session.save()
    return client


@pytest.fixture
def world():
    owner_org = make_org("owner-org", name="Owner Org", aliases=["owner-old"])
    owner = make_user("owner")
    grant(owner, owner_org)
    firm_org = make_org("firm-org", name="Firm Org")
    applicant = make_user("applicant")
    grant(applicant, firm_org)
    call = make_call(owner_org, title="Household survey in two districts", currency="NGN")
    return {
        "owner_org": owner_org,
        "owner": owner,
        "firm_org": firm_org,
        "applicant": applicant,
        "outsider": make_user("outsider"),
        "staff": make_user("support", staff=True),
        "call": call,
    }


def url(name, **kw):
    return reverse(f"solicitations:{name}", kwargs=kw)


# --- directory -----------------------------------------------------------------


def test_directory_lists_open_calls_with_owner_and_recently_closed(client, world):
    closed = make_call(
        world["owner_org"], title="Old call", application_deadline=timezone.localdate() - timedelta(days=3)
    )
    make_call(world["owner_org"], title="Secret draft", status="draft")
    make_call(world["owner_org"], title="Unlisted call", listed=False)
    body = client.get(url("public_list")).content.decode()
    assert world["call"].title in body
    assert "Owner Org" in body
    assert url("org_page", org_slug="owner-org") in body
    assert "Recently closed" in body and closed.title in body
    assert "Closed — deadline passed" in body
    assert "Secret draft" not in body
    assert "Unlisted call" not in body
    assert "<title>Open calls for partners · Solicitations</title>" in body


def test_directory_search_and_filters(client, world):
    other = make_org("other-org", name="Other Org")
    make_call(other, title="Malaria bednets", solicitation_type="eoi")
    body = client.get(url("public_list"), {"q": "bednets"}).content.decode()
    assert "Malaria bednets" in body and world["call"].title not in body
    body = client.get(url("public_list"), {"owner": "owner-org"}).content.decode()
    assert "Malaria bednets" not in body and world["call"].title in body
    body = client.get(url("public_list"), {"type": "eoi"}).content.decode()
    assert "Malaria bednets" in body and world["call"].title not in body


def test_card_preview_strips_markdown_and_clips_scale(client, world):
    make_call(
        world["owner_org"],
        title="Markdown call",
        description="## Heading\n\n**Bold** words here",
        estimated_scale="x" * 180,
    )
    body = client.get(url("public_list")).content.decode()
    assert "**Bold**" not in body and "## Heading" not in body
    assert "Heading Bold words here" in body
    assert 'class="truncate" title="' + "x" * 180 in body


# --- a call's page -----------------------------------------------------------------


def test_detail_names_owner_and_has_title(client, world):
    body = client.get(url("public_detail", pk=world["call"].public_id)).content.decode()
    assert "Owner Org" in body
    assert f"<title>{world['call'].title} · Solicitations</title>" in body
    assert "Open" in body


def test_legacy_id_resolves(client, world):
    world["call"].legacy_record_id = 4242
    world["call"].save()
    resp = client.get(url("public_detail", pk=4242))
    assert resp.status_code == 200
    assert world["call"].title in resp.content.decode()


def test_draft_call_is_404_to_the_public(client, world):
    draft = make_call(world["owner_org"], title="Draft", status="draft")
    assert client.get(url("public_detail", pk=draft.public_id)).status_code == 404


def test_owner_sees_manage_links_on_detail(client, world):
    login(client, world["owner"])
    body = client.get(url("public_detail", pk=world["call"].public_id)).content.decode()
    assert url("responses_list", pk=world["call"].public_id) in body


def test_long_call_gets_section_nav(client, world):
    world["call"].description = "word " * 800
    world["call"].save()
    body = client.get(url("public_detail", pk=world["call"].public_id)).content.decode()
    assert "On this page" in body and 'href="#criteria"' in body


# --- an organisation's pages ---------------------------------------------------------


def test_org_alias_redirects_to_slug(client, world):
    resp = client.get(url("org_page", org_slug="owner-old"))
    assert resp.status_code == 302
    assert resp["Location"] == url("org_page", org_slug="owner-org")


def test_org_page_404_when_nothing_listed_and_viewer_cannot_manage(client, world):
    make_org("empty-org")
    assert client.get(url("org_page", org_slug="empty-org")).status_code == 404
    assert client.get(url("org_page", org_slug="nobody")).status_code == 404


def test_org_page_shows_open_and_past(client, world):
    make_call(world["owner_org"], title="Past call", status="closed")
    body = client.get(url("org_page", org_slug="owner-org")).content.decode()
    assert "Open calls" in body and "Past calls" in body and "Past call" in body


def test_org_manage_for_owner_includes_drafts_and_counts(client, world):
    make_call(world["owner_org"], title="Secret draft", status="draft", listed=False)
    make_response(world["call"], world["applicant"])
    login(client, world["owner"])
    resp = client.get(url("org_manage", org_slug="owner-org"))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Secret draft" in body
    assert "1 response" in body
    assert f"{url('create')}?owner=owner-org" in body


def test_org_manage_refused_for_non_member(client, world):
    login(client, world["outsider"])
    resp = client.get(url("org_manage", org_slug="owner-org"))
    assert resp.status_code == 403
    assert "Only members of Owner Org" in resp.content.decode()


def test_staff_may_open_any_org_manage(client, world):
    login(client, world["staff"])
    assert client.get(url("org_manage", org_slug="owner-org")).status_code == 200


def test_manage_with_one_org_redirects(client, world):
    login(client, world["owner"])
    resp = client.get(url("manage_list"))
    assert resp.status_code == 302 and resp["Location"] == url("org_manage", org_slug="owner-org")


def test_manage_with_no_org_explains_how_to_get_access(client, world):
    login(client, world["outsider"])
    body = client.get(url("manage_list")).content.decode()
    assert "not a member of an organisation" in body
    assert "context selector" not in body


# --- create and edit -------------------------------------------------------------------


def _form(**kw):
    data = {
        "title": "New call",
        "description": "Desc",
        "solicitation_type": "rfp",
        "status": "active",
        "is_public": "on",
        "questions_json": "[]",
        "evaluation_criteria_json": "[]",
    }
    data.update(kw)
    return data


def test_create_picks_owner_from_manageable_orgs(client, world):
    login(client, world["owner"])
    page = client.get(url("create"), {"owner": "owner-org"}).content.decode()
    assert 'value="owner-org" selected' in page
    resp = client.post(url("create"), _form(owner_org="owner-org"))
    assert resp.status_code == 302
    from connect_labs.solicitations.local_models import Solicitation

    created = Solicitation.objects.get(title="New call")
    assert created.owner_org == world["owner_org"] and created.created_by == world["owner"]
    assert resp["Location"] == url("responses_list", pk=created.public_id) + "?created=1"


def test_create_refuses_an_org_you_do_not_manage(client, world):
    login(client, world["owner"])
    resp = client.post(url("create"), _form(owner_org="firm-org"))
    assert resp.status_code == 200
    assert "Choose the organisation" in resp.content.decode()


def test_create_without_any_org_explains(client, world):
    login(client, world["outsider"])
    body = client.get(url("create")).content.decode()
    assert "belong to an organisation to post a call" in body


def test_edit_refused_for_non_owner(client, world):
    login(client, world["outsider"])
    assert client.get(url("edit", pk=world["call"].public_id)).status_code == 403


def test_edit_keeps_questions_when_hidden_field_empty(client, world):
    login(client, world["owner"])
    resp = client.post(
        url("edit", pk=world["call"].public_id),
        _form(title="Renamed", questions_json="", evaluation_criteria_json=""),
    )
    assert resp.status_code == 302
    world["call"].refresh_from_db()
    assert world["call"].title == "Renamed"
    assert len(world["call"].questions) == 2
    assert len(world["call"].evaluation_criteria) == 2


# --- responding --------------------------------------------------------------------------


def test_respond_offers_the_users_orgs(client, world):
    login(client, world["applicant"])
    body = client.get(url("respond", pk=world["call"].public_id)).content.decode()
    assert 'value="firm-org"' in body
    assert "What happens next" in body


def test_respond_submits_as_member_org(client, world):
    login(client, world["applicant"])
    resp = client.post(
        url("respond", pk=world["call"].public_id),
        {"org": "firm-org", "question_q1": "Ten years.", "question_q2": "12", "submit": "1"},
    )
    assert resp.status_code == 302 and resp["Location"].endswith("?submitted=1")
    response = SolicitationResponse.objects.get(submitted_by=world["applicant"])
    assert response.status == "submitted" and response.llo_entity == world["firm_org"]


def test_respond_as_a_new_org_by_name(client, world):
    login(client, world["outsider"])
    client.post(
        url("respond", pk=world["call"].public_id),
        {
            "org": "__new__",
            "new_org_name": "Brand New Ltd",
            "new_org_email": "hi@brandnew.example",
            "question_q1": "x",
            "submit": "1",
        },
    )
    response = SolicitationResponse.objects.get(submitted_by=world["outsider"])
    assert response.org_name == "Brand New Ltd" and response.submitted_by_email == "hi@brandnew.example"
    assert response.llo_entity is None


def test_submit_needs_an_organisation(client, world):
    login(client, world["outsider"])
    resp = client.post(
        url("respond", pk=world["call"].public_id), {"org": "__new__", "question_q1": "x", "submit": "1"}
    )
    assert resp.status_code == 200
    assert "Name the organisation" in resp.content.decode()
    assert not SolicitationResponse.objects.exists()


def test_one_draft_edited_in_place(client, world):
    login(client, world["applicant"])
    target = url("respond", pk=world["call"].public_id)
    client.post(target, {"org": "firm-org", "question_q1": "first", "save_draft": "1"})
    client.post(target, {"org": "firm-org", "question_q1": "second", "save_draft": "1"})
    drafts = SolicitationResponse.objects.filter(submitted_by=world["applicant"])
    assert drafts.count() == 1 and drafts.get().responses["q1"] == "second"
    page = client.get(target).content.decode()
    assert "second" in page  # the draft comes back pre-filled


def test_deadline_passed_respond_refused(client, world):
    world["call"].application_deadline = timezone.localdate() - timedelta(days=1)
    world["call"].save()
    login(client, world["applicant"])
    page = client.get(url("respond", pk=world["call"].public_id))
    body = page.content.decode()
    assert "This call closed on" in body and 'name="submit"' not in body
    resp = client.post(url("respond", pk=world["call"].public_id), {"org": "firm-org", "question_q1": "x"})
    assert resp.status_code == 403
    assert not SolicitationResponse.objects.exists()


def test_respondent_sees_own_response_only(client, world):
    mine = make_response(world["call"], world["applicant"], org_name="Firm Org")
    theirs = make_response(world["call"], world["outsider"], org_name="Someone Else")
    login(client, world["applicant"])
    resp = client.get(url("response_detail", pk=mine.pk))
    assert resp.status_code == 200
    assert "Firm Org" in resp.content.decode()
    assert client.get(url("response_detail", pk=theirs.pk)).status_code == 403
    assert client.get(url("responses_list", pk=world["call"].public_id)).status_code == 403


def test_response_detail_heading_uses_display_name(client, world):
    response = make_response(
        world["call"], world["applicant"], org_name="", llo_entity_name="", submitted_by_name="Ada"
    )
    login(client, world["owner"])
    body = client.get(url("response_detail", pk=response.pk)).content.decode()
    assert ">Ada</h1>" in body


# --- the owner's inbox, review and award ---------------------------------------------------------


def test_responses_list_is_blind_and_links_to_review(client, world):
    response = make_response(world["call"], world["applicant"], org_name="Firm Org")
    login(client, world["owner"])
    body = client.get(url("responses_list", pk=world["call"].public_id)).content.decode()
    assert f"Response #{response.pk}" in body
    assert "Firm Org" not in body
    assert url("review", pk=response.pk) in body
    assert f'href="{url("response_detail", pk=response.pk)}"' not in body
    assert "get_solicitation_data" not in body


@pytest.mark.parametrize("name", ["review", "award"])
def test_non_owner_refused_review_and_award(client, world, name):
    response = make_response(world["call"], world["applicant"])
    login(client, world["outsider"])
    assert client.get(url(name, pk=response.pk)).status_code == 403


def test_respondent_cannot_review_own_response_even_as_owner(client, world):
    grant(world["applicant"], world["owner_org"])
    response = make_response(world["call"], world["applicant"])
    login(client, world["applicant"])
    assert client.get(url("review", pk=response.pk)).status_code == 403


def test_review_is_blind(client, world):
    response = make_response(world["call"], world["applicant"], org_name="Firm Org")
    login(client, world["owner"])
    resp = client.get(url("review", pk=response.pk))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Firm Org" not in body and "applicant@firm.example" not in body
    resp = client.post(
        url("review", pk=response.pk),
        {"score": 70, "recommendation": "approved", "criteria_score_c1": 8, "criteria_score_c2": 6},
    )
    assert resp.status_code == 302
    review = response.reviews.get()
    assert review.reviewer == world["owner"] and review.score == 72
    # A second review by the same person edits the first.
    client.post(url("review", pk=response.pk), {"score": 50, "recommendation": "rejected"})
    assert response.reviews.count() == 1


def test_award_uses_call_currency_and_is_honest_about_email(client, world, settings):
    response = make_response(world["call"], world["applicant"], org_name="Firm Org")
    login(client, world["owner"])
    page = client.get(url("award", pk=response.pk)).content.decode()
    assert "₦" not in page
    assert "Award budget (NGN)" in page
    assert 'name="org_id"' not in page  # Connect org id is for platform support only
    resp = client.post(url("award", pk=response.pk), {"reward_budget": "500000", "award_stage": "verification"})
    assert resp.status_code == 302
    body = client.get(url("award", pk=response.pk)).content.decode()
    assert "500,000 NGN" in body
    assert "No email was sent" in body


def test_staff_sees_connect_org_id_on_award(client, world):
    response = make_response(world["call"], world["applicant"])
    login(client, world["staff"])
    assert 'name="org_id"' in client.get(url("award", pk=response.pk)).content.decode()


def test_forbidden_page_is_plain(client, world):
    login(client, world["outsider"])
    resp = client.get(url("responses_list", pk=world["call"].public_id))
    assert resp.status_code == 403
    body = resp.content.decode()
    assert "You don't have access to this" in body
    assert "Only the organisation that owns this call" in body


# --- navigation ------------------------------------------------------------------------------


def test_sidenav_offers_solicitations_to_everyone(client, world):
    body = client.get(url("public_list")).content.decode()
    assert 'href="/solicitations/"' in body and ">Solicitations</span>" in body


def test_marketplace_org_page_links_its_calls(client, world):
    login(client, world["owner"])
    body = client.get(reverse("marketplace:organisation", kwargs={"slug": "owner-org"})).content.decode()
    assert url("org_page", org_slug="owner-org") in body
    body = client.get(reverse("marketplace:organisation", kwargs={"slug": "firm-org"})).content.decode()
    assert url("org_page", org_slug="firm-org") not in body
