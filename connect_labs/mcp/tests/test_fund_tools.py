"""Tests for migrated fund MCP tools (B.4).

Covers all 6 tools:
  - list_funds, get_fund (reads, is_write=False)
  - create_fund, update_fund, add_fund_allocation, remove_fund_allocation (writes, is_write=True)
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.users.models import User

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user(db):
    """User with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="fund-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    # client is unused: the MCP protocol endpoint is now a FastMCP ASGI app,
    # not a Django view. call_tool drives the same auth/handler/audit/rate-limit
    # path in-process and returns the same JSON-RPC-shaped envelope.
    return call_tool(raw_pat, tool_name, arguments)


def _make_mock_fund(record_id, experiment="test-fund", organization_id=None, data=None):
    """Build a MagicMock that mimics a LocalLabsRecord for a fund."""
    rec = MagicMock()
    rec.id = record_id
    rec.type = "fund"
    rec.experiment = experiment
    rec.organization_id = organization_id
    rec.data = data or {}
    return rec


# ---------------------------------------------------------------------------
# Registry tests — is_write flags
# ---------------------------------------------------------------------------


def test_write_tools_flagged_is_write():
    """Write tools must be registered with is_write=True so rate limiting and audit apply."""
    for name in ("create_fund", "update_fund", "add_fund_allocation", "remove_fund_allocation"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is True, f"{name} should have is_write=True"


def test_read_tools_not_flagged_is_write():
    """Read tools must NOT be flagged as writes."""
    for name in ("list_funds", "get_fund"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is False, f"{name} should have is_write=False"


# ---------------------------------------------------------------------------
# list_funds
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_list_funds_happy_path(mock_client_cls, client, auth_user):
    """Returns {funds: [...]} with flattened record dicts."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_fund(1, experiment="acme-fund", data={"name": "ACME Fund", "status": "active", "allocations": []}),
        _make_mock_fund(2, experiment="beta-fund", data={"name": "Beta Fund", "status": "active", "allocations": []}),
    ]

    data = _call_tool(client, raw, "list_funds", {"program_id": "25"})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "funds" in content
    assert len(content["funds"]) == 2
    first = content["funds"][0]
    assert first["id"] == 1
    assert first["name"] == "ACME Fund"
    assert first["status"] == "active"
    assert first["experiment"] == "acme-fund"

    mock_client.get_records.assert_called_once_with(
        type="fund",
        program_id=25,
    )


# ---------------------------------------------------------------------------
# get_fund
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_get_fund_found(mock_client_cls, client, auth_user):
    """Returns the flat record dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_fund(
        42, experiment="acme-fund", data={"name": "ACME Fund", "currency": "USD", "allocations": []}
    )

    data = _call_tool(client, raw, "get_fund", {"fund_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["name"] == "ACME Fund"
    assert content["currency"] == "USD"
    mock_client.get_record_by_id.assert_called_once_with(42, type="fund")


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_get_fund_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the fund does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_fund", {"fund_id": 999})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# create_fund
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_create_fund_happy_path(mock_client_cls, client, auth_user):
    """Creates a fund and returns its serialized form."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_fund(
        77,
        experiment="acme-fund",
        data={
            "name": "ACME Fund",
            "funder_slug": "acme-fund",
            "status": "active",
            "currency": "USD",
            "allocations": [],
        },
    )

    data = _call_tool(
        client,
        raw,
        "create_fund",
        {
            "public_record_acknowledged": True,
            "program_id": "25",
            "name": "ACME Fund",
            "total_budget": 50000,
            "currency": "USD",
        },
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 77
    assert content["name"] == "ACME Fund"
    assert content["funder_slug"] == "acme-fund"

    mock_client.create_record.assert_called_once()
    call_kwargs = mock_client.create_record.call_args.kwargs
    assert call_kwargs["experiment"] == "acme-fund"
    assert call_kwargs["type"] == "fund"
    assert call_kwargs["program_id"] == 25
    assert call_kwargs["public"] is True
    record_data = call_kwargs["data"]
    assert record_data["name"] == "ACME Fund"
    assert record_data["funder_slug"] == "acme-fund"
    assert record_data["total_budget"] == 50000
    assert record_data["currency"] == "USD"
    assert record_data["allocations"] == []


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_create_fund_minimal_params(mock_client_cls, client, auth_user):
    """Creates a fund with only required params, defaults applied."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_fund(
        78,
        experiment="my-fund",
        data={"name": "My Fund", "funder_slug": "my-fund", "status": "active", "currency": "USD", "allocations": []},
    )

    data = _call_tool(
        client, raw, "create_fund", {"public_record_acknowledged": True, "program_id": "10", "name": "My Fund"}
    )

    assert data["result"]["isError"] is False, data
    call_kwargs = mock_client.create_record.call_args.kwargs
    record_data = call_kwargs["data"]
    # Optional fields absent when not supplied
    assert "total_budget" not in record_data
    assert "description" not in record_data
    assert record_data["status"] == "active"
    assert record_data["currency"] == "USD"


@pytest.mark.django_db
def test_create_fund_requires_public_acknowledgment(client, auth_user):
    """create_fund raises POLICY_VIOLATION when public_record_acknowledged is false."""
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "create_fund",
        {"public_record_acknowledged": False, "program_id": "25", "name": "ACME Fund"},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "POLICY_VIOLATION"


# ---------------------------------------------------------------------------
# update_fund
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_update_fund_happy_path(mock_client_cls, client, auth_user):
    """Merges update_data into existing data and returns the updated record."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_fund(42, experiment="acme-fund", data={"name": "ACME Fund", "status": "active"})
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_fund(42, experiment="acme-fund", data={"name": "ACME Fund", "status": "closed"})
    mock_client.update_record.return_value = updated

    data = _call_tool(
        client,
        raw,
        "update_fund",
        {"fund_id": 42, "update_data": {"status": "closed"}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["status"] == "closed"

    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args.kwargs
    assert call_kwargs["record_id"] == 42
    assert call_kwargs["experiment"] == existing.experiment
    assert call_kwargs["type"] == existing.type
    merged = call_kwargs["data"]
    assert merged["name"] == "ACME Fund"
    assert merged["status"] == "closed"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_update_fund_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND when the fund does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "update_fund", {"fund_id": 999, "update_data": {"status": "closed"}})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_update_fund_strips_public_flag(mock_client_cls, client, auth_user):
    """update_fund strips is_public/public from update_data before merging."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_fund(42, experiment="acme-fund", data={"name": "ACME Fund", "status": "active"})
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = existing

    _call_tool(
        client,
        raw,
        "update_fund",
        {"fund_id": 42, "update_data": {"status": "active", "is_public": True, "public": True}},
    )

    call_kwargs = mock_client.update_record.call_args.kwargs
    merged = call_kwargs["data"]
    assert "is_public" not in merged, "is_public must be stripped before merge"
    assert "public" not in merged, "public must be stripped before merge"


# ---------------------------------------------------------------------------
# add_fund_allocation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_add_fund_allocation_happy_path(mock_client_cls, client, auth_user):
    """Appends an allocation to a fund's allocations array."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_fund(200, experiment="test-fund", data={"name": "Test Fund", "allocations": []})
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_fund(
        200, experiment="test-fund", data={"name": "Test Fund", "allocations": [{"amount": 500, "type": "award"}]}
    )
    mock_client.update_record.return_value = updated

    data = _call_tool(
        client,
        raw,
        "add_fund_allocation",
        {"fund_id": 200, "allocation": {"amount": 500, "type": "award"}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 200
    assert len(content["allocations"]) == 1
    assert content["allocations"][0]["amount"] == 500

    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args.kwargs
    assert call_kwargs["record_id"] == 200
    passed_data = call_kwargs["data"]
    assert len(passed_data["allocations"]) == 1
    assert passed_data["allocations"][0] == {"amount": 500, "type": "award"}


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_add_fund_allocation_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND when the fund does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "add_fund_allocation",
        {"fund_id": 999, "allocation": {"amount": 100}},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# remove_fund_allocation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_remove_fund_allocation_happy_path(mock_client_cls, client, auth_user):
    """Removes an allocation at the given index from a fund's allocations array."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_fund(
        200,
        experiment="test-fund",
        data={
            "name": "Test Fund",
            "allocations": [
                {"amount": 100, "type": "award"},
                {"amount": 200, "type": "grant"},
            ],
        },
    )
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_fund(
        200, experiment="test-fund", data={"name": "Test Fund", "allocations": [{"amount": 200, "type": "grant"}]}
    )
    mock_client.update_record.return_value = updated

    data = _call_tool(client, raw, "remove_fund_allocation", {"fund_id": 200, "index": 0})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 200
    assert len(content["allocations"]) == 1
    assert content["allocations"][0]["amount"] == 200

    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args.kwargs
    # After removing index 0, only the second allocation remains
    passed_data = call_kwargs["data"]
    assert len(passed_data["allocations"]) == 1
    assert passed_data["allocations"][0] == {"amount": 200, "type": "grant"}


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_remove_fund_allocation_index_out_of_range(mock_client_cls, client, auth_user):
    """Returns INVALID_SCHEMA when the index is out of range."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_fund(
        200, experiment="test-fund", data={"name": "Test Fund", "allocations": [{"amount": 100}]}
    )
    mock_client.get_record_by_id.return_value = existing

    data = _call_tool(client, raw, "remove_fund_allocation", {"fund_id": 200, "index": 5})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.funds.LabsRecordAPIClient")
def test_remove_fund_allocation_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND when the fund does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "remove_fund_allocation", {"fund_id": 999, "index": 0})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Missing Connect token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fund_tools_require_connect_token(client, db):
    """All fund tools fail with PERMISSION_DENIED when the user has no Connect token."""
    user = User.objects.create(username="no-conn-fund")
    _, raw = MCPAccessToken.create_token(user, name="t")

    for name, args in [
        ("list_funds", {"program_id": "25"}),
        ("get_fund", {"fund_id": 1}),
        ("create_fund", {"public_record_acknowledged": True, "program_id": "25", "name": "Test Fund"}),
        ("update_fund", {"fund_id": 1, "update_data": {"status": "closed"}}),
        ("add_fund_allocation", {"fund_id": 1, "allocation": {"amount": 100}}),
        ("remove_fund_allocation", {"fund_id": 1, "index": 0}),
    ]:
        resp_data = _call_tool(client, raw, name, args)
        assert (
            resp_data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
        ), f"{name} should return PERMISSION_DENIED without a Connect token"
