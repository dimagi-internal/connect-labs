"""The "no user visit data" rule: the /mcp/no_user_visit/ endpoint, restricted credentials,
and visit-reading tools that run only on generated synthetic data."""

import httpx
import pytest

from connect_labs.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import mark_generated
from connect_labs.mcp import oauth, token_scopes, visit_access
from connect_labs.mcp.models import MCPAccessToken, MCPAuditLog
from connect_labs.mcp.tests.test_delegation import _factory
from connect_labs.mcp.tests.test_oauth import _access_token, _mcp_application
from connect_labs.mcp.tool_registry import _REGISTRY, get_tool
from connect_labs.users.models import User

SENTINEL = "SENTINEL-real-visit-value"
SAFE_URL = "http://testserver/mcp/no_user_visit/"
FULL_URL = "http://testserver/mcp/"


def _call(url, raw, work):
    import anyio
    from fastmcp import Client as MCPClient
    from fastmcp.client.transports import StreamableHttpTransport

    from config.asgi import build_application

    app = build_application()

    async def _run():
        transport = StreamableHttpTransport(
            url=url, headers={"Authorization": f"Bearer {raw}"}, httpx_client_factory=_factory(app)
        )
        async with app.router.lifespan_context(app):
            async with MCPClient(transport) as mcp_client:
                return await work(mcp_client)

    return anyio.run(_run)


def _user(username):
    return User.objects.create(username=username, email=f"{username}@dimagi.com", view_synthetic_opps=True)


def _synthetic_opp(opportunity_id, *, user, generated):
    SyntheticOpportunity.objects.create(
        opportunity_id=opportunity_id,
        gdrive_folder_id=f"folder-{opportunity_id}",
        labs_only=True,
        enabled=True,
        created_by=user,
    )
    if generated:
        mark_generated(opportunity_id, f"folder-{opportunity_id}")
    return LabsLocalRecord.objects.create(
        opportunity_id=opportunity_id, experiment=str(opportunity_id), type="visit_note", data={"value": SENTINEL}
    )


# ---------------------------------------------------------------------------
# The lists
# ---------------------------------------------------------------------------


def test_every_restricted_tool_exists():
    from connect_labs.mcp import tools  # noqa: F401 -- registers the catalogue

    for name in token_scopes.RESTRICTED_TOOLS | token_scopes.USERVISIT_DATA_TOOLS:
        assert get_tool(name) is not None, f"token_scopes names a tool that does not exist: {name}"


def test_no_visit_data_tool_is_reachable_outright():
    outright = token_scopes.NO_USERVISIT_DATA_TOOLS | token_scopes.SYNTHETIC_TOOLS
    assert outright.isdisjoint(token_scopes.USERVISIT_DATA_TOOLS)
    assert token_scopes.USERVISIT_DATA_TOOLS & token_scopes.RESTRICTED_TOOLS <= token_scopes.GENERATED_ONLY_TOOLS


def test_every_generated_only_tool_has_a_resolver():
    assert set(visit_access.RESOLVERS) == token_scopes.GENERATED_ONLY_TOOLS


def test_reachable_outright_tools_do_not_write_except_the_synthetic_flow():
    from connect_labs.mcp import tools  # noqa: F401

    writers = {name for name in token_scopes.NO_USERVISIT_DATA_TOOLS if _REGISTRY[name].is_write}
    assert writers == set()


# ---------------------------------------------------------------------------
# The endpoint caps any credential; a restricted credential is capped anywhere
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_full_token_on_the_safe_endpoint_sees_only_the_restricted_tools():
    user = _user("safe-endpoint")
    _, raw = MCPAccessToken.create_token(user, name="laptop")

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        refused = await mcp_client.call_tool("get_sample_ids", {}, raise_on_error=False)
        return tools, refused

    tools, refused = _call(SAFE_URL, raw, work)

    assert {tool.name for tool in tools} == token_scopes.RESTRICTED_TOOLS
    assert refused.is_error


@pytest.mark.django_db(transaction=True)
def test_the_same_full_token_on_the_full_endpoint_sees_everything():
    user = _user("full-endpoint")
    _, raw = MCPAccessToken.create_token(user, name="laptop")

    async def work(mcp_client):
        return await mcp_client.list_tools()

    names = {tool.name for tool in _call(FULL_URL, raw, work)}

    assert {"get_sample_ids", "pipeline_preview", "workflow_delete"} <= names
    assert len(names) > len(token_scopes.RESTRICTED_TOOLS)


@pytest.mark.django_db(transaction=True)
def test_a_restricted_pat_is_restricted_on_the_full_endpoint():
    user = _user("restricted-pat")
    _, raw = MCPAccessToken.create_token(user, name="agent", scope=token_scopes.NO_USERVISIT_DATA)

    async def work(mcp_client):
        return await mcp_client.list_tools()

    assert {tool.name for tool in _call(FULL_URL, raw, work)} == token_scopes.RESTRICTED_TOOLS


@pytest.mark.django_db(transaction=True)
def test_an_oauth_sign_in_with_the_restricted_scope_is_restricted_on_the_full_endpoint():
    user = _user("restricted-oauth")
    raw = _access_token(user, _mcp_application(), scope=oauth.MCP_NO_USERVISIT_SCOPE)

    async def work(mcp_client):
        return await mcp_client.list_tools()

    assert {tool.name for tool in _call(FULL_URL, raw, work)} == token_scopes.RESTRICTED_TOOLS


@pytest.mark.django_db(transaction=True)
def test_the_restricted_endpoint_and_the_restricted_token_grant_the_same_tools():
    """The /mcp/no_user_visit/ address and a no-uservisit-data PAT are two ways to ask
    for the same permissions -- definition writes included -- and must never drift."""
    user = _user("same-reach")
    _, full = MCPAccessToken.create_token(user, name="laptop")
    _, restricted = MCPAccessToken.create_token(user, name="agent", scope=token_scopes.NO_USERVISIT_DATA)

    async def work(mcp_client):
        return await mcp_client.list_tools()

    by_endpoint = {tool.name for tool in _call(SAFE_URL, full, work)}
    by_token = {tool.name for tool in _call(FULL_URL, restricted, work)}

    assert by_endpoint == by_token == token_scopes.RESTRICTED_TOOLS
    assert token_scopes.DEFINITION_WRITE_TOOLS <= by_endpoint


@pytest.mark.django_db(transaction=True)
def test_a_definition_write_on_the_restricted_endpoint_passes_the_scope_gate():
    user = _user("endpoint-write")
    _, full = MCPAccessToken.create_token(user, name="laptop")

    async def work(mcp_client):
        return await mcp_client.call_tool(
            "semantic_registry_update", {"registry_id": 999999, "name": "x"}, raise_on_error=False
        )

    result = _call(SAFE_URL, full, work)

    # No such registry: the handler answers, not the scope gate.
    assert "scope does not include" not in result.content[0].text
    assert MCPAuditLog.objects.filter(user=user, tool_name="semantic_registry_update").exists()


@pytest.mark.django_db(transaction=True)
def test_a_token_holding_both_scopes_is_restricted():
    user = _user("both-scopes")
    raw = _access_token(user, _mcp_application(), scope=f"{oauth.MCP_SCOPE} {oauth.MCP_NO_USERVISIT_SCOPE}")

    async def work(mcp_client):
        return await mcp_client.list_tools()

    assert {tool.name for tool in _call(FULL_URL, raw, work)} == token_scopes.RESTRICTED_TOOLS


# ---------------------------------------------------------------------------
# Visit-reading tools run only on generated data (canary: the sentinel never leaks)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_safe_endpoint_reads_a_generated_opp_but_never_an_unmarked_one():
    user = _user("canary")
    generated = _synthetic_opp(10801, user=user, generated=True)
    unmarked = _synthetic_opp(10802, user=user, generated=False)
    _, raw = MCPAccessToken.create_token(user, name="laptop")

    async def work(mcp_client):
        ok = await mcp_client.call_tool(
            "synthetic_local_record_dump", {"opportunity_id": 10801, "record_id": generated.id}
        )
        refused = await mcp_client.call_tool(
            "synthetic_local_record_dump",
            {"opportunity_id": 10802, "record_id": unmarked.id},
            raise_on_error=False,
        )
        return ok, refused

    ok, refused = _call(SAFE_URL, raw, work)

    assert ok.structured_content["data"] == {"value": SENTINEL}
    assert refused.is_error
    assert SENTINEL not in str(refused.content)
    assert MCPAuditLog.objects.filter(
        user=user, tool_name="synthetic_local_record_dump", success=False, error_code="PERMISSION_DENIED"
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_the_full_endpoint_still_reads_an_unmarked_opp():
    """The control for the canary: the refusal above is the rule, not a broken tool."""
    user = _user("canary-control")
    unmarked = _synthetic_opp(10803, user=user, generated=False)
    _, raw = MCPAccessToken.create_token(user, name="laptop")

    async def work(mcp_client):
        return await mcp_client.call_tool(
            "synthetic_local_record_dump", {"opportunity_id": 10803, "record_id": unmarked.id}
        )

    assert _call(FULL_URL, raw, work).structured_content["data"] == {"value": SENTINEL}


@pytest.mark.django_db
def test_a_pipeline_preview_is_refused_if_any_opp_it_fans_out_to_is_not_generated():
    user = _user("fan-out")
    _synthetic_opp(10804, user=user, generated=True)
    _synthetic_opp(10805, user=user, generated=False)

    assert visit_access.denied_reason(user, "pipeline_preview", {"opportunity_id": 10804}) is None
    assert visit_access.denied_reason(user, "pipeline_preview", {"opportunity_id": 10804, "opportunity_ids": [10805]})
    assert visit_access.denied_reason(user, "pipeline_preview", {"opportunity_id": 10804, "opportunity_ids": [501]})


@pytest.mark.django_db
def test_a_workflow_run_is_refused_if_its_definition_reads_a_real_opp(monkeypatch):
    """The scope is generated, but the definition's opportunity_ids name a real opp."""
    user = _user("workflow-span")
    _synthetic_opp(10806, user=user, generated=True)

    class _Definition:
        opportunity_id = None
        opportunity_ids = [10806, 501]

    monkeypatch.setattr(visit_access, "_read_definition", lambda *a, **k: _Definition())
    assert visit_access.denied_reason(user, "workflow_run_context", {"run_id": 1, "opportunity_id": 10806})

    _Definition.opportunity_ids = [10806]
    assert visit_access.denied_reason(user, "workflow_run_context", {"run_id": 1, "opportunity_id": 10806}) is None


@pytest.mark.django_db
def test_a_workflow_scope_that_is_not_generated_is_refused_before_anything_is_read(monkeypatch):
    user = _user("workflow-scope")

    def _must_not_read(*args, **kwargs):
        raise AssertionError("read a definition through a scope the caller may not read")

    monkeypatch.setattr(visit_access, "_read_definition", _must_not_read)

    assert visit_access.denied_reason(user, "workflow_run_context", {"run_id": 1, "opportunity_id": 501})
    assert visit_access.denied_reason(user, "workflow_history_runs", {"definition_id": 1, "program_id": 501})


# ---------------------------------------------------------------------------
# Sign-in discovery for the safe endpoint
# ---------------------------------------------------------------------------


def _get(path):
    import anyio

    from config.asgi import build_application

    app = build_application()

    async def _run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            return await c.get(path)

    return anyio.run(_run)


def _post_unauthenticated(path):
    import anyio

    from config.asgi import build_application

    app = build_application()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    }

    async def _run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
                return await c.post(path, json=payload, headers={"Accept": "application/json, text/event-stream"})

    return anyio.run(_run)


@pytest.mark.django_db(transaction=True)
def test_the_safe_endpoint_challenge_asks_for_the_restricted_scope():
    challenge = _post_unauthenticated("/mcp/no_user_visit/").headers.get("www-authenticate", "")

    assert f'resource_metadata="{oauth.protected_resource_metadata_url(restricted=True)}"' in challenge
    assert f'scope="{oauth.MCP_NO_USERVISIT_SCOPE}"' in challenge


@pytest.mark.django_db(transaction=True)
def test_the_full_endpoint_challenge_asks_for_the_full_scope():
    challenge = _post_unauthenticated("/mcp/").headers.get("www-authenticate", "")

    assert f'resource_metadata="{oauth.protected_resource_metadata_url()}"' in challenge
    assert f'scope="{oauth.MCP_SCOPE}"' in challenge


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource/mcp/no_user_visit",
        "/.well-known/oauth-protected-resource/mcp/no_user_visit/",
    ],
)
def test_the_safe_endpoint_metadata_names_it_and_its_scope(path):
    body = _get(path).json()

    assert body["resource"] == oauth.resource_url(restricted=True)
    assert body["resource"].endswith("/mcp/no_user_visit/")
    assert body["scopes_supported"] == [oauth.MCP_NO_USERVISIT_SCOPE]


@pytest.mark.django_db
def test_an_mcp_client_may_ask_for_the_restricted_scope():
    from connect_labs.mcp.oauth import MCPScopes

    assert oauth.MCP_NO_USERVISIT_SCOPE in MCPScopes().get_available_scopes(application=_mcp_application())


@pytest.mark.parametrize(("auth_method", "is_delegated"), [("pat", False), ("oauth", False), ("delegated", True)])
def test_a_restricted_call_is_not_mistaken_for_canopy(monkeypatch, auth_method, is_delegated):
    """workflow_run's canopy-only rule (the definition must share with the agent) keys
    on the credential, not on the call being limited: a restricted call is limited too."""
    from fastmcp.server import dependencies

    from connect_labs.mcp.tools import workflow_run

    class _Token:
        claims = {"auth_method": auth_method}
        scopes = [oauth.MCP_NO_USERVISIT_SCOPE]

    monkeypatch.setattr(dependencies, "get_access_token", lambda: _Token())

    assert (workflow_run._delegated_token() is not None) is is_delegated


# ---------------------------------------------------------------------------
# A person an admin set to "no user visit data" is restricted everywhere
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("url", [FULL_URL, SAFE_URL])
def test_a_person_set_to_no_uservisit_data_is_restricted_with_a_full_token_on_either_address(url):
    user = _user(f"locked-{'safe' if url == SAFE_URL else 'full'}")
    user.mcp_no_uservisit_data = True
    user.save()
    _, raw = MCPAccessToken.create_token(user, name="laptop")  # a FULL token

    async def work(mcp_client):
        return await mcp_client.list_tools()

    assert {tool.name for tool in _call(url, raw, work)} == token_scopes.RESTRICTED_TOOLS


@pytest.mark.django_db(transaction=True)
def test_a_person_set_to_no_uservisit_data_is_restricted_when_signed_in_with_oauth():
    user = _user("locked-oauth")
    user.mcp_no_uservisit_data = True
    user.save()
    raw = _access_token(user, _mcp_application())  # the full `mcp` scope

    async def work(mcp_client):
        return await mcp_client.list_tools()

    assert {tool.name for tool in _call(FULL_URL, raw, work)} == token_scopes.RESTRICTED_TOOLS


def test_the_person_flag_restricts_a_delegated_call_too():
    from connect_labs.mcp.server import restricted_call

    class _Token:
        claims = {"auth_method": "delegated", "labs_restricted_user": True}
        scopes = []

    assert restricted_call(_Token())
