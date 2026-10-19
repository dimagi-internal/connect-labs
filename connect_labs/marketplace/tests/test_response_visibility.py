"""Who answered a round, and what they said, shows only to the round's owner. All data invented.

Signed in is not enough: an organisation that answered an EOI told the round's
owner, not every labs user (`access.can_read_round_responses`). Everyone else
still sees the call and how many answered it.
"""

import json

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
        claims = [
            {
                "id": "c1",
                "about": "criterion:reach",
                "text": "Lists are thin outside the city.",
                "cites": [{"question": "how", "quote": "customer lists of solar vendors"}],
            },
            {"id": "c2", "about": "strength", "text": "A real channel to households."},
        ]
        response = client.post(
            url,
            {
                "based_on": ai.pk,
                "stance": "amended",
                "criteria_score_reach": 6,
                "criteria_score_cost": 5,
                "outcome": "backup",
                "claims_json": json.dumps(claims),
            },
        )
        assert response.status_code == 302
        mine = submission.reviews.get(kind="human")
        assert (mine.based_on_id, mine.stance, mine.outcome, mine.score) == (ai.pk, "amended", "backup", 56)
        assert mine.criteria_notes == {"reach": "Lists are thin outside the city."}
        assert mine.strengths == "- A real channel to households."
        cite = mine.claims[0]["cites"][0]
        assert ANSWER[cite["start"] : cite["end"]] == "customer lists of solar vendors"
        ai.refresh_from_db()
        assert ai.outcome == "pick" and ai.criteria_scores == {"reach": 8, "cost": 5}

    def test_adding_to_it_keeps_the_original_whole(self, client, scored, member):
        """'Add to it' stores only what the person adds; the page reads the
        original with the additions marked, and the original is untouched."""
        submission, ai = scored
        client.force_login(member)
        url = reverse("solicitations:review", args=[submission.pk])
        response = client.get(url, {"from": ai.pk, "stance": "adds"})
        form = response.context["form"]
        assert json.loads(form.initial["claims_json"]) == [], "only additions are written here"
        assert form.initial["outcome"] == "pick" and form.initial["criteria_score_reach"] == 8
        page = response.content.decode()
        assert "Your additions to the AI first pass" in page and "Names solar vendors" in page
        added = [{"id": "c1", "about": "criterion:reach", "text": "Lists exist in two towns only."}]
        data = {k: v for k, v in form.initial.items() if v not in (None, "")}
        data.update({"claims_json": json.dumps(added), "ask_next": "Which towns?"})
        assert client.post(url, data).status_code == 302

        mine = submission.reviews.get(kind="human")
        assert (mine.stance, mine.based_on_id, mine.outcome) == ("adds", ai.pk, "pick")
        assert [c["text"] for c in mine.claims] == ["Lists exist in two towns only."]
        ai.refresh_from_db()
        assert ai.criteria_notes == {"reach": "Names solar vendors' lists."}

        detail = client.get(reverse("solicitations:response_detail", args=[submission.pk]))
        panel = next(r.panel for r in detail.context["reviews"] if r.pk == mine.pk)
        reach = next(g for g in panel if g["label"] == "Reach to households")
        assert [(c["origin"], c["text"]) for c in reach["claims"]] == [
            ("base", "Names solar vendors' lists."),
            ("added", "Lists exist in two towns only."),
        ]
        body = detail.content.decode()
        assert "added to it" in body and "Which towns?" in body
        assert f"?from={ai.pk}&stance=adds" in body

    def test_each_statement_names_the_questions_it_draws_on(self, client, scored, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, _ = scored
        SolicitationsDataAccess(user=ace).create_review(
            submission.pk,
            {
                "kind": "ai",
                "claims": [
                    {
                        "about": "criterion:reach",
                        "text": "Has a channel.",
                        "cites": [{"question": "how", "quote": "solar vendors"}],
                    }
                ],
            },
        )
        client.force_login(member)
        detail = client.get(reverse("solicitations:response_detail", args=[submission.pk]))
        claim = detail.context["reviews"][0].panel[0]["claims"][0]
        number = 1 + [a["id"] for a in detail.context["answers"]].index("how")
        assert claim["drawn_from"][0]["n"] == number
        assert f"Q{number}</span>" in detail.content.decode()

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
        review = {k: fenwick["review"][k] for k in ("outcome", "label", "by_person", "people", "has_ai")}
        assert review == {
            "outcome": "waitlist",
            "label": "Keep in view",
            "by_person": True,
            "people": 2,
            "has_ai": True,
        }
        assert response.context["reviewed"]["next_pk"] != submission.pk, "the next to check is one nobody has"
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


class TestEvidence:
    def _ai(self, ace, submission, claims):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        return SolicitationsDataAccess(user=ace).create_review(submission.pk, {"kind": "ai", "claims": claims})

    def test_quotes_are_checked_and_located(self, scored, ace):
        submission, _ = scored
        review = self._ai(
            ace,
            submission,
            [
                # Case, spacing and a curly apostrophe differ from the answer; still found.
                {
                    "about": "criterion:reach",
                    "text": "Has a channel.",
                    "cites": [{"question": "how", "quote": "THE  customer lists"}],
                },
                {"about": "concern", "text": "Generic text throughout.", "basis": "synthesized"},
                {"about": "concern", "text": "Names no buyer.", "cites": [{"question": "how", "absent": True}]},
                {
                    "about": "strength",
                    "text": "Delivered before.",
                    "cites": [{"source": "labs", "detail": "2 programmes"}],
                },
            ],
        )
        quoted = review.claims[0]["cites"][0]
        assert ANSWER[quoted["start"] : quoted["end"]] == "the customer lists"
        assert quoted["quote"] == "the customer lists", "the stored quote is the applicant's own text"
        assert review.claims[1]["basis"] == "synthesized" and review.claims[1]["cites"] == []
        assert review.concerns == "- Generic text throughout.\n- Names no buyer."
        assert review.criteria_notes == {"reach": "Has a channel."}

    def test_a_quote_the_applicant_never_wrote_refuses_the_review(self, scored, ace):
        from django.core.exceptions import ValidationError

        submission, ai = scored
        with pytest.raises(ValidationError) as caught:
            self._ai(
                ace,
                submission,
                [
                    {
                        "about": "strength",
                        "text": "x",
                        "cites": [{"question": "how", "quote": "door to door canvassing"}],
                    },
                    {"about": "criterion:nope", "text": "y"},
                    {"about": "concern", "text": "z", "cites": [{"source": "web", "detail": "no link"}]},
                ],
            )
        problems = " ".join(caught.value.messages)
        assert "door to door canvassing" in problems and "criterion:nope" in problems and "url" in problems
        ai.refresh_from_db()
        assert ai.claims == [], "nothing was saved"

    def test_the_mcp_tool_says_which_quotes_failed(self, scored, ace):
        from connect_labs.mcp.tools import reviews as review_tools

        submission, _ = scored
        with pytest.raises(tools.MCPToolError) as caught:
            review_tools.create_review(
                ace,
                True,
                submission.pk,
                "",
                kind="ai",
                claims=[{"about": "strength", "text": "x", "cites": [{"question": "how", "quote": "invented words"}]}],
            )
        assert caught.value.code == "BAD_REQUEST" and "invented words" in str(caught.value)
        answers = tools.marketplace_round_responses(ace, "battery-2026")["responses"][0]["answers"]
        assert answers[0]["question_id"] == "how"

    def test_the_response_page_is_split_and_carries_the_evidence(self, client, scored, member, ace):
        submission, _ = scored
        self._ai(
            ace,
            submission,
            [
                {
                    "about": "criterion:reach",
                    "text": "Has a channel.",
                    "cites": [{"question": "how", "quote": "solar vendors"}],
                },
            ],
        )
        client.force_login(member)
        response = client.get(reverse("solicitations:response_detail", args=[submission.pk]))
        assert response.templates[0].name == "solicitations/round_response.html"
        payload = response.context["evidence"]
        cite = payload["reviews"][0]["claims"][0]["cites"][0]
        assert ANSWER[cite["start"] : cite["end"]] == "solar vendors"
        body = response.content.decode()
        assert 'data-claim="c1"' in body and "review_evidence.js" in body and 'data-evidence-mode="all"' in body

    def test_a_review_written_before_claims_still_shows_its_statements(self, client, round_, member):
        from connect_labs.solicitations.local_models import SolicitationReview

        submission = round_.responses.get(source_row=2)
        SolicitationReview.objects.create(
            response=submission, reviewer=None, reviewer_name="old", strengths="- Knows the area", concerns="Costly"
        )
        client.force_login(member)
        payload = client.get(reverse("solicitations:response_detail", args=[submission.pk])).context["evidence"]
        texts = [c["text"] for c in payload["reviews"][0]["claims"]]
        assert texts == ["Knows the area", "Costly"]

    def test_the_copy_carries_its_evidence_into_the_form(self, client, scored, member, ace):
        submission, _ = scored
        ai = self._ai(
            ace,
            submission,
            [
                {
                    "about": "criterion:reach",
                    "text": "Has a channel.",
                    "cites": [{"question": "how", "quote": "solar vendors"}],
                },
            ],
        )
        client.force_login(member)
        response = client.get(reverse("solicitations:review", args=[submission.pk]), {"from": ai.pk})
        assert response.templates[0].name == "solicitations/round_review_form.html"
        carried = json.loads(response.context["form"].initial["claims_json"])
        assert carried[0]["cites"][0]["quote"] == "solar vendors"


class TestReadingForReview:
    def test_contact_details_are_folded_away_and_the_round_can_be_walked(self, client, round_, member):
        round_.questions = [
            {"id": "how", "text": "How would you find households?", "column": 1},
            {"id": "phone", "text": "Phone", "column": 2},
        ]
        round_.save()
        first, second = round_.responses.get(source_row=2), round_.responses.get(source_row=3)
        first.responses = {"how": ANSWER, "phone": "0800 000 000"}
        first.save()
        client.force_login(member)
        context = client.get(reverse("solicitations:response_detail", args=[first.pk])).context
        assert [a["id"] for a in context["answers"]] == ["how"]
        assert [c["id"] for c in context["contact"]] == ["phone"]
        assert context["applicant_count"] == 3
        assert second.pk in (context["previous_pk"], context["next_pk"])
        assert context["page_chrome"].labs_context is False

    def test_the_round_page_says_where_to_start(self, client, round_, member):
        client.force_login(member)
        body = client.get(reverse("marketplace:round", args=["battery-2026"])).content.decode()
        assert "Start reviewing" in body and "None checked by a person yet" in body

    def test_once_reviewed_the_list_is_a_shortlist_and_the_response_pages_follow_it(self, client, scored, member):
        """Picks first, best score first; previous/next walk the same order."""
        submission, _ = scored
        client.force_login(member)
        applicants = client.get(reverse("marketplace:round", args=["battery-2026"])).context["applicants"]
        assert applicants[0]["response"].pk == submission.pk, "the AI pick leads the list"
        context = client.get(reverse("solicitations:response_detail", args=[submission.pk])).context
        assert context["position"] == 1 and context["next_pk"] == applicants[1]["response"].pk


class TestScores:
    """The round's scores side by side, for the owner to filter and compare."""

    @pytest.fixture
    def two_scored(self, scored, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        other = scored[0].solicitation.responses.get(source_row=3)
        SolicitationsDataAccess(user=ace).create_review(
            other.pk,
            {
                "kind": "ai",
                "criteria_scores": {"reach": 4, "cost": 5},
                "outcome": "pass",
                "flags": ["generic-or-ai-drafted"],
                "provenance": {"model": "test", "rubric_version": "v1"},
            },
        )
        return scored

    def test_each_criterion_says_whether_it_decided_the_slate(self, client, two_scored, member):
        client.force_login(member)
        summary = client.get(reverse("marketplace:round_scores", args=["battery-2026"])).context["summary"]
        reach, cost = summary["criteria"]
        assert (reach["scored"], reach["mean"], reach["gap"]) == (2, 6.0, 4.0), "the pick out-reached the pass"
        assert cost["gap"] == 0.0, "cost did not separate them"
        assert summary["outcomes"] == {"pick": 1, "backup": 0, "waitlist": 0, "pass": 1, "undecided": 1}
        assert summary["flags"] == [("padded", 1)], "a flag written before the list reads as the one it meant"

    def test_every_applicant_is_a_row_with_its_scores(self, client, two_scored, member):
        body = _page(client, member, "marketplace:round_scores", "battery-2026")
        assert body.count('data-href="/solicitations/response/') == 3
        assert 'data-scores="8|5"' in body and 'data-scores="4|5"' in body and 'data-scores="|"' in body
        assert "Reach to households" in body and 'data-flags="padded"' in body and "Long but thin" in body

    def test_it_says_when_reviews_predate_the_current_criteria(self, client, two_scored, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, "v2")
        body = _page(client, member, "marketplace:round_scores", "battery-2026")
        assert "2 reviews scored on v1, not the current criteria (v2)" in body

    def test_adding_to_the_ai_review_keeps_its_scores(self, client, scored, member):
        submission, ai = scored
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=member).create_review(
            submission.pk, {"based_on": ai.pk, "stance": "adds", "criteria_scores": {"cost": 7}, "outcome": "pick"}
        )
        client.force_login(member)
        applicants = client.get(reverse("marketplace:round_scores", args=["battery-2026"])).context["applicants"]
        lead = next(a for a in applicants if a["response"].pk == submission.pk)
        assert lead["review"]["criteria_scores"] == {"reach": 8, "cost": 7} and lead["review"]["by_person"]

    def test_the_round_page_links_to_it(self, client, scored, member):
        assert 'data-testid="scores-link"' in _page(client, member, "marketplace:round", "battery-2026")

    def test_only_the_owner_sees_it(self, client, scored, outsider):
        client.force_login(outsider)
        assert client.get(reverse("marketplace:round_scores", args=["battery-2026"])).status_code == 404
        with pytest.raises(MCPToolError):
            tools.marketplace_round_scores(outsider, "battery-2026")

    def test_the_mcp_tool_reads_the_same_figures(self, client, two_scored, member, ace):
        client.force_login(member)
        page = client.get(reverse("marketplace:round_scores", args=["battery-2026"])).context["summary"]
        tool = tools.marketplace_round_scores(ace, "battery-2026")
        assert tool["criteria"] == page["criteria"] and tool["outcomes"] == page["outcomes"]
        fenwick = next(a for a in tool["applicants"] if a["org_slug"] == "fenwick")
        assert fenwick["criteria_scores"] == {"reach": 8, "cost": 5} and fenwick["rubric_version"] == "v1"


def test_a_persons_review_records_the_criteria_it_scored_against(client, scored, member):
    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    submission, ai = scored
    mine = SolicitationsDataAccess(user=member).create_review(
        submission.pk, {"based_on": ai.pk, "stance": "agrees", "criteria_scores": {"reach": 8, "cost": 5}}
    )
    assert mine.provenance["rubric_version"] == "v1"
    body = _page(client, member, "marketplace:round_scores", "battery-2026")
    assert 'data-testid="stale"' not in body and 'data-testid="unrecorded"' not in body


class TestTerms:
    """Every outcome and flag a reviewer reads is a defined word with its meaning."""

    def test_a_pick_reads_as_recommended(self, client, scored, member):
        body = _page(client, member, "marketplace:round_scores", "battery-2026")
        assert "Recommended" in body and "Proposed for one of the round" in body and ">Pick<" not in body

    def test_flags_come_from_the_list(self, scored, ace):
        from django.core.exceptions import ValidationError

        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        da = SolicitationsDataAccess(user=ace)
        assert da.update_review(ai.pk, {"flags": ["cost-outlier", "safety"]}).flags == ["cost", "safety"]
        with pytest.raises(ValidationError):
            da.update_review(ai.pk, {"flags": ["vibes-off"]})

    def test_the_review_form_offers_the_flags_with_their_meanings(self, client, scored, member):
        submission, _ = scored
        body = _page(client, member, "solicitations:review", submission.pk)
        assert 'data-testid="flag-choices"' in body and "Starts before contract" in body
        assert "Plans to begin work before a contract is in place." in body

    def test_scores_are_banded_green_amber_red(self):
        from connect_labs.marketplace.views import score_shade

        assert "#86efac" in score_shade(8) and "#fef3c7" in score_shade(5) and "#fca5a5" in score_shade(2)
        assert score_shade(None) == ""


class TestSummaryLink:
    URL = "https://docs.google.com/document/d/example-invalid/edit"

    def test_the_owner_links_it_by_hand_and_both_pages_show_it(self, client, scored, member):
        client.force_login(member)
        response = client.post(
            reverse("marketplace:round_summary", args=["battery-2026"]),
            {"summary_url": self.URL, "next": reverse("marketplace:round_scores", args=["battery-2026"])},
        )
        assert response.status_code == 302 and response.url.endswith("/scores/")
        assert self.URL in _page(client, member, "marketplace:round_scores", "battery-2026")
        assert 'data-testid="summary-link"' in _page(client, member, "marketplace:round", "battery-2026")

    def test_only_an_https_link(self, scored, ace):
        with pytest.raises(MCPToolError):
            tools.marketplace_round_summary_set(ace, "battery-2026", "javascript:alert(1)")
        assert tools.marketplace_round_summary_set(ace, "battery-2026", self.URL)["summary_url"] == self.URL

    def test_nobody_else_sets_it(self, client, scored, outsider):
        client.force_login(outsider)
        response = client.post(reverse("marketplace:round_summary", args=["battery-2026"]), {"summary_url": self.URL})
        assert response.status_code == 404
        with pytest.raises(MCPToolError):
            tools.marketplace_round_summary_set(outsider, "battery-2026", self.URL)


class TestDeletingAReview:
    def test_the_reviewer_deletes_it_and_reviews_built_on_it_stay(self, client, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess
        from connect_labs.solicitations.local_models import SolicitationReview

        submission, ai = scored
        mine = SolicitationsDataAccess(user=member).create_review(
            submission.pk, {"based_on": ai.pk, "stance": "agrees", "outcome": "pick"}
        )
        body = _page(client, member, "solicitations:response_detail", submission.pk)
        assert 'data-testid="delete-review"' in body
        response = client.post(reverse("solicitations:review_delete", args=[mine.pk]))
        assert response.status_code == 302 and not SolicitationReview.objects.filter(pk=mine.pk).exists()
        assert SolicitationReview.objects.filter(pk=ai.pk).exists()

    def test_nobody_else_deletes_it(self, client, scored, member, outsider):
        from connect_labs.mcp.tools import reviews as review_tools
        from connect_labs.solicitations.local_models import SolicitationReview

        _, ai = scored
        client.force_login(member)
        assert client.post(reverse("solicitations:review_delete", args=[ai.pk])).status_code == 404
        with pytest.raises(MCPToolError):
            review_tools.delete_review(outsider, ai.pk)
        assert SolicitationReview.objects.filter(pk=ai.pk).exists()

    def test_the_mcp_tool_deletes_your_own(self, scored, ace):
        from connect_labs.mcp.tools import reviews as review_tools

        submission, ai = scored
        assert review_tools.delete_review(ace, ai.pk) == {"deleted": ai.pk, "response_id": submission.pk}


CRITERIA_V2 = [
    {"id": "reach", "name": "Reach to households", "weight": 70},
    {"id": "cost", "name": "Cost fit", "weight": 30},
]


class TestVersions:
    """Every criteria version is kept, with the reviews scored on it, until someone deletes it."""

    @pytest.fixture
    def rescored(self, scored, ace):
        """v1 with an AI pick on Fenwick; then v2, re-scored as a backup."""
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai_v1 = scored
        da = SolicitationsDataAccess(user=ace)
        da.set_round_criteria("battery-2026", CRITERIA_V2, "Reach matters more.")
        ai_v2 = da.create_review(
            submission.pk,
            {
                "kind": "ai",
                "criteria_scores": {"reach": 6, "cost": 5},
                "outcome": "backup",
                "provenance": {"rubric_version": "v2"},
            },
        )
        return submission, ai_v1, ai_v2

    def test_rewriting_the_criteria_keeps_the_earlier_version(self, rescored, round_):
        assert list(round_.rubric_versions.values_list("version", "criteria__0__weight")) == [
            ("v1", 60.0),
            ("v2", 70.0),
        ]

    def test_a_rescore_on_new_criteria_keeps_the_earlier_ai_review(self, rescored):
        from connect_labs.solicitations.local_models import SolicitationReview

        submission, ai_v1, ai_v2 = rescored
        assert ai_v1.pk != ai_v2.pk
        assert list(submission.reviews.all()) == [ai_v2], "only the current one stands"
        old = SolicitationReview.all_versions.get(pk=ai_v1.pk)
        assert old.superseded_at is not None and old.outcome == "pick" and old.score == 68

    def test_the_page_reads_an_earlier_version_and_says_what_changed(self, client, rescored, member):
        client.force_login(member)
        context = client.get(reverse("marketplace:round_scores", args=["battery-2026"]), {"version": "v1"}).context
        assert not context["is_current"] and context["summary"]["outcomes"]["pick"] == 1
        current = client.get(reverse("marketplace:round_scores", args=["battery-2026"])).context
        moved = current["comparison"]["moved"]
        assert [(m["applicant"]["name"], m["from"], m["to"]) for m in moved] == [
            ("Fenwick Trust", "Recommended", "Backup")
        ]
        weights = {c["id"]: (c["weight_from"], c["weight_to"]) for c in current["comparison"]["criteria"]}
        assert weights == {"reach": (60.0, 70.0), "cost": (40.0, 30.0)}

    def test_the_comparison_says_what_its_numbers_are(self, client, rescored, member):
        client.force_login(member)
        page = client.get(reverse("marketplace:round_scores", args=["battery-2026"])).content.decode()
        assert "points out of 100 on average" in page and "Mean of 10" in page
        assert "Shortlist lead" in page and ">Difference<" not in page
        assert 'data-testid="comparison-note"' in page

    def test_the_mcp_tools_carry_versions_and_the_comparison(self, rescored, ace):
        versions = tools.marketplace_round_versions(ace, "battery-2026")
        assert [(v["version"], v["current"], v["ai_reviews"]) for v in versions["versions"]] == [
            ("v1", False, 1),
            ("v2", True, 1),
        ]
        scores = tools.marketplace_round_scores(ace, "battery-2026", version="v2", compare_with="v1")
        assert scores["comparison"]["moved"][0]["from"] == "pick"

    def test_earlier_history_is_archived_into_an_earlier_version(self, rescored, ace, round_):
        from connect_labs.solicitations.local_models import SolicitationReview

        other = round_.responses.get(source_row=3)
        result = tools.marketplace_round_version_archive(
            ace,
            "battery-2026",
            "v1",
            [{"response_id": other.pk, "criteria_scores": {"reach": 2, "cost": 4}, "outcome": "pass"}],
        )
        assert result == {"version": "v1", "reviews": 1}
        archived = SolicitationReview.all_versions.get(response=other, provenance__rubric_version="v1")
        assert archived.superseded_at is not None and archived.score == 28, "scored on v1's 60/40 weights"
        with pytest.raises(MCPToolError):
            tools.marketplace_round_version_archive(ace, "battery-2026", "v2", [])

    def test_an_earlier_version_is_deleted_with_its_reviews_and_never_the_current(
        self, client, rescored, member, outsider
    ):
        from connect_labs.solicitations.local_models import SolicitationReview

        submission, ai_v1, ai_v2 = rescored
        client.force_login(outsider)
        url = reverse("marketplace:round_version_delete", args=["battery-2026", "v1"])
        assert client.post(url).status_code == 404
        client.force_login(member)
        assert client.post(url).status_code == 302
        assert not SolicitationReview.all_versions.filter(pk=ai_v1.pk).exists()
        assert SolicitationReview.objects.filter(pk=ai_v2.pk).exists()
        assert list(submission.solicitation.rubric_versions.values_list("version", flat=True)) == ["v2"]
        client.post(reverse("marketplace:round_version_delete", args=["battery-2026", "v2"]))
        assert submission.solicitation.rubric_versions.filter(version="v2").exists()

    def test_a_deleted_version_number_is_never_reused(self, rescored, ace, round_):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        tools.marketplace_round_version_delete(ace, "battery-2026", "v1")
        assert SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA).rubric_version == "v3"


def test_versions_sort_by_number_not_by_when_they_were_archived(scored, ace):
    from connect_labs.solicitations.data_access import SolicitationsDataAccess
    from connect_labs.solicitations.local_models import RubricVersion

    da = SolicitationsDataAccess(user=ace)
    da.set_round_criteria("battery-2026", CRITERIA_V2)
    da.set_round_criteria("battery-2026", CRITERIA)
    v1 = RubricVersion.objects.get(version="v1")
    v1.delete()
    tools.marketplace_round_version_archive(ace, "battery-2026", "v1", [], CRITERIA)
    assert [v["version"] for v in tools.marketplace_round_versions(ace, "battery-2026")["versions"]] == [
        "v1",
        "v2",
        "v3",
    ]
    comparison = tools.marketplace_round_scores(ace, "battery-2026", compare_with="v1")["comparison"]
    assert (comparison["earlier"], comparison["later"]) == ("v1", "v3")
