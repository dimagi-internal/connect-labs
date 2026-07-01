"""
CLI OAuth tools for CommCare Connect.

Provides browser-based OAuth flow for command-line scripts.
Tokens are stored in the user's home directory (~/.commcare-connect/token.json).

Usage:
    from connect_labs.labs.integrations.connect.cli import get_oauth_token

    token = get_oauth_token(
        client_id="your_client_id",
        production_url="https://production.com"
    )

Or for scripts that need a Django request-like object:
    from connect_labs.labs.integrations.connect.cli import create_cli_request

    request = create_cli_request(opportunity_id=814)
"""

from connect_labs.labs.integrations.connect.cli.client import (
    create_cli_request,
    get_labs_user_from_token,
    get_oauth_token,
)
from connect_labs.labs.integrations.connect.cli.token_manager import TokenManager, get_or_refresh_token

__all__ = [
    "get_oauth_token",
    "get_labs_user_from_token",
    "create_cli_request",
    "TokenManager",
    "get_or_refresh_token",
]
