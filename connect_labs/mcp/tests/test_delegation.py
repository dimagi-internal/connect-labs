"""Canopy acting as a labs visitor: labs' wiring of the canopy SDK's host half.

The protocol — every refusal the jwt-bearer grant and the DPoP gate make — is
the canopy SDK's (``canopy_sdk.host``) and is tested there, check by check,
against host grant contract v1. What is tested HERE is that labs is wired to it
correctly, end to end, through labs' real URLs and its real ASGI app:

* the SDK's own conformance suite (discovery, JWKS, redeem + replay, one MCP
  session, bearer + replayed-proof refusals) passes against labs in-process;
* a redeemed token runs only its scope's tools, AS the visitor, and is audited
  with the client and the actor;
* the grant is off until configured, shares ``/o/token/`` with the toolkit, and
  never lands in the toolkit's table;
* PATs and ordinary OAuth sign-ins are exactly what they were.
"""

from __future__ import annotations

from unittest import mock

import httpx
import pytest
from canopy_sdk import conformance, contract
from canopy_sdk.django.models import DelegatedToken
from canopy_sdk.host import issue_id_jag
from django.core.cache import cache
from starlette.testclient import TestClient

from connect_labs.labs import canopy
from connect_labs.mcp import oauth
from connect_labs.mcp.models import MCPAccessToken, MCPAuditLog
from connect_labs.mcp.server import _verify_bearer_sync
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.users.models import User

BASE = "https://labs.example.org"
ISSUER = BASE
TOKEN_ENDPOINT = f"{BASE}/o/token/"
RESOURCE = f"{BASE}/mcp/"
JWKS_URL = f"{BASE}/labs/canopy/jwks/"


def _host_key() -> str:
    from canopy_sdk.keys import generate_private_key, private_pem

    return private_pem(generate_private_key("EdDSA"))


@pytest.fixture
def enabled(settings, canopy_client, canopy_client_documents):
    """Labs with the grant on, trusting the conformance plugin's canopy client.

    Labs fetches canopy's metadata document and JWKS through the SDK's SSRF-safe
    fetch; here that fetch answers from the plugin's documents instead.
    """
    settings.LABS_PUBLIC_URL = BASE
    settings.ALLOWED_HOSTS = ["labs.example.org", "testserver"]
    settings.CANOPY_BASE_URL = f"{BASE}/canopy"
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_SIGNING_KEY = _host_key()
    settings.CANOPY_CLIENT_ID = canopy_client.client_id
    cache.clear()

    def fetch(url, **kwargs):
        return canopy_client_documents[url]

    with mock.patch("canopy_sdk.fetch.get_json", side_effect=fetch):
        yield canopy_client
    cache.clear()


@pytest.fixture
def visitor(db):
    return User.objects.create(username="gillian", email="g@example.invalid")


def _id_jag(user, scopes=("marketplace:read",)) -> str:
    return issue_id_jag(canopy.host_config(), str(user.pk), scopes)


def _redeem(client, canopy_redeem, user, **extra):
    form, proof = canopy_redeem(_id_jag(user), TOKEN_ENDPOINT, RESOURCE, ISSUER)
    form.update(extra)
    return client.post("/o/token/", form, headers={"DPoP": proof})


# ---------------------------------------------------------------------------
# The SDK's own conformance suite, against labs' real app
# ---------------------------------------------------------------------------


class _Labs:
    """The conformance checks' transports, pointed at labs' ASGI app in-process.

    Labs' own URLs go to the app; canopy's (the client metadata the checks also
    fetch) answer from the plugin's documents.
    """

    def __init__(self, http: TestClient, documents: dict):
        self.http = http
        self.documents = documents

    def fetch_json(self, url: str) -> dict:
        if url in self.documents:
            return self.documents[url]
        response = self.http.get(url)
        if response.status_code != 200:
            from canopy_sdk.fetch import FetchError

            raise FetchError(f"{url} answered {response.status_code}")
        return response.json()

    def post_form(self, url, data, headers=None, what=""):
        response = self.http.post(url, data=data, headers=headers or {})
        try:
            body = response.json()
        except ValueError:
            body = {}
        return response.status_code, body, dict(response.headers)

    def post_json(self, url, payload, *, headers=None):
        response = self.http.post(url, json=payload, headers=headers or {})
        return response.status_code, response.content, dict(response.headers)


@pytest.mark.django_db(transaction=True)
def test_the_sdk_conformance_suite_passes_against_labs(enabled, canopy_client_documents, visitor):
    from config.asgi import build_application

    with TestClient(build_application(), base_url=BASE) as http:
        labs = _Labs(http, canopy_client_documents)
        report = conformance.run(
            ISSUER,
            RESOURCE,
            jwks_url=JWKS_URL,
            id_jag=_id_jag(visitor),
            credentials=enabled,
            fetch_json=labs.fetch_json,
            post_form=labs.post_form,
            post_json=labs.post_json,
        )

    report.raise_for_failures()
    names = {check.name for check in report.checks}
    assert {"grant_redeemed", "grant_single_use", "mcp_tools_list", "mcp_refuses_bound_token_as_bearer"} <= names


# ---------------------------------------------------------------------------
# The grant, through labs' /o/token/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_redeemed_token_is_stored_as_a_hash_bound_to_the_visitor(client, enabled, canopy_redeem, visitor):
    response = _redeem(client, canopy_redeem, visitor)

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["token_type"] == "DPoP"
    assert 0 < body["expires_in"] <= 900
    assert body["scope"] == "marketplace:read"
    assert "refresh_token" not in body
    row = DelegatedToken.objects.get()
    assert row.subject == str(visitor.pk)
    assert row.client_id == enabled.client_id
    assert row.cnf_jkt == enabled.dpop_jkt
    assert row.token_checksum == contract.token_checksum(body["access_token"])


@pytest.mark.django_db
def test_the_token_is_not_in_the_toolkits_table(client, enabled, canopy_redeem, visitor):
    """django-oauth-toolkit authenticates labs' REST API with ANY live row in its
    table, so a delegated token must never be one."""
    from oauth2_provider.models import get_access_token_model

    token = _redeem(client, canopy_redeem, visitor).json()["access_token"]

    assert not get_access_token_model().objects.exists()
    assert client.get("/api/", headers={"Authorization": f"Bearer {token}"}).status_code in (401, 403, 404)


@pytest.mark.django_db
def test_the_grant_is_off_until_a_canopy_client_is_configured(client, enabled, canopy_redeem, visitor, settings):
    form, proof = canopy_redeem(_id_jag(visitor), TOKEN_ENDPOINT, RESOURCE, ISSUER)
    settings.CANOPY_CLIENT_ID = ""

    response = client.post("/o/token/", form, headers={"DPoP": proof})

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"
    assert not DelegatedToken.objects.exists()


@pytest.mark.django_db
def test_an_id_jag_for_a_deactivated_labs_user_is_refused(client, enabled, canopy_redeem, visitor):
    """The SDK asks labs whether the subject is live; labs answers from its users."""
    form, proof = canopy_redeem(_id_jag(visitor), TOKEN_ENDPOINT, RESOURCE, ISSUER)
    visitor.is_active = False
    visitor.save()

    response = client.post("/o/token/", form, headers={"DPoP": proof})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_other_grant_types_still_reach_the_toolkit(client, enabled):
    """The endpoint is shared: everything but jwt-bearer is django-oauth-toolkit's answer."""
    response = client.post("/o/token/", {"grant_type": "client_credentials", "client_id": "nobody"})

    assert response.status_code in (400, 401)
    assert response.json()["error"] in ("invalid_client", "unsupported_grant_type")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_metadata_advertises_the_grant_only_when_it_is_on(settings):
    settings.LABS_PUBLIC_URL = BASE
    settings.CANOPY_SIGNING_KEY = _host_key()
    settings.CANOPY_CLIENT_ID = ""
    off = oauth.authorization_server_metadata()
    assert contract.JWT_BEARER_GRANT not in off["grant_types_supported"]
    assert "dpop_signing_alg_values_supported" not in off
    assert "dpop_signing_alg_values_supported" not in oauth.protected_resource_metadata()

    settings.CANOPY_CLIENT_ID = f"{BASE}/canopy/oauth/client.json"
    on = oauth.authorization_server_metadata()
    assert on["grant_types_supported"] == ["authorization_code", "refresh_token", contract.JWT_BEARER_GRANT]
    assert on["token_endpoint_auth_methods_supported"] == ["none", "private_key_jwt"]
    assert set(on["dpop_signing_alg_values_supported"]) == {"EdDSA", "ES256"}
    assert on["scopes_supported"] == ["mcp", "marketplace:read", "workflow:act", "workflow:read"]
    assert on["token_endpoint"] == TOKEN_ENDPOINT
    prm = oauth.protected_resource_metadata()
    assert prm["dpop_signing_alg_values_supported"] == ["EdDSA", "ES256"]
    assert prm["scopes_supported"] == ["mcp", "marketplace:read", "workflow:act", "workflow:read"]
    assert prm["resource"] == RESOURCE


def test_no_signing_key_means_no_grant_is_advertised(settings):
    settings.LABS_PUBLIC_URL = BASE
    settings.CANOPY_CLIENT_ID = f"{BASE}/canopy/oauth/client.json"
    settings.CANOPY_SIGNING_KEY = ""

    assert contract.JWT_BEARER_GRANT not in oauth.authorization_server_metadata()["grant_types_supported"]


# ---------------------------------------------------------------------------
# Labs' scope -> tool map
# ---------------------------------------------------------------------------


def test_every_scoped_tool_exists_and_only_previewed_scopes_write():
    """A scope reaches writes only if every tool in it acts solely on a confirmed
    preview (workflow/actions.py) -- never on one call."""
    from connect_labs.mcp import tools  # noqa: F401 -- registers the catalogue

    for scope, names in canopy.SCOPE_TOOLS.items():
        for name in names:
            spec = get_tool(name)
            assert spec is not None, f"{scope} names a tool that does not exist: {name}"
            if scope not in canopy.PREVIEWED_WRITE_SCOPES:
                assert not spec.is_write, f"{scope} is a read scope but {name} writes"
    assert canopy.PREVIEWED_WRITE_SCOPES == {"workflow:act"}
    assert canopy.SCOPE_TOOLS["workflow:act"] == {"workflow_run_action"}
    assert all(scope.endswith(":read") for scope in set(canopy.SCOPE_TOOLS) - canopy.PREVIEWED_WRITE_SCOPES)


def test_every_page_scope_is_one_the_server_offers():
    for page, scopes in canopy.PAGE_SCOPES.items():
        assert scopes, page
        assert set(scopes) <= set(canopy.SCOPE_TOOLS), page


# ---------------------------------------------------------------------------
# Labs' MCP token verifier
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_bound_token_resolves_to_the_visitor_only_with_its_own_key(client, enabled, canopy_redeem, visitor):
    raw = _redeem(client, canopy_redeem, visitor).json()["access_token"]
    jkt = enabled.dpop_jkt

    assert _verify_bearer_sync(raw, None) is None, "a bound token is not a bearer token"
    assert _verify_bearer_sync(raw, "some-other-thumbprint") is None
    user, method, client_id, scopes, extra = _verify_bearer_sync(raw, jkt)
    assert user == visitor
    assert method == "delegated"
    assert client_id == enabled.client_id
    assert scopes == ["marketplace:read"]
    assert extra["cnf"] == {"jkt": jkt}
    assert extra["act"] == {"sub": enabled.client_id}

    visitor.is_active = False
    visitor.save()
    assert _verify_bearer_sync(raw, jkt) is None, "a deactivated visitor's token stops working at once"


@pytest.mark.django_db
def test_a_labs_without_a_signing_key_resolves_no_delegated_token(settings, visitor):
    settings.CANOPY_SIGNING_KEY = ""

    assert _verify_bearer_sync("anything", "some-thumbprint") is None


# ---------------------------------------------------------------------------
# End to end over Streamable HTTP
# ---------------------------------------------------------------------------


def _factory(app, *, on_request=None):
    def factory(headers=None, timeout=None, auth=None, **kwargs):
        kwargs.pop("transport", None)
        hooks = {"request": [on_request]} if on_request else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            event_hooks=hooks,
            **kwargs,
        )

    return factory


def _run_mcp(app, headers, on_request, work):
    import anyio
    from fastmcp import Client as MCPClient
    from fastmcp.client.transports import StreamableHttpTransport

    async def _run():
        transport = StreamableHttpTransport(
            url="http://testserver/mcp/", headers=headers, httpx_client_factory=_factory(app, on_request=on_request)
        )
        async with app.router.lifespan_context(app):
            async with MCPClient(transport) as mcp_client:
                return await work(mcp_client)

    return anyio.run(_run)


@pytest.mark.django_db(transaction=True)
def test_canopy_calls_only_its_scoped_tools_as_the_visitor(enabled, canopy_redeem, visitor):
    from django.test import Client

    from config.asgi import build_application

    token = _redeem(Client(), canopy_redeem, visitor).json()["access_token"]
    app = build_application()

    async def add_proof(request):
        # A fresh proof per request, as canopy sends. `htu` is labs' PUBLIC MCP
        # URL, not the in-process one the request goes to.
        request.headers["DPoP"] = enabled.dpop_proof(request.method, RESOURCE, access_token=token)

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        called = await mcp_client.call_tool("marketplace_rounds_list", {})
        refused = await mcp_client.call_tool("list_templates", {}, raise_on_error=False)
        return tools, called, refused

    tools, called, refused = _run_mcp(app, {"Authorization": f"DPoP {token}", "Canopy-Actor": "ace"}, add_proof, work)

    assert {tool.name for tool in tools} == {"marketplace_orgs_get", "marketplace_rounds_list"}
    assert called.structured_content is not None
    assert refused.is_error

    row = MCPAuditLog.objects.get(tool_name="marketplace_rounds_list", success=True)
    assert row.user == visitor, "the tool ran as the visitor"
    assert row.client_id == enabled.client_id
    assert row.actor == "ace"


def _raw_mcp_post(app, headers):
    import anyio

    async def _run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
                return await c.post(
                    "/mcp/",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream", **headers},
                )

    return anyio.run(_run)


@pytest.mark.django_db(transaction=True)
def test_a_bound_token_with_no_proof_is_refused_as_dpop_not_as_bearer(enabled, canopy_redeem, visitor):
    """The gate sits OUTSIDE labs' Bearer challenge, so its RFC 9449 refusal
    reaches canopy intact rather than rewritten into a sign-in prompt."""
    from django.test import Client

    from config.asgi import build_application

    token = _redeem(Client(), canopy_redeem, visitor).json()["access_token"]

    response = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {token}"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_dpop_proof"
    assert response.headers["www-authenticate"].startswith("DPoP ")


@pytest.mark.django_db(transaction=True)
def test_a_dpop_request_to_a_labs_without_a_key_is_refused_not_an_error(enabled, settings):
    from config.asgi import build_application

    settings.CANOPY_SIGNING_KEY = ""
    proof = enabled.dpop_proof("POST", RESOURCE, access_token="whatever")

    response = _raw_mcp_post(build_application(), {"Authorization": "DPoP whatever", "DPoP": proof})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_dpop_proof"


# ---------------------------------------------------------------------------
# Regression: the other two ways in are exactly what they were
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["pat", "oauth"])
def test_pats_and_oauth_sign_ins_are_unchanged(kind, enabled):
    """No DPoP, the whole catalogue, and no delegation fields in the audit —
    even with the grant switched on."""
    from config.asgi import build_application
    from connect_labs.mcp.tests.test_oauth import _access_token, _mcp_application

    user = User.objects.create(username=f"direct-{kind}")
    if kind == "pat":
        _, raw = MCPAccessToken.create_token(user, name="regression")
    else:
        raw = _access_token(user, _mcp_application())

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        await mcp_client.call_tool("list_templates", {})
        return tools

    tools = _run_mcp(build_application(), {"Authorization": f"Bearer {raw}", "Canopy-Actor": "spoof"}, None, work)

    names = {tool.name for tool in tools}
    assert "list_templates" in names
    assert "marketplace_orgs_get" in names
    assert len(names) > 100, "the full catalogue, not a scoped slice"
    row = MCPAuditLog.objects.get(user=user, tool_name="list_templates", success=True)
    assert row.client_id == ""
    assert row.actor == "", "the actor header means nothing without a delegated token"


@pytest.mark.django_db(transaction=True)
def test_a_pat_sent_with_a_dpop_scheme_is_not_a_delegated_token(enabled):
    """A token the SDK did not issue gets nothing from the DPoP scheme: the PAT
    still resolves as a PAT, with the whole catalogue, exactly as before."""
    user = User.objects.create(username="pat-over-dpop")
    _, raw = MCPAccessToken.create_token(user, name="regression")

    resolved = _verify_bearer_sync(raw, enabled.dpop_jkt)

    assert resolved[0] == user
    assert resolved[1] == "pat"
