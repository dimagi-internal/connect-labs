"""Google OAuth CLI for Connect Labs MCP tools.

Handles browser-based OAuth flow for Google APIs (Sheets, Drive, Docs).
Caches tokens at ~/.connect-labs/google-token.json.

Usage:
    python tools/commcare_mcp/google_auth.py login
    python tools/commcare_mcp/google_auth.py status
    python tools/commcare_mcp/google_auth.py logout
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

logger = logging.getLogger(__name__)

TOKEN_DIR = Path.home() / ".connect-labs"
TOKEN_FILE = TOKEN_DIR / "google-token.json"
CLIENT_SECRET_FILE = TOKEN_DIR / "google-client-secret.json"

# Read-only access to Sheets and Drive metadata
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _ensure_dir():
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)


def get_credentials() -> Credentials | None:
    """Load cached Google credentials, refreshing if needed.

    Returns None if no cached credentials exist or refresh fails.
    """
    if not TOKEN_FILE.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    except Exception:
        logger.warning("Failed to load cached Google credentials")
        return None

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            return creds
        except Exception:
            logger.warning("Failed to refresh Google credentials — re-login required")
            return None

    return None


def _save_credentials(creds: Credentials):
    _ensure_dir()
    TOKEN_FILE.write_text(
        json.dumps(
            {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or SCOPES),
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _get_client_config() -> dict | str:
    """Get OAuth client config from file or env vars.

    Returns either a path string (to client secret JSON) or a config dict.
    """
    if CLIENT_SECRET_FILE.exists():
        return str(CLIENT_SECRET_FILE)

    # Fall back to env vars
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if client_id and client_secret:
        return {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": ["http://localhost"],
            }
        }

    return ""


def login():
    """Run the browser-based OAuth flow and cache the token."""
    config = _get_client_config()
    if not config:
        print("Error: No Google OAuth client credentials found.")
        print()
        print("Option 1: Download client secret JSON to:")
        print(f"  {CLIENT_SECRET_FILE}")
        print()
        print("Option 2: Set env vars:")
        print("  GOOGLE_OAUTH_CLIENT_ID=...")
        print("  GOOGLE_OAUTH_CLIENT_SECRET=...")
        print()
        print("Create credentials at: https://console.cloud.google.com/apis/credentials")
        print("  > Create OAuth Client ID > Desktop app")
        sys.exit(1)

    # Check if already logged in
    existing = get_credentials()
    if existing:
        print("Already logged in with valid credentials.")
        print(f"Token file: {TOKEN_FILE}")
        print("Run 'logout' first to re-authenticate.")
        return

    if isinstance(config, dict):
        flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(config, scopes=SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_credentials(creds)
    print("Login successful! Token cached at:")
    print(f"  {TOKEN_FILE}")


def status():
    """Show current auth status."""
    if not TOKEN_FILE.exists():
        print("Not logged in.")
        print(f"Run: python {__file__} login")
        return

    creds = get_credentials()
    if creds and creds.valid:
        expiry = creds.expiry
        if expiry:
            now = datetime.now(timezone.utc)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            remaining = expiry - now
            print(f"Logged in. Token expires in {remaining}.")
        else:
            print("Logged in. No expiry info.")
        print(f"Token file: {TOKEN_FILE}")
    else:
        print("Token exists but is invalid or expired.")
        print(f"Run: python {__file__} login")


def logout():
    """Remove cached credentials."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print("Logged out. Token removed.")
    else:
        print("Not logged in.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {__file__} <login|status|logout>")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "login":
        login()
    elif cmd == "status":
        status()
    elif cmd == "logout":
        logout()
    else:
        print(f"Unknown command: {cmd}")
        print(f"Usage: python {__file__} <login|status|logout>")
        sys.exit(1)
