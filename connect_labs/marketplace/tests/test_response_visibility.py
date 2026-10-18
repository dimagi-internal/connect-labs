"""Who answered a round, and what they said, shows only to the round's owner. All data invented.

Signed in is not enough: an organisation that answered an EOI told the round's
owner, not every labs user (`access.can_read_round_responses`). Everyone else
still sees the call and how many answered it.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgProfile
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import marketplace as tools
from connect_labs.solicitations import access
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.supply_chain.runner import dimagi_org

pytestmark = pytest.mark.django_db

ANSWER = "Through the customer lists of solar vendors."


@pytest.fixture
def round_():
    applicant = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")
    OrgProfile.objects.create(org=applicant, countries=["Ghana"])
    # Applied, but not in the directory: only the application puts it on the network.
    LabsOrg.objects.create(slug="quietwater", name="Quietwater Collective")
    call = Solicitation.objects.create(
        owner_org=dimagi_org(),
        slug="battery-2026",
        title="Battery scoping",
        solicitation_type="eoi",
        delivery_type="chc",
        status="closed",
        response_spreadsheet_id="sheet-id-invalid",
        questions=[{"id": "how", "text": "How would you find households?", "column": 1}],
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        llo_entity=applicant,
        org_name="Fenwick Trust",
        source_row=2,
        match_state=SolicitationResponse.MATCH_EMAIL,
        responses={"how": ANSWER},
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        llo_entity=LabsOrg.objects.get(slug="quietwater"),
        org_name="Quietwater Collective",
        source_row=3,
        match_state=SolicitationResponse.MATCH_EMAIL,
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        org_name="Unclaimed Group",
        source_row=4,
        match_state=SolicitationResponse.MATCH_UNMATCHED,
    )
    return call


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user(username="member", password="x", email="m@dimagi-associate.com")


@pytest.fixture
def outsider(django_user_model):
    return django_user_model.objects.create_user(username="outsider", password="x", email="o@partner.invalid")


def _page(client, user, name, *args, **params):
    client.force_login(user)
    response = client.get(reverse(name, args=args), params)
    assert response.status_code == 200
    return response.content.decode()


def test_only_the_owner_reads_a_round(round_, member, outsider):
    assert access.readable_round_ids(member) == {round_.pk}
    assert access.readable_round_ids(outsider) == frozenset()
    assert access.readable_round_ids(None) == frozenset()


class TestTheRoundPage:
    def test_the_owner_sees_who_applied(self, client, round_, member):
        body = _page(client, member, "marketplace:round", "battery-2026")
        assert "Fenwick Trust" in body and "Unclaimed Group" in body
        assert "sheet-id-invalid" in body

    def test_anyone_else_sees_the_call_and_the_count_only(self, client, round_, outsider):
        body = _page(client, outsider, "marketplace:round", "battery-2026")
        assert "Battery scoping" in body
        # The questions are the form, which anyone who sees the call may read.
        assert reverse("solicitations:respond", args=[round_.public_id]) in body
        assert "Fenwick Trust" not in body and "Unclaimed Group" not in body
        assert "sheet-id-invalid" not in body, "the response sheet is the submissions"
        assert "visible only to members of Dimagi" in body
        assert "fenwick" not in str(
            client.get(reverse("marketplace:round", args=["battery-2026"])).context["canopy_panel"]
        )


class TestTheOrganisationPage:
    def test_the_owner_reads_its_answers(self, client, round_, member):
        assert ANSWER in _page(client, member, "marketplace:organisation", "fenwick")

    def test_anyone_else_does_not(self, client, round_, outsider):
        body = _page(client, outsider, "marketplace:organisation", "fenwick")
        assert "Fenwick Trust" in body, "the directory entry itself is not a submission"
        assert ANSWER not in body and "Battery scoping" not in body


def test_the_verdict_queue_is_the_owners(client, round_, member, outsider):
    assert "Unclaimed Group" in _page(client, member, "marketplace:unmatched")
    assert "Unclaimed Group" not in _page(client, outsider, "marketplace:unmatched")


class TestTheNetwork:
    def test_the_owner_sees_what_each_organisation_applied_to(self, client, round_, member):
        body = _page(client, member, "marketplace:network", applied="chc")
        assert "Fenwick Trust" in body and "Quietwater Collective" in body

    def test_anyone_else_sees_the_directory_without_applications(self, client, round_, outsider):
        assert "Fenwick Trust" in _page(client, outsider, "marketplace:network")
        assert "Quietwater Collective" not in _page(client, outsider, "marketplace:network")
        assert "Fenwick Trust" not in _page(client, outsider, "marketplace:network", applied="chc")


class TestTheTools:
    def test_applicants_are_the_owners(self, round_, member, outsider):
        out = tools.marketplace_rounds_list(member, slug="battery-2026", include_applicants=True)
        assert {a["name"] for a in out["rounds"][0]["applicants"]} >= {"Fenwick Trust"}
        with pytest.raises(MCPToolError) as refused:
            tools.marketplace_rounds_list(outsider, slug="battery-2026", include_applicants=True)
        assert refused.value.code == "PERMISSION_DENIED"

    def test_the_round_itself_is_anyones(self, round_, outsider):
        out = tools.marketplace_rounds_list(outsider, slug="battery-2026")
        assert out["rounds"][0]["applications"] == 3 and "applicants" not in out["rounds"][0]

    def test_applied_programs_are_the_owners(self, round_, member, outsider):
        (mine,) = tools.marketplace_orgs_get(member, slugs=["fenwick"])["organizations"]
        (theirs,) = tools.marketplace_orgs_get(outsider, slugs=["fenwick"])["organizations"]
        assert mine["applied_programs"] and theirs["applied_programs"] == []


class TestOneResponse:
    def test_the_owner_opens_a_submission_from_the_round(self, client, round_, member):
        submission = round_.responses.get(source_row=2)
        body = _page(client, member, "marketplace:round", "battery-2026")
        assert reverse("solicitations:response_detail", args=[submission.pk]) in body
        body = _page(client, member, "solicitations:response_detail", submission.pk)
        assert ANSWER in body and "How would you find households?" in body
        assert "Write my own review" in body
        assert reverse("solicitations:award", args=[submission.pk]) not in body, "a round is decided outside labs"

    def test_to_anyone_else_it_does_not_exist(self, client, round_, outsider):
        client.force_login(outsider)
        submission = round_.responses.get(source_row=2)
        for name in ("solicitations:response_detail", "solicitations:review"):
            assert client.get(reverse(name, args=[submission.pk])).status_code == 404

    def test_the_form_shows_the_questions_and_no_answers(self, client, round_, outsider):
        body = _page(client, outsider, "solicitations:respond", round_.public_id)
        assert "How would you find households?" in body
        assert ANSWER not in body
        assert "<textarea" in body and "disabled" in body

    def test_nothing_answers_a_round_from_its_form(self, client, round_, outsider):
        """Its responses came through the Google Form; this copy is read-only."""
        client.force_login(outsider)
        before = round_.responses.count()
        response = client.post(
            reverse("solicitations:respond", args=[round_.public_id]),
            {"org": "__new__", "new_org_name": "Somebody", "question_how": "door to door"},
        )
        assert response.status_code == 403
        assert round_.responses.count() == before

    def test_signed_out_it_asks_you_to_sign_in(self, client, round_):
        response = client.get(reverse("solicitations:respond", args=[round_.public_id]))
        assert response.status_code == 302


class TestTheRoundMix:
    def test_new_returning_and_countries(self, client, round_, member):
        earlier = Solicitation.objects.create(
            owner_org=dimagi_org(), slug="older-2024", title="Older", solicitation_type="eoi", status="closed"
        )
        SolicitationResponse.objects.create(
            solicitation=earlier,
            llo_entity=LabsOrg.objects.get(slug="quietwater"),
            source_row=2,
            submission_date="2024-03-01T00:00:00Z",
            match_state=SolicitationResponse.MATCH_EMAIL,
        )
        round_.published_on = "2026-10-01"
        round_.save()
        client.force_login(member)
        mix = client.get(reverse("marketplace:round", args=["battery-2026"])).context["mix"]
        # Quietwater answered a 2024 round; Fenwick and the unmatched one are new.
        assert (mix["new"], mix["returning"]) == (2, 1)
        assert mix["countries"] == [("Ghana", 1), ("Not stated", 2)]
        assert mix["country_count"] == 1

    def test_an_outsider_gets_no_mix(self, client, round_, outsider):
        client.force_login(outsider)
        assert client.get(reverse("marketplace:round", args=["battery-2026"])).context["mix"] == {}


CRITERIA = [
    {"id": "reach", "name": "Reach to households", "weight": 60, "rationale": "Half name no channel."},
    {"id": "cost", "name": "Cost fit", "weight": 40, "rationale": "Quotes vary tenfold."},
]


@pytest.fixture
def ace(django_user_model):
    return django_user_model.objects.create_user(username="ace", password="x", email="ace@dimagi-ai.com")


@pytest.fixture
def scored(round_, ace):
    """The round with criteria and an AI first pass on Fenwick."""
    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, "Read all three.")
    submission = round_.responses.get(source_row=2)
    ai = SolicitationsDataAccess(user=ace).create_review(
        submission.pk,
        {
            "kind": "ai",
            "criteria_scores": {"reach": 8, "cost": 5},
            "criteria_notes": {"reach": "Names solar vendors' lists."},
            "outcome": "pick",
            "strengths": "A real channel to households.",
            "provenance": {"model": "test", "rubric_version": "v1"},
        },
    )
    return submission, ai


class TestReviewingARound:
    def test_criteria_are_versioned_and_must_add_up(self, round_, ace):
        from django.core.exceptions import ValidationError

        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        da = SolicitationsDataAccess(user=ace)
        assert da.set_round_criteria("battery-2026", CRITERIA).rubric_version == "v1"
        assert da.set_round_criteria("battery-2026", CRITERIA).rubric_version == "v2"
        with pytest.raises(ValidationError):
            da.set_round_criteria("battery-2026", [{"id": "a", "name": "A", "weight": 50}])

    def test_an_outsider_cannot_set_them(self, round_, outsider):
        from django.core.exceptions import PermissionDenied

        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        with pytest.raises(PermissionDenied):
            SolicitationsDataAccess(user=outsider).set_round_criteria("battery-2026", CRITERIA)

    def test_the_ai_review_is_scored_on_the_round_criteria(self, scored):
        _, ai = scored
        assert ai.kind == "ai" and ai.score == 68  # (8*60 + 5*40) / 100 * 10

    def test_the_page_shows_it_as_unchecked_with_ways_to_respond(self, client, scored, member):
        submission, ai = scored
        body = _page(client, member, "solicitations:response_detail", submission.pk)
        assert "AI first pass" in body and "no one has checked it yet" in body
        assert f"?from={ai.pk}&stance=agrees" in body
        assert f"?from={ai.pk}" in body
        assert "Names solar vendors" in body

    def test_copying_it_prefills_everything_and_leaves_the_original(self, client, scored, member):
        submission, ai = scored
        client.force_login(member)
        url = reverse("solicitations:review", args=[submission.pk])
        page = client.get(url, {"from": ai.pk}).content.decode()
        assert "starting from the AI first pass" in page and "A real channel to households." in page
        response = client.post(
            url,
            {
                "based_on": ai.pk,
                "stance": "amended",
                "criteria_score_reach": 6,
                "criteria_score_cost": 5,
                "criteria_note_reach": "Lists are thin outside the city.",
                "outcome": "backup",
                "strengths": "A real channel to households.",
            },
        )
        assert response.status_code == 302
        mine = submission.reviews.get(kind="human")
        assert (mine.based_on_id, mine.stance, mine.outcome, mine.score) == (ai.pk, "amended", "backup", 56)
        ai.refresh_from_db()
        assert ai.outcome == "pick" and ai.criteria_scores == {"reach": 8, "cost": 5}

    def test_agreeing_is_one_save(self, client, scored, member):
        submission, ai = scored
        client.force_login(member)
        url = reverse("solicitations:review", args=[submission.pk])
        form = client.get(url, {"from": ai.pk, "stance": "agrees"}).context["form"]
        data = {k: v for k, v in form.initial.items() if v not in (None, "")}
        assert client.post(url, data).status_code == 302
        mine = submission.reviews.get(kind="human")
        assert (mine.stance, mine.outcome, mine.score) == ("agrees", "pick", ai.score)

    def test_several_people_review_and_the_round_page_counts_them(self, client, scored, member, django_user_model):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        second = django_user_model.objects.create_user(username="second", password="x", email="s@dimagi.com")
        for person in (member, second):
            SolicitationsDataAccess(user=person).create_review(
                submission.pk, {"outcome": "waitlist", "based_on": ai.pk, "stance": "disagrees"}
            )
        assert submission.reviews.filter(kind="human").count() == 2
        client.force_login(member)
        response = client.get(reverse("marketplace:round", args=["battery-2026"]))
        assert response.context["reviewed"]["people"] == 1
        fenwick = next(a for a in response.context["applicants"] if a["response"].pk == submission.pk)
        assert fenwick["review"] == {
            "outcome": "waitlist",
            "label": "Waitlist",
            "by_person": True,
            "people": 2,
            "has_ai": True,
        }
        body = response.content.decode()
        assert "HOW RESPONSES ARE SCORED" in body and "Half name no channel." in body

    def test_a_review_cannot_start_from_another_responses_review(self, scored, member, round_):
        from django.core.exceptions import ValidationError

        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        _, ai = scored
        other = round_.responses.get(source_row=3)
        with pytest.raises(ValidationError):
            SolicitationsDataAccess(user=member).create_review(other.pk, {"based_on": ai.pk})

    def test_the_mcp_tools_carry_it(self, scored, ace):
        from connect_labs.mcp.tools import reviews as review_tools

        submission, ai = scored
        rows = tools.marketplace_round_responses(ace, "battery-2026")
        assert rows["round"]["rubric_version"] == "v1"
        assert submission.pk in {r["response_id"] for r in rows["responses"]}
        listed = review_tools.list_reviews(ace, submission.pk)["reviews"]
        assert listed[0]["kind"] == "ai" and listed[0]["criteria_notes"]["reach"].startswith("Names")
        again = review_tools.create_review(
            ace, True, submission.pk, "fenwick", kind="ai", outcome="backup", criteria_scores={"reach": 7}
        )
        assert again["id"] == ai.pk, "re-running the AI pass replaces it"
        set_ = tools.marketplace_round_criteria_set(ace, "battery-2026", CRITERIA, "again")
        assert set_["rubric_version"] == "v2"
