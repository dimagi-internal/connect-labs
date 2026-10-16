"""Past calls: the marketplace's historical EOI rounds, read-only on Solicitations.

Directory rounds (``source="directory"``) were all run by Dimagi and every
announcement was public (product owner, 2026-10-15), so their *call* is shown to
anyone. What is never shown: the application form, the response sheet, the
column map, the import notes, and anything about who applied -- except a bare
count, and that only to someone signed in. Nothing on the solicitations side
may manage, edit, award or respond to one. Every name and address is invented.
"""

from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.solicitations import access
from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import SOURCE_DIRECTORY, Solicitation
from connect_labs.solicitations.tests.factories import grant, make_call, make_org, make_response, make_user

pytestmark = pytest.mark.django_db

FORM_URL = "https://forms.invalid/apply-here"
SHEET_ID = "1SheetIdThatMustNeverRender0123456789"
NOTE = "internal note: chased twice"


def make_round(slug="chc-2025", **kw):
    defaults = {
        "slug": slug,
        "source": SOURCE_DIRECTORY,
        "title": "Community health partners 2025",
        "solicitation_type": "eoi",
        "status": "closed",
        "delivery_type": "chc",
        "target_countries": "Nigeria, Kenya",
        "published_on": date(2025, 1, 8),
        "application_deadline": date(2025, 2, 1),
        "decision_on": date(2025, 3, 1),
        "announcement_url": "https://announce.invalid/chc-2025",
        "form_url": FORM_URL,
        "response_spreadsheet_id": SHEET_ID,
        "response_tab": "Form Responses 1",
        "column_map": {"Organisation name": "org_name"},
        "notes": NOTE,
        "listed": False,
        "questions": [],
        "evaluation_criteria": [],
    }
    defaults.update(kw)
    return Solicitation.objects.create(**defaults)


@pytest.fixture
def world():
    dimagi = make_org("dimagi", name="Dimagi")
    applicant_org = make_org("applicant-org", name="Applicant Org")
    round_ = make_round()
    first = make_response(
        round_,
        None,
        source_row=2,
        llo_entity=applicant_org,
        org_name="Applicant Org",
        submitted_by_name="Ada Applicant",
        submitted_by_email="ada@applicant.invalid",
    )
    make_response(round_, None, source_row=3, org_name="Unmatched Group", submitted_by_email="b@unmatched.invalid")
    return {
        "dimagi": dimagi,
        "round": round_,
        "response": first,
        "user": make_user("reader"),
        "staff": make_user("support", staff=True),
    }


def url(name, **kw):
    return reverse(f"solicitations:{name}", kwargs=kw)


def login(client, user):
    client.force_login(user)
    return client


def assert_no_private_fields(body):
    for secret in (FORM_URL, SHEET_ID, "Form Responses 1", "Organisation name", NOTE):
        assert secret not in body, secret
    for pii in ("Ada Applicant", "ada@applicant.invalid", "Applicant Org", "Unmatched Group", "b@unmatched.invalid"):
        assert pii not in body, pii


# --- the directory ---------------------------------------------------------------


def test_directory_lists_past_calls_by_year_with_program_and_owner(client, world):
    make_round("readers-2024", title="Readers partners 2024", delivery_type="readers", published_on=date(2024, 5, 2))
    body = client.get(url("public_list")).content.decode()
    assert "Past calls" in body
    assert "Community health partners 2025" in body and "Readers partners 2024" in body
    assert body.index(">2025<") < body.index(">2024<")
    assert "Published 8 Jan 2025" in body
    assert "Nigeria, Kenya" in body
    assert "images/programs/chc.jpg" in body
    assert "Dimagi" in body
    assert url("past_detail", slug="chc-2025") in body
    assert_no_private_fields(body)


def test_a_round_without_a_publication_date_is_dated_by_its_deadline(client, world):
    make_round("rfp-2023", title="Undated round", published_on=None, application_deadline=date(2023, 6, 30))
    body = client.get(url("public_list")).content.decode()
    assert ">2023<" in body and "Deadline 30 Jun 2023" in body


def test_a_round_still_taking_applications_is_not_a_past_call(client, world):
    make_round(
        "open-round",
        title="Still open round",
        status="active",
        decision_on=None,
        application_deadline=timezone.localdate() + timedelta(days=10),
    )
    body = client.get(url("public_list")).content.decode()
    assert "Still open round" not in body
    assert client.get(url("past_detail", slug="open-round")).status_code == 404


def test_past_calls_follow_the_type_program_owner_filters_and_search(client, world):
    make_round("rfp-malaria", title="Malaria survey RFP", solicitation_type="rfp", delivery_type="malaria")
    rfp = client.get(url("public_list"), {"type": "rfp"}).content.decode()
    assert "Malaria survey RFP" in rfp and "Community health partners 2025" not in rfp

    program = client.get(url("public_list"), {"program": "chc"}).content.decode()
    assert "Community health partners 2025" in program and "Malaria survey RFP" not in program

    search = client.get(url("public_list"), {"q": "malaria"}).content.decode()
    assert "Malaria survey RFP" in search and "Community health partners 2025" not in search
    by_country = client.get(url("public_list"), {"q": "kenya"}).content.decode()
    assert "Community health partners 2025" in by_country

    other = make_org("other-org", name="Other Org")
    make_call(other, title="Other org call")
    dimagi_only = client.get(url("public_list"), {"owner": "dimagi"}).content.decode()
    assert "Community health partners 2025" in dimagi_only and "Other org call" not in dimagi_only
    other_only = client.get(url("public_list"), {"owner": "other-org"}).content.decode()
    assert "Other org call" in other_only and "Community health partners 2025" not in other_only


def test_dimagi_is_display_only(client, world):
    client.get(url("public_list"))
    client.get(url("past_detail", slug="chc-2025"))
    world["round"].refresh_from_db()
    assert world["round"].owner_org_id is None


# --- the page for one past call -----------------------------------------------------


def test_past_call_page_for_anonymous_shows_the_call_and_nothing_private(client, world):
    response = client.get(url("past_detail", slug="chc-2025"))
    assert response.status_code == 200
    body = response.content.decode()
    assert "Community health partners 2025" in body
    assert "Expression of Interest" in body and "Closed" in body
    assert "Child Health Campaign" in body  # the program label
    assert "Nigeria, Kenya" in body
    assert "8 Jan 2025" in body and "1 Feb 2025" in body and "1 Mar 2025" in body
    assert "Read the announcement" in body and "https://announce.invalid/chc-2025" in body
    assert "organisations applied" not in body and "organisation applied" not in body  # no count for anonymous
    assert reverse("marketplace:round", args=["chc-2025"]) not in body
    assert url("respond", pk=world["round"].pk) not in body
    assert_no_private_fields(body)


def test_past_call_page_shows_the_count_and_marketplace_link_to_a_signed_in_user(client, world):
    body = login(client, world["user"]).get(url("past_detail", slug="chc-2025")).content.decode()
    assert "2 organisations applied" in body
    assert reverse("marketplace:round", args=["chc-2025"]) in body
    assert_no_private_fields(body)


def test_past_call_page_without_an_announcement_says_so(client, world):
    make_round("quiet-2024", title="Quiet round", announcement_url="")
    body = client.get(url("past_detail", slug="quiet-2024")).content.decode()
    assert "Read the announcement" not in body


def test_an_unsafe_announcement_link_is_not_rendered(client, world):
    make_round("odd-2024", title="Odd round", announcement_url="javascript:alert(1)")
    body = client.get(url("past_detail", slug="odd-2024")).content.decode()
    assert "javascript:alert" not in body


def test_past_call_shows_description_and_scope_when_present(client, world):
    make_round("described", title="Described round", description="What we looked for.", scope_of_work="Two states.")
    body = client.get(url("past_detail", slug="described")).content.decode()
    assert "What we looked for." in body and "Two states." in body


def test_past_call_is_reachable_by_id(client, world):
    round_ = world["round"]
    for path in (url("public_detail", pk=round_.pk), url("past_detail", slug=str(round_.pk))):
        response = client.get(path)
        assert response.status_code == 302, path
        assert response["Location"] == url("past_detail", slug="chc-2025")


def test_an_unknown_past_call_is_404(client, world):
    assert client.get(url("past_detail", slug="nope")).status_code == 404
    call = make_call(make_org("owner-org"), title="An owned call", slug="owned-call")
    # An owned call is not a past call, whatever its slug.
    assert client.get(url("past_detail", slug=call.slug)).status_code == 404


# --- nothing on the solicitations side manages one -------------------------------------


def test_access_never_lets_anyone_manage_a_directory_round(world):
    for who in (None, world["user"], world["staff"]):
        assert access.can_manage(who, world["round"]) is False
        assert access.can_review(who, world["response"]) is False
        assert access.can_award(who, world["response"]) is False
        assert access.can_read_response(who, world["response"]) is False
    assert access.standing(world["staff"], world["round"]) == access.PUBLIC


def test_respond_edit_responses_award_review_refused_on_a_directory_round(client, world):
    round_, response = world["round"], world["response"]
    login(client, world["staff"])
    for path in (
        url("respond", pk=round_.pk),
        url("edit", pk=round_.pk),
        url("responses_list", pk=round_.pk),
        url("response_detail", pk=response.pk),
        url("award", pk=response.pk),
        url("review", pk=response.pk),
    ):
        assert client.get(path).status_code == 404, path
    assert client.post(url("respond", pk=round_.pk), {"org_choice": "x"}).status_code == 404
    assert client.post(url("award", pk=response.pk), {"reward_budget": "5"}).status_code == 404
    response.refresh_from_db()
    assert response.status == "submitted" and response.reward_budget is None


def test_data_access_refuses_writes_on_a_directory_round(world):
    staff = SolicitationsDataAccess(user=world["staff"])
    with pytest.raises(Solicitation.DoesNotExist):
        staff.create_response(world["round"].pk, None, {"responses": {}})
    assert staff.get_responses_for_solicitation(world["round"].pk) == []
    assert world["round"].responses.count() == 2


def test_past_call_page_offers_no_respond_or_manage_controls_to_staff(client, world):
    body = login(client, world["staff"]).get(url("past_detail", slug="chc-2025")).content.decode()
    round_ = world["round"]
    for path in (url("respond", pk=round_.pk), url("edit", pk=round_.pk), url("responses_list", pk=round_.pk)):
        assert path not in body
    assert FORM_URL not in body and SHEET_ID not in body


# --- Dimagi's organisation page -----------------------------------------------------------


def test_dimagi_org_page_lists_the_past_calls(client, world):
    body = client.get(url("org_page", org_slug="dimagi")).content.decode()
    assert "Past calls" in body
    assert "Community health partners 2025" in body
    assert url("past_detail", slug="chc-2025") in body
    assert_no_private_fields(body)


def test_other_org_pages_do_not_list_the_past_calls(client, world):
    other = make_org("other-org", name="Other Org")
    make_call(other, title="Other org call")
    body = client.get(url("org_page", org_slug="other-org")).content.decode()
    assert "Community health partners 2025" not in body


def test_without_a_dimagi_org_row_the_owner_is_still_named(client, world):
    world["dimagi"].delete()
    body = client.get(url("past_detail", slug="chc-2025")).content.decode()
    assert "Dimagi" in body
    assert "dimagi" not in [o.slug for o in access.manageable_orgs(None)]


# --- the marketplace round links to the public page ---------------------------------------


def test_marketplace_round_links_to_the_public_call_page(client, world):
    login(client, world["user"])
    body = client.get(reverse("marketplace:round", args=["chc-2025"])).content.decode()
    assert "Public call page" in body
    assert url("past_detail", slug="chc-2025") in body


def test_marketplace_round_still_open_has_no_public_call_link(client, world):
    make_round(
        "open-round",
        title="Still open",
        status="active",
        decision_on=None,
        application_deadline=timezone.localdate() + timedelta(days=5),
    )
    login(client, world["user"])
    body = client.get(reverse("marketplace:round", args=["open-round"])).content.decode()
    assert "Public call page" not in body


def test_member_of_dimagi_gets_no_management_of_past_calls(client, world):
    member = make_user("dimagi-member", email="someone@example.org")
    grant(member, world["dimagi"])
    assert access.can_manage(member, world["round"]) is False
    body = login(client, member).get(url("past_detail", slug="chc-2025")).content.decode()
    assert url("edit", pk=world["round"].pk) not in body
