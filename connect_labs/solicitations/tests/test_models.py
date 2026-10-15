"""The local solicitation tables: the proxy vocabulary they keep, and what they add."""

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.solicitations.local_models import (
    LOCAL_ID_FLOOR,
    SOURCE_DIRECTORY,
    Solicitation,
    SolicitationResponse,
    weighted_score,
)
from connect_labs.solicitations.tests.factories import make_call, make_org, make_response, make_review, make_user

pytestmark = pytest.mark.django_db


def test_slug_generated_and_unique_for_web_calls():
    a = make_call(title="Same title")
    b = make_call(title="Same title")
    assert a.slug.startswith("same-title-")
    assert a.slug != b.slug


def test_directory_slug_is_kept():
    call = Solicitation.objects.create(slug="eoi-2025-chc", title="CHC EOI")
    assert call.slug == "eoi-2025-chc"
    assert call.source == SOURCE_DIRECTORY


def test_ids_start_above_prod_record_ids():
    assert make_call().pk >= LOCAL_ID_FLOOR
    assert make_response(make_call()).pk >= LOCAL_ID_FLOOR


def test_deadline_closes_a_call():
    today = timezone.localdate()
    open_call = make_call(application_deadline=today)
    past = make_call(application_deadline=today - timedelta(days=1))
    assert open_call.can_accept_responses
    assert open_call.can_accept_responses() is True  # the proxy's method form still works
    assert not past.can_accept_responses
    assert past.can_accept_responses() is False
    assert past.status_label == "Closed — deadline passed"
    assert not make_call(status="draft").can_accept_responses


def test_proxy_vocabulary():
    org = make_org("owner-org", name="Owner Org")
    call = make_call(org, listed=True, program_id=25, criteria_locked_at=timezone.now())
    assert call.is_public is True
    assert call.criteria_locked is True
    assert call.owner_name == "Owner Org"
    assert call.type_label == "Request for Proposal"
    record = call.to_record_dict()
    assert record["id"] == call.pk
    assert record["type"] == "solicitation"
    assert record["program_id"] == 25
    assert record["experiment"] == "25"
    assert record["is_public"] is True
    assert record["owner_org"] == "owner-org"
    assert record["title"] == call.title
    assert record["criteria_locked"] is True


def test_response_vocabulary():
    user = make_user("applicant")
    org = make_org("firm-org", name="Firm Org")
    response = make_response(make_call(), user, llo_entity=org, reward_budget=500)
    assert response.blind_label == f"Response #{response.pk}"
    assert response.submitted_by_username == "applicant"
    assert response.display_name == "Firm Org"
    assert response.award_stage == "verification"
    assert response.source_row is None
    record = response.to_record_dict()
    assert record["type"] == "solicitation_response"
    assert record["labs_record_id"] == response.solicitation_id
    assert record["llo_entity_id"] == "firm-org"
    assert record["reward_budget"] == 500


def test_many_web_responses_without_source_row():
    call = make_call()
    make_response(call)
    make_response(call)
    assert call.responses.count() == 2


def test_review_vocabulary():
    reviewer = make_user("reviewer")
    review = make_review(make_response(make_call()), reviewer, score=70, criteria_scores={"c1": 7})
    assert review.reviewer_username == "reviewer"
    imported = make_review(review.response, None, reviewer_name="someone")
    assert imported.reviewer_username == "someone"
    assert review.to_record_dict()["labs_record_id"] == review.response_id


def test_weighted_score():
    criteria = [{"id": "c1", "weight": 60}, {"id": "c2", "weight": 40}]
    assert weighted_score({"c1": 10, "c2": 5}, criteria) == 80
    assert weighted_score({"c1": 7}, criteria) == 70  # unscored criteria left out
    assert weighted_score({}, criteria) is None
    assert weighted_score({"zz": 9}, criteria) is None


def test_marketplace_reads_only_directory_rounds():
    """A response to an owned call is confidential: the marketplace (any signed-in user) never sees it."""
    from connect_labs.marketplace import queries

    org = make_org("applicant-org")
    round_ = Solicitation.objects.create(slug="eoi-round", title="EOI round")
    SolicitationResponse.objects.create(solicitation=round_, source_row=2, llo_entity=org)
    call = make_call(make_org("owner-org"))
    make_response(call, llo_entity=org)

    assert list(queries.rounds_with_counts().values_list("slug", flat=True)) == ["eoi-round"]
    assert queries.network_totals()["applications"] == 1
    assert SolicitationResponse.objects.directory_rounds().count() == 1
    assert org.solicitation_responses.count() == 2  # the relation itself is unchanged
