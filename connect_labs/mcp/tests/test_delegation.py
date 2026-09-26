"""Canopy acting as a labs visitor: the jwt-bearer grant, DPoP, and the scoped MCP.

The contract under test is canopy-web's host-grant contract v1. Most of this
file is refusals, because that is where the security lives: every check the
grant and the MCP gate make is exercised by a request that fails exactly that
check and nothing else. The happy path is asserted once end to end, over the
real Streamable-HTTP app. And the other two ways in — PATs and ordinary OAuth
sign-ins — are asserted to be exactly what they were.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import uuid
from datetime import timedelta
from unittest import mock

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from django.core.cache import cache
from django.utils import timezone
from jwt.algorithms import ECAlgorithm, OKPAlgorithm

from connect_labs.labs import canopy
from connect_labs.mcp import client_metadata, delegation, dpop, oauth
from connect_labs.mcp.models import DelegatedAccessToken, MCPAccessToken, MCPAuditLog
from connect_labs.mcp.server import _verify_bearer_sync
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.users.models import User

BASE = "https://labs.example.org"
ISSUER = BASE
TOKEN_ENDPOINT = f"{BASE}/o/token/"
RESOURCE = f"{BASE}/mcp/"
CLIENT_ID = f"{BASE}/canopy/oauth/client.json"
JWKS_URI = f"{BASE}/canopy/oauth/jwks.json"


# ---------------------------------------------------------------------------
# Keys and signed things
# ---------------------------------------------------------------------------


def _pem(private) -> str:
    return private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _okp_jwk(private, kid=None) -> dict:
    jwk = OKPAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"use": "sig", "alg": "EdDSA", "kid": kid or dpop.jwk_thumbprint(jwk)})
    return jwk


def _ec_jwk(private) -> dict:
    return ECAlgorithm.to_jwk(private.public_key(), as_dict=True)


class Keys:
    def __init__(self):
        self.host = ed25519.Ed25519PrivateKey.generate()
        self.client = ed25519.Ed25519PrivateKey.generate()
        self.client_jwk = _okp_jwk(self.client, kid="canopy-client-1")
        self.dpop = ec.generate_private_key(ec.SECP256R1())
        self.dpop_jwk = _ec_jwk(self.dpop)


@pytest.fixture
def keys():
    return Keys()


@pytest.fixture
def enabled(settings, keys):
    settings.LABS_PUBLIC_URL = BASE
    settings.CANOPY_BASE_URL = f"{BASE}/canopy"
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_SIGNING_KEY = _pem(keys.host)
    settings.CANOPY_CLIENT_ID = CLIENT_ID
    cache.clear()
    documents = {
        CLIENT_ID: {
            "client_id": CLIENT_ID,
            "client_name": "canopy",
            "jwks_uri": JWKS_URI,
            "token_endpoint_auth_method": "private_key_jwt",
            "grant_types": [delegation.JWT_BEARER_GRANT],
            "dpop_bound_access_tokens": True,
        },
        JWKS_URI: {"keys": [keys.client_jwk]},
    }
    with mock.patch.object(client_metadata, "_fetch", side_effect=lambda url: documents[url]) as fetch:
        fetch.documents = documents
        yield fetch
    cache.clear()


@pytest.fixture
def visitor(db):
    return User.objects.create(username="gillian", email="g@example.invalid")


def _client_assertion(keys, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": CLIENT_ID,
        "sub": CLIENT_ID,
        "aud": ISSUER,
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return jwt.encode(claims, keys.client, algorithm="EdDSA", headers={"kid": keys.client_jwk["kid"]})


def _proof(keys, htm="POST", htu=TOKEN_ENDPOINT, access_token=None, key=None, jwk=None, alg="ES256", **overrides):
    claims = {"htm": htm, "htu": htu, "iat": int(time.time()), "jti": str(uuid.uuid4())}
    if access_token is not None:
        claims["ath"] = base64.urlsafe_b64encode(hashlib.sha256(access_token.encode()).digest()).decode().rstrip("=")
    claims.update(overrides)
    return jwt.encode(
        claims,
        key or keys.dpop,
        algorithm=alg,
        headers={"typ": "dpop+jwt", "jwk": jwk or keys.dpop_jwk},
    )


def _id_jag(keys, user, *, key=None, alg="EdDSA", headers=None, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": ISSUER,
        "sub": str(user.pk),
        "client_id": CLIENT_ID,
        "resource": RESOURCE,
        "scope": "marketplace:read",
        "iat": now,
        "exp": now + 120,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    host_kid = canopy.public_jwk()["kid"]
    return jwt.encode(
        claims,
        key or keys.host,
        algorithm=alg,
        headers=headers or {"kid": host_kid, "typ": "oauth-id-jag+jwt"},
    )


def _redeem(client, keys, user, *, assertion=None, client_assertion=None, proof="default", extra=None, drop=()):
    data = {
        "grant_type": delegation.JWT_BEARER_GRANT,
        "assertion": assertion if assertion is not None else canopy.id_jag_for(user, ["marketplace:read"]),
        "client_id": CLIENT_ID,
        "client_assertion_type": delegation.CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion if client_assertion is not None else _client_assertion(keys),
        "resource": RESOURCE,
    }
    data.update(extra or {})
    for name in drop:
        data.pop(name, None)
    headers = {}
    if proof == "default":
        proof = _proof(keys)
    if proof is not None:
        headers["DPoP"] = proof
    return client.post("/o/token/", data, headers=headers)


def _ath(token: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(token.encode()).digest()).decode().rstrip("=")


# ---------------------------------------------------------------------------
# The grant: success
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_valid_grant_issues_a_short_dpop_bound_token_with_no_refresh(client, keys, enabled, visitor):
    response = _redeem(client, keys, visitor)

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["token_type"] == "DPoP"
    assert 0 < body["expires_in"] <= 900
    assert body["scope"] == "marketplace:read"
    assert "refresh_token" not in body
    assert response["Cache-Control"] == "no-store"

    row = DelegatedAccessToken.objects.get()
    assert row.user == visitor
    assert row.client_id == CLIENT_ID
    assert row.actor == CLIENT_ID
    assert row.cnf_jkt == dpop.jwk_thumbprint(keys.dpop_jwk)
    assert row.token_checksum == hashlib.sha256(body["access_token"].encode()).hexdigest()
    assert body["access_token"] not in row.token_checksum, "only the hash is stored"


@pytest.mark.django_db
def test_the_token_is_not_in_the_toolkits_table(client, keys, enabled, visitor):
    """django-oauth-toolkit authenticates labs' REST API with ANY live row in its
    table, so a delegated token must never be one."""
    from oauth2_provider.models import get_access_token_model

    token = _redeem(client, keys, visitor).json()["access_token"]

    assert not get_access_token_model().objects.exists()
    assert client.get("/api/", headers={"Authorization": f"Bearer {token}"}).status_code in (401, 403, 404)


@pytest.mark.django_db
def test_a_requested_scope_may_narrow_but_not_widen(client, keys, enabled, visitor):
    widened = _redeem(client, keys, visitor, extra={"scope": "marketplace:read mcp"})
    assert widened.status_code == 400
    assert widened.json()["error"] == "invalid_scope"

    narrowed = _redeem(client, keys, visitor, extra={"scope": "marketplace:read"})
    assert narrowed.status_code == 200, narrowed.content


# ---------------------------------------------------------------------------
# The grant: refusals
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_grant_is_off_until_a_canopy_client_is_configured(client, keys, enabled, visitor, settings):
    assertion = canopy.id_jag_for(visitor, ["marketplace:read"])
    settings.CANOPY_CLIENT_ID = ""

    response = _redeem(client, keys, visitor, assertion=assertion)

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_grant_type"
    assert not DelegatedAccessToken.objects.exists()


@pytest.mark.django_db
def test_only_the_configured_client_may_redeem(client, keys, enabled, visitor):
    response = _redeem(client, keys, visitor, extra={"client_id": "https://evil.example/client.json"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
def test_the_client_must_authenticate(client, keys, enabled, visitor):
    response = _redeem(client, keys, visitor, drop=("client_assertion",))

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
def test_a_client_assertion_by_an_unpublished_key_is_refused(client, keys, enabled, visitor):
    forged = jwt.encode(
        {
            "iss": CLIENT_ID,
            "sub": CLIENT_ID,
            "aud": ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": "x1",
        },
        ed25519.Ed25519PrivateKey.generate(),
        algorithm="EdDSA",
        headers={"kid": keys.client_jwk["kid"]},
    )

    response = _redeem(client, keys, visitor, client_assertion=forged)

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"aud": "https://someone-else.example"}, id="wrong_aud"),
        pytest.param({"iss": "https://someone-else.example"}, id="wrong_iss"),
        pytest.param({"exp": int(time.time()) - 120, "iat": int(time.time()) - 150}, id="expired"),
        pytest.param({"exp": int(time.time()) + 600}, id="lives_too_long"),
    ],
)
def test_a_bad_client_assertion_is_refused(client, keys, enabled, visitor, overrides):
    response = _redeem(client, keys, visitor, client_assertion=_client_assertion(keys, **overrides))

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
def test_an_hmac_client_assertion_is_refused(client, keys, enabled, visitor):
    hmac_assertion = jwt.encode(
        {
            "iss": CLIENT_ID,
            "sub": CLIENT_ID,
            "aud": ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": "h1",
        },
        "a-shared-secret-of-sufficient-length-for-hs256",
        algorithm="HS256",
    )

    response = _redeem(client, keys, visitor, client_assertion=hmac_assertion)

    assert response.status_code == 401


@pytest.mark.django_db
def test_unreachable_client_metadata_fails_closed(client, keys, enabled, visitor):
    enabled.side_effect = client_metadata.MetadataError("timed out")

    response = _redeem(client, keys, visitor)

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


@pytest.mark.django_db
def test_metadata_naming_a_different_client_is_refused(client, keys, enabled, visitor):
    enabled.documents[CLIENT_ID] = {**enabled.documents[CLIENT_ID], "client_id": "https://other.example/c.json"}

    assert _redeem(client, keys, visitor).status_code == 401


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"aud": "https://canopy.example"}, id="wrong_aud"),
        pytest.param({"iss": "https://canopy.example"}, id="wrong_iss"),
        pytest.param({"client_id": "https://other-client.example/c.json"}, id="other_client"),
        pytest.param({"resource": "https://other.example/mcp/"}, id="other_resource"),
        pytest.param({"exp": int(time.time()) - 120, "iat": int(time.time()) - 200}, id="expired"),
        pytest.param({"exp": int(time.time()) + 3600}, id="lives_too_long"),
        pytest.param({"scope": "admin:everything"}, id="unknown_scope"),
    ],
)
def test_a_bad_id_jag_is_refused(client, keys, enabled, visitor, overrides):
    response = _redeem(client, keys, visitor, assertion=_id_jag(keys, visitor, **overrides))

    assert response.status_code == 400
    assert response.json()["error"] in ("invalid_grant", "invalid_scope")
    assert not DelegatedAccessToken.objects.exists()


@pytest.mark.django_db
def test_an_id_jag_not_signed_by_labs_is_refused(client, keys, enabled, visitor):
    """Canopy holds no key labs trusts to name a user: a grant it signed itself,
    with its own client key, is worth nothing here."""
    forged = _id_jag(keys, visitor, key=keys.client)

    response = _redeem(client, keys, visitor, assertion=forged)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_an_id_jag_must_say_it_is_one(client, keys, enabled, visitor):
    """A visitor assertion is signed by the same key; it must not double as a grant."""
    response = _redeem(client, keys, visitor, assertion=canopy.assertion_for(visitor))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_an_id_jag_for_an_inactive_user_is_refused(client, keys, enabled, visitor):
    assertion = canopy.id_jag_for(visitor, ["marketplace:read"])
    visitor.is_active = False
    visitor.save()

    response = _redeem(client, keys, visitor, assertion=assertion)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_an_id_jag_works_once(client, keys, enabled, visitor):
    assertion = canopy.id_jag_for(visitor, ["marketplace:read"])

    first = _redeem(client, keys, visitor, assertion=assertion)
    second = _redeem(client, keys, visitor, assertion=assertion)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
    assert DelegatedAccessToken.objects.count() == 1


@pytest.mark.django_db
def test_a_client_assertion_works_once(client, keys, enabled, visitor):
    client_assertion = _client_assertion(keys)

    first = _redeem(client, keys, visitor, client_assertion=client_assertion)
    second = _redeem(client, keys, visitor, client_assertion=client_assertion)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_a_failed_redemption_does_not_burn_the_grant(client, keys, enabled, visitor):
    """jtis are consumed only once every check passed, so a request that fails on
    (say) its proof does not spend the grant it carried."""
    assertion = canopy.id_jag_for(visitor, ["marketplace:read"])

    refused = _redeem(client, keys, visitor, assertion=assertion, proof=None)
    accepted = _redeem(client, keys, visitor, assertion=assertion)

    assert refused.status_code == 400
    assert accepted.status_code == 200


@pytest.mark.django_db
def test_the_resource_must_be_this_mcp(client, keys, enabled, visitor):
    response = _redeem(client, keys, visitor, extra={"resource": "https://other.example/mcp/"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_target"


# --- DPoP at the token endpoint ------------------------------------------------


@pytest.mark.django_db
def test_a_grant_without_a_dpop_proof_is_refused(client, keys, enabled, visitor):
    response = _redeem(client, keys, visitor, proof=None)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_dpop_proof"
    assert "DPoP" in response["WWW-Authenticate"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda k: _proof(k, htu="https://labs.example.org/o/other/"), id="wrong_htu"),
        pytest.param(lambda k: _proof(k, htm="GET"), id="wrong_htm"),
        pytest.param(lambda k: _proof(k, iat=int(time.time()) - 600), id="stale"),
        pytest.param(lambda k: _proof(k, iat=int(time.time()) + 600), id="from_the_future"),
        pytest.param(
            lambda k: _proof(k, jwk=_ec_jwk(ec.generate_private_key(ec.SECP256R1()))), id="signed_by_another_key"
        ),
        pytest.param(lambda k: _proof(k, jwk={**k.dpop_jwk, "d": "c2VjcmV0"}), id="private_key_in_header"),
        pytest.param(lambda k: _proof(k, access_token="some-token"), id="ath_at_token_endpoint"),
        pytest.param(
            lambda k: jwt.encode(
                {"htm": "POST", "htu": TOKEN_ENDPOINT, "iat": int(time.time()), "jti": "j"},
                k.dpop,
                algorithm="ES256",
                headers={"typ": "JWT", "jwk": k.dpop_jwk},
            ),
            id="wrong_typ",
        ),
        pytest.param(
            lambda k: jwt.encode(
                {"htm": "POST", "htu": TOKEN_ENDPOINT, "iat": int(time.time()), "jti": "j"},
                "secret-secret-secret-secret-secret!",
                algorithm="HS256",
                headers={"typ": "dpop+jwt", "jwk": k.dpop_jwk},
            ),
            id="hmac",
        ),
        pytest.param(lambda k: "not-a-jwt", id="garbage"),
    ],
)
def test_a_bad_dpop_proof_is_refused(client, keys, enabled, visitor, make):
    response = _redeem(client, keys, visitor, proof=make(keys))

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_dpop_proof"
    assert not DelegatedAccessToken.objects.exists()


@pytest.mark.django_db
def test_the_dpop_key_must_not_be_the_client_key(client, keys, enabled, visitor):
    proof = _proof(
        keys, key=keys.client, jwk={k: v for k, v in keys.client_jwk.items() if k in ("kty", "crv", "x")}, alg="EdDSA"
    )

    response = _redeem(client, keys, visitor, proof=proof)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_dpop_proof"


@pytest.mark.django_db
def test_a_dpop_proof_works_once(client, keys, enabled, visitor):
    proof = _proof(keys)

    assert _redeem(client, keys, visitor, proof=proof).status_code == 200
    replay = _redeem(client, keys, visitor, proof=proof)

    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


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
    settings.CANOPY_CLIENT_ID = ""
    off = oauth.authorization_server_metadata()
    assert delegation.JWT_BEARER_GRANT not in off["grant_types_supported"]
    assert "dpop_signing_alg_values_supported" not in off

    settings.CANOPY_CLIENT_ID = CLIENT_ID
    on = oauth.authorization_server_metadata()
    assert delegation.JWT_BEARER_GRANT in on["grant_types_supported"]
    assert set(on["dpop_signing_alg_values_supported"]) == {"EdDSA", "ES256"}
    assert "private_key_jwt" in on["token_endpoint_auth_methods_supported"]
    assert oauth.protected_resource_metadata()["dpop_signing_alg_values_supported"] == ["EdDSA", "ES256"]


# ---------------------------------------------------------------------------
# The scope -> tool map
# ---------------------------------------------------------------------------


def test_every_scoped_tool_exists_and_is_read_only():
    from connect_labs.mcp import tools  # noqa: F401 -- registers the catalogue

    for scope, names in delegation.SCOPE_TOOLS.items():
        for name in names:
            spec = get_tool(name)
            assert spec is not None, f"{scope} names a tool that does not exist: {name}"
            assert not spec.is_write, f"{scope} is a read scope but {name} writes"


def test_every_page_scope_is_one_the_server_offers():
    for page, scopes in canopy.PAGE_SCOPES.items():
        assert scopes, page
        assert set(scopes) <= set(delegation.SCOPE_TOOLS), page


# ---------------------------------------------------------------------------
# The MCP verifier
# ---------------------------------------------------------------------------


def _delegated_row(user, jkt, *, scope="marketplace:read", expires_in=timedelta(minutes=10)) -> str:
    raw = secrets.token_urlsafe(32)
    DelegatedAccessToken.objects.create(
        token_checksum=hashlib.sha256(raw.encode()).hexdigest(),
        user=user,
        client_id=CLIENT_ID,
        actor=CLIENT_ID,
        scope=scope,
        cnf_jkt=jkt,
        expires_at=timezone.now() + expires_in,
    )
    return raw


@pytest.mark.django_db
def test_a_bound_token_needs_a_proof_by_its_own_key(keys, visitor):
    jkt = dpop.jwk_thumbprint(keys.dpop_jwk)
    raw = _delegated_row(visitor, jkt)

    assert _verify_bearer_sync(raw, None) is None, "a bound token is not a bearer token"
    assert _verify_bearer_sync(raw, "some-other-thumbprint") is None
    resolved = _verify_bearer_sync(raw, jkt)
    assert resolved[0] == visitor
    assert resolved[1] == "delegated"
    assert resolved[3] == ["marketplace:read"]
    assert resolved[4]["cnf"] == {"jkt": jkt}


@pytest.mark.django_db
def test_an_expired_delegated_token_is_refused(keys, visitor):
    jkt = dpop.jwk_thumbprint(keys.dpop_jwk)
    raw = _delegated_row(visitor, jkt, expires_in=timedelta(seconds=-1))

    assert _verify_bearer_sync(raw, jkt) is None


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


def _proving(keys, token, **overrides):
    async def add_proof(request):
        request.headers["DPoP"] = _proof(keys, htm=request.method, htu=RESOURCE, access_token=token, **overrides)

    return add_proof


@pytest.mark.django_db(transaction=True)
def test_canopy_redeems_a_grant_and_calls_only_its_scoped_tools_as_the_visitor(keys, enabled, visitor):
    from django.test import Client

    from config.asgi import build_application

    token = _redeem(Client(), keys, visitor).json()["access_token"]
    app = build_application()

    async def work(mcp_client):
        tools = await mcp_client.list_tools()
        called = await mcp_client.call_tool("marketplace_rounds_list", {})
        refused = await mcp_client.call_tool("list_templates", {}, raise_on_error=False)
        return tools, called, refused

    tools, called, refused = _run_mcp(
        app,
        {"Authorization": f"DPoP {token}", "Canopy-Actor": "ace"},
        _proving(keys, token),
        work,
    )

    assert {tool.name for tool in tools} == {"marketplace_orgs_get", "marketplace_rounds_list"}
    assert called.structured_content is not None
    assert refused.is_error

    row = MCPAuditLog.objects.get(tool_name="marketplace_rounds_list", success=True)
    assert row.user == visitor, "the tool ran as the visitor"
    assert row.client_id == CLIENT_ID
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


@pytest.fixture
def bound(keys, settings, transactional_db):
    settings.LABS_PUBLIC_URL = BASE
    user = User.objects.create(username="bound-visitor")
    return _delegated_row(user, dpop.jwk_thumbprint(keys.dpop_jwk))


@pytest.mark.django_db(transaction=True)
def test_a_bound_token_sent_as_a_bearer_is_refused(bound):
    from config.asgi import build_application

    response = _raw_mcp_post(build_application(), {"Authorization": f"Bearer {bound}"})

    assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_a_bound_token_with_no_proof_is_refused(bound):
    from config.asgi import build_application

    response = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {bound}"})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_dpop_proof"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda k, t: _proof(k, htm="POST", htu=RESOURCE, access_token="another-token"), id="wrong_ath"),
        pytest.param(lambda k, t: _proof(k, htm="POST", htu=RESOURCE), id="no_ath"),
        pytest.param(lambda k, t: _proof(k, htm="GET", htu=RESOURCE, access_token=t), id="wrong_htm"),
        pytest.param(lambda k, t: _proof(k, htm="POST", htu=f"{BASE}/o/token/", access_token=t), id="wrong_htu"),
        pytest.param(
            lambda k, t: _proof(k, htm="POST", htu=RESOURCE, access_token=t, iat=int(time.time()) - 120), id="stale"
        ),
    ],
)
def test_a_bad_proof_at_the_mcp_is_refused(keys, bound, make):
    from config.asgi import build_application

    response = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {bound}", "DPoP": make(keys, bound)})

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_dpop_proof"


@pytest.mark.django_db(transaction=True)
def test_a_proof_by_a_key_the_token_is_not_bound_to_is_refused(keys, bound):
    from config.asgi import build_application

    other = ec.generate_private_key(ec.SECP256R1())
    proof = _proof(keys, htu=RESOURCE, access_token=bound, key=other, jwk=_ec_jwk(other))

    response = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {bound}", "DPoP": proof})

    assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_a_replayed_proof_at_the_mcp_is_refused(keys, bound):
    from config.asgi import build_application

    proof = _proof(keys, htu=RESOURCE, access_token=bound)

    first = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {bound}", "DPoP": proof})
    second = _raw_mcp_post(build_application(), {"Authorization": f"DPoP {bound}", "DPoP": proof})

    assert first.status_code == 200, first.content
    assert second.status_code == 401
    assert second.json()["error"] == "invalid_dpop_proof"


# ---------------------------------------------------------------------------
# Regression: the other two ways in are exactly what they were
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", ["pat", "oauth"])
def test_pats_and_oauth_sign_ins_are_unchanged(kind, keys, enabled):
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


# ---------------------------------------------------------------------------
# Fetching client metadata without SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://canopy.example/client.json",
        "https://user:pw@canopy.example/client.json",
        "https://canopy.example:8443/client.json",
        "https://127.0.0.1/client.json",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/jwks.json",
        "https://[::1]/jwks.json",
        "file:///etc/passwd",
        "https://canopy.example/a b",
    ],
)
def test_metadata_urls_that_are_not_public_https_are_refused(url):
    with pytest.raises(client_metadata.MetadataError):
        client_metadata.vet_url(url)


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.1.2.3"],
        ["169.254.169.254"],
        ["127.0.0.1"],
        ["93.184.216.34", "192.168.1.1"],
        ["::ffff:10.0.0.1"],
        ["100.64.0.1"],
    ],
)
def test_a_host_resolving_to_a_private_address_is_not_connected_to(addresses):
    import socket

    import httpcore

    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 443)) for a in addresses]
    with mock.patch.object(client_metadata.socket, "getaddrinfo", return_value=infos):
        with mock.patch.object(httpcore.SyncBackend, "connect_tcp") as connect:
            with pytest.raises(httpcore.ConnectError):
                client_metadata._VettedBackend().connect_tcp("canopy.example", 443)
    connect.assert_not_called()


def test_a_public_host_is_connected_to_at_the_vetted_address():
    import socket

    import httpcore

    infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    with mock.patch.object(client_metadata.socket, "getaddrinfo", return_value=infos):
        with mock.patch.object(httpcore.SyncBackend, "connect_tcp", return_value="stream") as connect:
            assert client_metadata._VettedBackend().connect_tcp("canopy.example", 443) == "stream"
    assert connect.call_args.args[0] == "93.184.216.34", "no second lookup to be rebound"


def test_metadata_is_cached():
    cache.clear()
    with mock.patch.object(client_metadata, "_fetch", return_value={"a": 1}) as fetch:
        client_metadata.fetch_json("https://canopy.example/c.json")
        client_metadata.fetch_json("https://canopy.example/c.json")
    assert fetch.call_count == 1
    cache.clear()
