"""The 4 review MCP tools, on the labs database: only a call's owner reviews it."""

import pytest

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.solicitations.tests.factories import (
    grant,
    make_call,
    make_org,
    make_response,
    make_review,
    make_user,
)


def pat(user):
    _, raw = MCPAccessToken.create_token(user, name="t")
    return raw


def ok(envelope):
    assert envelope["result"]["isError"] is False, envelope
    return envelope["result"]["structuredContent"]


def err(envelope):
    return envelope["result"]["structuredContent"]["error"]["code"]


@pytest.fixture
def world(db):
    owner_org = make_org("owner-org")
    owner = make_user("owner")
    grant(owner, owner_org)
    applicant = make_user("applicant")
    response = make_response(make_call(owner_org), applicant)
    return {
        "owner": owner,
        "owner_pat": pat(owner),
        "applicant": applicant,
        "applicant_pat": pat(applicant),
        "outsider_pat": pat(make_user("outsider")),
        "owner_org": owner_org,
        "response": response,
    }


def test_registered_with_write_flags():
    for name in ("create_review", "update_review"):
        assert get_tool(name).is_write is True
    for name in ("list_reviews", "get_review"):
        assert get_tool(name).is_write is False


@pytest.mark.django_db
def test_create_review_requires_acknowledgment(world):
    envelope = call_tool(
        world["owner_pat"],
        "create_review",
        {"public_record_acknowledged": False, "response_id": world["response"].pk, "llo_entity_id": "x"},
    )
    assert err(envelope) == "POLICY_VIOLATION"


@pytest.mark.django_db
def test_create_review_shape_and_weighted_score(world):
    content = ok(
        call_tool(
            world["owner_pat"],
            "create_review",
            {
                "public_record_acknowledged": True,
                "response_id": world["response"].pk,
                "llo_entity_id": "individual",
                "score": 10,
                "recommendation": "approved",
                "criteria_scores": {"c1": 10, "c2": 5},
                "notes": "Strong methodology.",
                "tags": "shortlist",
            },
        )
    )
    assert content["type"] == "solicitation_review"
    assert content["labs_record_id"] == world["response"].pk
    assert content["response_id"] == world["response"].pk
    assert content["score"] == 80  # weighted from criteria, not the 10 sent
    assert content["reviewer_username"] == "owner"
    assert content["recommendation"] == "approved"


@pytest.mark.django_db
def test_second_create_updates_the_same_review(world):
    args = {"public_record_acknowledged": True, "response_id": world["response"].pk, "llo_entity_id": "x"}
    first = ok(call_tool(world["owner_pat"], "create_review", {**args, "notes": "first"}))
    second = ok(call_tool(world["owner_pat"], "create_review", {**args, "recommendation": "rejected"}))
    assert first["id"] == second["id"]
    assert second["notes"] == "first"


@pytest.mark.django_db
def test_respondent_and_outsider_cannot_review(world):
    args = {"public_record_acknowledged": True, "response_id": world["response"].pk, "llo_entity_id": "x"}
    assert err(call_tool(world["applicant_pat"], "create_review", args)) in ("PERMISSION_DENIED", "NOT_FOUND")
    assert err(call_tool(world["outsider_pat"], "create_review", args)) == "NOT_FOUND"
    grant(world["applicant"], world["owner_org"])  # even as a member of the owner
    assert err(call_tool(world["applicant_pat"], "create_review", args)) == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_list_and_get_reviews_only_for_owner(world):
    review = make_review(world["response"], world["owner"], score=55)
    listed = ok(call_tool(world["owner_pat"], "list_reviews", {"response_id": world["response"].pk}))
    assert [r["id"] for r in listed["reviews"]] == [review.pk]
    assert (
        ok(call_tool(world["applicant_pat"], "list_reviews", {"response_id": world["response"].pk}))["reviews"] == []
    )
    assert ok(call_tool(world["owner_pat"], "get_review", {"review_id": review.pk}))["score"] == 55
    assert err(call_tool(world["applicant_pat"], "get_review", {"review_id": review.pk})) == "NOT_FOUND"


@pytest.mark.django_db
def test_update_review_merges(world):
    review = make_review(world["response"], world["owner"], score=55, notes="keep me")
    content = ok(
        call_tool(
            world["owner_pat"],
            "update_review",
            {"review_id": review.pk, "update_data": {"recommendation": "approved", "is_public": True}},
        )
    )
    assert content["recommendation"] == "approved"
    assert content["notes"] == "keep me"
    assert err(call_tool(world["owner_pat"], "update_review", {"review_id": 999_999_999, "update_data": {}})) == (
        "NOT_FOUND"
    )


@pytest.mark.django_db
def test_only_the_reviewer_updates(world):
    review = make_review(world["response"], world["owner"], score=55)
    colleague = make_user("colleague")
    grant(colleague, world["owner_org"])
    envelope = call_tool(pat(colleague), "update_review", {"review_id": review.pk, "update_data": {"notes": "x"}})
    assert err(envelope) == "PERMISSION_DENIED"
