"""canopy's live probe at labs: the probe principal, the endpoint, and the whole chain.

The probe's protocol (who may ask, what the answer carries, every refusal) is the
canopy SDK's (``canopy_sdk.host.ProbeHandler``) and is tested there. What is
tested HERE is labs' side of it:

* the probe principal is a service account nobody can sign in as;
* the probe is OFF — a 404, and not advertised — until that account exists;
* with it, the SDK's own live conformance run passes against labs' real app:
  canopy gets a probe ID-JAG, redeems it at ``/o/token/``, the probe tool
  SUCCEEDS as the probe user, a tool outside its scope is refused, and the call
  without a valid DPoP proof is refused;
* labs' configuration agrees with labs' URLconf and tool registry.
"""

from __future__ import annotations

from unittest import mock

import jwt
import pytest
from canopy_sdk import conformance, contract
from canopy_sdk.django import conf
from django.apps import apps as django_apps
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from starlette.testclient import TestClient

from connect_labs.labs import canopy
from connect_labs.mcp import oauth
from connect_labs.mcp.models import MCPAuditLog
from connect_labs.mcp.tests.test_delegation import BASE, ISSUER, RESOURCE, _host_key, _Labs
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.users.models import User

PROBE_URL = f"{BASE}/labs/canopy/probe/"


def _create_probe_user():
    """Exactly what the deploy's ``migrate`` runs (``mcp/0006``)."""
    import importlib

    migration = importlib.import_module("connect_labs.mcp.migrations.0006_canopy_probe_user")
    migration.create_probe_user(django_apps, None)
    return User.objects.get(username=canopy.PROBE_USERNAME)


@pytest.fixture
def enabled(settings, canopy_client, canopy_client_documents):
    """Labs with the grant on, trusting the conformance plugin's canopy client."""
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


def _ask(client, credentials, **extra):
    form = {
        "client_id": credentials.client_id,
        "client_assertion_type": contract.CLIENT_ASSERTION_TYPE,
        "client_assertion": credentials.client_assertion(ISSUER),
        **extra,
    }
    return client.post("/labs/canopy/probe/", form, headers={"DPoP": credentials.dpop_proof("POST", PROBE_URL)})


# ---------------------------------------------------------------------------
# The probe principal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheProbePrincipal:
    def test_it_is_an_active_service_account_with_no_way_to_sign_in(self):
        user = _create_probe_user()

        assert user.is_active, "the SDK refuses an inactive subject"
        assert not user.has_usable_password()
        assert not user.is_staff and not user.is_superuser
        assert user.email is None
        assert not user.groups.exists() and not user.user_permissions.exists()

    def test_the_migration_restores_that_shape_on_an_existing_row(self):
        user = _create_probe_user()
        user.set_password("hunter2")
        user.is_staff = True
        user.email = "someone@example.invalid"
        user.save()

        user = _create_probe_user()

        assert not user.has_usable_password()
        assert not user.is_staff
        assert user.email is None
        assert User.objects.filter(username=canopy.PROBE_USERNAME).count() == 1

    def test_no_connect_username_can_be_the_probe(self):
        """Connect validates usernames with the same validator; a ``:`` fails it."""
        with pytest.raises(ValidationError):
            UnicodeUsernameValidator()(canopy.PROBE_USERNAME)

    def test_the_oauth_callback_refuses_the_probe_username(self):
        from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback

        _create_probe_user()
        request = RequestFactory().get("/labs/callback/", {"state": "s", "code": "c"})
        request.session = {"oauth_state": "s", "oauth_code_verifier": "v", "oauth_next": "/labs/overview/"}
        token = mock.MagicMock()
        token.json.return_value = {"access_token": "a", "refresh_token": "r", "expires_in": 3600}
        views = "connect_labs.labs.integrations.connect.oauth_views"
        with (
            mock.patch("httpx.post", return_value=token),
            mock.patch("httpx.get", return_value=mock.MagicMock(status_code=500)),
            mock.patch(f"{views}.introspect_token", return_value={"id": 1, "username": canopy.PROBE_USERNAME}),
            mock.patch(f"{views}.fetch_user_organization_data", return_value={}),
            mock.patch(f"{views}.login") as login,
            mock.patch(f"{views}.messages"),
        ):
            response = labs_oauth_callback(request)

        assert response.status_code == 302
        assert response.url == reverse("labs:login")
        login.assert_not_called()
        assert "labs_oauth" not in request.session
        assert not User.objects.get(username=canopy.PROBE_USERNAME).has_usable_password()


# ---------------------------------------------------------------------------
# Off until the principal exists
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_probe_is_off_until_its_user_exists(client, enabled):
    User.objects.filter(username=canopy.PROBE_USERNAME).delete()

    assert _ask(client, enabled).status_code == 404
    assert contract.PROBE_ENDPOINT_METADATA_FIELD not in oauth.authorization_server_metadata()

    _create_probe_user()

    assert oauth.authorization_server_metadata()[contract.PROBE_ENDPOINT_METADATA_FIELD] == PROBE_URL
    assert _ask(client, enabled).status_code == 200


@pytest.mark.django_db
def test_the_probe_is_off_while_the_grant_is(client, enabled, settings):
    _create_probe_user()
    settings.CANOPY_CLIENT_ID = ""

    assert _ask(client, enabled).status_code == 404


@pytest.mark.django_db
def test_it_issues_an_id_jag_for_the_probe_user_and_nobody_else(client, enabled):
    user = _create_probe_user()

    response = _ask(client, enabled)

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["subject"] == str(user.pk)
    assert (body["scope"], body["tool"], body["arguments"]) == (
        "marketplace:read",
        "marketplace_rounds_list",
        {"open_only": True},
    )
    assert body["denied_tool"] == "list_templates"
    assert body["resource"] == RESOURCE
    claims = jwt.decode(body["id_jag"], options={"verify_signature": False})
    assert claims["sub"] == str(user.pk)
    assert claims[contract.PROBE_CLAIM] is True

    # The request may not pick the principal.
    other = User.objects.create(username="gillian")
    assert _ask(client, enabled, sub=str(other.pk)).status_code == 400


@pytest.mark.django_db
def test_a_deactivated_probe_user_is_refused_not_hidden(client, enabled):
    user = _create_probe_user()
    user.is_active = False
    user.save()

    response = _ask(client, enabled)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


# ---------------------------------------------------------------------------
# The whole chain, as canopy walks it
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_the_sdk_live_probe_passes_against_labs(enabled, canopy_client_documents):
    from config.asgi import build_application

    user = _create_probe_user()
    with TestClient(build_application(), base_url=BASE) as http:
        labs = _Labs(http, canopy_client_documents)
        report = conformance.run_live(
            ISSUER,
            RESOURCE,
            credentials=enabled,
            fetch_json=labs.fetch_json,
            post_form=labs.post_form,
            post_json=labs.post_json,
        )

    report.raise_for_failures()
    names = {check.name for check in report.checks}
    assert {
        "probe_issued",
        "live_grant_redeemed",
        "probe_tool_succeeds",
        "out_of_scope_not_listed",
        "out_of_scope_refused",
        "dpop_required_no_proof",
        "dpop_required_wrong_key",
        "dpop_required_not_bearer",
    } <= names
    row = MCPAuditLog.objects.get(tool_name=canopy.PROBE_TOOL, success=True)
    assert row.user == user, "the probe tool ran as the probe user"
    assert row.client_id == enabled.client_id
    assert not MCPAuditLog.objects.filter(tool_name=canopy.PROBE_DENIED_TOOL, success=True).exists()


# ---------------------------------------------------------------------------
# Labs' configuration agrees with itself
# ---------------------------------------------------------------------------


def test_the_endpoint_is_where_labs_mounts_the_view(settings):
    settings.LABS_PUBLIC_URL = BASE

    assert reverse("labs:canopy_probe") == "/labs/canopy/probe/"
    assert canopy.host_settings()["PROBE"]["ENDPOINT"] == BASE + reverse("labs:canopy_probe")


def test_no_public_url_means_no_probe_block(settings):
    settings.LABS_PUBLIC_URL = ""

    assert "PROBE" not in canopy.host_settings()


def test_the_probe_tool_is_in_scope_and_the_denied_tool_is_a_real_one_outside_it():
    from connect_labs.mcp import tools  # noqa: F401 -- registers the catalogue

    in_scope = canopy.SCOPE_TOOLS[canopy.PROBE_SCOPE]
    assert canopy.PROBE_TOOL in in_scope
    assert not get_tool(canopy.PROBE_TOOL).is_write
    assert canopy.PROBE_DENIED_TOOL not in in_scope
    assert get_tool(canopy.PROBE_DENIED_TOOL) is not None, "a denied tool no server offers proves less"
    assert canopy.PROBE_PAGE in canopy.PAGE_SCOPES


@pytest.mark.django_db
def test_the_sdk_reads_the_probe_labs_configured(enabled):
    user = _create_probe_user()

    probe = conf.get_host_config().probe

    assert probe is not None, "a PROBE block the SDK cannot use is logged and switched off"
    assert (probe.endpoint, probe.subject, probe.tool) == (PROBE_URL, str(user.pk), canopy.PROBE_TOOL)
