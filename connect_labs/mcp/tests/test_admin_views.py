"""Tests for the browser-driven MCP token-creation view."""
import pytest
from django.urls import reverse

from connect_labs.mcp.models import MCPAccessToken
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
    assert b"Claude Code is requesting" in resp.content
    # Username visible on the page
    assert b"tokenflow" in resp.content


@pytest.mark.django_db
def test_post_creates_token_and_redirects_with_token(client, logged_in_user):
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
