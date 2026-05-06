"""Tests for migrated review MCP tools (B.3).

Covers all 4 tools: list_reviews, get_review (reads), create_review, update_review (writes).
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
    user = User.objects.create(username="rev-test")
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


def _make_mock_record(record_id, rtype, experiment="llo-entity-1", labs_record_id=None, data=None):
    """Build a MagicMock that mimics a LocalLabsRecord."""
    rec = MagicMock()
    rec.id = record_id
    rec.type = rtype
    rec.experiment = experiment
    rec.labs_record_id = labs_record_id
    rec.data = data or {}
    return rec


# ---------------------------------------------------------------------------
# Registry tests — is_write flags
# ---------------------------------------------------------------------------


def test_write_tools_flagged_is_write():
    """Write tools must be registered with is_write=True so rate limiting and audit apply."""
    for name in ("create_review", "update_review"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is True, f"{name} should have is_write=True"


def test_read_tools_not_flagged_is_write():
    """Read tools must NOT be flagged as writes."""
    for name in ("list_reviews", "get_review"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is False, f"{name} should have is_write=False"


# ---------------------------------------------------------------------------
# list_reviews
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_list_reviews_happy_path(mock_client_cls, client, auth_user):
    """Returns {reviews: [...]} scoped by response_id via labs_record_id."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_record(1, "solicitation_review", labs_record_id=10, data={"recommendation": "approved"}),
        _make_mock_record(2, "solicitation_review", labs_record_id=10, data={"recommendation": "under_review"}),
    ]

    data = _call_tool(client, raw, "list_reviews", {"response_id": 10})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "reviews" in content
    assert len(content["reviews"]) == 2
    first = content["reviews"][0]
    assert first["id"] == 1
    assert first["recommendation"] == "approved"
    assert first["labs_record_id"] == 10

    mock_client.get_records.assert_called_once_with(
        type="solicitation_review",
        labs_record_id=10,
    )


# ---------------------------------------------------------------------------
# get_review
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_get_review_found(mock_client_cls, client, auth_user):
    """Returns the flat record dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42, "solicitation_review", labs_record_id=10, data={"recommendation": "approved", "score": 85}
    )

    data = _call_tool(client, raw, "get_review", {"review_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["recommendation"] == "approved"
    assert content["score"] == 85
    mock_client.get_record_by_id.assert_called_once_with(42, type="solicitation_review")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_get_review_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the record is missing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_review", {"review_id": 999})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# create_review
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_create_review_happy_path(mock_client_cls, client, auth_user):
    """Creates a review record and returns its serialized form."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_record(
        77,
        "solicitation_review",
        experiment="llo-entity-1",
        labs_record_id=10,
        data={
            "response_id": 10,
            "llo_entity_id": "llo-entity-1",
            "recommendation": "approved",
            "score": 90,
            "notes": "Great work",
        },
    )

    data = _call_tool(
        client,
        raw,
        "create_review",
        {
            "public_record_acknowledged": True,
            "response_id": 10,
            "llo_entity_id": "llo-entity-1",
            "score": 90,
            "recommendation": "approved",
            "notes": "Great work",
        },
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 77
    assert content["recommendation"] == "approved"
    assert content["score"] == 90
    assert content["notes"] == "Great work"

    # Verify create_record was called with correct args
    mock_client.create_record.assert_called_once()
    call_kwargs = mock_client.create_record.call_args.kwargs
    assert call_kwargs["experiment"] == "llo-entity-1"
    assert call_kwargs["type"] == "solicitation_review"
    assert call_kwargs["labs_record_id"] == 10
    assert call_kwargs["public"] is True
    record_data = call_kwargs["data"]
    assert record_data["recommendation"] == "approved"
    assert record_data["score"] == 90
    assert record_data["notes"] == "Great work"
    assert "review_date" in record_data


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_create_review_minimal_params(mock_client_cls, client, auth_user):
    """Creates a review with only the required params (defaults applied)."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.create_record.return_value = _make_mock_record(
        78,
        "solicitation_review",
        experiment="llo-entity-2",
        labs_record_id=20,
        data={"response_id": 20, "llo_entity_id": "llo-entity-2", "recommendation": "under_review"},
    )

    data = _call_tool(
        client,
        raw,
        "create_review",
        {"public_record_acknowledged": True, "response_id": 20, "llo_entity_id": "llo-entity-2"},
    )

    assert data["result"]["isError"] is False, data
    call_kwargs = mock_client.create_record.call_args.kwargs
    # Default recommendation should be "under_review"
    assert call_kwargs["data"]["recommendation"] == "under_review"
    # Optional fields should not be present when not supplied
    assert "score" not in call_kwargs["data"]
    assert "notes" not in call_kwargs["data"]
    assert "tags" not in call_kwargs["data"]


@pytest.mark.django_db
def test_create_review_requires_public_acknowledgment(client, auth_user):
    """create_review raises POLICY_VIOLATION when public_record_acknowledged is false."""
    _, raw = auth_user
    data = _call_tool(
        client,
        raw,
        "create_review",
        {"public_record_acknowledged": False, "response_id": 1, "llo_entity_id": "llo-entity-1"},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "POLICY_VIOLATION"


# ---------------------------------------------------------------------------
# update_review
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_update_review_happy_path(mock_client_cls, client, auth_user):
    """Merges update_data into existing data and returns the updated record."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(
        42,
        "solicitation_review",
        experiment="llo-entity-1",
        labs_record_id=10,
        data={"recommendation": "under_review", "score": 50},
    )
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_record(
        42,
        "solicitation_review",
        experiment="llo-entity-1",
        labs_record_id=10,
        data={"recommendation": "approved", "score": 88},
    )
    mock_client.update_record.return_value = updated

    data = _call_tool(
        client,
        raw,
        "update_review",
        {"review_id": 42, "update_data": {"recommendation": "approved", "score": 88}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["recommendation"] == "approved"
    assert content["score"] == 88

    # Verify merged data passed to update_record
    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args.kwargs
    assert call_kwargs["record_id"] == 42
    assert call_kwargs["experiment"] == existing.experiment
    assert call_kwargs["type"] == existing.type
    merged = call_kwargs["data"]
    assert merged["recommendation"] == "approved"
    assert merged["score"] == 88


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_update_review_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND error when the review does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "update_review",
        {"review_id": 999, "update_data": {"recommendation": "rejected"}},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.reviews.LabsRecordAPIClient")
def test_update_review_strips_public_flag(mock_client_cls, client, auth_user):
    """update_review strips is_public/public from update_data before merging."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(
        42,
        "solicitation_review",
        experiment="llo-entity-1",
        labs_record_id=10,
        data={"recommendation": "under_review", "score": 50},
    )
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = existing

    _call_tool(
        client,
        raw,
        "update_review",
        {"review_id": 42, "update_data": {"score": 80, "is_public": True, "public": True}},
    )

    call_kwargs = mock_client.update_record.call_args.kwargs
    merged = call_kwargs["data"]
    assert "is_public" not in merged, "is_public must be stripped before merge"
    assert "public" not in merged, "public must be stripped before merge"


# ---------------------------------------------------------------------------
# Missing Connect token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_review_tools_require_connect_token(client, db):
    """All review tools fail with PERMISSION_DENIED when the user has no Connect token."""
    user = User.objects.create(username="no-conn-rev")
    _, raw = MCPAccessToken.create_token(user, name="t")

    for name, args in [
        ("list_reviews", {"response_id": 1}),
        ("get_review", {"review_id": 1}),
        ("create_review", {"public_record_acknowledged": True, "response_id": 1, "llo_entity_id": "llo-entity-1"}),
        ("update_review", {"review_id": 1, "update_data": {"recommendation": "approved"}}),
    ]:
        resp_data = _call_tool(client, raw, name, args)
        assert (
            resp_data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
        ), f"{name} should return PERMISSION_DENIED without a Connect token"
