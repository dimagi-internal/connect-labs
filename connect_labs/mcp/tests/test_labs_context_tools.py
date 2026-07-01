"""Tests for the labs_context MCP tool."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User


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
    # client is unused: the MCP protocol endpoint is now a FastMCP ASGI app,
    # not a Django view. call_tool drives the same auth/handler/audit/rate-limit
    # path in-process and returns the same JSON-RPC-shaped envelope.
    return call_tool(raw_pat, tool_name, arguments)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
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
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
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
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
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


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_search_prunes_tree_to_matching_subtrees(mock_fetch, client, auth_user):
    """search is a case-insensitive substring match on org name/slug, program
    name, and opportunity name. Orgs / programs without a matching descendant
    are dropped entirely. The `totals` object reflects the filtered view, not
    the full tree.
    """
    _, raw = auth_user
    mock_fetch.return_value = {
        "user": {},
        "organizations": [
            {"id": 1, "slug": "acme", "name": "Acme Health"},
            {"id": 2, "slug": "beta", "name": "Beta Org"},
        ],
        "programs": [
            {"id": 10, "name": "ECD v6", "organization": "acme", "delivery_type": "chc", "currency": "USD"},
            {"id": 11, "name": "KMC Wave", "organization": "acme", "delivery_type": "chc", "currency": "USD"},
        ],
        "opportunities": [
            {
                "id": 100,
                "name": "Demo Opp",
                "organization": "partnerlab",
                "program": 10,
                "is_active": True,
                "end_date": None,
                "visit_count": 0,
            },
            {
                "id": 200,
                "name": "KMC Longitudinal",
                "organization": "acme",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 0,
            },
            {
                "id": 300,
                "name": "Beta Opp",
                "organization": "beta",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 0,
            },
        ],
    }

    # Match on program name "ECD v6" — should include acme with just that program.
    data = _call_tool(client, raw, "labs_context", {"search": "ecd v6"})
    content = data["result"]["structuredContent"]
    assert content["search"] == "ecd v6"
    assert [o["slug"] for o in content["organizations"]] == ["acme"]
    acme = content["organizations"][0]
    assert [p["name"] for p in acme["programs"]] == ["ECD v6"]
    assert acme["opportunities"] == []  # KMC Longitudinal doesn't match
    assert content["totals"] == {"organizations": 1, "programs": 1, "opportunities": 1}


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_search_matches_org_slug(mock_fetch, client, auth_user):
    """Org slug matches include everything beneath it verbatim — a common
    `labs_context({search: "acme"})` "show me this org" flow."""
    _, raw = auth_user
    mock_fetch.return_value = {
        "user": {},
        "organizations": [{"id": 1, "slug": "acme", "name": "Acme"}],
        "programs": [],
        "opportunities": [
            {
                "id": 100,
                "name": "Alpha",
                "organization": "acme",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 0,
            },
            {
                "id": 101,
                "name": "Gamma",
                "organization": "acme",
                "program": None,
                "is_active": True,
                "end_date": None,
                "visit_count": 0,
            },
        ],
    }
    data = _call_tool(client, raw, "labs_context", {"search": "acme"})
    content = data["result"]["structuredContent"]
    assert len(content["organizations"]) == 1
    assert len(content["organizations"][0]["opportunities"]) == 2


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_search_with_no_matches_returns_empty(mock_fetch, client, auth_user):
    _, raw = auth_user
    mock_fetch.return_value = {
        "user": {},
        "organizations": [{"id": 1, "slug": "acme", "name": "Acme"}],
        "programs": [],
        "opportunities": [],
    }
    data = _call_tool(client, raw, "labs_context", {"search": "nonsense"})
    content = data["result"]["structuredContent"]
    assert content["organizations"] == []
    assert content["totals"] == {"organizations": 0, "programs": 0, "opportunities": 0}


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_merges_labs_only_when_user_opted_in(mock_fetch, client, auth_user):
    """labs_context surfaces labs-only synthetic opps when view_synthetic_opps is on."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    user, raw = auth_user
    user.email = "ace@dimagi-ai.com"
    user.view_synthetic_opps = True
    user.save()

    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        label="CHC demo",
        org_name="Labs Synthetic",
        program_name="Labs Synthetic",
        gdrive_folder_id="folder-xyz",
        labs_only=True,
        allowed_domains=["@dimagi-ai.com"],
    )

    mock_fetch.return_value = {
        "user": {},
        "organizations": [{"id": 1, "slug": "acme", "name": "Acme Org"}],
        "programs": [],
        "opportunities": [],
    }

    resp = _call_tool(client, raw, "labs_context", {})
    tree = resp["result"]["structuredContent"]["organizations"]
    org_slugs = {o["slug"] for o in tree}
    assert "acme" in org_slugs
    labs_org = next(o for o in tree if o["slug"].startswith("labs-synthetic-"))
    assert any(opp["id"] == 10_000 for prog in labs_org["programs"] for opp in prog["opportunities"]) or any(
        opp["id"] == 10_000 for opp in labs_org["opportunities"]
    )


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.labs_context.fetch_user_organization_data")
def test_labs_context_does_not_merge_when_user_opted_out(mock_fetch, client, auth_user):
    """labs-only opps stay hidden when view_synthetic_opps is False."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    user, raw = auth_user
    user.email = "ace@dimagi-ai.com"
    user.view_synthetic_opps = False
    user.save()

    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="folder-xyz",
        labs_only=True,
        allowed_domains=["@dimagi-ai.com"],
    )

    mock_fetch.return_value = {
        "user": {},
        "organizations": [{"id": 1, "slug": "acme", "name": "Acme Org"}],
        "programs": [],
        "opportunities": [],
    }

    resp = _call_tool(client, raw, "labs_context", {})
    tree = resp["result"]["structuredContent"]["organizations"]
    assert {o["slug"] for o in tree} == {"acme"}
