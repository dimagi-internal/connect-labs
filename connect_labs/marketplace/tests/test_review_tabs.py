"""A round's Review tab: four steps, criteria -> AI review -> AI analysis -> human review. All data invented.

The steps are declared once, in `marketplace/review_steps.py`. These tests hold
the parts a change to that list must keep: every step renders for the round's
owner and for nobody else, the step bar names each step with where it stands,
and a bare /review/ opens on the step with something still to do.
"""

import pytest
from django.template.loader import get_template
from django.urls import reverse

from connect_labs.marketplace import queries, review_steps
from connect_labs.marketplace.tests.round_fixtures import CRITERIA

pytestmark = pytest.mark.django_db


def _get(client, user, step="", **params):
    client.force_login(user)
    name, args = (
        ("marketplace:round_review_step", ["battery-2026", step])
        if step
        else ("marketplace:round_review", ["battery-2026"])
    )
    return client.get(reverse(name, args=args), params)


class TestTheSteps:
    def test_every_step_has_its_template(self):
        for step in review_steps.STEPS:
            get_template(step.template)

    def test_the_keys_are_unique_url_segments(self):
        keys = [s.key for s in review_steps.STEPS]
        assert len(keys) == len(set(keys)) and all(k == k.lower() and " " not in k for k in keys)

    def test_the_order_is_the_loop_then_the_check(self):
        assert [s.label for s in review_steps.STEPS] == [
            "Criteria",
            "AI Review",
            "AI Analysis",
            "Human Review",
        ]

    @pytest.mark.parametrize("step", [s.key for s in review_steps.STEPS])
    def test_each_step_renders_for_the_owner_with_the_step_bar(self, client, scored, member, step):
        response = _get(client, member, step)
        assert response.status_code == 200
        page = response.content.decode()
        assert page.count('data-step="') == len(review_steps.STEPS)
        assert f'data-step="{step}"' in page and 'aria-current="step"' in page

    @pytest.mark.parametrize("step", ["", *[s.key for s in review_steps.STEPS]])
    def test_nobody_else_sees_any_step(self, client, scored, outsider, step):
        assert _get(client, outsider, step).status_code == 404

    def test_an_unknown_step_does_not_exist(self, client, scored, member):
        assert _get(client, member, "scores").status_code == 404

    def test_each_step_renders_before_there_are_criteria(self, client, round_, member):
        for step in review_steps.STEPS:
            assert _get(client, member, step.key).status_code == 200, step.key


class TestWhereEachStepStands:
    def _statuses(self, client, user):
        return {s["key"]: s["status"] for s in _get(client, user, "criteria").context["steps"]}

    def test_before_criteria(self, client, round_, member):
        statuses = self._statuses(client, member)
        assert statuses["criteria"] == "None yet"
        assert statuses["ai-review"] == "Waits for criteria"
        assert statuses["ai-analysis"] == "Waits for the AI review"
        assert statuses["human-review"] == "0 of 3 checked"

    def test_after_a_first_ai_pass(self, client, scored, member):
        statuses = self._statuses(client, member)
        assert statuses["criteria"] == "Version 1 · 2 criteria"
        assert statuses["ai-review"] == "1 of 3 on version 1"
        assert statuses["ai-analysis"] == "First version: nothing to compare yet"

    def test_a_revision_leaves_the_ai_review_behind_until_it_is_run_again(self, client, scored, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, "Reweighted.")
        statuses = self._statuses(client, member)
        assert statuses["criteria"] == "Version 2 · 2 criteria"
        assert statuses["ai-review"] == "0 of 3 on version 2"


class TestOpeningReview:
    def test_no_criteria_opens_on_criteria(self, client, round_, member):
        assert _get(client, member).url.endswith("/review/criteria/")

    def test_criteria_with_applicants_left_to_score_opens_on_ai_review(self, client, scored, member):
        assert _get(client, member).url.endswith("/review/ai-review/")

    def test_once_people_are_checking_it_opens_on_human_review(self, client, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(submission.pk, {"based_on": ai.pk, "stance": "agrees"})
        assert _get(client, member).url.endswith("/review/human-review/")

    def test_the_round_page_links_to_review(self, client, round_, member):
        client.force_login(member)
        page = client.get(reverse("marketplace:round", args=["battery-2026"])).content.decode()
        assert 'data-testid="review-link"' in page and "/review/" in page


class TestCriteria:
    def test_it_shows_the_criteria_with_their_reasons_and_how_they_were_chosen(self, client, scored, member):
        page = _get(client, member, "criteria").content.decode()
        assert 'data-testid="criteria-list"' in page and "Quotes vary tenfold." in page
        assert 'data-testid="basis"' in page and "Read all three." in page
        assert 'data-testid="versions"' in page

    def test_without_criteria_it_says_how_they_come_about(self, client, round_, member):
        page = _get(client, member, "criteria").content.decode()
        assert 'data-testid="no-criteria"' in page and 'data-testid="criteria-list"' not in page

    def test_an_earlier_version_shows_its_own_criteria(self, client, scored, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria(
            "battery-2026",
            [{**CRITERIA[0], "weight": 70}, {**CRITERIA[1], "weight": 30}],
            "Reach matters more.",
        )
        old = _get(client, member, "criteria", version="v1")
        assert [c["weight"] for c in old.context["shown_criteria"]] == [60, 40]
        assert old.context["shown_basis"] == "Read all three."
        assert 'data-testid="earlier-version"' in old.content.decode()


class TestHumanReview:
    def test_it_sets_the_ais_review_beside_the_peoples(self, client, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(
            submission.pk,
            {"based_on": ai.pk, "stance": "disagrees", "outcome": "waitlist"},
        )
        response = _get(client, member, "human-review")
        rows = {r["applicant"]["name"]: r for r in response.context["human_rows"]}
        fenwick = rows["Fenwick Trust"]
        assert fenwick["ai"]["outcome"] == "pick" and fenwick["ai"]["version"] == "v1"
        assert [(p["who"], p["stance_text"], p["outcome"]) for p in fenwick["people"]] == [
            ("member", "disagrees with the AI", "waitlist")
        ]
        assert rows["Quietwater Collective"]["people"] == [] and rows["Quietwater Collective"]["ai"] is None
        counts = response.context["human_counts"]
        assert (counts["checked"], counts["disagrees"], counts["reviewers"]) == (
            1,
            1,
            1,
        )
        page = response.content.decode()
        assert "disagrees with the AI" in page and 'data-testid="next-to-check"' in page


class TestWhoseReviews:
    """AI Review and AI Analysis judge the AI's scores; the header stays the round's, as on Overview."""

    @pytest.fixture
    def disagreed(self, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(
            submission.pk, {"based_on": ai.pk, "stance": "disagrees", "outcome": "waitlist"}
        )
        return submission

    def test_ai_review_shows_the_ais_recommendation_not_the_persons(self, client, disagreed, member):
        applicants = _get(client, member, "ai-review").context["applicants"]
        row = next(a for a in applicants if a["response"].pk == disagreed.pk)
        assert row["review"]["outcome"] == "pick" and row["review"]["criteria_scores"] == {"reach": 8, "cost": 5}

    @pytest.mark.parametrize("step", ["ai-review", "ai-analysis"])
    def test_the_header_still_counts_the_round_as_overview_does(self, client, disagreed, member, step):
        client.force_login(member)
        overview = client.get(reverse("marketplace:round", args=["battery-2026"])).context["outcomes"]
        on_step = _get(client, member, step).context["outcomes"]
        assert [(o["key"], o["count"]) for o in on_step] == [(o["key"], o["count"]) for o in overview]
        assert {o["key"]: o["count"] for o in on_step}["waitlist"] == 1, "the person's waitlist leads"


def test_a_redirect_into_review_keeps_only_the_parameters_a_step_reads(client, scored, member):
    client.force_login(member)
    url = client.get(
        reverse("marketplace:round_scores", args=["battery-2026"]),
        {"version": "v1", "next": "https://elsewhere.invalid/"},
    ).url
    assert url == reverse("marketplace:round_review_step", args=["battery-2026", "ai-review"]) + "?version=v1"
    landed = _get(client, member, next="https://elsewhere.invalid/").url
    assert "elsewhere" not in landed and landed.endswith("/review/ai-review/")


LEGACY_BASIS = (
    "Read all 45.\n\nWhat changed: cost counts less.\n\n"
    "Outcome rules:\nRecommended: the top scorer per slot.\nNot recommended: otherwise."
)


class TestOutcomeRules:
    def test_rules_written_apart_stay_apart(self):
        assert queries.outcome_rules_of("How.", "Recommended: top two.") == ("How.", "Recommended: top two.")

    def test_rules_kept_inside_an_older_basis_are_read_off_its_end(self):
        basis, rules = queries.outcome_rules_of(LEGACY_BASIS)
        assert basis == "Read all 45.\n\nWhat changed: cost counts less."
        assert rules == "Recommended: the top scorer per slot.\nNot recommended: otherwise."

    def test_a_basis_without_rules_is_left_whole(self):
        assert queries.outcome_rules_of("Read all three.") == ("Read all three.", "")

    def test_the_criteria_step_shows_them_under_their_own_heading(self, client, round_, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria(
            "battery-2026", CRITERIA, "Read all three.", "Recommended: top one."
        )
        response = _get(client, member, "criteria")
        assert response.context["shown_outcome_rules"] == "Recommended: top one."
        assert response.context["shown_basis"] == "Read all three."
        assert 'data-testid="outcome-rules"' in response.content.decode()

    def test_an_older_round_shows_its_rules_apart_too(self, client, round_, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, LEGACY_BASIS)
        response = _get(client, member, "criteria")
        assert response.context["shown_outcome_rules"].startswith("Recommended: the top scorer")
        assert "Outcome rules:" not in response.context["shown_basis"]


class TestAIObservations:
    @pytest.fixture
    def two_versions(self, scored, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, "Reweighted.")
        return SolicitationsDataAccess(user=ace)

    def test_written_on_a_version_and_shown_on_ai_analysis(self, client, two_versions, member, ace):
        two_versions.set_round_observations("battery-2026", "v2", "Cost did not separate anyone.", "v1")
        response = _get(client, member, "ai-analysis")
        page = response.content.decode()
        assert response.context["shown_record"]["observations"] == "Cost did not separate anyone."
        assert 'data-testid="observations-text"' in page and "compared with version 1" in page

    def test_the_step_bar_says_the_current_version_has_them(self, client, scored, member, ace):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        SolicitationsDataAccess(user=ace).set_round_observations("battery-2026", "v1", "Reach decides.")
        steps = {s["key"]: s["status"] for s in _get(client, member, "criteria").context["steps"]}
        assert steps["ai-analysis"] == "First version: nothing to compare yet · AI Observations"

    def test_each_version_keeps_its_own(self, client, two_versions, member):
        two_versions.set_round_observations("battery-2026", "v1", "First pass: reach decides.")
        two_versions.set_round_observations("battery-2026", "v2", "Second pass: cost decides.", "v1")
        assert _get(client, member, "ai-analysis", version="v1").context["shown_record"]["observations"] == (
            "First pass: reach decides."
        )

    def test_none_yet_says_how_they_come_about(self, client, scored, member):
        page = _get(client, member, "ai-analysis").content.decode()
        assert 'data-testid="no-observations"' in page and 'data-testid="observations-text"' not in page

    def test_empty_text_clears_them(self, two_versions):
        two_versions.set_round_observations("battery-2026", "v2", "Something.", "v1")
        record = two_versions.set_round_observations("battery-2026", "v2", "  ")
        assert (record.observations, record.observations_compared_with, record.observations_at) == ("", "", None)

    def test_only_the_rounds_organisation_writes_them(self, two_versions, outsider):
        from django.core.exceptions import PermissionDenied

        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        with pytest.raises(PermissionDenied):
            SolicitationsDataAccess(user=outsider).set_round_observations("battery-2026", "v2", "Mine.")

    def test_they_name_a_real_other_version(self, two_versions):
        from django.core.exceptions import ValidationError

        from connect_labs.solicitations.local_models import RubricVersion

        with pytest.raises(RubricVersion.DoesNotExist):
            two_versions.set_round_observations("battery-2026", "v9", "Text.")
        for bad in ("v2", "v7"):
            with pytest.raises(ValidationError):
                two_versions.set_round_observations("battery-2026", "v2", "Text.", bad)

    def test_the_scores_tool_returns_them_and_reads_the_ai_alone_when_asked(self, scored, member, ace):
        from connect_labs.mcp.tool_registry import get_tool
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(
            submission.pk, {"based_on": ai.pk, "stance": "disagrees", "outcome": "waitlist"}
        )
        SolicitationsDataAccess(user=ace).set_round_observations("battery-2026", "v1", "Reach decides.")
        scores = get_tool("marketplace_round_scores").handler
        lead = scores(user=member, slug="battery-2026")
        ai_only = scores(user=member, slug="battery-2026", reviews="ai")
        assert lead["ai_observations"]["text"] == "Reach decides."
        assert lead["outcomes"]["waitlist"] == 1 and ai_only["outcomes"]["pick"] == 1
        assert ai_only["reviews"] == "ai"


class TestAgreement:
    def test_a_person_who_agrees_lands_on_the_ais_outcome(self, client, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(submission.pk, {"based_on": ai.pk, "stance": "agrees"})
        response = _get(client, member, "human-review")
        counts = response.context["human_counts"]
        assert (counts["compared"], counts["same_outcome"], counts["agreement"]) == (1, 1, 100)
        fenwick = next(r for r in response.context["human_rows"] if r["applicant"]["name"] == "Fenwick Trust")
        assert fenwick["people"][0]["outcome"] == "pick", "an agreeing review reads as the AI's outcome"
        assert 'data-testid="agreement"' in response.content.decode()

    def test_a_different_outcome_is_not_agreement(self, client, scored, member):
        from connect_labs.solicitations.data_access import SolicitationsDataAccess

        submission, ai = scored
        SolicitationsDataAccess(user=member).create_review(
            submission.pk, {"based_on": ai.pk, "stance": "disagrees", "outcome": "waitlist"}
        )
        counts = _get(client, member, "human-review").context["human_counts"]
        assert (counts["compared"], counts["same_outcome"], counts["agreement"]) == (1, 0, 0)

    def test_nobody_checked_has_no_figure(self, client, scored, member):
        assert _get(client, member, "human-review").context["human_counts"]["agreement"] is None
