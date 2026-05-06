"""Tests for migrated solicitation MCP tools (B.2).

Covers all 7 tools: list_solicitations, get_solicitation, list_responses,
get_response (reads), create_solicitation, update_solicitation, award_response
(writes).

Each write tool also has an is_write registry check to confirm rate-limiting
and audit apply.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.tool_registry import get_tool
from commcare_connect.users.models import User

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user(db):
    """User with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="sol-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    resp = client.post(
        reverse("mcp:endpoint"),
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_pat}",
    )
    return resp.json()


def _make_mock_record(record_id, rtype, experiment="25", program_id=25, data=None, labs_record_id=None):
    """Build a MagicMock that mimics a LocalLabsRecord."""
    rec = MagicMock()
    rec.id = record_id
    rec.type = rtype
    rec.experiment = experiment
    rec.program_id = program_id
    rec.labs_record_id = labs_record_id
    rec.data = data or {}
    return rec


# ---------------------------------------------------------------------------
# Registry tests — is_write flags
# ---------------------------------------------------------------------------


def test_write_tools_flagged_is_write():
    """Writes must be registered with is_write=True so rate limiting and audit apply."""
    for name in ("create_solicitation", "update_solicitation", "award_response"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is True, f"{name} should have is_write=True"


def test_read_tools_not_flagged_is_write():
    """Read tools must NOT be flagged as writes."""
    for name in ("list_solicitations", "get_solicitation", "list_responses", "get_response"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is False, f"{name} should have is_write=False"


# ---------------------------------------------------------------------------
# list_solicitations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_happy_path(mock_client_cls, client, auth_user):
    """Returns {solicitations: [...]} with flattened record dicts."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_record(1, "solicitation", data={"title": "Sol A", "status": "active"}),
        _make_mock_record(2, "solicitation", data={"title": "Sol B", "status": "closed"}),
    ]

    data = _call_tool(client, raw, "list_solicitations", {"program_id": "25"})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "solicitations" in content
    assert len(content["solicitations"]) == 2
    # Flat merge: top-level fields + data fields
    first = content["solicitations"][0]
    assert first["id"] == 1
    assert first["title"] == "Sol A"
    assert first["status"] == "active"
    assert first["program_id"] == 25


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_filters_passed(mock_client_cls, client, auth_user):
    """Status and solicitation_type kwargs are forwarded as data__{field} filters."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = []

    _call_tool(
        client,
        raw,
        "list_solicitations",
        {"program_id": "7", "status": "active", "solicitation_type": "grant"},
    )

    mock_client.get_records.assert_called_once_with(
        type="solicitation",
        experiment="7",
        status="active",
        solicitation_type="grant",
    )


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_no_scope(mock_client_cls, client, auth_user):
    """Without program_id/organization_id the experiment kwarg is omitted."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = []

    _call_tool(client, raw, "list_solicitations", {})

    mock_client.get_records.assert_called_once_with(type="solicitation")


# ---------------------------------------------------------------------------
# get_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_found(mock_client_cls, client, auth_user):
    """Returns the flat record dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(42, "solicitation", data={"title": "My Sol"})

    data = _call_tool(client, raw, "get_solicitation", {"solicitation_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["title"] == "My Sol"
    mock_client.get_record_by_id.assert_called_once_with(42, type="solicitation")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the record is missing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_solicitation", {"solicitation_id": 999})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# list_responses
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_responses_happy_path(mock_client_cls, client, auth_user):
    """Returns {responses: [...]} scoped by solicitation_id via labs_record_id."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_record(10, "solicitation_response", labs_record_id=42, data={"org": "Org A"}),
    ]

    data = _call_tool(client, raw, "list_responses", {"solicitation_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "responses" in content
    assert len(content["responses"]) == 1
    assert content["responses"][0]["org"] == "Org A"

    mock_client.get_records.assert_called_once_with(
        type="solicitation_response",
        labs_record_id=42,
    )


# ---------------------------------------------------------------------------
# get_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_response_found(mock_client_cls, client, auth_user):
    """Returns the flat dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        55, "solicitation_response", data={"status": "submitted"}
    )

    data = _call_tool(client, raw, "get_response", {"response_id": 55})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 55
    assert content["status"] == "submitted"
    mock_client.get_record_by_id.assert_called_once_with(55, type="solicitation_response")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_response_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the response record is missing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_response", {"response_id": 888})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# create_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_create_solicitation_happy_path(mock_client_cls, client, auth_user):
    """Creates a record and returns its serialized form."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_record(
        77, "solicitation", data={"title": "New Sol", "status": "draft"}
    )

    data = _call_tool(
        client,
        raw,
        "create_solicitation",
        {"program_id": "25", "data": {"title": "New Sol", "status": "draft"}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 77
    assert content["title"] == "New Sol"

    mock_client.create_record.assert_called_once_with(
        experiment="25",
        type="solicitation",
        data={"title": "New Sol", "status": "draft"},
        program_id=25,
        public=False,
    )


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_create_solicitation_uses_is_public_flag(mock_client_cls, client, auth_user):
    """is_public in data is forwarded as public=True to create_record."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_record(
        78, "solicitation", data={"title": "Public Sol", "is_public": True}
    )

    _call_tool(
        client,
        raw,
        "create_solicitation",
        {"program_id": "25", "data": {"title": "Public Sol", "is_public": True}},
    )

    mock_client.create_record.assert_called_once_with(
        experiment="25",
        type="solicitation",
        data={"title": "Public Sol", "is_public": True},
        program_id=25,
        public=True,
    )


@pytest.mark.django_db
def test_create_solicitation_missing_scope(client, auth_user):
    """Fails with INVALID_SCHEMA if both program_id and organization_id are absent."""
    _, raw = auth_user

    data = _call_tool(
        client,
        raw,
        "create_solicitation",
        {"data": {"title": "Scopeless"}},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


# ---------------------------------------------------------------------------
# update_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_happy_path(mock_client_cls, client, auth_user):
    """Merges update_data into existing data and returns the updated record."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(42, "solicitation", data={"title": "Old Title", "status": "draft"})
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_record(42, "solicitation", data={"title": "New Title", "status": "active"})
    mock_client.update_record.return_value = updated

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 42, "update_data": {"title": "New Title", "status": "active"}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["title"] == "New Title"

    # Verify the merged data passed to update_record
    mock_client.update_record.assert_called_once_with(
        record_id=42,
        experiment=existing.experiment,
        type=existing.type,
        data={"title": "New Title", "status": "active"},
        current_record=existing,
    )


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND error when the solicitation does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 999, "update_data": {"status": "closed"}},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_strips_public_flag(mock_client_cls, client, auth_user):
    """update_solicitation strips is_public/public from update_data before merging."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = MagicMock()
    existing.id = 10
    existing.experiment = "25"
    existing.type = "solicitation"
    existing.data = {"title": "Test Sol", "status": "open"}
    existing.labs_record_id = None
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = existing

    _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 10, "update_data": {"status": "closed", "is_public": True, "public": True}},
    )

    call_kwargs = mock_client.update_record.call_args.kwargs
    merged = call_kwargs["data"]
    assert "is_public" not in merged, "is_public must be stripped before merge"
    assert "public" not in merged, "public must be stripped before merge"


# ---------------------------------------------------------------------------
# award_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_happy_path_no_fund(mock_client_cls, client, auth_user):
    """Awards a response without fund allocation when fund_id is absent."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "submitted", "solicitation_id": "42", "llo_entity_name": "Org X"},
    )
    awarded_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "awarded", "reward_budget": 1000, "org_id": "org-1"},
    )

    def _get_record_by_id(record_id, **kwargs):
        if record_id == 10:
            return existing_response
        return None

    mock_client.get_record_by_id.side_effect = _get_record_by_id
    mock_client.update_record.return_value = awarded_response

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 10, "reward_budget": 1000, "org_id": "org-1"},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 10
    assert content["status"] == "awarded"

    # update_record called exactly once (response update only — no fund)
    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args
    assert call_kwargs.kwargs["record_id"] == 10
    passed_data = call_kwargs.kwargs["data"]
    assert passed_data["status"] == "awarded"
    assert passed_data["reward_budget"] == 1000
    assert passed_data["org_id"] == "org-1"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_with_fund_allocation(mock_client_cls, client, auth_user):
    """When fund_id is provided, the fund record gets an allocation appended."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={
            "status": "submitted",
            "solicitation_id": "42",
            "llo_entity_name": "Org X",
        },
    )
    awarded_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "awarded", "reward_budget": 500, "org_id": "org-2"},
    )
    sol_record = _make_mock_record(42, "solicitation", data={"title": "Big Grant"})
    fund_record = _make_mock_record(200, "fund", experiment="test-fund", data={"name": "Test Fund", "allocations": []})
    updated_fund = _make_mock_record(
        200,
        "fund",
        experiment="test-fund",
        data={"name": "Test Fund", "allocations": [{"amount": 500}]},
    )

    def _get_record_by_id(record_id, **kwargs):
        mapping = {10: existing_response, 42: sol_record, 200: fund_record}
        return mapping.get(record_id)

    mock_client.get_record_by_id.side_effect = _get_record_by_id

    call_count = [0]

    def _update_record(**kwargs):
        call_count[0] += 1
        if kwargs["record_id"] == 10:
            return awarded_response
        return updated_fund

    mock_client.update_record.side_effect = _update_record

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 10, "reward_budget": 500, "org_id": "org-2", "fund_id": 200},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 10

    # Should have called update_record twice: once for response, once for fund
    assert call_count[0] == 2

    # Verify the fund update included the new allocation with the solicitation title
    fund_update_call = [c for c in mock_client.update_record.call_args_list if c.kwargs["record_id"] == 200][0]
    fund_data = fund_update_call.kwargs["data"]
    assert len(fund_data["allocations"]) == 1
    allocation = fund_data["allocations"][0]
    assert allocation["amount"] == 500
    assert allocation["type"] == "award"
    assert allocation["response_id"] == 10
    assert allocation["org_id"] == "org-2"
    assert allocation["notes"] == "Award from Big Grant"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND error when the response record does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 999, "reward_budget": 100, "org_id": "org-x"},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Missing Connect token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tools_require_connect_token(client, db):
    """All tools fail with PERMISSION_DENIED when the user has no Connect token."""
    user = User.objects.create(username="no-conn-sol")
    _, raw = MCPAccessToken.create_token(user, name="t")

    for name, args in [
        ("list_solicitations", {}),
        ("get_solicitation", {"solicitation_id": 1}),
        ("list_responses", {"solicitation_id": 1}),
        ("get_response", {"response_id": 1}),
        ("create_solicitation", {"program_id": "1", "data": {"title": "X"}}),
        ("update_solicitation", {"solicitation_id": 1, "update_data": {"title": "X"}}),
        ("award_response", {"response_id": 1, "reward_budget": 100, "org_id": "o"}),
    ]:
        resp_data = _call_tool(client, raw, name, args)
        assert (
            resp_data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
        ), f"{name} should return PERMISSION_DENIED without a Connect token"
