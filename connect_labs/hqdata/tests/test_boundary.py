"""A person who reaches labs only through CommCare is confined to /hq/ and has no MCP."""

import pytest
from django.test import override_settings
from django.utils import timezone

from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.mcp import server as mcp_server
from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LABS_MIDDLEWARE = "connect_labs.labs.oauth_session.LabsOAuthSessionMiddleware"


@pytest.fixture
def with_labs_boundary(settings):
    """The middleware stack the real deployments run (local.py / labs_aws.py), which the test settings omit."""
    middleware = list(settings.MIDDLEWARE)
    at = middleware.index("connect_labs.utils.sandbox_guard.SandboxOriginGuardMiddleware") + 1
    middleware.insert(at, LABS_MIDDLEWARE)
    with override_settings(MIDDLEWARE=middleware):
        yield


def signed_in(client):
    return "_auth_user_id" in client.session


def test_hq_is_a_satellite_so_the_labs_boundary_leaves_it_alone(client, with_labs_boundary):
    make_hq_user(client)
    assert client.get("/commcare/").status_code == 200
    assert signed_in(client)


@pytest.mark.parametrize(
    "path",
    [
        "/labs/overview/",
        "/labs/workflow/",
        "/labs/data/",
        "/labs/pulse/",
        "/labs/marketplace/",
        "/labs/mcp/tokens/",
        "/supply/",
        "/microplans/",
        "/solicitations/",
        "/audit/",
        "/tasks/",
        "/funder/",
        "/wa-revisit/",
    ],
)
def test_a_commcare_only_session_is_signed_out_the_moment_it_touches_labs(client, with_labs_boundary, path):
    make_hq_user(client)
    client.get(path)
    assert not signed_in(client)


def test_a_connect_person_with_a_labs_session_is_unaffected_by_hq(client, with_labs_boundary):
    from datetime import timedelta

    from connect_labs.labs.models import UserCCHQToken

    user = UserFactory()
    UserCCHQToken.objects.create(
        user=user, access_token="a", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "x",
        "expires_at": timezone.now().timestamp() + 3600,
        "organization_data": {},
    }
    session.save()
    from unittest import mock

    with mock.patch(
        "connect_labs.hqdata.identity.fetch_identity",
        return_value={"username": "a@example.org", "email": "", "name": "", "domains": []},
    ):
        assert client.get("/commcare/").status_code == 200
    assert signed_in(client) and "labs_oauth" in client.session


def test_a_commcare_only_person_gets_nothing_from_the_mcp_door(client):
    hq_user = make_hq_user(client)
    token, raw = MCPAccessToken.create_token(hq_user, "t", scope=token_scopes.NO_USERVISIT_DATA)
    assert mcp_server._resolve_bearer_sync(raw) is not None  # the token itself is real...
    assert mcp_server._verify_bearer_sync(raw) is None  # ...and the door still refuses it


def test_a_connect_person_still_gets_through_the_mcp_door():
    user = UserFactory()
    from datetime import timedelta

    from connect_labs.labs.models import UserConnectToken

    UserConnectToken.objects.create(
        user=user, access_token="a", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    token, raw = MCPAccessToken.create_token(user, "t", scope=token_scopes.NO_USERVISIT_DATA)
    resolved = mcp_server._verify_bearer_sync(raw)
    assert resolved is not None and resolved[0].pk == user.pk


def test_a_url_context_on_an_hq_path_is_never_validated_or_saved(client, with_labs_boundary):
    make_hq_user(client)
    assert client.get("/commcare/?opportunity_id=-1000000005&program_id=42").status_code == 200
    assert "labs_context" not in client.session


def test_opening_the_sandbox_shell_does_not_sign_a_commcare_person_out(client, with_labs_boundary):
    make_hq_user(client)
    client.get("/labs/sandbox/render/")
    assert signed_in(client)
