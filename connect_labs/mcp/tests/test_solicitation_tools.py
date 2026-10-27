"""The 9 solicitation MCP tools, on the labs database, run as the caller.

Names, argument schemas and output shapes are the contract; standing is
enforced underneath by SolicitationsDataAccess.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.testing import call_tool, mint_token
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.solicitations.local_models import Solicitation
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

TOOLS = (
    "list_solicitations",
    "get_solicitation",
    "list_responses",
    "get_response",
    "create_solicitation",
    "create_response",
    "update_solicitation",
    "delete_solicitation",
    "award_response",
)


def pat(user):
    _, raw = mint_token(user, name="t")
    return raw


def ok(envelope):
    assert envelope["result"]["isError"] is False, envelope
    return envelope["result"]["structuredContent"]


def err(envelope):
    return envelope["result"]["structuredContent"]["error"]["code"]


@pytest.fixture
def world(db):
    owner_org = make_org("owner-org", name="Owner Org")
    owner = make_user("owner")
    grant(owner, owner_org)
    applicant = make_user("applicant")
    firm = make_org("firm-org", name="Firm Org")
    grant(applicant, firm)
    return {
        "owner_org": owner_org,
        "owner": owner,
        "owner_pat": pat(owner),
        "applicant": applicant,
        "applicant_pat": pat(applicant),
        "outsider_pat": pat(make_user("outsider")),
        "staff": make_user("support", staff=True),
    }


def test_all_nine_registered_with_write_flags():
    for name in TOOLS:
        assert get_tool(name) is not None, name
    for name in (
        "create_solicitation",
        "create_response",
        "update_solicitation",
        "award_response",
        "delete_solicitation",
    ):
        assert get_tool(name).is_write is True
    for name in ("list_solicitations", "get_solicitation", "list_responses", "get_response"):
        assert get_tool(name).is_write is False


def test_create_solicitation_schema_keeps_old_args_and_adds_org_slug():
    props = get_tool("create_solicitation").input_schema["properties"]
    for name in ("org_slug", "program_id", "organization_id", "is_public", "title", "questions", "currency"):
        assert name in props
    assert get_tool("create_solicitation").input_schema["required"] == ["title", "description", "solicitation_type"]


def test_read_tools_stay_on_the_restricted_endpoint():
    from connect_labs.mcp.token_scopes import RESTRICTED_TOOLS

    for name in ("list_solicitations", "get_solicitation", "list_responses", "get_response"):
        assert name in RESTRICTED_TOOLS


# --- reads -------------------------------------------------------------------


@pytest.mark.django_db
def test_list_solicitations_shape_and_scope(world):
    mine = make_call(world["owner_org"], listed=False, program_id=25)
    listed = make_call(make_org("other-org"), program_id=26)
    content = ok(call_tool(world["owner_pat"], "list_solicitations", {}))
    ids = {s["id"] for s in content["solicitations"]}
    assert ids == {mine.pk, listed.pk}
    first = next(s for s in content["solicitations"] if s["id"] == mine.pk)
    for key in ("id", "experiment", "type", "program_id", "labs_record_id", "title", "status", "is_public"):
        assert key in first
    assert first["owner_org"] == "owner-org"

    outsider = ok(call_tool(world["outsider_pat"], "list_solicitations", {}))
    assert [s["id"] for s in outsider["solicitations"]] == [listed.pk]

    by_program = ok(call_tool(world["owner_pat"], "list_solicitations", {"program_id": "25"}))
    assert [s["id"] for s in by_program["solicitations"]] == [mine.pk]
    by_org = ok(call_tool(world["owner_pat"], "list_solicitations", {"organization_id": "owner-org"}))
    assert [s["id"] for s in by_org["solicitations"]] == [mine.pk]
    filtered = ok(call_tool(world["owner_pat"], "list_solicitations", {"status": "closed"}))
    assert filtered["solicitations"] == []


@pytest.mark.django_db
def test_get_solicitation(world):
    hidden = make_call(world["owner_org"], listed=False)
    imported = make_call(world["owner_org"], legacy_record_id=2841)
    assert ok(call_tool(world["owner_pat"], "get_solicitation", {"solicitation_id": hidden.pk}))["id"] == hidden.pk
    assert err(call_tool(world["outsider_pat"], "get_solicitation", {"solicitation_id": hidden.pk})) == "NOT_FOUND"
    got = ok(call_tool(world["outsider_pat"], "get_solicitation", {"solicitation_id": 2841}))
    assert got["id"] == imported.pk
    assert got["legacy_record_id"] == 2841


@pytest.mark.django_db
def test_list_and_get_responses_are_confidential(world):
    call = make_call(world["owner_org"])
    response = make_response(call, world["applicant"])
    content = ok(call_tool(world["owner_pat"], "list_responses", {"solicitation_id": call.pk}))
    assert [r["id"] for r in content["responses"]] == [response.pk]
    assert content["responses"][0]["type"] == "solicitation_response"
    assert content["responses"][0]["labs_record_id"] == call.pk
    assert ok(call_tool(world["outsider_pat"], "list_responses", {"solicitation_id": call.pk}))["responses"] == []
    assert ok(call_tool(world["applicant_pat"], "get_response", {"response_id": response.pk}))["id"] == response.pk
    assert err(call_tool(world["outsider_pat"], "get_response", {"response_id": response.pk})) == "NOT_FOUND"
    assert err(call_tool(world["owner_pat"], "list_responses", {"solicitation_id": 999_999_999})) == "NOT_FOUND"


# --- create / update / delete --------------------------------------------------


def _fields(**kw):
    data = {
        "title": "Survey firm wanted",
        "description": "A survey.",
        "solicitation_type": "rfp",
        "status": "active",
        "questions": QUESTIONS,
        "evaluation_criteria": CRITERIA,
    }
    data.update(kw)
    return data


@pytest.mark.django_db
def test_create_solicitation_with_org_slug(world):
    content = ok(
        call_tool(
            world["owner_pat"],
            "create_solicitation",
            _fields(org_slug="owner-org", is_public=True, program_id="25", connect_opportunity_id=900),
        )
    )
    call = Solicitation.objects.get(pk=content["id"])
    assert call.owner_org == world["owner_org"]
    assert call.created_by == world["owner"]
    assert content["is_public"] is True
    assert content["program_id"] == 25
    assert content["connect_opportunity_id"] == 900


@pytest.mark.django_db
def test_create_solicitation_from_programme_runner(world):
    from connect_labs.supply_chain.runner import declare

    declare(31, world["owner_org"])
    content = ok(call_tool(world["owner_pat"], "create_solicitation", _fields(program_id="31")))
    assert content["owner_org"] == "owner-org"


@pytest.mark.django_db
def test_create_solicitation_programme_without_runner_names_org_slug(world):
    envelope = call_tool(world["owner_pat"], "create_solicitation", _fields(program_id="32"))
    assert err(envelope) == "INVALID_SCHEMA"
    assert "org_slug" in envelope["result"]["structuredContent"]["error"]["message"]


@pytest.mark.django_db
def test_create_solicitation_refused_for_non_member(world):
    envelope = call_tool(world["outsider_pat"], "create_solicitation", _fields(org_slug="owner-org"))
    assert err(envelope) == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_create_solicitation_rejects_drift(world):
    envelope = call_tool(
        world["owner_pat"],
        "create_solicitation",
        _fields(
            org_slug="owner-org",
            evaluation_criteria=[{"id": "c", "name": "x", "weight": 5, "linked_questions": ["nope"]}],
        ),
    )
    assert err(envelope) == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_update_solicitation_merges_and_flips_listing(world):
    call = make_call(world["owner_org"], listed=False, estimated_scale="500 households")
    content = ok(
        call_tool(
            world["owner_pat"],
            "update_solicitation",
            {"solicitation_id": call.pk, "update_data": {"title": "Renamed", "is_public": True}},
        )
    )
    assert content["title"] == "Renamed"
    assert content["estimated_scale"] == "500 households"
    assert content["is_public"] is True
    hidden = make_call(world["owner_org"], listed=False)
    assert (
        err(call_tool(world["outsider_pat"], "update_solicitation", {"solicitation_id": hidden.pk, "update_data": {}}))
        == "NOT_FOUND"
    )
    listed = make_call(world["owner_org"])
    assert (
        err(call_tool(world["outsider_pat"], "update_solicitation", {"solicitation_id": listed.pk, "update_data": {}}))
        == "PERMISSION_DENIED"
    )


@pytest.mark.django_db
def test_update_solicitation_locked_rubric(world):
    call = make_call(world["owner_org"], criteria_locked_at=timezone.now())
    envelope = call_tool(
        world["owner_pat"],
        "update_solicitation",
        {"solicitation_id": call.pk, "update_data": {"evaluation_criteria": []}},
    )
    assert err(envelope) == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_delete_solicitation_guard_and_force(world):
    call = make_call(world["owner_org"])
    make_review(make_response(call), world["owner"])
    assert err(call_tool(world["owner_pat"], "delete_solicitation", {"solicitation_id": call.pk})) == (
        "FAILED_PRECONDITION"
    )
    content = ok(call_tool(world["owner_pat"], "delete_solicitation", {"solicitation_id": call.pk, "force": True}))
    assert content == {"solicitation_id": call.pk, "deleted": {"solicitations": 1, "responses": 1, "reviews": 1}}


# --- responses and award --------------------------------------------------------


@pytest.mark.django_db
def test_create_response_by_owner_is_on_behalf(world):
    call = make_call(world["owner_org"], status="closed")
    content = ok(
        call_tool(
            world["owner_pat"],
            "create_response",
            {
                "solicitation_id": call.pk,
                "llo_entity_id": "firm-org",
                "responses": {"q1": "answer"},
                "submitted_by_name": "Firm Contact",
                "submitted_by_email": "contact@firm.example",
            },
        )
    )
    assert content["status"] == "submitted"
    assert content["llo_entity_id"] == "firm-org"
    assert content["submitted_by_username"] == ""
    assert content["org_name"] == "Firm Contact"


@pytest.mark.django_db
def test_create_response_by_applicant_is_their_own(world):
    call = make_call(world["owner_org"])
    content = ok(
        call_tool(
            world["applicant_pat"],
            "create_response",
            {"solicitation_id": call.pk, "llo_entity_id": "firm-org", "responses": {"q1": "x"}},
        )
    )
    assert content["submitted_by_username"] == "applicant"
    envelope = call_tool(
        world["outsider_pat"], "create_response", {"solicitation_id": call.pk, "llo_entity_id": "firm-org"}
    )
    assert err(envelope) == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_award_response(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    content = ok(
        call_tool(
            world["owner_pat"], "award_response", {"response_id": response.pk, "reward_budget": 500, "org_id": "7"}
        )
    )
    assert content["status"] == "awarded"
    assert content["reward_budget"] == 500
    assert content["org_id"] == "7"
    assert content["awardee_notified"] is False  # labs email is off in tests
    assert (
        err(
            call_tool(
                world["applicant_pat"],
                "award_response",
                {"response_id": response.pk, "reward_budget": 1, "org_id": "7"},
            )
        )
        == "PERMISSION_DENIED"
    )


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.solicitations._add_allocation_to_fund")
@patch("connect_labs.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_with_fund_allocation(client_cls, add_allocation, world):
    UserConnectToken.objects.create(
        user=world["owner"], access_token="connect-tok", expires_at=timezone.now() + timedelta(hours=1)
    )
    client_cls.return_value = MagicMock()
    call = make_call(world["owner_org"], title="Survey call")
    response = make_response(call, world["applicant"], org_name="Firm Ltd")
    ok(
        call_tool(
            world["owner_pat"],
            "award_response",
            {"response_id": response.pk, "reward_budget": 900, "org_id": "7", "fund_id": 55},
        )
    )
    _, fund_id, allocation = add_allocation.call_args[0]
    assert fund_id == 55
    assert allocation["amount"] == 900
    assert allocation["solicitation_id"] == call.pk
    assert allocation["org_name"] == "Firm Ltd"
    assert allocation["notes"] == "Award from Survey call"


@pytest.mark.django_db
def test_award_with_fund_needs_a_connect_token(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    envelope = call_tool(
        world["owner_pat"],
        "award_response",
        {"response_id": response.pk, "reward_budget": 1, "org_id": "7", "fund_id": 5},
    )
    assert err(envelope) == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_respondent_never_sees_owner_working_fields(world):
    call = make_call(world["owner_org"])
    response = make_response(call, world["applicant"], ai_proposed_scores={"c1": {"score": 3}}, org_id="77")
    mine = ok(call_tool(world["applicant_pat"], "get_response", {"response_id": response.pk}))
    assert "ai_proposed_scores" not in mine and "org_id" not in mine
    listed = ok(call_tool(world["applicant_pat"], "list_responses", {"solicitation_id": call.pk}))["responses"]
    assert all("ai_proposed_scores" not in r and "org_id" not in r for r in listed)
    owner = ok(call_tool(world["owner_pat"], "get_response", {"response_id": response.pk}))
    assert owner["ai_proposed_scores"] == {"c1": {"score": 3}}
    assert owner["org_id"] == "77"


@pytest.mark.django_db
def test_create_response_twice_is_refused(world):
    call = make_call(world["owner_org"])
    args = {"solicitation_id": call.pk, "llo_entity_id": "firm-org", "responses": {"q1": "x"}}
    ok(call_tool(world["applicant_pat"], "create_response", args))
    assert call_tool(world["applicant_pat"], "create_response", args)["result"]["isError"] is True
    assert call.responses.count() == 1


@pytest.mark.django_db
def test_award_response_twice_is_refused(world):
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    args = {"response_id": response.pk, "reward_budget": 500, "org_id": "7"}
    ok(call_tool(world["owner_pat"], "award_response", args))
    assert err(call_tool(world["owner_pat"], "award_response", args)) == "FAILED_PRECONDITION"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.solicitations._add_allocation_to_fund")
@patch("connect_labs.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_with_a_missing_fund_fails_before_awarding(client_cls, add_allocation, world):
    UserConnectToken.objects.create(
        user=world["owner"], access_token="connect-tok", expires_at=timezone.now() + timedelta(hours=1)
    )
    client_cls.return_value.get_record_by_id.return_value = None
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    envelope = call_tool(
        world["owner_pat"],
        "award_response",
        {"response_id": response.pk, "reward_budget": 1, "org_id": "7", "fund_id": 404},
    )
    assert err(envelope) == "NOT_FOUND"
    add_allocation.assert_not_called()
    response.refresh_from_db()
    assert response.status != "awarded"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.solicitations._add_allocation_to_fund", side_effect=RuntimeError("prod down"))
@patch("connect_labs.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_reports_a_failed_allocation_instead_of_raising(client_cls, add_allocation, world):
    UserConnectToken.objects.create(
        user=world["owner"], access_token="connect-tok", expires_at=timezone.now() + timedelta(hours=1)
    )
    client_cls.return_value = MagicMock()
    response = make_response(make_call(world["owner_org"]), world["applicant"])
    content = ok(
        call_tool(
            world["owner_pat"],
            "award_response",
            {"response_id": response.pk, "reward_budget": 1, "org_id": "7", "fund_id": 55},
        )
    )
    assert content["status"] == "awarded"
    assert "prod down" in content["allocation_error"]


@pytest.mark.django_db
def test_directory_rounds_stay_out_of_the_solicitation_tools(world):
    """Past directory rounds are shown read-only on the Solicitations pages, but
    the MCP solicitation tools are the API for calls that can be managed and
    answered: a round there would invite get/list_responses/award calls that
    must all be refused. Agents read rounds through `marketplace_rounds_list`."""
    from connect_labs.solicitations.local_models import SOURCE_DIRECTORY

    round_ = make_call(None, source=SOURCE_DIRECTORY, slug="chc-2025", status="closed", listed=False)
    submission = make_response(round_, None, source_row=2)
    staff_pat = pat(world["staff"])
    for token in (world["owner_pat"], world["outsider_pat"], staff_pat):
        listed = ok(call_tool(token, "list_solicitations", {}))
        assert round_.pk not in {s["id"] for s in listed["solicitations"]}
        assert err(call_tool(token, "get_solicitation", {"solicitation_id": round_.pk})) == "NOT_FOUND"
        assert err(call_tool(token, "list_responses", {"solicitation_id": round_.pk})) == "NOT_FOUND"
        if token is not staff_pat:
            # Platform support may read a round's submissions (to review them); nobody else here.
            assert err(call_tool(token, "get_response", {"response_id": submission.pk})) == "NOT_FOUND"


# --- demonstration awards ----------------------------------------------------------


@pytest.mark.django_db
def test_schema_exposes_synthetic_and_demonstration():
    assert "synthetic" in get_tool("create_response").input_schema["properties"]
    assert "demonstration" in get_tool("award_response").input_schema["properties"]
    assert "demonstration" in get_tool("create_review").input_schema["properties"]


@pytest.mark.django_db
def test_owner_seeds_and_demonstration_awards_its_own_side(world):
    winner = make_org("winner-org", name="Winner Org")
    grant(world["owner"], winner)
    call = make_call(world["owner_org"], listed=False)
    seeded = ok(
        call_tool(
            world["owner_pat"],
            "create_response",
            {"solicitation_id": call.pk, "llo_entity_id": "winner-org", "responses": {"q1": "x"}, "synthetic": True},
        )
    )
    assert seeded["synthetic"] is True
    args = {"response_id": seeded["id"], "reward_budget": 5, "org_id": "399"}
    assert err(call_tool(world["owner_pat"], "award_response", args)) == "PERMISSION_DENIED"
    review_args = {"public_record_acknowledged": True, "response_id": seeded["id"], "llo_entity_id": "winner-org"}
    assert err(call_tool(world["owner_pat"], "create_review", review_args)) == "PERMISSION_DENIED"
    review = ok(call_tool(world["owner_pat"], "create_review", {**review_args, "demonstration": True}))
    assert review["demonstration"] is True
    awarded = ok(call_tool(world["owner_pat"], "award_response", {**args, "demonstration": True}))
    assert awarded["status"] == "awarded"
    assert awarded["demonstration_award"] is True
    assert awarded["awardee_notified"] is False


@pytest.mark.django_db
def test_demonstration_award_refused_for_a_real_or_listed_response(world):
    winner = make_org("winner-org", name="Winner Org")
    grant(world["owner"], winner)
    real = make_response(make_call(world["owner_org"], listed=False), None, llo_entity=winner)
    args = {"response_id": real.pk, "reward_budget": 5, "org_id": "399", "demonstration": True}
    assert err(call_tool(world["owner_pat"], "award_response", args)) == "PERMISSION_DENIED"
    listed = make_call(world["owner_org"], listed=True)
    envelope = call_tool(
        world["owner_pat"],
        "create_response",
        {"solicitation_id": listed.pk, "llo_entity_id": "winner-org", "synthetic": True},
    )
    assert envelope["result"]["isError"] is True


# --- where the work happens -------------------------------------------------------


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


@pytest.fixture
def boundaries(monkeypatch):
    """Two invented areas, Kano (a state) and Gwale (a ward in it), from search and from geometry."""
    from connect_labs.microplans.core import admin_boundaries
    from connect_labs.microplans.core.admin_boundaries import AdminArea

    shapes = {"Kano": _square(8, 11, 9, 12), "Gwale": _square(8.4, 11.4, 8.5, 11.5)}

    class Resolver:
        def list_areas(self, country, level, *, name_contains=None, limit=25, **kw):
            rows = {1: "Kano", 3: "Gwale"}
            name = rows.get(level)
            if not name or (name_contains or "").lower() not in name.lower():
                return []
            return [AdminArea(name=name, level=level, source="labs", country=country, ref={"boundary_id": name})]

        def geometry(self, area):
            return shapes.get(area.name)

    monkeypatch.setattr(admin_boundaries, "get_resolver", lambda: Resolver())


def test_area_search_is_a_read_on_the_restricted_endpoint():
    from connect_labs.mcp.token_scopes import RESTRICTED_TOOLS

    assert get_tool("solicitation_area_search").is_write is False
    assert "solicitation_area_search" in RESTRICTED_TOOLS


@pytest.mark.django_db
def test_an_agent_finds_a_state_limits_a_call_to_it_and_a_firm_picks_a_ward_inside(world, boundaries):
    [kano] = ok(call_tool(world["owner_pat"], "solicitation_area_search", {"country": "NG", "q": "kan"}))["areas"]
    assert kano["level_label"] == "State" and kano["country"] == "NGA"
    call = ok(
        call_tool(
            world["owner_pat"],
            "create_solicitation",
            {
                "org_slug": "owner-org",
                "title": "Survey in Kano",
                "description": "d",
                "solicitation_type": "rfp",
                "status": "active",
                "is_public": True,
                "service_area_limits": [kano],
            },
        )
    )
    # Names and levels come back; outlines are for maps and stay out of the tool's answer.
    assert [a["name"] for a in call["service_area_limits"]] == ["Kano"]
    assert "geometry" not in call["service_area_limits"][0]

    [gwale] = ok(call_tool(world["applicant_pat"], "solicitation_area_search", {"country": "NGA", "q": "gwa"}))[
        "areas"
    ]
    response = ok(
        call_tool(
            world["applicant_pat"],
            "create_response",
            {"solicitation_id": call["id"], "llo_entity_id": "firm-org", "service_areas": [gwale]},
        )
    )
    assert [a["name"] for a in response["service_areas"]] == ["Gwale"]
    assert "geometry" not in response["service_areas"][0]
