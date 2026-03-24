"""
Tests for security fixes: SSRF prevention, open redirect protection,
and authentication requirements.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from commcare_connect.solicitations.views import _fetch_url_content, validate_url_safe
from commcare_connect.users.models import User


class TestSSRFProtection:
    """Test that _fetch_url_content blocks requests to internal/private addresses."""

    BLOCKED_URLS = [
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8080/admin",
        "http://127.0.0.1/secret",
        "http://10.0.0.1/internal",
        "http://172.16.0.1/private",
        "http://192.168.1.1/router",
        "http://[::1]/ipv6-loopback",
    ]

    BLOCKED_MESSAGE = "[Blocked: URL points to internal/private address]"

    @pytest.mark.parametrize("url", BLOCKED_URLS)
    def test_validate_url_safe_blocks_internal(self, url):
        """validate_url_safe returns a blocked message for internal addresses."""
        result = validate_url_safe(url)
        assert result == self.BLOCKED_MESSAGE

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8080/admin",
            "http://127.0.0.1/secret",
            "http://10.0.0.1/internal",
        ],
    )
    def test_fetch_url_content_blocks_internal(self, url):
        """_fetch_url_content returns blocked message without making HTTP requests."""
        with patch("commcare_connect.solicitations.views.validate_url_safe", wraps=validate_url_safe):
            result = _fetch_url_content(url)
        assert result == self.BLOCKED_MESSAGE

    def test_fetch_url_content_allows_public_url(self):
        """A valid public URL should attempt the HTTP fetch (mocked)."""
        mock_response = MagicMock()
        mock_response.text = "<html><body>Hello world</body></html>"
        mock_response.raise_for_status = MagicMock()

        with (
            patch(
                "commcare_connect.solicitations.views.validate_url_safe",
                return_value=None,
            ),
            patch("httpx.get", return_value=mock_response) as mock_get,
        ):
            result = _fetch_url_content("https://example.com/page")

        mock_get.assert_called_once()
        assert "Hello world" in result

    def test_validate_url_safe_allows_public(self):
        """A public hostname resolving to a public IP should return None (safe)."""
        # Mock DNS resolution to return a public IP
        fake_addrinfo = [(2, 1, 6, "", ("93.184.216.34", 80))]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            result = validate_url_safe("https://example.com/page")
        assert result is None

    def test_validate_url_safe_blocks_dns_rebinding(self):
        """A hostname that resolves to a private IP should be blocked (DNS rebinding)."""
        fake_addrinfo = [(2, 1, 6, "", ("10.0.0.5", 80))]
        with patch("socket.getaddrinfo", return_value=fake_addrinfo):
            result = validate_url_safe("https://evil-rebind.example.com/")
        assert result == self.BLOCKED_MESSAGE


@pytest.mark.django_db
class TestOpenRedirectProtection:
    """Test that the OAuth callback does not redirect to external URLs."""

    def test_callback_blocks_external_redirect(self):
        """When oauth_next contains an external URL, redirect should go to /labs/overview/."""
        factory = RequestFactory()
        request = factory.get("/labs/callback/", {"state": "test-state", "code": "test-code"})

        # Set up session data
        request.session = {
            "oauth_state": "test-state",
            "oauth_code_verifier": "test-verifier",
            "oauth_next": "https://evil.example.com/steal",
        }

        # Mock the token exchange and introspection to succeed
        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh",
            "expires_in": 3600,
        }
        mock_token_response.raise_for_status = MagicMock()

        mock_profile = {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        }

        with (
            patch("httpx.post", return_value=mock_token_response),
            patch(
                "commcare_connect.labs.integrations.connect.oauth_views.introspect_token",
                return_value=mock_profile,
            ),
            patch(
                "commcare_connect.labs.integrations.connect.oauth_views.fetch_user_organization_data",
                return_value={"organizations": [], "programs": [], "opportunities": [], "user": {}},
            ),
            patch("commcare_connect.labs.integrations.connect.oauth_views.login"),
            patch("commcare_connect.labs.integrations.connect.oauth_views.messages"),
        ):
            from commcare_connect.labs.integrations.connect.oauth_views import labs_oauth_callback

            response = labs_oauth_callback(request)

        # Should redirect to safe default, not the evil URL
        assert response.status_code == 302
        assert response.url != "https://evil.example.com/steal"
        assert response.url == "/labs/overview/"

    def test_callback_allows_internal_redirect(self):
        """When oauth_next contains a safe internal path, it should be used."""
        factory = RequestFactory()
        request = factory.get("/labs/callback/", {"state": "test-state", "code": "test-code"})

        request.session = {
            "oauth_state": "test-state",
            "oauth_code_verifier": "test-verifier",
            "oauth_next": "/audit/",
        }

        mock_token_response = MagicMock()
        mock_token_response.json.return_value = {
            "access_token": "fake-token",
            "refresh_token": "fake-refresh",
            "expires_in": 3600,
        }
        mock_token_response.raise_for_status = MagicMock()

        mock_profile = {
            "id": 1,
            "username": "testuser_internal",
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        }

        with (
            patch("httpx.post", return_value=mock_token_response),
            patch(
                "commcare_connect.labs.integrations.connect.oauth_views.introspect_token",
                return_value=mock_profile,
            ),
            patch(
                "commcare_connect.labs.integrations.connect.oauth_views.fetch_user_organization_data",
                return_value={"organizations": [], "programs": [], "opportunities": [], "user": {}},
            ),
            patch("commcare_connect.labs.integrations.connect.oauth_views.login"),
            patch("commcare_connect.labs.integrations.connect.oauth_views.messages"),
        ):
            from commcare_connect.labs.integrations.connect.oauth_views import labs_oauth_callback

            response = labs_oauth_callback(request)

        assert response.status_code == 302
        assert response.url == "/audit/"


@pytest.mark.django_db
class TestClearContextAuth:
    """Test that the clear_context view requires authentication."""

    def test_unauthenticated_post_redirects_to_login(self, client):
        """An unauthenticated POST to clear-context should redirect to login."""
        response = client.post("/labs/clear-context/")
        assert response.status_code == 302
        assert "/labs/login/" in response.url

    def test_authenticated_post_succeeds(self, client):
        """An authenticated POST to clear-context should not return 302 to login."""
        User.objects.create_user(username="authuser", password="testpass123")
        client.login(username="authuser", password="testpass123")

        # Need a session with labs context for clear_context to process
        session = client.session
        session["labs_context"] = {"opportunity_id": 123}
        session.save()

        response = client.post("/labs/clear-context/")
        # Should redirect back to referrer or overview, not to login
        assert response.status_code == 302
        assert "/labs/login/" not in response.url
