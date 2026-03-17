"""
Token Manager for CommCare Connect OAuth CLI tokens.

Handles secure storage, loading, and validation of OAuth tokens for CLI usage.
Supports multiple named profiles with an active profile concept.

File format (v2):
{
    "_version": 2,
    "_active_profile": "jjackson",
    "profiles": {
        "jjackson": { "access_token": "...", ... },
        "test-user": { "access_token": "...", ... }
    }
}
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


class TokenManager:
    """
    Manages OAuth token storage and retrieval for CLI tools.

    Tokens are stored in a versioned JSON file supporting multiple named profiles.
    When no profile is specified, the active profile is used.
    """

    def __init__(self, token_file: str = None, profile: str | None = None):
        """
        Initialize token manager.

        Args:
            token_file: Path to token file. Defaults to ~/.commcare-connect/token.json
            profile: Named profile to use. None means use the active profile.
        """
        if token_file:
            self.token_file = Path(token_file)
        else:
            config_dir = Path.home() / ".commcare-connect"
            config_dir.mkdir(exist_ok=True)
            self.token_file = config_dir / "token.json"

        self._profile = profile

    def _load_store(self) -> dict:
        """Load the full token store, migrating v1 format if needed."""
        if not self.token_file.exists():
            return {"_version": 2, "_active_profile": None, "profiles": {}}

        try:
            with open(self.token_file) as f:
                data = json.load(f)
        except Exception:
            return {"_version": 2, "_active_profile": None, "profiles": {}}

        if "_version" not in data:
            return self._migrate_v1(data)

        return data

    def _migrate_v1(self, data: dict) -> dict:
        """Migrate a v1 flat token dict to v2 multi-profile format."""
        user_profile = data.get("user_profile", {})
        profile_name = user_profile.get("username", "default")

        store = {
            "_version": 2,
            "_active_profile": profile_name,
            "profiles": {
                profile_name: data,
            },
        }

        self._save_store(store)
        return store

    def _save_store(self, store: dict) -> None:
        """Write the full token store to disk."""
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, "w") as f:
            json.dump(store, f, indent=2)
        os.chmod(self.token_file, 0o600)

    def _resolve_profile(self, store: dict) -> str | None:
        """Determine which profile name to use."""
        if self._profile:
            return self._profile
        return store.get("_active_profile")

    def save_token(self, token_data: dict, user_profile: dict | None = None) -> bool:
        """
        Save OAuth token under the resolved profile.

        Args:
            token_data: Token response from OAuth provider
            user_profile: Optional user profile dict

        Returns:
            True if successful, False otherwise
        """
        try:
            if "expires_in" in token_data:
                expires_at = (datetime.now() + timedelta(seconds=token_data["expires_in"])).isoformat()
                token_data["expires_at"] = expires_at

            token_data["saved_at"] = datetime.now().isoformat()

            if user_profile:
                token_data["user_profile"] = user_profile

            store = self._load_store()

            # Determine profile name
            profile_name = self._profile
            if not profile_name:
                # Auto-detect from user_profile or token_data
                up = user_profile or token_data.get("user_profile", {})
                profile_name = up.get("username", "default")

            store["profiles"][profile_name] = token_data
            store["_active_profile"] = profile_name

            self._save_store(store)
            return True
        except Exception as e:
            print(f"Failed to save token: {e}")
            return False

    def load_token(self) -> dict | None:
        """
        Load OAuth token for the resolved profile.

        Returns:
            Token data dict or None if not found
        """
        try:
            store = self._load_store()
            profile_name = self._resolve_profile(store)
            if not profile_name:
                return None
            return store["profiles"].get(profile_name)
        except Exception:
            return None

    def get_valid_token(self) -> str | None:
        """
        Get a valid access token, checking expiration.

        Returns:
            Access token string if valid, None if expired or not found
        """
        token_data = self.load_token()

        if not token_data:
            return None

        if "expires_at" in token_data:
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            if datetime.now() >= (expires_at - timedelta(minutes=5)):
                return None

        return token_data.get("access_token")

    def is_expired(self) -> bool:
        """
        Check if the stored token is expired.

        Returns:
            True if expired or no token, False if still valid
        """
        return self.get_valid_token() is None

    def clear_token(self) -> bool:
        """
        Remove the resolved profile from the store.

        Returns:
            True if successful, False otherwise
        """
        try:
            store = self._load_store()
            profile_name = self._resolve_profile(store)
            if not profile_name:
                return True

            store["profiles"].pop(profile_name, None)

            # If we removed the active profile, pick another or set None
            if store["_active_profile"] == profile_name:
                remaining = list(store["profiles"].keys())
                store["_active_profile"] = remaining[0] if remaining else None

            self._save_store(store)
            return True
        except Exception:
            return False

    def get_token_info(self) -> dict | None:
        """
        Get information about the stored token without returning the token itself.

        Returns:
            Dict with token metadata or None if no token
        """
        token_data = self.load_token()

        if not token_data:
            return None

        info = {
            "saved_at": token_data.get("saved_at"),
            "expires_at": token_data.get("expires_at"),
            "token_type": token_data.get("token_type", "Bearer"),
            "has_refresh_token": "refresh_token" in token_data,
            "is_valid": self.get_valid_token() is not None,
        }

        if "expires_at" in token_data:
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            now = datetime.now()
            if now < expires_at:
                time_remaining = expires_at - now
                info["expires_in_seconds"] = int(time_remaining.total_seconds())
            else:
                info["expires_in_seconds"] = 0

        return info

    def list_profiles(self) -> list[dict]:
        """
        List all stored profiles with metadata.

        Returns:
            List of dicts with profile name, active status, and token info
        """
        store = self._load_store()
        active = store.get("_active_profile")
        result = []

        for name, token_data in store.get("profiles", {}).items():
            up = token_data.get("user_profile", {})
            result.append(
                {
                    "name": name,
                    "active": name == active,
                    "username": up.get("username", ""),
                    "email": up.get("email", ""),
                    "saved_at": token_data.get("saved_at"),
                    "is_expired": self._is_token_expired(token_data),
                }
            )

        return result

    def set_active_profile(self, profile_name: str) -> bool:
        """
        Switch the active profile.

        Args:
            profile_name: Name of the profile to activate

        Returns:
            True if successful, False if profile doesn't exist
        """
        store = self._load_store()
        if profile_name not in store["profiles"]:
            return False
        store["_active_profile"] = profile_name
        self._save_store(store)
        return True

    def get_active_profile_name(self) -> str | None:
        """Return the name of the currently active profile."""
        store = self._load_store()
        return store.get("_active_profile")

    @staticmethod
    def _is_token_expired(token_data: dict) -> bool:
        """Check if a token data dict is expired."""
        if "expires_at" not in token_data:
            return False
        expires_at = datetime.fromisoformat(token_data["expires_at"])
        return datetime.now() >= (expires_at - timedelta(minutes=5))


def get_or_refresh_token(
    client_id: str,
    production_url: str,
    token_file: str = None,
    verbose: bool = True,
) -> str | None:
    """
    Get a valid token, fetching a new one if needed.

    This is a convenience function that:
    1. Checks for existing valid token
    2. Returns it if valid
    3. Fetches new token via OAuth flow if expired/missing

    Args:
        client_id: OAuth client ID
        production_url: Production URL
        token_file: Optional custom token file path
        verbose: Print status messages

    Returns:
        Valid access token or None if failed
    """
    from commcare_connect.labs.integrations.connect.cli.client import get_oauth_token

    manager = TokenManager(token_file)

    # Try to get existing valid token
    token = manager.get_valid_token()

    if token:
        if verbose:
            info = manager.get_token_info()
            if info and "expires_in_seconds" in info:
                minutes = info["expires_in_seconds"] // 60
                print(f"Using cached token (expires in {minutes} minutes)")
        return token

    # Need new token
    if verbose:
        print("No valid token found. Starting OAuth flow...")

    token_data = get_oauth_token(
        client_id=client_id,
        production_url=production_url,
        verbose=verbose,
    )

    if not token_data:
        return None

    # Save for future use
    manager.save_token(token_data)

    if verbose:
        print(f"Token saved to: {manager.token_file}")

    return token_data.get("access_token")
