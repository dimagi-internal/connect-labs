"""Vouching for a labs visitor, and the panel that renders from it.

Two things are worth testing and one is worth testing hard. The easy one is that
the panel fails closed when the deployment has no key. The hard one is the
subject of the assertion: labs is telling canopy "this is a real person here",
and if the subject could come from anywhere but the session, any caller could be
anybody.
"""

from __future__ import annotations

import json
from unittest import mock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from django.urls import reverse

from connect_labs.labs import canopy


def _keypair() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode(),
        private.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode(),
    )


@pytest.fixture
def keys():
    return _keypair()


@pytest.fixture
def configured(settings, keys):
    private, public = keys
    settings.CANOPY_BASE_URL = "https://labs.example.invalid/canopy"
    settings.CANOPY_APP_NAME = "connect-labs"
    settings.CANOPY_SIGNING_KEY = private
    return public


@pytest.fixture
def marketplace_round(db):
    from connect_labs.solicitations.local_models import Solicitation

    return Solicitation.objects.create(
        slug="mg-2026", title="Matching Grant Pilot", solicitation_type="eoi", status="active"
    )


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(
        username="staff", password="x", email="staff@dimagi.com", name="Sam Staff"
    )


class TestConfiguration:
    def test_no_key_means_no_panel(self, settings):
        settings.CANOPY_BASE_URL = "https://labs.example.invalid/canopy"
        settings.CANOPY_SIGNING_KEY = ""

        assert canopy.is_configured() is False
        assert canopy.panel_context(resource="x://")["ready"] is False

    def test_all_three_are_required(self, settings, keys):
        """A launcher that opens onto an unexplainable error is worse than none,
        so a partial configuration must read as no panel rather than most of one."""
        settings.CANOPY_BASE_URL = ""
        settings.CANOPY_APP_NAME = "connect-labs"
        settings.CANOPY_SIGNING_KEY = keys[0]

        assert canopy.is_configured() is False


class TestTheAssertion:
    def test_it_verifies_against_the_public_half(self, configured, user):
        claims = jwt.decode(
            canopy.assertion_for(user),
            configured,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["iss"] == "connect-labs"
        assert claims["sub"] == str(user.pk)
        assert claims["jti"]

    def test_the_subject_is_our_own_id_and_never_an_email(self, configured, user):
        """`sub` identifies the person in LABS' namespace. An email here would be
        a claim about an identity labs did not verify, and canopy pairs `sub`
        with the app to keep visitors from colliding across sites."""
        claims = jwt.decode(
            canopy.assertion_for(user),
            configured,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["sub"] == str(user.pk)
        assert "@" not in claims["sub"]

    def test_it_vouches_for_the_signed_in_users_email(self, configured, user):
        """`email_verified` is how canopy recognises a member of the site's
        workspace as their own canopy account rather than a contact."""
        claims = jwt.decode(
            canopy.assertion_for(user),
            configured,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["email"] == user.email
        assert claims["email_verified"] is True

    def test_it_never_vouches_for_an_empty_address(self, configured, user):
        user.email = ""
        claims = jwt.decode(
            canopy.assertion_for(user),
            configured,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["email_verified"] is False

    def test_it_expires_inside_canopys_cap(self, configured, user):
        claims = jwt.decode(
            canopy.assertion_for(user),
            configured,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["exp"] - claims["iat"] <= 120, "canopy refuses a longer declared life"

    def test_the_audience_names_one_canopy_without_a_trailing_slash(self, settings, user, keys):
        """Canopy compares against its own base URL, which carries no trailing
        slash — and an address bar supplies one."""
        settings.CANOPY_BASE_URL = "https://labs.example.invalid/canopy/"
        settings.CANOPY_APP_NAME = "connect-labs"
        settings.CANOPY_SIGNING_KEY = keys[0]

        claims = jwt.decode(
            canopy.assertion_for(user),
            keys[1],
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["aud"] == "https://labs.example.invalid/canopy"

    def test_it_is_signed_asymmetrically(self, configured, user):
        """Canopy holds the public half, so a symmetric algorithm would make the
        verification key a signing key — anyone holding it could forge."""
        header = jwt.get_unverified_header(canopy.assertion_for(user))

        assert header["alg"] == "EdDSA"

    def test_each_assertion_is_single_use(self, configured, user):
        """Canopy refuses a replayed `jti`, so two mints must not collide."""
        first = jwt.decode(canopy.assertion_for(user), options={"verify_signature": False})
        second = jwt.decode(canopy.assertion_for(user), options={"verify_signature": False})

        assert first["jti"] != second["jti"]


@pytest.mark.django_db
class TestTheEndpoint:
    def test_it_requires_a_login(self, client, configured):
        response = client.post(reverse("labs:canopy_token"))

        assert response.status_code in (302, 403)

    def test_it_rejects_a_get(self, client, user, configured):
        client.force_login(user)

        assert client.get(reverse("labs:canopy_token")).status_code == 405

    def test_it_mints_for_the_session_user(self, client, user, configured):
        client.force_login(user)
        with mock.patch.object(
            canopy, "vouch_for", return_value={"token": "tok", "expires_at": "2026-01-01T00:00:00Z"}
        ) as vouch:
            response = client.post(reverse("labs:canopy_token"))

        assert response.status_code == 200
        assert response.json() == {"token": "tok", "expires_at": "2026-01-01T00:00:00Z"}
        assert vouch.call_args.args[0].pk == user.pk

    def test_a_subject_in_the_body_is_ignored(self, client, user, django_user_model, configured):
        """The one that matters. Were the subject read from the request, any
        signed-in person could be vouched for as anybody else."""
        victim = django_user_model.objects.create_user(username="someone-else", password="x")
        client.force_login(user)

        with mock.patch.object(canopy, "vouch_for", return_value={"token": "t", "expires_at": ""}) as vouch:
            client.post(
                reverse("labs:canopy_token"),
                data=json.dumps({"sub": str(victim.pk), "username": victim.username}),
                content_type="application/json",
            )

        assert vouch.call_args.args[0].pk == user.pk

    def test_an_unconfigured_deployment_says_so(self, client, user, settings):
        settings.CANOPY_SIGNING_KEY = ""
        client.force_login(user)

        assert client.post(reverse("labs:canopy_token")).status_code == 503

    def test_canopys_refusal_does_not_reach_the_browser(self, client, user, configured):
        """Canopy's codes (`replayed`, `bad_signature`) describe labs' credential,
        not the visitor's session, and the visitor can act on none of them."""
        client.force_login(user)
        with mock.patch.object(
            canopy, "vouch_for", side_effect=canopy.CanopyMintFailed("canopy returned 401: bad_signature")
        ):
            response = client.post(reverse("labs:canopy_token"))

        assert response.status_code == 502
        assert "bad_signature" not in response.content.decode()


@pytest.mark.django_db
class TestTheTenant:
    """canopy-web #960: a site's name is unique only within one canopy workspace,
    so the panel names its agent — or canopy refuses once a second workspace
    registers a `connect-labs`."""

    def test_the_mint_names_the_agent(self, configured, user, settings):
        settings.CANOPY_AGENT_SLUG = "ace"
        sent = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"token": "t", "expires_at": ""}'

        def fake_urlopen(request, timeout):
            sent.update(json.loads(request.data))
            return _Resp()

        with mock.patch.object(canopy.urllib.request, "urlopen", fake_urlopen):
            canopy.vouch_for(user)

        assert sent["agent_slug"] == "ace"
        assert sent["assertion"]

    def test_the_panel_names_the_agent_to_the_widget(self, client, user, configured, settings):
        settings.CANOPY_AGENT_SLUG = "ace"
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert 'agent: "ace"' in body


@pytest.mark.django_db
class TestTheJwks:
    """Publishing a URL rather than a pasted key is what makes rotation free:
    swap the secret and canopy follows by `kid` on its next fetch."""

    def test_it_needs_no_session(self, client, configured):
        """A public key is public, and canopy fetches it from outside any session."""
        response = client.get(reverse("labs:canopy_jwks"))

        assert response.status_code == 200
        assert response.json()["keys"][0]["kty"] == "OKP"

    def test_it_publishes_the_public_half_and_never_the_private_one(self, client, configured):
        response = client.get(reverse("labs:canopy_jwks"))

        body = response.content.decode()
        assert "PRIVATE KEY" not in body
        assert "d" not in response.json()["keys"][0], "`d` is the private scalar of an OKP key"

    def test_the_published_key_verifies_what_we_sign(self, client, user, configured):
        """The whole contract in one assertion: canopy fetches this document and
        must be able to check our signature with it."""
        from jwt import PyJWK

        published = client.get(reverse("labs:canopy_jwks")).json()["keys"][0]
        claims = jwt.decode(
            canopy.assertion_for(user),
            PyJWK.from_dict(published).key,
            algorithms=["EdDSA"],
            audience="https://labs.example.invalid/canopy",
        )

        assert claims["sub"] == str(user.pk)

    def test_the_assertion_names_the_key_that_signed_it(self, client, configured, user):
        """Canopy selects a verification key by `kid`. Without it a rotation has
        nothing to select on and succeeds only by luck of ordering."""
        published = client.get(reverse("labs:canopy_jwks")).json()["keys"][0]

        header = jwt.get_unverified_header(canopy.assertion_for(user))

        assert header["kid"] == published["kid"]

    def test_the_kid_is_derived_from_the_key_so_rotation_changes_it(self, settings, keys):
        """A fixed label would give two different keys the same name."""
        settings.CANOPY_BASE_URL = "https://labs.example.invalid/canopy"
        settings.CANOPY_APP_NAME = "connect-labs"

        settings.CANOPY_SIGNING_KEY = keys[0]
        before = canopy.public_jwk()["kid"]
        settings.CANOPY_SIGNING_KEY = _keypair()[0]
        after = canopy.public_jwk()["kid"]

        assert before != after

    def test_an_unreadable_key_publishes_an_empty_set(self, client, settings):
        """Canopy then refuses our assertions, rather than being handed something
        it cannot parse."""
        settings.CANOPY_BASE_URL = "https://labs.example.invalid/canopy"
        settings.CANOPY_APP_NAME = "connect-labs"
        settings.CANOPY_SIGNING_KEY = "not a pem"

        response = client.get(reverse("labs:canopy_jwks"))

        assert response.status_code == 503
        assert response.json() == {"keys": []}


class TestPageState:
    def test_it_carries_the_selection_and_not_the_rows(self):
        state = canopy.panel_context(
            resource="labs-marketplace://orgs",
            backing_tool="marketplace_orgs_get",
            visible_ids=["acme-health", "beta-care"],
            filters={"country": "Uganda"},
        )["page_state"]

        assert state["visible_ids"] == ["acme-health", "beta-care"]
        assert state["backing_tool"] == "marketplace_orgs_get"
        assert state["filters"] == {"country": "Uganda"}

    def test_a_long_selection_is_truncated_rather_than_refused(self):
        """Canopy refuses a state over 8 KiB outright, which would leave the agent
        blind to the whole screen. A truncated selection still describes most of
        it, so the cap is applied here where the page is built."""
        state = canopy.panel_context(
            resource="labs-marketplace://orgs",
            visible_ids=[f"org-{n}" for n in range(1000)],
        )["page_state"]

        assert len(state["visible_ids"]) == canopy.MAX_VISIBLE_IDS
        assert len(json.dumps(state).encode()) < 8192, "canopy's own cap"

    def test_a_full_page_of_org_slugs_fits_canopys_byte_cap(self):
        """400 realistic slugs are ~11 KiB — over canopy's 8 KiB cap, which
        refused the whole state and left the agent blind (2026-09-25)."""
        slugs = [f"some-organisation-name-{i:04d}" for i in range(400)]

        state = canopy.panel_context(
            resource="labs-marketplace://orgs", visible_ids=slugs, backing_tool="marketplace_orgs_get"
        )["page_state"]

        assert len(json.dumps(state).encode()) <= 8192
        assert state["visible_ids"] == slugs[: len(state["visible_ids"])], "a prefix, in order"
        assert len(state["visible_ids"]) > 100, "trimmed, not emptied"

    def test_no_resource_means_no_declaration(self):
        """A page that says nothing must not declare an empty screen — the agent
        would reason about a blank selection as though it were the truth."""
        assert canopy.panel_context()["page_state"] is None


@pytest.mark.django_db
class TestTheRenderedPanel:
    """The include is the part that ships, so assert against rendered HTML rather
    than the context that feeds it."""

    def test_it_renders_nothing_without_a_key(self, client, user, settings):
        settings.CANOPY_SIGNING_KEY = ""
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert "embed/widget.js" not in body

    def test_it_renders_the_launcher_and_the_page_state(self, client, user, configured):
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert "https://labs.example.invalid/canopy/embed/widget.js" in body
        assert 'id="canopy-page-state"' in body
        assert "labs-marketplace://orgs" in body
        assert "marketplace_orgs_get" in body
        # The CSRF binding: labs' cookie is HttpOnly, so the token comes from the
        # DOM. Without this the mint 403s and the widget never starts.
        assert "csrfmiddlewaretoken" in body
        assert 'name="csrfmiddlewaretoken"' in body, "the token the panel reads must be on the page"

    def test_the_launcher_is_rendered_inside_the_body(self, client, user, configured):
        """`{% block javascript %}` renders in <head>, where `document.body` is
        still null and the launcher has nothing to attach to — so the panel goes
        at the end of the body instead. A silent no-launcher is the symptom."""
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert body.index("<body") < body.index("embed/widget.js")
        # And after the token it reads, which base.html renders in the body.
        assert body.index('name="csrfmiddlewaretoken"') < body.index("canopy-page-state")

    def test_the_round_page_declares_its_round(self, client, user, configured, marketplace_round):
        """So "each of these orgs, for THIS EOI" resolves without the visitor
        having to name the round."""
        client.force_login(user)

        body = client.get(reverse("marketplace:round", args=["mg-2026"])).content.decode()

        assert "labs-marketplace://rounds/mg-2026" in body
        assert "marketplace_rounds_list" in body

    def test_the_template_leaves_no_django_comment_in_the_output(self, client, user, configured):
        client.force_login(user)

        body = client.get(reverse("marketplace:network")).content.decode()

        assert "{#" not in body
        assert "{%" not in body
