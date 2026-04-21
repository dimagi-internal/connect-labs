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
import secrets

import httpx

logger = logging.getLogger(__name__)

DRIVE_API = "https://www.googleapis.com/drive/v3"
SCOPES = ["https://www.googleapis.com/auth/drive"]
# All requests include supportsAllDrives so the SA can operate on parents
# inside Shared Drives (the common Dimagi case). Safe on My Drive too.
_SHARED_DRIVES = {"supportsAllDrives": "true"}


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
            "includeItemsFromAllDrives": "true",
            "corpora": "allDrives",
            **_SHARED_DRIVES,
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
                params={"alt": "media", **_SHARED_DRIVES},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DriveAPIError(f"download_file({file_id}) failed: {e}") from e

        return resp.content

    def create_folder(self, name: str, parent_id: str) -> str:
        """Create a folder inside `parent_id`; return the new folder ID."""
        payload = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        try:
            resp = httpx.post(
                f"{DRIVE_API}/files",
                headers={**self._headers(), "Content-Type": "application/json"},
                params=_SHARED_DRIVES,
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DriveAPIError(f"create_folder({name}, parent={parent_id}) failed: {e}") from e
        return resp.json()["id"]

    def upload_file(self, folder_id: str, filename: str, content: bytes) -> str:
        """Upload `content` as `filename` into `folder_id`; return the new file ID."""
        metadata = {"name": filename, "parents": [folder_id]}
        boundary = "----labs-synthetic-" + secrets.token_hex(8)
        body = (
            (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: application/json\r\n\r\n"
            ).encode()
            + content
            + f"\r\n--{boundary}--".encode()
        )

        try:
            resp = httpx.post(
                "https://www.googleapis.com/upload/drive/v3/files",
                headers={**self._headers(), "Content-Type": f"multipart/related; boundary={boundary}"},
                params={"uploadType": "multipart", **_SHARED_DRIVES},
                content=body,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DriveAPIError(f"upload_file({filename}) failed: {e}") from e
        return resp.json()["id"]
