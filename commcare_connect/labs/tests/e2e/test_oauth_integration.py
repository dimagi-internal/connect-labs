"""
Simple integration tests for OAuth - tests the actual user workflow.

Setup: python manage.py get_cli_token

Tests use the saved token from ~/.commcare-connect/token.json
and load credentials from .env (same as real CLI).
"""
import pytest
from django.conf import settings

from commcare_connect.labs.integrations.connect.cli import TokenManager, get_labs_user_from_token
from commcare_connect.labs.integrations.connect.oauth import introspect_token


@pytest.mark.e2e
def test_token_manager_loads_saved_token():
    """Test that TokenManager loads the token saved by get_cli_token command."""
    manager = TokenManager()
    access_token = manager.get_valid_token()

    assert access_token is not None, "No token found. Run: python manage.py get_cli_token"
    print(f"\n[OK] Loaded token: {access_token[:20]}...")


@pytest.mark.e2e
def test_introspect_saved_token():
    """Test introspecting the saved token to get user profile."""
    manager = TokenManager()
    access_token = manager.get_valid_token()
    assert access_token is not None, "No token found. Run: python manage.py get_cli_token"

    # Uses WEB OAuth credentials from .env (confidential client can introspect)
    user_profile = introspect_token(
        access_token=access_token,
        client_id=settings.CONNECT_OAUTH_CLIENT_ID,
        client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
        production_url=settings.CONNECT_PRODUCTION_URL,
    )

    assert user_profile is not None, "Introspection failed. Check CONNECT_OAUTH_CLIENT_SECRET in .env"
    assert user_profile["username"]
    # Note: ID may be 0 (introspection doesn't always return user_id)
    print(f"\n[OK] User: {user_profile['username']}")


@pytest.mark.e2e
def test_get_labs_user_like_real_script():
    """Test creating User from saved token - the typical CLI pattern."""
    # Exactly what a real script does
    user = get_labs_user_from_token()

    assert user is not None, "Failed to create User. Check token and .env credentials"
    assert user.username
    assert user.is_authenticated is True
    print(f"\n[OK] Created User: {user.username}")
