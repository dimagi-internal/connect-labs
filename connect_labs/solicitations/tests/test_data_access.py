"""SolicitationsDataAccess on the labs database: standing enforced at the choke point."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.utils import timezone

from connect_labs.solicitations.data_access import HasResponses, SolicitationsDataAccess
from connect_labs.solicitations.local_models import SOURCE_LABS, Solicitation, SolicitationResponse
from connect_labs.solicitations.tests.factories import (
    CRITERIA,
    QUESTIONS,
    grant,
    make_call,
    make_org,
    make_response,
    make_review,
    make_user,
)

pytestmark = pytest.mark.django_db


def da(user=None, **kw):
    return SolicitationsDataAccess(user=user, **kw)


@pytest.fixture
def world():
    owner_org = make_org("owner-org", name="Owner Org")
    owner = make_user("owner")
    grant(owner, owner_org)
    firm_org = make_org("firm-org", name="Firm Org")
    applicant = make_user("applicant")
    grant(applicant, firm_org)
    return {
        "owner_org": owner_org,
        "owner": owner,
        "firm_org": firm_org,
        "applicant": applicant,
        "outsider": make_user("outsider"),
        "staff": make_user("support", staff=True),
    }


def payload(**kw):
    data = {
        "title": "Survey firm wanted",
        "description": "A household survey.",
        "solicitation_type": "rfp",
        "status": "active",
        "questions": QUESTIONS,
        "evaluation_criteria": CRITERIA,
    }
    data.update(kw)
    return data


# --- construction ------------------------------------------------------------


def test_no_token_needed():
    access = SolicitationsDataAccess()
    assert access.access_token is None
    assert access.user is None


def test_request_supplies_user_token_and_context(world):
    request = MagicMock()
    request.user = world["owner"]
    request.session = {"labs_oauth": {"access_token": "tok", "expires_at": timezone.now().timestamp() + 60}}
    request.labs_context = {"program_id": 25, "organization_id": "owner-org"}
    access = SolicitationsDataAccess(request=request)
    assert access.user == world["owner"]
    assert access.access_token == "tok"
    assert access.program_id == 25
    assert access.organization_id == "owner-org"


# --- create ------------------------------------------------------------------


def test_create_owned_by_named_org(world):
    call = da(world["owner"]).create_solicitation(payload(org_slug="owner-org", is_public=True, program_id="25"))
    assert call.owner_org == world["owner_org"]
    assert call.source == SOURCE_LABS
    assert call.listed is True
    assert call.program_id == 25
    assert call.created_by == world["owner"]
    assert call.slug


def test_create_refused_for_non_member(world):
    with pytest.raises(PermissionDenied):
        da(world["outsider"]).create_solicitation(payload(org_slug="owner-org"))


def test_staff_creates_for_any_org(world):
    call = da(world["staff"]).create_solicitation(payload(org_slug="owner-org"))
    assert call.owner_org == world["owner_org"]


def test_owner_from_programme_runner(world):
    from connect_labs.supply_chain.runner import declare

    declare(77, world["owner_org"])
    call = da(world["owner"], program_id=77).create_solicitation(payload())
    assert call.owner_org == world["owner_org"]
    assert call.program_id == 77


def test_programme_without_runner_names_org_slug(world):
    with pytest.raises(ValidationError) as exc:
        da(world["owner"], program_id=78).create_solicitation(payload())
    assert "org_slug" in str(exc.value)


def test_no_owner_at_all_names_org_slug(world):
    with pytest.raises(ValidationError, match="org_slug"):
        da(world["owner"]).create_solicitation(payload())


def test_create_validates_payload(world):
    with pytest.raises(ValidationError):
        da(world["owner"]).create_solicitation(payload(org_slug="owner-org", overview="drift"))


def test_create_locks_rubric(world):
    call = da(world["owner"]).create_solicitation(payload(org_slug="owner-org", criteria_locked=True))
    assert call.criteria_locked


# --- read --------------------------------------------------------------------


def test_get_by_id_hides_unreadable(world):
    hidden = make_call(world["owner_org"], listed=False)
    assert da(world["outsider"]).get_solicitation_by_id(hidden.pk) is None
    assert da(world["owner"]).get_solicitation_by_id(hidden.pk) == hidden
    assert da().get_solicitation_by_id(make_call(world["owner_org"]).pk) is not None  # anonymous, listed


def test_get_by_id_prefers_legacy_record_id(world):
    imported = make_call(world["owner_org"], legacy_record_id=2841)
    assert da().get_solicitation_by_id(2841) == imported
    assert da().get_solicitation_by_id(imported.pk) == imported


def test_public_list_and_open_only(world):
    today = timezone.localdate()
    open_call = make_call(world["owner_org"], application_deadline=today + timedelta(days=3))
    past = make_call(world["owner_org"], application_deadline=today - timedelta(days=1))
    make_call(world["owner_org"], listed=False)
    make_call(world["owner_org"], status="draft")
    Solicitation.objects.create(slug="directory-round", title="EOI", status="active", listed=True)
    assert set(da().get_public_solicitations()) == {open_call, past}
    assert da().get_public_solicitations(open_only=True) == [open_call]
    assert da().get_public_solicitations(owner="firm-org") == []


def test_get_solicitations_lists_managed_calls(world):
    mine = make_call(world["owner_org"], listed=False)
    other = make_call(make_org("other-org"))
    assert da(world["owner"]).get_solicitations() == [mine]
    assert set(da(world["staff"]).get_solicitations()) == {mine, other}
    assert da(world["outsider"]).get_solicitations() == []
    assert da(world["outsider"]).get_solicitations(include_listed=True) == [other]
    assert da(world["outsider"]).get_solicitations(org="owner-org") == []
    assert da(world["owner"]).get_solicitations(org="owner-org") == [mine]


def test_by_fund_id(world):
    call = make_call(world["owner_org"], fund_id=42)
    make_call(world["owner_org"], fund_id=42, listed=False)
    assert da().get_solicitations_by_fund_id(42) == [call]
    assert len(da(world["owner"]).get_solicitations_by_fund_id(42)) == 2


# --- update ------------------------------------------------------------------


def test_update_merges(world):
    call = make_call(world["owner_org"], estimated_scale="500 households", program_name="Programme A")
    updated = da(world["owner"]).update_solicitation(call.pk, {"title": "New title"})
    assert updated.title == "New title"
    assert updated.estimated_scale == "500 households"
    assert updated.program_name == "Programme A"
    assert updated.questions == QUESTIONS


def test_update_refused_for_non_owner(world):
    call = make_call(world["owner_org"])
    with pytest.raises(PermissionDenied):
        da(world["outsider"]).update_solicitation(call.pk, {"title": "Hijacked"})


def test_update_missing_call(world):
    with pytest.raises(Solicitation.DoesNotExist):
        da(world["owner"]).update_solicitation(999_999_999, {"title": "x"})


def test_is_public_flips_listed(world):
    call = make_call(world["owner_org"], listed=False)
    assert da(world["owner"]).update_solicitation(call.pk, {"is_public": True}).listed is True


def test_locked_rubric_cannot_change(world):
    call = make_call(world["owner_org"], criteria_locked_at=timezone.now())
    owner = da(world["owner"])
    with pytest.raises(ValidationError, match="locked"):
        owner.update_solicitation(call.pk, {"evaluation_criteria": [{"id": "c1", "name": "Only", "weight": 100}]})
    with pytest.raises(ValidationError, match="locked"):
        owner.update_solicitation(call.pk, {"criteria_locked": False})
    with pytest.raises(ValidationError, match="locked"):
        da(world["staff"]).update_solicitation(call.pk, {"evaluation_criteria": []})
    # resending the same rubric is fine (the edit form always sends it)
    owner.update_solicitation(call.pk, {"evaluation_criteria": CRITERIA, "title": "Renamed"})


def test_update_validates_merged_shape(world):
    call = make_call(world["owner_org"])
    with pytest.raises(ValidationError):
        da(world["owner"]).update_solicitation(
            call.pk, {"evaluation_criteria": [{"id": "c9", "name": "x", "weight": 5, "linked_questions": ["nope"]}]}
        )


def test_delete_guard_and_force(world):
    call = make_call(world["owner_org"])
    make_review(make_response(call), world["owner"])
    with pytest.raises(HasResponses):
        da(world["owner"]).delete_solicitation(call.pk)
    assert da(world["owner"]).delete_solicitation(call.pk, force=True) == {
        "solicitations": 1,
        "responses": 1,
        "reviews": 1,
    }
    assert not Solicitation.objects.filter(pk=call.pk).exists()


# --- responses ---------------------------------------------------------------


def test_respond_as_own_org(world):
    call = make_call(world["owner_org"])
    response = da(world["applicant"]).create_response(
        call.pk, "firm-org", {"responses": {"q1": "Yes"}, "status": "submitted"}
    )
    assert response.submitted_by == world["applicant"]
    assert response.llo_entity == world["firm_org"]
    assert response.org_name == "Firm Org"
    assert response.status == "submitted"
    assert response.submission_date is not None
    assert response.submitted_by_email == world["applicant"].email


def test_cannot_respond_as_someone_elses_org(world):
    call = make_call(world["owner_org"])
    with pytest.raises(PermissionDenied):
        da(world["outsider"]).create_response(call.pk, "firm-org", {"status": "submitted"})


def test_unregistered_org_name_is_kept_as_typed(world):
    call = make_call(world["owner_org"])
    response = da(world["outsider"]).create_response(call.pk, None, {"org_name": "New Firm", "status": "submitted"})
    assert response.llo_entity is None
    assert response.org_name == "New Firm"


def test_one_draft_per_user_edited_in_place(world):
    call = make_call(world["owner_org"])
    access = da(world["applicant"])
    first = access.create_response(call.pk, None, {"responses": {"q1": "draft 1"}, "status": "draft"})
    second = access.create_response(call.pk, None, {"responses": {"q1": "draft 2"}, "status": "draft"})
    assert first.pk == second.pk
    submitted = access.create_response(call.pk, None, {"responses": {"q1": "final"}, "status": "submitted"})
    assert submitted.pk == first.pk
    assert submitted.status == "submitted"
    assert call.responses.count() == 1


def test_deadline_refuses_responses(world):
    call = make_call(world["owner_org"], application_deadline=timezone.localdate() - timedelta(days=1))
    with pytest.raises(PermissionDenied, match="deadline"):
        da(world["applicant"]).create_response(call.pk, None, {"status": "submitted"})


def test_anonymous_cannot_respond(world):
    with pytest.raises(PermissionDenied):
        da().create_response(make_call(world["owner_org"]).pk, None, {"status": "submitted"})


def test_on_behalf_by_owner_only(world):
    call = make_call(world["owner_org"], status="closed")
    response = da(world["owner"]).create_response(
        call.pk, "firm-org", {"status": "submitted", "ai_proposed_scores": {"c1": {"score": 7}}}, on_behalf=True
    )
    assert response.submitted_by is None
    assert response.llo_entity == world["firm_org"]
    assert response.ai_proposed_scores == {"c1": {"score": 7}}
    with pytest.raises(PermissionDenied):
        da(world["outsider"]).create_response(call.pk, None, {"status": "submitted"}, on_behalf=True)


def test_responses_confidential_to_owner(world):
    call = make_call(world["owner_org"])
    theirs = make_response(call, world["applicant"])
    mine = make_response(call, world["outsider"])
    draft = make_response(call, make_user("drafter"), status="draft")
    assert set(da(world["owner"]).get_responses_for_solicitation(call.pk)) == {theirs, mine}
    assert da(world["outsider"]).get_responses_for_solicitation(call.pk) == [mine]
    assert da().get_responses_for_solicitation(call.pk) == []
    assert da(world["outsider"]).get_response_by_id(theirs.pk) is None
    assert da(world["owner"]).get_response_by_id(draft.pk) is None
    assert da(world["applicant"]).get_response_by_id(theirs.pk) == theirs


def test_get_my_response(world):
    call = make_call(world["owner_org"])
    make_response(call, world["applicant"])
    draft = make_response(call, world["applicant"], status="draft")
    assert da(world["applicant"]).get_my_response(call.pk) == draft
    assert da(world["outsider"]).get_my_response(call.pk) is None


def test_respondent_edits_draft_and_owner_moves_status(world):
    call = make_call(world["owner_org"])
    response = make_response(call, world["applicant"], status="draft", responses={"q1": "a", "q2": "1"})
    updated = da(world["applicant"]).update_response(response.pk, {"responses": {"q1": "b", "q2": "1"}})
    assert updated.responses["q1"] == "b"
    submitted = da(world["applicant"]).update_response(response.pk, {"status": "submitted"})
    assert submitted.status == "submitted"
    assert submitted.responses["q1"] == "b"  # merged, not replaced
    with pytest.raises(PermissionDenied):
        da(world["applicant"]).update_response(response.pk, {"responses": {"q1": "too late"}})
    assert da(world["owner"]).update_response(response.pk, {"status": "rejected"}).status == "rejected"
    with pytest.raises(ValidationError):
        da(world["owner"]).update_response(response.pk, {"status": "awarded"})


# --- award -------------------------------------------------------------------


def test_award_records_and_does_not_claim_notification_when_email_off(world):
    call = make_call(world["owner_org"])
    response = make_response(call, world["applicant"])
    awarded = da(world["owner"]).award_response(response.pk, reward_budget=5000, org_id="77", award_stage="full")
    assert awarded.status == "awarded"
    assert awarded.reward_budget == 5000
    assert awarded.award_stage == "full"
    assert awarded.awarded_at is not None
    assert awarded.awardee_notified is False
    call.refresh_from_db()
    assert call.status == "awarded"


@override_settings(LABS_EMAIL_ENABLED=True)
def test_award_notifies_when_email_on(world):
    response = make_response(make_call(world["owner_org"], currency="KES"), world["applicant"])
    with patch("connect_labs.utils.email.send_labs_email", return_value=MagicMock()) as send:
        awarded = da(world["owner"]).award_response(response.pk, reward_budget=100)
    assert awarded.awardee_notified is True
    subject, message, recipients = send.call_args[0]
    assert recipients == ["applicant@firm.example"]
    assert "KES" in message


@override_settings(LABS_EMAIL_ENABLED=True)
def test_submission_confirms_to_respondent_and_owner(world):
    call = make_call(world["owner_org"])
    with patch("connect_labs.utils.email.send_labs_email", return_value=MagicMock()) as send:
        da(world["applicant"]).create_response(call.pk, None, {"status": "submitted"})
    sent_to = [c[0][2] for c in send.call_args_list]
    assert [world["applicant"].email] in sent_to
    assert ["calls@owner.example"] in sent_to


def test_respondent_cannot_award_own_response(world):
    grant(world["applicant"], world["owner_org"])
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    with pytest.raises(PermissionDenied):
        da(world["applicant"]).award_response(response.pk, reward_budget=1)


def test_outsider_cannot_award(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    with pytest.raises(SolicitationResponse.DoesNotExist):
        da(world["outsider"]).award_response(response.pk, reward_budget=1)


def test_award_allocates_fund_without_program(world):
    call = make_call(world["owner_org"], fund_id=9)
    response = make_response(call, world["applicant"], llo_entity=world["firm_org"])
    with patch("connect_labs.funder_dashboard.data_access.FunderDashboardDataAccess") as fda:
        da(world["owner"], access_token="tok").award_response(response.pk, reward_budget=10, org_id="77")
    allocation = fda.return_value.add_allocation.call_args.kwargs["allocation"]
    assert fda.return_value.add_allocation.call_args.kwargs["fund_id"] == 9
    assert allocation["program_id"] is None
    assert allocation["org_name"] == "Firm Org"
    assert allocation["amount"] == 10


def test_award_survives_allocation_failure(world):
    call = make_call(world["owner_org"], fund_id=9)
    response = make_response(call, world["applicant"])
    with patch("connect_labs.funder_dashboard.data_access.FunderDashboardDataAccess", side_effect=RuntimeError):
        awarded = da(world["owner"], access_token="tok").award_response(response.pk, reward_budget=10)
    assert awarded.status == "awarded"


# --- reviews -----------------------------------------------------------------


def test_review_upserts_per_reviewer_and_weights_score(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    owner = da(world["owner"])
    review = owner.create_review(response.pk, {"criteria_scores": {"c1": 10, "c2": 5}, "score": 3, "notes": "ok"})
    assert review.score == 80
    assert review.reviewer == world["owner"]
    again = owner.create_review(response.pk, {"recommendation": "approved"})
    assert again.pk == review.pk
    assert again.notes == "ok"  # merged
    assert again.recommendation == "approved"
    response.refresh_from_db()
    assert response.status == "under_review"


def test_score_without_criteria_is_kept(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    assert da(world["owner"]).create_review(response.pk, {"score": 64}).score == 64


def test_reviews_hidden_from_respondent_and_outsiders(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    review = make_review(response, world["owner"], score=50)
    assert da(world["owner"]).get_reviews_for_response(response.pk) == [review]
    assert da(world["applicant"]).get_reviews_for_response(response.pk) == []
    assert da(world["applicant"]).get_review_by_id(review.pk) is None
    assert da(world["outsider"]).get_review_by_id(review.pk) is None


def test_respondent_cannot_review_own_response(world):
    grant(world["applicant"], world["owner_org"])
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    with pytest.raises(PermissionDenied):
        da(world["applicant"]).create_review(response.pk, {"score": 99})


def test_only_reviewer_or_staff_updates_a_review(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    review = make_review(response, world["owner"], score=50)
    colleague = make_user("colleague")
    grant(colleague, world["owner_org"])
    with pytest.raises(PermissionDenied):
        da(colleague).update_review(review.pk, {"notes": "mine now"})
    assert da(world["staff"]).update_review(review.pk, {"notes": "support fix"}).notes == "support fix"
