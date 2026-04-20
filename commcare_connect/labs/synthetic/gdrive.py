"""Minimal Google Drive client for synthetic fixture access.

Uses a service account (env var `LABS_SYNTHETIC_GDRIVE_SA_KEY` — either a
filesystem path to a JSON key file, or the JSON blob itself). The service
account must be shared on the dedicated labs-synthetic parent folder.

Only two operations are needed:
    - list immediate children of a folder (filename -> file ID)
    - download a file's bytes by ID
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


class DriveAuthError(RuntimeError):
    """Raised when service account credentials cannot be resolved."""


class DriveAPIError(RuntimeError):
    """Raised on Drive HTTP errors."""


def _load_credentials():
    """Return google-auth Credentials, or None if env var is unset/invalid."""
    raw = os.environ.get("LABS_SYNTHETIC_GDRIVE_SA_KEY")
    if not raw:
        return None

    from google.oauth2 import service_account  # local import: heavy + optional

    # Support either a filesystem path or the JSON blob itself.
    if raw.strip().startswith("{"):
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return service_account.Credentials.from_service_account_file(raw, scopes=SCOPES)


class DriveClient:
    def __init__(self, credentials=None, timeout: float = 30.0):
        self.credentials = credentials or _load_credentials()
        if self.credentials is None:
            raise DriveAuthError("LABS_SYNTHETIC_GDRIVE_SA_KEY not set — cannot construct DriveClient.")
        self._timeout = timeout

    def _bearer(self) -> str:
        from google.auth.transport.requests import Request

        self.credentials.refresh(Request())
        return self.credentials.token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._bearer()}"}

    def list_folder(self, folder_id: str) -> dict[str, str]:
        """Return {filename: file_id} for immediate children of `folder_id`."""
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "files(id,name)",
            "pageSize": 1000,
        }
        try:
            resp = httpx.get(
                f"{DRIVE_API}/files",
                headers=self._headers(),
                params=params,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DriveAPIError(f"list_folder({folder_id}) failed: {e}") from e

        files = resp.json().get("files", [])
        return {f["name"]: f["id"] for f in files}

    def download_file(self, file_id: str) -> bytes:
        """Return the raw bytes of a Drive file."""
        try:
            resp = httpx.get(
                f"{DRIVE_API}/files/{file_id}",
                headers=self._headers(),
                params={"alt": "media"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DriveAPIError(f"download_file({file_id}) failed: {e}") from e

        return resp.content
