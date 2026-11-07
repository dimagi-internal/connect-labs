"""A round's AI reviews are proposed by an agent and written only by a person's Confirm
(mcp/tools/review_batch.py). All data invented."""

from unittest.mock import patch

import pytest

from connect_labs.labs import canopy
from connect_labs.labs.models import LabsOrg
from connect_labs.mcp.models import StagedEdit
from connect_labs.mcp.testing import call_tool, mint_token
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.mcp.tools import review_batch, workflow_edit
from connect_labs.solicitations.local_models import REVIEW_AI, Solicitation, SolicitationResponse, SolicitationReview
from connect_labs.supply_chain.runner import dimagi_org

pytestmark = pytest.mark.django_db

ANSWER = "Through the customer lists of solar vendors in Tamale and Kumasi."


@pytest.fixture
def round_():
    applicant = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")
    call = Solicitation.objects.create(
        owner_org=dimagi_org(),
        slug="battery-2026",
        title="Battery scoping",
        solicitation_type="eoi",
        delivery_type="chc",
        status="closed",
        questions=[{"id": "how", "text": "How would you find households?", "column": 1}],
    )
    for row, name in enumerate(("Fenwick Trust", "Quietwater Collective"), start=2):
        SolicitationResponse.objects.create(
            solicitation=call,
            llo_entity=applicant if row == 2 else None,
            org_name=name,
            source_row=row,
            match_state=SolicitationResponse.MATCH_EMAIL,
            responses={"how": ANSWER},
        )
    return call


@pytest.fixture
def member(django_user_model):
    user = django_user_model.objects.create_user(username="member", password="x", email="m@dimagi-associate.com")
    return user, mint_token(user, name="t")[1]


@pytest.fixture
def outsider(django_user_model):
    user = django_user_model.objects.create_user(username="outsider", password="x", email="o@partner.invalid")
    return user, mint_token(user, name="t")[1]


def _ok(response):
    result = response["result"]
    assert not result["isError"], result
    return result["structuredContent"]


def _error(response):
    result = response["result"]
    assert result["isError"], result
    return result["structuredContent"]["error"]


def _batch(round_, quote="customer lists of solar vendors", **extra):
    first, second = list(round_.responses.order_by("source_row"))
    review = {
        "claims": [
            {
                "id": "c1",
                "about": "strength",
                "basis": "quoted",
                "text": "Reaches households through vendors.",
                "cites": [{"question": "how", "quote": quote}],
            }
        ],
        "provenance": {"model": "test", "skill": "eoi-round-review"},
    }
    return {
        "round_slug": round_.slug,
        "reviews": [
            {**review, "response_id": first.pk, "outcome": "pick", "flags": []},
            {**review, "response_id": second.pk, "outcome": "pass", "flags": ["padded"]},
        ],
        **extra,
    }


def _propose(raw, arguments, tool="marketplace_reviews_write"):
    return call_tool(raw, "workflow_edit_propose", {"tool": tool, "arguments": arguments})


def test_a_proposal_writes_nothing_and_groups_what_would_be_written(round_, member):
    _, raw = member
    out = _ok(_propose(raw, _batch(round_)))
    assert out["state"] == "pending"
    assert not SolicitationReview.objects.exists()
    labels = [c["label"] for c in StagedEdit.objects.get(pk=out["change_id"]).preview["changes"]]
    assert labels == ["Recommended (1)", "Not recommended (1)"]


def test_the_card_names_a_response_with_no_name_of_its_own(round_, member):
    """Found on the live RUTF round: its responses have an empty org_name, so the card lines were blank."""
    _, raw = member
    first, second = list(round_.responses.order_by("source_row"))
    SolicitationResponse.objects.filter(pk=first.pk).update(org_name="")
    SolicitationResponse.objects.filter(pk=second.pk).update(org_name="", llo_entity=None)
    out = _ok(_propose(raw, _batch(round_)))
    changes = StagedEdit.objects.get(pk=out["change_id"]).preview["changes"]
    lines = "\n".join(c["diff"] for c in changes)
    assert "+ Fenwick Trust --" in lines, "named by the directory organisation it matched"
    assert f"+ response {second.pk} --" in lines, "and by its id when nothing names it"
    assert "+  --" not in lines


def test_confirm_writes_each_review_as_the_persons_ai_pass_once(round_, member):
    user, raw = member
    change_id = _ok(_propose(raw, _batch(round_, summary_url="https://docs.example/report")))["change_id"]
    card = _ok(call_tool(raw, "workflow_edit_review_view", {"change_id": change_id}))
    out = _ok(call_tool(raw, "workflow_edit_apply", {"change_id": change_id, "confirm": card["confirm"]}))
    assert out["state"] == "applied" and out["result"]["written"] == 2
    reviews = SolicitationReview.objects.filter(response__solicitation=round_)
    assert reviews.count() == 2
    assert {r.kind for r in reviews} == {REVIEW_AI} and {r.reviewer_id for r in reviews} == {user.pk}
    round_.refresh_from_db()
    assert round_.summary_url == "https://docs.example/report"
    again = call_tool(raw, "workflow_edit_apply", {"change_id": change_id, "confirm": card["confirm"]})
    assert _error(again)["code"] == "CONFLICT"
    assert SolicitationReview.objects.count() == 2, "a confirmed batch writes once"


def test_declining_writes_nothing(round_, member):
    _, raw = member
    change_id = _ok(_propose(raw, _batch(round_)))["change_id"]
    card = _ok(call_tool(raw, "workflow_edit_review_view", {"change_id": change_id}))
    assert _ok(call_tool(raw, "workflow_edit_decline", {"change_id": change_id, "confirm": card["confirm"]}))
    assert not SolicitationReview.objects.exists()


def test_a_quote_not_in_the_answer_stops_the_whole_proposal(round_, member):
    _, raw = member
    err = _error(_propose(raw, _batch(round_, quote="a phrase the applicant never wrote")))
    assert err["code"] == "BAD_REQUEST" and "nothing was proposed" in err["message"]
    assert not StagedEdit.objects.exists() and not SolicitationReview.objects.exists()


def test_a_response_from_another_round_is_refused(round_, member):
    _, raw = member
    other = Solicitation.objects.create(
        owner_org=dimagi_org(), slug="other-2026", title="Other", solicitation_type="eoi", delivery_type="chc"
    )
    stray = SolicitationResponse.objects.create(
        solicitation=other, org_name="Stray", source_row=2, match_state=SolicitationResponse.MATCH_EMAIL
    )
    batch = _batch(round_)
    batch["reviews"][0]["response_id"] = stray.pk
    err = _error(_propose(raw, batch))
    assert err["code"] == "INVALID_SCHEMA" and "not a response to battery-2026" in err["message"]


def test_a_field_a_review_does_not_take_is_refused(round_, member):
    _, raw = member
    batch = _batch(round_)
    batch["reviews"][0]["kind"] = "human"
    assert _error(_propose(raw, batch))["code"] == "INVALID_SCHEMA"


def test_someone_outside_the_owning_organisation_cannot_propose(round_, outsider):
    _, raw = outsider
    assert _error(_propose(raw, _batch(round_)))["code"] == "PERMISSION_DENIED"
    assert not StagedEdit.objects.exists()


@pytest.mark.parametrize(
    "scopes,tool,allowed",
    [
        ({"marketplace:review"}, "marketplace_reviews_write", True),
        ({"marketplace:review"}, "marketplace_round_criteria_set", True),
        ({"marketplace:review"}, "marketplace_round_observations_set", True),
        ({"marketplace:review"}, "workflow_update_definition", False),
        ({"workflow:edit"}, "marketplace_reviews_write", False),
        ({"workflow:edit"}, "workflow_update_definition", True),
        ({"marketplace:read", "reviews:read"}, "marketplace_reviews_write", False),
    ],
)
def test_a_tokens_scope_decides_which_proposals_it_may_make(scopes, tool, allowed):
    with patch.object(workflow_edit, "_token_scopes", return_value=frozenset(scopes)):
        assert (workflow_edit.scope_refusal(tool) is None) is allowed


def test_a_persons_own_connection_is_not_narrowed_by_scope():
    assert workflow_edit.scope_refusal("marketplace_reviews_write") is None


def test_the_write_tools_are_writes_and_never_in_a_delegated_scope():
    for name in review_batch.REVIEW_TOOLS:
        spec = get_tool(name)
        assert spec is not None and spec.is_write, name
    delegated = set().union(*canopy.SCOPE_TOOLS.values())
    assert not (review_batch.REVIEW_TOOLS & delegated), "reached through a proposal only"


def test_the_round_page_grants_the_review_scopes_to_its_owner_only_tools():
    scopes = canopy.PAGE_SCOPES["marketplace:round"]
    assert {"marketplace:read", "reviews:read", "marketplace:review"} == set(scopes)
    assert canopy.SCOPE_TOOLS["reviews:read"] == {
        "marketplace_round_responses",
        "list_reviews",
        "get_review",
        "marketplace_round_scores",
    }


def test_ai_observations_are_proposed_and_written_on_confirm(round_, member):
    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    user, raw = member
    criteria = [{"id": "reach", "name": "Reach", "weight": 100}]
    SolicitationsDataAccess(user=user).set_round_criteria(round_.slug, criteria, "First.", "Recommended: the top two.")
    SolicitationsDataAccess(user=user).set_round_criteria(round_.slug, criteria, "Second.")
    arguments = {
        "slug": round_.slug,
        "version": "v2",
        "compared_with": "v1",
        "observations": "Reach did not separate anyone: 9 of 10 scored 7.\n\nCost tracks answer length.",
    }
    change_id = _ok(_propose(raw, arguments, tool="marketplace_round_observations_set"))["change_id"]
    staged = StagedEdit.objects.get(pk=change_id).preview
    assert staged["summary"] == "Write the AI Observations on battery-2026 version 2, compared with version 1"
    assert staged["changes"][0]["diff"].startswith("+ Reach did not separate anyone")
    assert not round_.rubric_versions.get(version="v2").observations, "nothing written before Confirm"
    card = _ok(call_tool(raw, "workflow_edit_review_view", {"change_id": change_id}))
    out = _ok(call_tool(raw, "workflow_edit_apply", {"change_id": change_id, "confirm": card["confirm"]}))
    assert out["state"] == "applied"
    record = round_.rubric_versions.get(version="v2")
    assert record.observations.startswith("Reach did not") and record.observations_compared_with == "v1"
    assert record.observations_by == user


def test_the_criteria_card_shows_the_outcome_rules(round_, member):
    _, raw = member
    arguments = {
        "slug": round_.slug,
        "criteria": [{"id": "reach", "name": "Reach", "weight": 100}],
        "outcome_rules": "Recommended: the top two.\nNot recommended: otherwise.",
    }
    change_id = _ok(_propose(raw, arguments, tool="marketplace_round_criteria_set"))["change_id"]
    changes = StagedEdit.objects.get(pk=change_id).preview["changes"]
    assert [c["label"] for c in changes] == ["criteria", "outcome rules"]
    assert changes[1]["diff"] == "+ Recommended: the top two.\n+ Not recommended: otherwise."
