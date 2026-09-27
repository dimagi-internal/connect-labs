"""The canopy agent panel on labs' pages: labs' wiring of the canopy SDK.

Signing, the page token and the mint view are the SDK's (``canopy_sdk``) and are
tested there. What is tested HERE is labs' side of them, through labs' real
URLs and pages: ``CANOPY_HOST`` is built from labs' existing settings, the mint
at ``/labs/canopy/token/`` vouches for the SESSION's user with labs' claims and
labs' page scopes, the key canopy fetches is still at ``/labs/canopy/jwks/``,
and the panel renders on the marketplace pages exactly as it did.

The one worth testing hard is the subject: labs is telling canopy "this is a
real person here", and if the subject could come from anywhere but the session,
any caller could be anybody.
"""

from __future__ import annotations

import json
from unittest import mock
from urllib.parse import unquote

import jwt
import pytest
from canopy_sdk.keys import generate_private_key, private_pem
from django.urls import reverse
from jwt import PyJWK

from connect_labs.labs import canopy

BASE = "https://labs.example.invalid"
CANOPY = f"{BASE}/canopy"
CLIENT_ID = f"{CANOPY}/oauth/client.json"


@pytest.fixture
def configured(settings):
    settings.LABS_PUBLIC_URL = BASE
    settings.CANOPY_BASE_URL = CANOPY
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_AGENT_SLUG = "ace"
    settings.CANOPY_SIGNING_KEY = private_pem(generate_private_key("EdDSA"))
    settings.CANOPY_CLIENT_ID = CLIENT_ID


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="staff", password="x", email="staff@dimagi.com", name="Sam Staff"
    )


@pytest.fixture
def marketplace_round(db):
    from connect_labs.solicitations.local_models import Solicitation

    return Solicitation.objects.create(
        slug="mg-2026", title="Matching Grant Pilot", solicitation_type="eoi", status="active"
    )


class _CanopyStub:
    """Stands in for canopy's arrival endpoint and records what labs sent it."""

    def __init__(self, status=200, body=b'{"token": "tok", "expires_at": "2026-01-01T00:00:00Z"}'):
        self.sent: dict = {}
        self.url = ""
        self.status = status
        self.body = body

    def __call__(self, request, timeout=None):
        import urllib.error

        self.url = request.full_url
        self.sent = json.loads(request.data)
        if self.status != 200:
            raise urllib.error.HTTPError(self.url, self.status, "refused", {}, _Body(self.body))
        return _Body(self.body)


class _Body:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body

    def close(self):
        pass


def _mint(client, query=""):
    stub = _CanopyStub()
    with mock.patch("urllib.request.urlopen", stub):
        response = client.post(reverse("labs:canopy_token") + query)
    return response, stub


def _published_key(client):
    return PyJWK.from_dict(client.get(reverse("labs:canopy_jwks")).json()["keys"][0]).key


def _token_url(body: str) -> str:
    """The widget's ``tokenUrl`` as the browser reads it (the template escapes it
    for a JS string, so ``=`` arrives as ``\\u003D``)."""
    marker = 'tokenUrl: "'
    start = body.index(marker) + len(marker)
    return json.loads('"' + body[start : body.index('"', start)] + '"')


def _rendered_page_token(client, name="marketplace:network", args=()):
    url = _token_url(client.get(reverse(name, args=args)).content.decode())
    assert "?page=" in url, "the registered page carries its token to the widget"
    return unquote(url.split("?page=", 1)[1])


# ---------------------------------------------------------------------------
# CANOPY_HOST, from labs' existing settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_canopy_host_reads_labs_settings_live(self, configured, settings):
        from django.conf import settings as live

        host = dict(live.CANOPY_HOST)

        assert host["CANOPY_BASE_URL"] == CANOPY
        assert host["APP_NAME"] == "connect-labs"
        assert host["AGENT_SLUG"] == "ace"
        assert host["CLIENT_ID"] == CLIENT_ID
        assert host["ISSUER"] == BASE
        assert host["RESOURCE"] == f"{BASE}/mcp/"
        assert host["TOKEN_ENDPOINT"] == f"{BASE}/o/token/"
        assert host["PANEL_TOKEN_URL"] == "/labs/canopy/token/"
        assert host["PAGE_SCOPES"] is canopy.PAGE_SCOPES

        settings.CANOPY_CLIENT_ID = ""
        assert live.CANOPY_HOST["CLIENT_ID"] == "", "an override applies without a rebuild"

    def test_no_public_origin_means_no_grant(self, configured, settings):
        settings.LABS_PUBLIC_URL = ""

        assert canopy.host_config().grant_enabled is False
        assert canopy.host_config().assertions_enabled is True, "the panel itself still works"

    def test_the_repr_never_shows_the_signing_key(self, configured):
        from django.conf import settings as live

        assert "PRIVATE KEY" not in repr(live.CANOPY_HOST)

    def test_no_key_means_no_host(self, configured, settings):
        settings.CANOPY_SIGNING_KEY = ""

        assert canopy.host_config() is None


# ---------------------------------------------------------------------------
# The mint, at /labs/canopy/token/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheMint:
    def test_it_requires_a_login(self, client, configured):
        assert client.post(reverse("labs:canopy_token")).status_code in (302, 403)

    def test_it_rejects_a_get(self, client, user, configured):
        client.force_login(user)

        assert client.get(reverse("labs:canopy_token")).status_code == 405

    def test_it_vouches_for_the_session_user_with_labs_claims(self, client, user, configured):
        client.force_login(user)

        response, canopy_stub = _mint(client)

        assert response.status_code == 200
        assert response.json() == {"token": "tok", "expires_at": "2026-01-01T00:00:00Z"}
        assert canopy_stub.url == f"{CANOPY}/api/auth/contact-token"
        assert canopy_stub.sent["agent_slug"] == "ace", "names canopy's tenant (canopy-web #960)"
        claims = jwt.decode(
            canopy_stub.sent["assertion"], _published_key(client), algorithms=["EdDSA"], audience=CANOPY
        )
        assert claims["iss"] == "connect-labs"
        assert claims["sub"] == str(user.pk), "labs' own id, never an email"
        assert claims["name"] == "Sam Staff", "labs' single `name` field, not first/last"
        assert claims["email"] == "staff@dimagi.com"
        assert claims["email_verified"] is True, "how canopy recognises a workspace member"
        assert claims["exp"] - claims["iat"] <= 120

    def test_it_never_vouches_for_an_empty_address(self, client, user, configured):
        user.email = ""
        user.save()
        client.force_login(user)

        _, canopy_stub = _mint(client)

        claims = jwt.decode(canopy_stub.sent["assertion"], options={"verify_signature": False})
        assert claims["email_verified"] is False

    def test_a_subject_in_the_body_is_ignored(self, client, user, django_user_model, configured):
        """The one that matters. Were the subject read from the request, any
        signed-in person could be vouched for as anybody else."""
        victim = django_user_model.objects.create_user(username="someone-else", password="x")
        client.force_login(user)
        canopy_stub = _CanopyStub()

        with mock.patch("urllib.request.urlopen", canopy_stub):
            client.post(
                reverse("labs:canopy_token"),
                data=json.dumps({"sub": str(victim.pk), "username": victim.username}),
                content_type="application/json",
            )

        claims = jwt.decode(canopy_stub.sent["assertion"], options={"verify_signature": False})
        assert claims["sub"] == str(user.pk)

    def test_an_unconfigured_deployment_says_so(self, client, user, configured, settings):
        settings.CANOPY_SIGNING_KEY = ""
        client.force_login(user)

        assert client.post(reverse("labs:canopy_token")).status_code == 503

    def test_canopys_refusal_does_not_reach_the_browser(self, client, user, configured):
        """Canopy's codes (`replayed`, `bad_signature`) describe labs' credential,
        not the visitor's session, and the visitor can act on none of them."""
        client.force_login(user)
        refusing = _CanopyStub(status=401, body=b'{"detail": "bad_signature"}')

        with mock.patch("urllib.request.urlopen", refusing):
            response = client.post(reverse("labs:canopy_token"))

        assert response.status_code == 502
        assert "bad_signature" not in response.content.decode()


# ---------------------------------------------------------------------------
# The grant rides the mint: labs' page registry decides it
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheIdJag:
    def test_a_registered_page_sends_an_id_jag_for_the_same_person(self, client, user, configured):
        client.force_login(user)
        token = _rendered_page_token(client)

        _, canopy_stub = _mint(client, f"?page={token}")

        header = jwt.get_unverified_header(canopy_stub.sent["id_jag"])
        grant = jwt.decode(canopy_stub.sent["id_jag"], _published_key(client), algorithms=["EdDSA"], audience=BASE)
        assertion = jwt.decode(canopy_stub.sent["assertion"], options={"verify_signature": False})
        assert header["typ"] == "oauth-id-jag+jwt"
        assert header["kid"] == jwt.get_unverified_header(canopy_stub.sent["assertion"])["kid"], "one key"
        assert grant["iss"] == grant["aud"] == BASE, "labs grants for its own authorization server"
        assert grant["client_id"] == CLIENT_ID
        assert grant["resource"] == f"{BASE}/mcp/"
        assert grant["scope"] == "marketplace:read"
        assert grant["sub"] == assertion["sub"] == str(user.pk)

    def test_without_a_page_token_the_request_is_exactly_what_it_was(self, client, user, configured):
        client.force_login(user)

        _, canopy_stub = _mint(client)

        assert set(canopy_stub.sent) == {"assertion", "agent_slug"}

    def test_the_grant_switched_off_means_no_id_jag(self, client, user, configured, settings):
        client.force_login(user)
        token = _rendered_page_token(client)
        settings.CANOPY_CLIENT_ID = ""

        _, canopy_stub = _mint(client, f"?page={token}")

        assert set(canopy_stub.sent) == {"assertion", "agent_slug"}

    def test_scopes_in_the_body_or_query_are_ignored(self, client, user, configured):
        client.force_login(user)
        canopy_stub = _CanopyStub()

        with mock.patch("urllib.request.urlopen", canopy_stub):
            client.post(
                f"{reverse('labs:canopy_token')}?scope=admin&page=marketplace:network",
                data=json.dumps({"scope": "marketplace:read", "page": "marketplace:network"}),
                content_type="application/json",
            )

        assert "id_jag" not in canopy_stub.sent

    def test_another_users_page_token_yields_nothing(self, client, user, configured, django_user_model):
        client.force_login(user)
        token = _rendered_page_token(client)
        other = django_user_model.objects.create_user(username="other", password="x", email="o@example.invalid")
        client.force_login(other)

        _, canopy_stub = _mint(client, f"?page={token}")

        assert "id_jag" not in canopy_stub.sent

    def test_a_page_dropped_from_the_registry_stops_granting_at_once(self, client, user, configured):
        """Scopes are read from labs' registry at mint time, not from the token."""
        client.force_login(user)
        token = _rendered_page_token(client)

        with mock.patch.dict(canopy.PAGE_SCOPES, clear=True):
            _, canopy_stub = _mint(client, f"?page={token}")

        assert "id_jag" not in canopy_stub.sent

    def test_both_marketplace_pages_are_registered(self, client, user, configured, marketplace_round):
        client.force_login(user)

        assert _rendered_page_token(client, "marketplace:network")
        assert _rendered_page_token(client, "marketplace:round", args=["mg-2026"])


# ---------------------------------------------------------------------------
# The key canopy fetches, at the URL canopy has registered for labs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheJwks:
    def test_it_is_still_at_the_url_canopy_has_registered(self):
        assert reverse("labs:canopy_jwks") == "/labs/canopy/jwks/"

    def test_it_needs_no_session_and_publishes_only_the_public_half(self, client, configured):
        response = client.get(reverse("labs:canopy_jwks"))

        assert response.status_code == 200
        key = response.json()["keys"][0]
        assert key["kty"] == "OKP"
        assert "d" not in key, "`d` is the private scalar of an OKP key"
        assert "PRIVATE KEY" not in response.content.decode()

    def test_an_unreadable_key_publishes_an_empty_set(self, client, configured, settings):
        settings.CANOPY_SIGNING_KEY = "not a pem"

        response = client.get(reverse("labs:canopy_jwks"))

        assert response.status_code == 503
        assert response.json() == {"keys": []}


# ---------------------------------------------------------------------------
# The rendered panel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheRenderedPanel:
    """The include is the part that ships, so assert against rendered HTML."""

    def test_it_renders_nothing_without_a_key(self, client, user, configured, settings):
        settings.CANOPY_SIGNING_KEY = ""
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert "embed/widget.js" not in body

    def test_it_renders_the_launcher_and_the_page_state(self, client, user, configured):
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert f"{CANOPY}/embed/widget.js" in body
        assert 'id="canopy-page-state"' in body
        assert "labs-marketplace://orgs" in body
        assert "marketplace_orgs_get" in body
        assert 'agent: "ace"' in body
        assert _token_url(body).startswith("/labs/canopy/token/?page=")
        # Labs' look, unchanged.
        assert "#3F4FA0" in body
        assert "Ask an agent" in body
        # The CSRF binding: labs' cookie is HttpOnly, so the token comes from the
        # DOM. Without this the mint 403s and the widget never starts.
        assert 'name="csrfmiddlewaretoken"' in body, "the token the panel reads must be on the page"

    def test_the_launcher_is_rendered_inside_the_body(self, client, user, configured):
        """`{% block javascript %}` renders in <head>, where `document.body` is
        still null and the launcher has nothing to attach to."""
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert body.index("<body") < body.index("embed/widget.js")
        assert body.index('name="csrfmiddlewaretoken"') < body.index("canopy-page-state")

    def test_the_round_page_declares_its_round(self, client, user, configured, marketplace_round):
        client.force_login(user)

        body = client.get(reverse("marketplace:round", args=["mg-2026"])).content.decode()

        assert "labs-marketplace://rounds/mg-2026" in body
        assert "marketplace_rounds_list" in body

    def test_the_template_leaves_no_django_comment_in_the_output(self, client, user, configured):
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert "{#" not in body
        assert "{%" not in body
