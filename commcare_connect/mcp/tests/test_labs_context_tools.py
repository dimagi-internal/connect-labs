"""Tests for the labs_context MCP tool."""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.users.models import User


@pytest.fixture
def auth_user(db):
    """User with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="labs-ctx-test")
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


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_builds_hierarchy(mock_fetch, client, auth_user):
    """Organizations nest programs, which nest managed opps. Non-managed opps
    nest under their org's ``opportunities`` list."""
    _, raw = auth_user
    mock_fetch.return_value = {
        "user": {"email": "a@b.com", "commcare_username": "a@b.com"},
        "organizations": [
            {"id": 1, "slug": "acme", "name": "Acme Org"},
            {"id": 2, "slug": "beta", "name": "Beta Org"},
        ],
        "programs": [
            {"id": 10, "name": "Acme Program A", "organization": "acme", "delivery_type": "dt", "currency": "USD"},
            {"id": 11, "name": "Acme Program B", "organization": "acme", "delivery_type": "dt", "currency": "USD"},
        ],
        "opportunities": [
            # Managed opp → program 10
            {
                "id": 100,
                "name": "Managed Opp 1",
                "organization": "partnerlab",
                "program": 10,
                "is_active": True,
                "end_date": None,
                "visit_count": 5,
            },
            # Managed opp → program 11
            {
                "id": 101,
                "name": "Managed Opp 2",
                "organization": "partnerlab",
                "program": 11,
                "is_active": False,
                "end_date": "2025-01-01",
                "visit_count": 0,
            },
            # Non-managed opp owned directly by Acme (program null)
            {
                "id": 200,
                "name": "Direct Opp",
                "organization": "acme",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 2,
            },
            # Non-managed opp owned directly by Beta
            {
                "id": 300,
                "name": "Beta Opp",
                "organization": "beta",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 1,
            },
        ],
    }

    data = _call_tool(client, raw, "labs_context", {})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]

    assert content["totals"] == {"organizations": 2, "programs": 2, "opportunities": 4}
    assert content["user"] == {"email": "a@b.com", "commcare_username": "a@b.com"}

    orgs = {o["slug"]: o for o in content["organizations"]}
    assert set(orgs) == {"acme", "beta"}

    # Acme has two programs, each with its managed opp, plus one direct opp.
    acme = orgs["acme"]
    assert acme["name"] == "Acme Org"
    assert acme["id"] == 1
    acme_programs = {p["id"]: p for p in acme["programs"]}
    assert set(acme_programs) == {10, 11}
    assert [o["id"] for o in acme_programs[10]["opportunities"]] == [100]
    assert acme_programs[10]["opportunities"][0]["name"] == "Managed Opp 1"
    assert acme_programs[10]["opportunities"][0]["visit_count"] == 5
    assert [o["id"] for o in acme_programs[11]["opportunities"]] == [101]
    assert [o["id"] for o in acme["opportunities"]] == [200]

    # Beta has no programs, one direct opp.
    beta = orgs["beta"]
    assert beta["programs"] == []
    assert [o["id"] for o in beta["opportunities"]] == [300]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_empty(mock_fetch, client, auth_user):
    """Empty data from upstream produces empty structures without errors."""
    _, raw = auth_user
    mock_fetch.return_value = {
        "user": {},
        "organizations": [],
        "programs": [],
        "opportunities": [],
    }

    data = _call_tool(client, raw, "labs_context", {})
    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]

    assert content["organizations"] == []
    assert content["totals"] == {"organizations": 0, "programs": 0, "opportunities": 0}


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_upstream_failure(mock_fetch, client, auth_user):
    """If the production API call fails, we surface an UPSTREAM_ERROR."""
    _, raw = auth_user
    mock_fetch.return_value = None

    data = _call_tool(client, raw, "labs_context", {})
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "UPSTREAM_ERROR"


@pytest.mark.django_db
def test_labs_context_requires_connect_token(client, db):
    """A user without a Connect token gets PERMISSION_DENIED, same as other tools."""
    user = User.objects.create(username="no-conn-labs-ctx")
    _, raw = MCPAccessToken.create_token(user, name="t")

    data = _call_tool(client, raw, "labs_context", {})
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "PERMISSION_DENIED"
