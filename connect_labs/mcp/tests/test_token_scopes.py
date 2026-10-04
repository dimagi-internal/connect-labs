"""A no-uservisit-data PAT never sees user visit data, and can edit definitions.

It reads definitions (``NO_USERVISIT_DATA_TOOLS``, which stay read-only), edits
workflow and indicator definitions and triggers server-side computation
(``DEFINITION_WRITE_TOOLS``, whose responses carry no visit-derived values), and reads
visits only on generated synthetic opportunities (``GENERATED_ONLY_TOOLS``).
"""

import pytest
from django.core.management import call_command
from django.urls import reverse

from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken, MCPAuditLog
from connect_labs.mcp.server import _verify_bearer_sync, allowed_tools
from connect_labs.mcp.tests.test_delegation import _run_mcp
from connect_labs.mcp.tool_registry import _REGISTRY, get_tool
from connect_labs.users.models import User


def test_every_no_uservisit_data_tool_exists_and_reads():
    from connect_labs.mcp import tools  # noqa: F401 -- registers the catalogue

    for name in token_scopes.NO_USERVISIT_DATA_TOOLS:
        spec = get_tool(name)
        assert spec is not None, f"NO_USERVISIT_DATA_TOOLS names a tool that does not exist: {name}"
        assert (
            not spec.is_write
        ), f"{name} writes; a write belongs in DEFINITION_WRITE_TOOLS, if its response carries no visit data"


# Tools whose response carries visit-derived values: a snapshot payload, indicator
# values, rows, or per-worker / per-case data. None of them may be a definition write.
_VISIT_VALUE_READERS = {
    "workflow_preview_snapshot",
    "workflow_preview_as_of",
    "workflow_history_runs",
    "workflow_run_context",
    "workflow_run_indicators",
    "workflow_indicator_explain",
    "pipeline_preview",
    "custom_analysis_run",
    "get_sample_ids",
}


def test_every_definition_write_tool_exists_and_writes():
    from connect_labs.mcp import tools  # noqa: F401

    for name in token_scopes.DEFINITION_WRITE_TOOLS:
        spec = get_tool(name)
        assert spec is not None, f"DEFINITION_WRITE_TOOLS names a tool that does not exist: {name}"
        assert spec.is_write, f"{name} does not write, so it belongs in NO_USERVISIT_DATA_TOOLS"


def test_no_definition_write_tool_returns_visit_data():
    writes = token_scopes.DEFINITION_WRITE_TOOLS
    assert writes.isdisjoint(token_scopes.USERVISIT_DATA_TOOLS)
    assert writes.isdisjoint(token_scopes.GENERATED_ONLY_TOOLS)
    assert writes.isdisjoint(_VISIT_VALUE_READERS)
    assert writes <= token_scopes.RESTRICTED_TOOLS


def test_deletes_that_are_not_definition_edits_stay_out_of_reach():
    for name in ("workflow_delete", "pipeline_delete", "pipeline_update_schema", "benchmarks_cohort_delete"):
        assert name not in token_scopes.RESTRICTED_TOOLS, name


def test_no_visit_data_tool_is_no_uservisit_data():
    from connect_labs.mcp import tools  # noqa: F401

    assert token_scopes.NO_USERVISIT_DATA_TOOLS.isdisjoint(token_scopes.USERVISIT_DATA_TOOLS)
    for name in token_scopes.USERVISIT_DATA_TOOLS:
        assert name in _REGISTRY, f"USERVISIT_DATA_TOOLS names a tool that does not exist: {name}"


def test_create_token_refuses_an_unknown_scope(db):
    user = User.objects.create(username="scope-typo")
    with pytest.raises(ValueError):
        MCPAccessToken.create_token(user, name="x", scope="admin")


@pytest.mark.django_db
def test_a_no_uservisit_data_pat_resolves_to_its_tools():
    user = User.objects.create(username="meta-resolve")
    _, raw = MCPAccessToken.create_token(user, name="agent", scope=token_scopes.NO_USERVISIT_DATA)

    resolved_user, method, _, scopes, _ = _verify_bearer_sync(raw)

    assert resolved_user == user
    assert method == "pat"

    class _Token:
        claims = {"auth_method": "pat"}

    _Token.scopes = scopes
    assert allowed_tools(_Token) == token_scopes.RESTRICTED_TOOLS


@pytest.mark.django_db
def test_a_pat_with_a_scope_nobody_defined_fails_closed():
    """A row whose scope the code no longer knows gets the narrow list, not everything."""
    user = User.objects.create(username="meta-unknown")
    token, raw = MCPAccessToken.create_token(user, name="agent")
    MCPAccessToken.objects.filter(pk=token.pk).update(scope="retired")

    _, _, _, scopes, _ = _verify_bearer_sync(raw)

    assert scopes == [token_scopes.TOKEN_SCOPE_STRINGS[token_scopes.NO_USERVISIT_DATA]]


@pytest.mark.django_db(transaction=True)
def test_a_no_uservisit_data_pat_sees_and_calls_only_its_tools():
    from config.asgi import build_application

    user = User.objects.create(username="meta-e2e")
    _, raw = MCPAccessToken.create_token(user, name="agent", scope=token_scopes.NO_USERVISIT_DATA)

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        called = await mcp_client.call_tool("list_templates", {})
        visit_data = await mcp_client.call_tool(
            "pipeline_preview", {"pipeline_id": 1, "opportunity_id": 1}, raise_on_error=False
        )
        snapshot = await mcp_client.call_tool(
            "workflow_preview_snapshot", {"run_id": 1, "opportunity_id": 1}, raise_on_error=False
        )
        definition_write = await mcp_client.call_tool(
            "workflow_update_definition",
            {"workflow_id": 999999, "opportunity_id": 1, "patch": {"name": "x"}, "expected_version": 1},
            raise_on_error=False,
        )
        delete = await mcp_client.call_tool("workflow_delete", {"workflow_id": 1}, raise_on_error=False)
        return tools, called, visit_data, snapshot, definition_write, delete

    tools, called, visit_data, snapshot, definition_write, delete = _run_mcp(
        build_application(), {"Authorization": f"Bearer {raw}"}, None, work
    )

    names = {tool.name for tool in tools}
    assert names == token_scopes.RESTRICTED_TOOLS
    assert token_scopes.DEFINITION_WRITE_TOOLS <= names
    assert called.structured_content is not None
    # Visit readers on a real opportunity: refused.
    assert visit_data.is_error
    assert "pipeline_preview" in visit_data.content[0].text
    assert snapshot.is_error
    assert "workflow_preview_snapshot" in snapshot.content[0].text
    # A definition write gets past the scope gate. This user has no Connect token, so
    # the handler itself fails -- but not with the scope refusal.
    assert definition_write.is_error
    assert "scope does not include" not in definition_write.content[0].text
    assert "user visit data" not in definition_write.content[0].text
    assert MCPAuditLog.objects.filter(user=user, tool_name="workflow_update_definition").exists()
    # A delete is not a definition edit: refused by the scope gate.
    assert delete.is_error
    assert "scope does not include workflow_delete" in delete.content[0].text
    assert MCPAuditLog.objects.filter(user=user, tool_name="list_templates", success=True).exists()
    assert not MCPAuditLog.objects.filter(
        user=user,
        tool_name__in=["pipeline_preview", "workflow_preview_snapshot", "workflow_delete"],
        success=True,
    ).exists()


# ---------------------------------------------------------------------------
# Minting one: the tokens page, the browser flow and the command
# ---------------------------------------------------------------------------


@pytest.fixture
def alice(client, db):
    user = User.objects.create(username="alice")
    client.force_login(user)
    return user


def test_the_tokens_page_mints_a_no_uservisit_data_token(client, alice):
    resp = client.post(reverse("labs:mcp_tokens_create"), {"name": "agent", "scope": "no-uservisit-data"})

    assert resp.status_code == 200
    assert MCPAccessToken.objects.get(user=alice, name="agent").scope == token_scopes.NO_USERVISIT_DATA


def test_the_tokens_page_defaults_to_full(client, alice):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "laptop"})

    assert MCPAccessToken.objects.get(user=alice, name="laptop").scope == token_scopes.FULL


def test_the_tokens_page_refuses_an_unknown_scope(client, alice):
    client.post(reverse("labs:mcp_tokens_create"), {"name": "sneaky", "scope": "admin"})

    assert not MCPAccessToken.objects.filter(user=alice).exists()


def test_rotating_a_no_uservisit_data_token_keeps_its_scope(client, alice):
    old, _ = MCPAccessToken.create_token(alice, name="agent", scope=token_scopes.NO_USERVISIT_DATA)

    client.post(reverse("labs:mcp_tokens_rotate", args=[old.pk]))

    new = MCPAccessToken.objects.get(user=alice, name="agent", is_active=True)
    assert new.pk != old.pk
    assert new.scope == token_scopes.NO_USERVISIT_DATA


def test_the_browser_flow_mints_the_scope_it_names(client, alice):
    params = {"callback": "http://127.0.0.1:8765/cb", "state": "x" * 16, "scope": "no-uservisit-data"}

    page = client.get(reverse("mcp:admin_create_token"), params)
    assert b"No user visit data" in page.content
    resp = client.post(reverse("mcp:admin_create_token") + "?scope=no-uservisit-data", {**params, "name": "agent"})

    assert resp.status_code == 302
    assert MCPAccessToken.objects.get(user=alice, name="agent").scope == token_scopes.NO_USERVISIT_DATA


def test_the_browser_flow_refuses_an_unknown_scope(client, alice):
    params = {"callback": "http://127.0.0.1:8765/cb", "state": "x" * 16, "scope": "admin"}

    assert client.get(reverse("mcp:admin_create_token"), params).status_code == 400


def test_the_command_mints_a_no_uservisit_data_token(db):
    user = User.objects.create(username="cli-user")

    call_command("mcp_create_token", user="cli-user", name="agent", scope="no-uservisit-data")

    assert MCPAccessToken.objects.get(user=user, name="agent").scope == token_scopes.NO_USERVISIT_DATA


# ---------------------------------------------------------------------------
# Endpoints outside the MCP tool gate that take a PAT
# ---------------------------------------------------------------------------


def test_the_shared_pat_verifier_refuses_a_restricted_token(db, rf):
    from connect_labs.mcp.auth import authenticate_request

    user = User.objects.create(username="reseed-caller")
    _, restricted = MCPAccessToken.create_token(user, name="agent", scope=token_scopes.NO_USERVISIT_DATA)
    _, full = MCPAccessToken.create_token(user, name="laptop")

    denied_user, failure = authenticate_request(rf.post("/", HTTP_AUTHORIZATION=f"Bearer {restricted}"))
    allowed_user, no_failure = authenticate_request(rf.post("/", HTTP_AUTHORIZATION=f"Bearer {full}"))

    assert denied_user is None
    assert failure.status_code == 403
    assert allowed_user == user
    assert no_failure is None
