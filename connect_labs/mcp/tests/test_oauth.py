"""Tests for the standard MCP sign-in (connect_labs/mcp/oauth.py).

The contract under test is the one every MCP client relies on, not anything
specific to one client: discovery documents, dynamic client registration, an
authorization-code flow with PKCE through labs' own OAuth server, and a token
the MCP verifier accepts. Plus the two confinement rules that keep an MCP
sign-in from becoming a key to labs' other OAuth APIs, and the reverse.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model

from connect_labs.mcp import oauth
from connect_labs.mcp.models import MCPAuditLog, MCPOAuthClient
from connect_labs.mcp.server import CommCarePATVerifier
from connect_labs.users.models import User

LOOPBACK = "http://127.0.0.1:33418/callback"


@pytest.fixture(autouse=True)
def _clear_registration_rate_limit():
    """Registration is capped per caller per hour, and every test here shares one address."""
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register(client, **overrides):
    body = {"client_name": "Test client", "redirect_uris": [LOOPBACK], "token_endpoint_auth_method": "none"}
    body.update(overrides)
    return client.post(oauth.REGISTRATION_PATH, data=json.dumps(body), content_type="application/json")


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_params(client_id, challenge, redirect_uri=LOOPBACK, scope=oauth.MCP_SCOPE):
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st4te",
        "scope": scope,
    }


def _sign_in(client, user):
    """Register, approve on the consent screen, exchange the code. Returns (client_id, token response)."""
    registered = _register(client).json()
    verifier, challenge = _pkce()
    client.force_login(user)
    params = _authorize_params(registered["client_id"], challenge)

    consent = client.get("/o/authorize/", params)
    assert consent.status_code == 200, consent.content[:500]

    approved = client.post("/o/authorize/", {**params, "allow": "Approve", "nonce": "", "claims": ""})
    assert approved.status_code == 302, approved.content[:500]
    location = urlparse(approved["Location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == LOOPBACK
    query = parse_qs(location.query)
    assert query["state"] == ["st4te"]

    token = client.post(
        "/o/token/",
        {
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": LOOPBACK,
            "client_id": registered["client_id"],
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200, token.content
    return registered["client_id"], token.json()


def _verify(raw):
    return asyncio.run(CommCarePATVerifier().verify_token(raw))


def _mcp_application(redirect_uris=LOOPBACK):
    Application = get_application_model()
    application = Application.objects.create(
        name="MCP test client",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris=redirect_uris,
    )
    MCPOAuthClient.objects.create(application=application)
    return application


def _other_application():
    Application = get_application_model()
    return Application.objects.create(
        name="Some other labs OAuth app",
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="https://app.example/callback",
    )


def _access_token(user, application, scope=oauth.MCP_SCOPE, expires_in=timedelta(hours=1)):
    raw = secrets.token_urlsafe(32)
    get_access_token_model().objects.create(
        user=user,
        application=application,
        token=raw,
        scope=scope,
        expires=timezone.now() + expires_in,
    )
    return raw


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------


def test_protected_resource_metadata_names_the_endpoint_and_its_authorization_server(settings):
    settings.LABS_PUBLIC_URL = "https://labs.example.org/"

    doc = oauth.protected_resource_metadata()

    assert doc["resource"] == "https://labs.example.org/mcp/"
    assert doc["authorization_servers"] == ["https://labs.example.org"]
    assert doc["scopes_supported"] == ["mcp"]
    expected_metadata_url = "https://labs.example.org/.well-known/oauth-protected-resource/mcp"
    assert oauth.protected_resource_metadata_url() == expected_metadata_url


def test_authorization_server_metadata_advertises_what_the_mcp_flow_needs(settings):
    settings.LABS_PUBLIC_URL = "https://labs.example.org"

    doc = oauth.authorization_server_metadata()

    assert doc["issuer"] == "https://labs.example.org"
    assert doc["authorization_endpoint"] == "https://labs.example.org/o/authorize/"
    assert doc["token_endpoint"] == "https://labs.example.org/o/token/"
    assert doc["registration_endpoint"] == "https://labs.example.org/o/register/"
    assert doc["response_types_supported"] == ["code"]
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["token_endpoint_auth_methods_supported"] == ["none"]
    assert set(doc["grant_types_supported"]) == {"authorization_code", "refresh_token"}


def test_the_toolkit_is_wired_to_the_mcp_hooks():
    """The confinement rules only hold if django-oauth-toolkit actually uses them."""
    from oauth2_provider.settings import oauth2_settings

    assert oauth2_settings.SCOPES_BACKEND_CLASS is oauth.MCPScopes
    assert oauth2_settings.OAUTH2_VALIDATOR_CLASS is oauth.MCPOAuth2Validator
    assert oauth2_settings.PKCE_REQUIRED is True


# ---------------------------------------------------------------------------
# Dynamic client registration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_registration_creates_a_public_client_confined_to_mcp(client):
    resp = _register(client)

    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [LOOPBACK]
    assert body["scope"] == "mcp"
    assert "client_secret" not in body
    application = get_application_model().objects.get(client_id=body["client_id"])
    assert application.client_type == application.CLIENT_PUBLIC
    assert oauth.is_mcp_client(application)


@pytest.mark.django_db
def test_registration_needs_no_csrf_token_or_session():
    resp = _register(Client(enforce_csrf_checks=True))

    assert resp.status_code == 201, resp.content


@pytest.mark.django_db
@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://attacker.example/callback",
        "javascript:alert(1)",
        "https://app.example/callback#fragment",
        "ftp://app.example/callback",
        "not a url",
    ],
)
def test_registration_refuses_redirects_that_are_not_https_or_loopback(client, redirect_uri):
    resp = _register(client, redirect_uris=[redirect_uri])

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert not MCPOAuthClient.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "overrides",
    [
        {"redirect_uris": []},
        {"redirect_uris": LOOPBACK},
        {"token_endpoint_auth_method": "client_secret_basic"},
        {"scope": "mcp export"},
        {"grant_types": ["client_credentials"]},
        {"response_types": ["token"]},
    ],
)
def test_registration_refuses_metadata_it_cannot_honour(client, overrides):
    resp = _register(client, **overrides)

    assert resp.status_code == 400
    assert resp.json()["error"] in {"invalid_redirect_uri", "invalid_client_metadata"}
    assert not MCPOAuthClient.objects.exists()


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_signing_in_starts_at_the_labs_login(client):
    registered = _register(client).json()
    _, challenge = _pkce()

    resp = client.get("/o/authorize/", _authorize_params(registered["client_id"], challenge))

    assert resp.status_code == 302
    assert resp["Location"].startswith(settings.LOGIN_URL)


@pytest.mark.django_db(transaction=True)
def test_signing_in_yields_a_token_the_mcp_verifier_accepts(client):
    user = User.objects.create(username="oauth-sign-in")

    client_id, token = _sign_in(client, user)

    assert token["token_type"] == "Bearer"
    assert token["scope"] == "mcp"
    assert token["refresh_token"]
    access = _verify(token["access_token"])
    assert access is not None
    assert access.claims["user_id"] == user.pk
    assert access.claims["auth_method"] == "oauth"
    assert access.client_id == client_id
    assert access.scopes == ["mcp"]


@pytest.mark.django_db(transaction=True)
def test_a_refresh_rotates_the_tokens(client):
    user = User.objects.create(username="oauth-refresh")
    client_id, token = _sign_in(client, user)

    refreshed = client.post(
        "/o/token/", {"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": client_id}
    )

    assert refreshed.status_code == 200, refreshed.content
    renewed = refreshed.json()
    assert renewed["access_token"] != token["access_token"]
    assert renewed["refresh_token"] != token["refresh_token"]
    assert _verify(renewed["access_token"]) is not None
    # Rotation is only worth anything if the old access token stops working.
    assert _verify(token["access_token"]) is None
    replayed = client.post(
        "/o/token/", {"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": client_id}
    )
    assert replayed.status_code == 400


@pytest.mark.django_db
def test_a_localhost_redirect_may_use_any_port(client):
    """RFC 8252 section 7.3. A native client listens on whatever port is free when it signs in."""
    user = User.objects.create(username="oauth-loopback")
    registered = _register(client, redirect_uris=["http://localhost:1234/callback"]).json()
    _, challenge = _pkce()
    client.force_login(user)

    resp = client.get(
        "/o/authorize/",
        _authorize_params(registered["client_id"], challenge, redirect_uri="http://localhost:5678/callback"),
    )

    assert resp.status_code == 200, resp.content[:500]


@pytest.mark.django_db
def test_an_https_redirect_must_match_exactly(client):
    user = User.objects.create(username="oauth-https")
    registered = _register(client, redirect_uris=["https://app.example/callback"]).json()
    _, challenge = _pkce()
    client.force_login(user)

    resp = client.get(
        "/o/authorize/",
        _authorize_params(registered["client_id"], challenge, redirect_uri="https://app.example/elsewhere"),
    )

    assert resp.status_code == 400, resp.status_code
    assert "redirect" in resp.content.decode().lower()


# ---------------------------------------------------------------------------
# Confinement: the mcp scope and MCP clients stay inside each other
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_mcp_client_cannot_ask_for_labs_other_scopes(client):
    user = User.objects.create(username="oauth-scope")
    registered = _register(client).json()
    _, challenge = _pkce()
    client.force_login(user)

    resp = client.get("/o/authorize/", _authorize_params(registered["client_id"], challenge, scope="export"))

    assert resp.status_code == 302, resp.content[:300]
    assert parse_qs(urlparse(resp["Location"]).query)["error"] == ["invalid_scope"]


@pytest.mark.django_db
def test_no_other_application_can_be_granted_the_mcp_scope():
    other = _other_application()
    mcp_client = _mcp_application()
    scopes = oauth.MCPScopes()

    assert "mcp" not in scopes.get_available_scopes(application=other)
    assert "mcp" not in scopes.get_default_scopes(application=other)
    assert "export" in scopes.get_available_scopes(application=other)
    assert scopes.get_available_scopes(application=mcp_client) == ["mcp"]
    assert scopes.get_default_scopes(application=mcp_client) == ["mcp"]


@pytest.mark.django_db(transaction=True)
def test_the_verifier_accepts_a_live_mcp_grant():
    user = User.objects.create(username="oauth-live")
    raw = _access_token(user, _mcp_application())

    access = _verify(raw)

    assert access is not None
    assert access.claims["auth_method"] == "oauth"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", ["other_application", "wrong_scope", "expired", "inactive_user"])
def test_the_verifier_accepts_only_live_mcp_grants(case):
    user = User.objects.create(username=f"oauth-{case}")
    if case == "other_application":
        raw = _access_token(user, _other_application(), scope="mcp")
    elif case == "wrong_scope":
        raw = _access_token(user, _mcp_application(), scope="read export")
    elif case == "expired":
        raw = _access_token(user, _mcp_application(), expires_in=timedelta(seconds=-1))
    else:
        raw = _access_token(user, _mcp_application())
        user.is_active = False
        user.save(update_fields=["is_active"])

    assert _verify(raw) is None


# ---------------------------------------------------------------------------
# End to end over the wire
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_an_oauth_token_calls_tools_over_streamable_http():
    import anyio
    from fastmcp import Client as MCPClient
    from fastmcp.client.transports import StreamableHttpTransport

    from config.asgi import build_application
    from connect_labs.mcp.tests.test_asgi_integration import _client_factory_to_asgi

    user = User.objects.create(username="oauth-e2e")
    raw = _access_token(user, _mcp_application())
    application = build_application()

    async def _run():
        transport = StreamableHttpTransport(
            url="http://testserver/mcp/",
            headers={"Authorization": f"Bearer {raw}"},
            httpx_client_factory=_client_factory_to_asgi(application),
        )
        async with application.router.lifespan_context(application):
            async with MCPClient(transport) as mcp_client:
                return await mcp_client.call_tool("list_templates", {})

    result = anyio.run(_run)

    assert result.structured_content is not None
    assert MCPAuditLog.objects.filter(user=user, tool_name="list_templates", success=True).exists()


# ---------------------------------------------------------------------------
# The consent screen cannot be skipped, and PKCE must be real
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["plain", None])
def test_an_mcp_client_must_use_s256_pkce(client, method):
    """oauthlib defaults the method to `plain` when absent, which protects nothing."""
    user = User.objects.create(username=f"oauth-pkce-{method}")
    registered = _register(client).json()
    client.force_login(user)
    params = _authorize_params(registered["client_id"], "a-challenge")
    if method is None:
        params.pop("code_challenge_method")
    else:
        params["code_challenge_method"] = method

    resp = client.get("/o/authorize/", params)

    assert resp.status_code == 400, resp.status_code
    assert "S256" in resp.content.decode()


@pytest.mark.django_db(transaction=True)
def test_a_second_sign_in_still_asks_for_consent(client):
    """`approval_prompt=auto` comes from the query string and would otherwise skip the screen."""
    user = User.objects.create(username="oauth-consent")
    client_id, _token = _sign_in(client, user)
    _, challenge = _pkce()
    params = _authorize_params(client_id, challenge)
    params["approval_prompt"] = "auto"

    resp = client.get("/o/authorize/", params)

    assert resp.status_code == 200, f"a token was minted with no consent screen: {resp.status_code}"


@pytest.mark.django_db
def test_the_consent_screen_says_the_app_is_unverified_and_where_it_sends_you(client):
    user = User.objects.create(username="oauth-consent-copy")
    registered = _register(client, client_name="Claude Code").json()
    client.force_login(user)
    _, challenge = _pkce()

    page = client.get("/o/authorize/", _authorize_params(registered["client_id"], challenge)).content.decode()

    assert "Claude Code" in page
    assert "no one has verified it" in page
    assert "http://127.0.0.1:33418" in page


# ---------------------------------------------------------------------------
# The separation holds in both directions
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_mcp_token_does_not_authenticate_on_labs_rest_api(client):
    """No labs API view checks a scope, so the MCP token has to be refused at the door."""
    user = User.objects.create(username="oauth-api-mcp")
    raw = _access_token(user, _mcp_application())

    resp = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")

    assert resp.status_code == 401, resp.status_code


@pytest.mark.django_db
def test_a_non_mcp_token_still_authenticates_on_labs_rest_api(client):
    user = User.objects.create(username="oauth-api-other")
    raw = _access_token(user, _other_application(), scope="read")

    resp = client.get("/api/users/me/", HTTP_AUTHORIZATION=f"Bearer {raw}")

    assert resp.status_code == 200, resp.status_code


@pytest.mark.django_db
def test_a_non_mcp_client_gets_no_loopback_port_relaxation(client):
    """The relaxation is for MCP clients; every other application keeps exact matching."""
    Application = get_application_model()
    other = Application.objects.create(
        name="Not an MCP client",
        client_type=Application.CLIENT_PUBLIC,
        authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
        redirect_uris="http://localhost:1234/callback",
    )
    user = User.objects.create(username="oauth-not-mcp")
    client.force_login(user)
    _, challenge = _pkce()

    resp = client.get(
        "/o/authorize/",
        _authorize_params(other.client_id, challenge, redirect_uri="http://localhost:5678/callback"),
    )

    assert not oauth.is_mcp_client(other)
    assert resp.status_code == 400, resp.status_code
    assert "redirect" in resp.content.decode().lower()


@pytest.mark.parametrize("value", [None, "some-client-id-string", object()])
def test_is_mcp_client_fails_closed_on_anything_that_is_not_an_application(value):
    assert oauth.is_mcp_client(value) is False


# ---------------------------------------------------------------------------
# Disconnecting really disconnects
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_disconnecting_an_app_ends_its_access_and_its_refresh(client):
    """Deleting the access token alone leaves the refresh token, which signs straight back in."""
    user = User.objects.create(username="oauth-disconnect")
    client_id, token = _sign_in(client, user)
    application = get_application_model().objects.get(client_id=client_id)

    client.force_login(user)
    resp = client.post(f"/labs/mcp/clients/{application.pk}/disconnect/")

    assert resp.status_code == 302
    assert _verify(token["access_token"]) is None
    refreshed = client.post(
        "/o/token/", {"grant_type": "refresh_token", "refresh_token": token["refresh_token"], "client_id": client_id}
    )
    assert refreshed.status_code == 400, "the refresh token outlived the disconnect"


@pytest.mark.django_db(transaction=True)
def test_the_tokens_page_lists_a_signed_in_app(client):
    user = User.objects.create(username="oauth-listed")
    _sign_in(client, user)

    client.force_login(user)
    page = client.get("/labs/mcp/tokens/").content.decode()

    assert "Apps you've signed in" in page
    assert "Test client" in page


# ---------------------------------------------------------------------------
# Registration limits
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://ok.example/cb http://evil.example/cb",
        "https://ok.example/cb\thttp://evil.example/cb",
    ],
)
def test_registration_refuses_a_redirect_uri_containing_whitespace(client, redirect_uri):
    """Redirect URIs are stored space-separated and the toolkit re-splits them.

    Without this, one registered string smuggles in a second URI that the
    https/loopback rule never saw.
    """
    resp = _register(client, redirect_uris=[redirect_uri])

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"
    assert not MCPOAuthClient.objects.exists()


@pytest.mark.django_db
def test_registration_refuses_an_overlong_redirect_uri(client):
    resp = _register(client, redirect_uris=["https://app.example/" + "a" * 600])

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_redirect_uri"


@pytest.mark.django_db
def test_registration_is_capped_per_caller(client):
    for _ in range(oauth._REGISTRATIONS_PER_HOUR_PER_IP):
        assert _register(client).status_code == 201

    resp = _register(client)

    assert resp.status_code == 429
    assert resp.json()["error"] == "too_many_requests"
