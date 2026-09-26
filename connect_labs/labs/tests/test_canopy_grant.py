"""Labs issuing an ID-JAG for the visitor, and the page registry that decides its scopes.

The rule these pin: the SERVER decides whether canopy may act as the visitor,
and with what — from the route the page was rendered for, carried back as
labs' own signature. Nothing the browser says can add a scope, pick another
user, or conjure a grant on a page that is not registered.
"""

from __future__ import annotations

import json
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from django.core import signing
from django.test import RequestFactory
from django.urls import reverse
from jwt import PyJWK

from connect_labs.labs import canopy

BASE = "https://labs.example.org"
CLIENT_ID = f"{BASE}/canopy/oauth/client.json"


def _pem() -> str:
    return (
        ed25519.Ed25519PrivateKey.generate()
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.fixture
def configured(settings):
    settings.LABS_PUBLIC_URL = BASE
    settings.CANOPY_BASE_URL = f"{BASE}/canopy"
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_SIGNING_KEY = _pem()
    settings.CANOPY_CLIENT_ID = CLIENT_ID


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="visitor", password="x", email="v@example.invalid")


def _decode_id_jag(token: str) -> dict:
    return jwt.decode(
        token,
        PyJWK.from_dict(canopy.public_jwk()).key,
        algorithms=["EdDSA"],
        audience=BASE,
    )


class _Resp:
    def __init__(self):
        self.body = b'{"token": "t", "expires_at": ""}'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def _capture_mint():
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(json.loads(request.data))
        return _Resp()

    return sent, mock.patch.object(canopy.urllib.request, "urlopen", fake_urlopen)


class TestTheIdJag:
    def test_it_matches_the_contract(self, configured, user):
        token = canopy.id_jag_for(user, ["marketplace:read"])
        header = jwt.get_unverified_header(token)
        claims = _decode_id_jag(token)

        assert header["typ"] == "oauth-id-jag+jwt"
        assert header["alg"] == "EdDSA"
        assert header["kid"] == canopy.public_jwk()["kid"], "the key canopy already verifies assertions with"
        assert claims["iss"] == BASE
        assert claims["aud"] == BASE, "labs grants for its own authorization server"
        assert claims["client_id"] == CLIENT_ID
        assert claims["resource"] == f"{BASE}/mcp/"
        assert claims["scope"] == "marketplace:read"
        assert claims["exp"] - claims["iat"] <= 300
        assert claims["jti"]

    def test_its_subject_is_the_assertions_subject(self, configured, user):
        grant = _decode_id_jag(canopy.id_jag_for(user, ["marketplace:read"]))
        assertion = jwt.decode(canopy.assertion_for(user), options={"verify_signature": False})

        assert grant["sub"] == assertion["sub"] == str(user.pk)

    def test_unknown_scopes_are_dropped_and_none_left_is_refused(self, configured, user):
        with pytest.raises(ValueError):
            canopy.id_jag_for(user, ["admin:everything"])

    def test_no_canopy_client_means_no_id_jag(self, configured, user, settings):
        settings.CANOPY_CLIENT_ID = ""

        with pytest.raises(canopy.CanopyNotConfigured):
            canopy.id_jag_for(user, ["marketplace:read"])


class TestTheMintCarriesIt:
    def test_a_registered_page_sends_an_id_jag(self, configured, user):
        sent, patched = _capture_mint()
        with patched:
            canopy.vouch_for(user, scopes=("marketplace:read",))

        assert _decode_id_jag(sent["id_jag"])["scope"] == "marketplace:read"
        assert sent["assertion"], "the visitor assertion is still sent, unchanged"

    def test_no_scopes_means_no_id_jag(self, configured, user):
        sent, patched = _capture_mint()
        with patched:
            canopy.vouch_for(user)

        assert "id_jag" not in sent

    def test_the_grant_switched_off_means_no_id_jag(self, configured, user, settings):
        settings.CANOPY_CLIENT_ID = ""
        sent, patched = _capture_mint()
        with patched:
            canopy.vouch_for(user, scopes=("marketplace:read",))

        assert "id_jag" not in sent
        assert set(sent) == {"assertion", "agent_slug"}, "exactly the request it was before"


@pytest.mark.django_db
class TestThePageToken:
    def _rendered_token(self, client, user, name="marketplace:network"):
        client.force_login(user)
        body = client.get(reverse(name)).content.decode()
        marker = "?page="
        assert marker in body, "the registered page carries its token to the widget"
        start = body.index(marker) + len(marker)
        from urllib.parse import unquote

        return unquote(body[start : body.index('"', start)].encode().decode("unicode_escape"))

    def test_a_registered_page_renders_a_token_that_yields_its_scopes(self, client, configured, user):
        token = self._rendered_token(client, user)

        assert canopy.scopes_for_page_token(token, user) == ("marketplace:read",)

    def test_the_mint_endpoint_turns_the_page_token_into_scopes(self, client, configured, user):
        token = self._rendered_token(client, user)
        with mock.patch.object(canopy, "vouch_for", return_value={"token": "t", "expires_at": ""}) as vouch:
            response = client.post(f"{reverse('labs:canopy_token')}?page={token}")

        assert response.status_code == 200
        assert vouch.call_args.kwargs["scopes"] == ("marketplace:read",)

    def test_without_a_page_token_the_mint_carries_no_scopes(self, client, configured, user):
        client.force_login(user)
        with mock.patch.object(canopy, "vouch_for", return_value={"token": "t", "expires_at": ""}) as vouch:
            client.post(reverse("labs:canopy_token"))

        assert vouch.call_args.kwargs["scopes"] == ()

    def test_scopes_in_the_body_or_query_are_ignored(self, client, configured, user):
        client.force_login(user)
        with mock.patch.object(canopy, "vouch_for", return_value={"token": "t", "expires_at": ""}) as vouch:
            client.post(
                f"{reverse('labs:canopy_token')}?scope=admin&page=marketplace:network",
                data=json.dumps({"scope": "marketplace:read", "page": "marketplace:network"}),
                content_type="application/json",
            )

        assert vouch.call_args.kwargs["scopes"] == ()

    def test_a_forged_page_token_yields_nothing(self, configured, user):
        forged = signing.dumps({"page": "marketplace:network", "user": user.pk}, salt="someone-elses-salt")

        assert canopy.scopes_for_page_token(forged, user) == ()
        assert canopy.scopes_for_page_token("garbage", user) == ()

    def test_another_users_page_token_yields_nothing(self, client, configured, user, django_user_model):
        token = self._rendered_token(client, user)
        other = django_user_model.objects.create_user(username="other", password="x")

        assert canopy.scopes_for_page_token(token, other) == ()

    def test_an_expired_page_token_yields_nothing(self, client, configured, user):
        token = self._rendered_token(client, user)
        with mock.patch.object(canopy, "PAGE_TOKEN_MAX_AGE_SECONDS", -1):
            assert canopy.scopes_for_page_token(token, user) == ()

    def test_an_unregistered_page_gets_no_token(self, configured, user):
        request = RequestFactory().get("/labs/somewhere/")
        request.user = user
        request.resolver_match = mock.Mock(view_name="labs:somewhere_else")

        assert canopy.page_token(request) == ""
        assert canopy.panel_context(resource="x://y", request=request)["page_token"] == ""

    def test_a_page_dropped_from_the_registry_stops_granting_at_once(self, client, configured, user):
        """Scopes are read from the registry at mint time, not from the token."""
        token = self._rendered_token(client, user)
        with mock.patch.dict(canopy.PAGE_SCOPES, clear=True):
            assert canopy.scopes_for_page_token(token, user) == ()

    def test_both_marketplace_pages_are_registered(self, client, configured, user):
        from connect_labs.solicitations.local_models import Solicitation

        Solicitation.objects.create(slug="mg-2026", title="Matching Grant", solicitation_type="eoi", status="active")
        client.force_login(user)
        body = client.get(reverse("marketplace:round", args=["mg-2026"])).content.decode()

        assert "?page=" in body
