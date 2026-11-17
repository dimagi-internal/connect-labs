from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from django.utils import timezone

from connect_labs.hqdata.identity import IdentityError, identity_username
from connect_labs.hqdata.models import HQIdentity, HQProject, HQProjectMember
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User
from connect_labs.users.tests.factories import UserFactory
from connect_labs.utils.dimagi_user import is_dimagi_user

pytestmark = pytest.mark.django_db

HOST = "www.commcarehq.org"
IDENTITY = {"username": "simon@example.org", "email": "simon@example.org", "name": "Simon S", "domains": ["demo"]}


def token_response(status=200):
    resp = mock.Mock(status_code=status)
    resp.json.return_value = {"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}
    return resp


@pytest.fixture
def arrived(client):
    """A browser that has been sent to CommCare and is coming back with `code` and the saved state."""
    client.get("/commcare/login/initiate/?next=/commcare/projects/7/")
    return client.session["hq_login_state"]


def call_back(client, state, *, identity=IDENTITY, token=None, **params):
    token = token or token_response()
    with (
        mock.patch("httpx.Client.post", return_value=token),
        mock.patch("connect_labs.hqdata.web.auth_views.fetch_identity", return_value=identity),
    ):
        return client.get("/commcare/login/callback/", {"code": "c", "state": state, **params})


def test_initiate_sends_the_person_to_commcare_with_pkce_and_our_callback(client):
    resp = client.get("/commcare/login/initiate/")
    assert resp.status_code == 302
    url = urlparse(resp["Location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://www.commcarehq.org/oauth/authorize/"
    assert q["client_id"] == "client-id" and q["scope"] == "access_apis" and q["response_type"] == "code"
    # Until /commcare/login/callback/ is registered on the HQ OAuth app, sign-in keeps using the registered /hq/ one.
    assert q["redirect_uri"].endswith("/hq/login/callback/")
    assert (
        q["code_challenge_method"] == "S256" and q["code_challenge"] and q["state"] == client.session["hq_login_state"]
    )


@pytest.mark.parametrize(
    "asked,landed",
    [
        ("/commcare/projects/3/", "/commcare/projects/3/"),
        ("https://evil.example/hq/", "/commcare/"),
        ("//evil.example/hq/", "/commcare/"),
        ("/labs/overview/", "/commcare/"),
        ("/commcare/../labs/overview/", "/commcare/"),  # a browser would resolve this to /labs/overview/
    ],
)
def test_next_only_ever_stays_under_hq(client, asked, landed):
    client.get("/commcare/login/initiate/", {"next": asked})
    assert client.session["hq_login_next"] == landed


def test_sign_in_creates_a_commcare_only_user_with_no_email_and_no_labs_session(client, arrived):
    resp = call_back(client, arrived)
    assert resp.status_code == 302 and resp["Location"] == "/commcare/projects/7/"

    user = User.objects.get(username=identity_username(HOST, "simon@example.org"))
    assert user.email is None and not user.has_usable_password() and user.name == "Simon S"
    assert client.session["_auth_user_id"] == str(user.pk)
    assert client.session["hq_auth"]["host"] == HOST
    assert "labs_oauth" not in client.session
    token = UserCCHQToken.objects.get(user=user)
    assert (token.access_token, token.refresh_token) == ("acc", "ref")
    identity = HQIdentity.objects.get(user=user)
    assert identity.domains == ["demo"] and identity.hq_username == "simon@example.org"


def test_a_dimagi_address_on_a_commcare_account_does_not_make_anyone_staff(client, arrived):
    call_back(client, arrived, identity={**IDENTITY, "username": "x@evil.example", "email": "boss@dimagi.com"})
    user = User.objects.get(username=identity_username(HOST, "x@evil.example"))
    assert user.email is None and not is_dimagi_user(user) and not user.is_staff


def test_a_commcare_account_claiming_someones_email_does_not_sign_in_as_them(client, arrived):
    boss = UserFactory(username="connect-boss", email="boss@dimagi.com", is_staff=True)
    call_back(client, arrived, identity={**IDENTITY, "username": "imposter@example.org", "email": "boss@dimagi.com"})
    assert client.session["_auth_user_id"] != str(boss.pk)
    assert User.objects.get(pk=client.session["_auth_user_id"]).username == identity_username(
        HOST, "imposter@example.org"
    )
    boss.refresh_from_db()
    assert boss.email == "boss@dimagi.com" and not HQIdentity.objects.filter(user=boss).exists()


def test_signing_in_again_reuses_the_user_and_restamps_memberships(client, arrived):
    call_back(client, arrived)
    user = User.objects.get(username=identity_username(HOST, "simon@example.org"))
    project = HQProject.objects.create(domain="demo")
    HQProjectMember.objects.create(project=project, user=user, verified_at=timezone.now() - timedelta(days=3))
    gone = HQProject.objects.create(domain="left")
    HQProjectMember.objects.create(project=gone, user=user)
    client.get("/commcare/logout/")
    client.get("/commcare/login/initiate/")
    with mock.patch(
        "connect_labs.labs.integrations.commcare.api_client.CommCareDataAccess.verify_hq_access", return_value=True
    ):
        call_back(client, client.session["hq_login_state"])
    assert User.objects.filter(username=user.username).count() == 1
    assert HQProjectMember.objects.get(project=project, user=user).verified_at > timezone.now() - timedelta(minutes=1)
    assert not HQProjectMember.objects.filter(project=gone, user=user).exists()


@pytest.mark.parametrize("state", ["", "wrong"])
def test_a_callback_with_the_wrong_state_signs_nobody_in(client, arrived, state):
    resp = call_back(client, state)
    assert (
        resp.status_code == 400
        and "_auth_user_id" not in client.session
        and not User.objects.filter(username__startswith="cchq:").exists()
    )


def test_a_callback_with_no_login_in_progress_is_refused(client):
    resp = client.get("/commcare/login/callback/", {"code": "c", "state": "s"})
    assert resp.status_code == 400


def test_commcare_saying_no_is_shown_and_signs_nobody_in(client, arrived):
    resp = client.get("/commcare/login/callback/", {"error": "access_denied", "error_description": "You said no"})
    assert resp.status_code == 403 and b"You said no" in resp.content


def test_a_rejected_token_exchange_signs_nobody_in(client, arrived):
    resp = call_back(client, arrived, token=token_response(400))
    assert resp.status_code == 403 and "_auth_user_id" not in client.session


def test_an_unreachable_commcare_is_a_502_not_a_crash(client, arrived):
    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectError("down")):
        resp = client.get("/commcare/login/callback/", {"code": "c", "state": arrived})
    assert resp.status_code == 502 and "_auth_user_id" not in client.session


def test_an_unreadable_identity_signs_nobody_in(client, arrived):
    with (
        mock.patch("httpx.Client.post", return_value=token_response()),
        mock.patch("connect_labs.hqdata.web.auth_views.fetch_identity", side_effect=IdentityError("nope")),
    ):
        resp = client.get("/commcare/login/callback/", {"code": "c", "state": arrived})
    assert resp.status_code == 403 and "_auth_user_id" not in client.session


def test_a_deactivated_account_is_not_signed_in(client, arrived):
    User.objects.create(username=identity_username(HOST, "simon@example.org"), email=None, is_active=False)
    resp = call_back(client, arrived)
    assert resp.status_code == 403 and "_auth_user_id" not in client.session


def test_the_state_cannot_be_replayed(client, arrived):
    call_back(client, arrived)
    client.get("/commcare/logout/")
    assert call_back(client, arrived).status_code == 400


def test_a_very_long_commcare_username_still_fits_the_user_table():
    assert len(identity_username(HOST, "x" * 400)) <= 150
    assert identity_username(HOST, "x" * 400) == identity_username(HOST, "x" * 400)
    assert identity_username(HOST, "x" * 400) != identity_username(HOST, "x" * 399 + "y")


def test_the_callback_path_is_a_setting_so_it_can_move_once_the_new_address_is_registered(client, settings):
    settings.HQ_LOGIN_CALLBACK_PATH = "/commcare/login/callback/"
    resp = client.get("/commcare/login/initiate/")
    q = {k: v[0] for k, v in parse_qs(urlparse(resp["Location"]).query).items()}
    assert q["redirect_uri"].endswith("/commcare/login/callback/")


def test_the_old_callback_address_still_receives_the_sign_in_and_both_addresses_reach_the_same_view(client):
    for path in ("/hq/login/callback/", "/commcare/login/callback/"):
        resp = client.get(path)  # no code or state: refused by the callback itself, not a 404 and not a redirect
        assert resp.status_code == 400, path


def test_everything_else_on_the_old_address_moves_to_the_new_one_and_keeps_its_query(client):
    resp = client.get("/hq/projects/3/workflows/9/?x=1")
    assert resp.status_code == 302 and resp["Location"] == "/commcare/projects/3/workflows/9/?x=1"
    assert client.get("/hq/")["Location"] == "/commcare/"
    posted = client.post("/hq/projects/3/act/", {"action": "sync_now"})
    assert posted.status_code == 307 and posted["Location"] == "/commcare/projects/3/act/"


def test_an_old_link_in_next_is_carried_over_to_the_new_address(client):
    client.get("/commcare/login/initiate/?next=/hq/projects/3/")
    assert client.session["hq_login_next"] == "/commcare/projects/3/"
