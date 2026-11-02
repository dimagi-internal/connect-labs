"""Tests for the browser-driven MCP token-creation view."""

import asyncio
import importlib.util
import os
import stat
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from starlette.applications import Starlette
from starlette.routing import Mount

from config.asgi import _ReprefixApp
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import allow_self_mint
from connect_labs.users.models import User

VALID_CALLBACK = "http://127.0.0.1:8765/cb"
VALID_STATE = "x" * 16


@pytest.fixture
def logged_in_user(client, db):
    user = User.objects.create(username="tokenflow")
    client.force_login(user)
    return user


@pytest.mark.django_db
def test_get_renders_consent_page(client, logged_in_user):
    url = reverse("mcp:admin_create_token")
    resp = client.get(url, {"callback": VALID_CALLBACK, "state": VALID_STATE})
    assert resp.status_code == 200
    # Nothing tells labs which app opened this page, so it does not name one.
    assert b"Claude Code" not in resp.content
    assert b"Labs can't tell which app is asking" in resp.content
    # Username visible on the page
    assert b"tokenflow" in resp.content


@pytest.mark.django_db
def test_post_creates_token_and_redirects_with_token(client, logged_in_user):
    allow_self_mint(logged_in_user)
    url = reverse("mcp:admin_create_token")
    resp = client.post(
        url,
        {"callback": VALID_CALLBACK, "state": VALID_STATE, "name": "mylaptop"},
    )
    assert resp.status_code == 302
    assert resp["Location"].startswith(VALID_CALLBACK)
    assert "token=" in resp["Location"]
    assert "state=" + VALID_STATE in resp["Location"]
    assert "name=mylaptop" in resp["Location"]
    # DB row created
    assert MCPAccessToken.objects.filter(user=logged_in_user, name="mylaptop").exists()


@pytest.mark.django_db
def test_post_auto_generates_name_when_empty(client, logged_in_user):
    allow_self_mint(logged_in_user)
    url = reverse("mcp:admin_create_token")
    resp = client.post(
        url,
        {"callback": VALID_CALLBACK, "state": VALID_STATE, "name": ""},
    )
    assert resp.status_code == 302
    # Auto-name contains the claude-code- prefix
    token = MCPAccessToken.objects.filter(user=logged_in_user).first()
    assert token.name.startswith("claude-code-")


@pytest.mark.django_db
def test_unauthenticated_redirects_to_login(client):
    url = reverse("mcp:admin_create_token")
    resp = client.get(url, {"callback": VALID_CALLBACK, "state": VALID_STATE})
    # login_required kicks in — expect redirect (302) to login url
    assert resp.status_code == 302


@pytest.mark.django_db
def test_non_localhost_callback_rejected(client, logged_in_user):
    url = reverse("mcp:admin_create_token")
    for bad in [
        "https://evil.com/cb",
        "http://example.com/cb",
        "http://localhost.evil.com/cb",
        "http://10.0.0.1:8000/cb",
    ]:
        resp = client.get(url, {"callback": bad, "state": VALID_STATE})
        assert resp.status_code == 400, f"expected 400 for {bad}"


@pytest.mark.django_db
def test_out_of_range_port_rejected(client, logged_in_user):
    url = reverse("mcp:admin_create_token")
    for bad in [
        "http://127.0.0.1:80/cb",  # privileged
        "http://127.0.0.1/cb",  # missing
        "http://127.0.0.1:99999/cb",
    ]:
        resp = client.get(url, {"callback": bad, "state": VALID_STATE})
        assert resp.status_code == 400, f"expected 400 for {bad}"


@pytest.mark.django_db
def test_short_state_rejected(client, logged_in_user):
    url = reverse("mcp:admin_create_token")
    resp = client.get(url, {"callback": VALID_CALLBACK, "state": "too-short"})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_post_rejects_mismatched_callback(client, logged_in_user):
    """POST should re-validate; attacker can't swap callback between GET and POST."""
    url = reverse("mcp:admin_create_token")
    resp = client.post(
        url,
        {"callback": "http://evil.com/", "state": VALID_STATE, "name": "x"},
    )
    assert resp.status_code == 400


# --- what the page says ---------------------------------------------------------------


def _page(client, **params):
    return client.get(
        reverse("mcp:admin_create_token"), {"callback": VALID_CALLBACK, "state": VALID_STATE, **params}
    ).context["consent"]


@pytest.mark.django_db
def test_the_page_describes_each_access_level_as_what_it_is(client, logged_in_user):
    assert "visit data" in _page(client).access_text
    assert _page(client).next_step  # full access: the passkey comes next
    restricted = _page(client, scope="no-uservisit-data")
    assert restricted.access_label == "No user visit data" and not restricted.next_step
    # This page used to tell a coaching-pictures token it could do anything.
    assert "coaching pictures" in _page(client, scope="coach-images").access_text


@pytest.mark.django_db
def test_an_account_that_self_mints_is_not_promised_a_passkey(client, logged_in_user):
    allow_self_mint(logged_in_user)
    consent = _page(client)
    assert not consent.next_step and consent.button == "Approve"


# --- a temporary token ------------------------------------------------------------------


@pytest.mark.django_db
def test_the_page_says_how_long_a_token_lasts(client, logged_in_user):
    assert _page(client).lasts.startswith("90 days")
    assert _page(client, temporary="1").lasts.startswith("1 hour")


@pytest.mark.django_db
def test_a_temporary_token_lasts_an_hour(client, logged_in_user):
    allow_self_mint(logged_in_user)
    resp = client.post(
        reverse("mcp:admin_create_token"),
        {"callback": VALID_CALLBACK, "state": VALID_STATE, "temporary": "1"},
    )
    assert resp.status_code == 302
    token = MCPAccessToken.objects.get(user=logged_in_user)
    assert token.name.startswith("temporary-")
    assert timedelta(minutes=59) < token.expires_at - timezone.now() <= timedelta(hours=1)


@pytest.mark.django_db
def test_without_the_flag_a_token_still_lasts_ninety_days(client, logged_in_user):
    allow_self_mint(logged_in_user)
    client.post(reverse("mcp:admin_create_token"), {"callback": VALID_CALLBACK, "state": VALID_STATE})
    token = MCPAccessToken.objects.get(user=logged_in_user)
    assert token.expires_at - timezone.now() > timedelta(days=89)


# --- the setup script's temporary mode (.claude/skills is outside CI's path, this is not) ---


def _setup_script():
    path = Path(settings.BASE_DIR) / ".claude/skills/labs-token-setup/setup_labs_token.py"
    spec = importlib.util.spec_from_file_location("setup_labs_token", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_setup_script_writes_a_temporary_token_only_its_user_can_read(tmp_path):
    written = _setup_script().write_temporary_token("s3cret", str(tmp_path / "token"))
    assert Path(written).read_text() == "s3cret"
    assert stat.S_IMODE(os.stat(written).st_mode) == 0o600


def test_the_setup_script_tightens_a_file_that_was_already_there(tmp_path):
    existing = tmp_path / "token"
    existing.write_text("old old old")
    existing.chmod(0o644)
    _setup_script().write_temporary_token("new", str(existing))
    assert existing.read_text() == "new"
    assert stat.S_IMODE(existing.stat().st_mode) == 0o600


def test_the_setup_script_makes_a_private_file_when_given_no_path():
    written = _setup_script().write_temporary_token("s3cret")
    try:
        assert stat.S_IMODE(os.stat(written).st_mode) == 0o600
    finally:
        os.unlink(written)


# --- through the ASGI mount, which Django's test client never sees ------------------------


def _scope_django_sees(url):
    """What the app behind the real ``/mcp/admin`` mount is handed for ``url``."""
    seen = {}

    async def capture(scope, receive, send):
        seen.update(path=scope["path"], root_path=scope["root_path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = Starlette(routes=[Mount("/mcp/admin", app=_ReprefixApp("/mcp/admin", capture))])

    async def call():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            await client.get(url)

    asyncio.run(call())
    return seen


def test_the_mount_gives_django_the_public_path_and_no_script_prefix():
    """A script prefix makes Django prepend /mcp/admin to every URL it writes: the page's
    links, the form's own action (which then 404s), and the login redirect's ``next``."""
    assert _scope_django_sees("/mcp/admin/create-token/?state=x") == {
        "path": "/mcp/admin/create-token/",
        "root_path": "",
    }


@pytest.mark.django_db
def test_the_form_posts_to_the_public_address(client, logged_in_user):
    resp = client.get(reverse("mcp:admin_create_token"), {"callback": VALID_CALLBACK, "state": VALID_STATE})
    assert b'action="/mcp/admin/create-token/"' in resp.content
