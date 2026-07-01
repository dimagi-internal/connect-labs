"""Compose engine output with the GDrive uploader and SyntheticOpportunity registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.registry import invalidate_cache

_FILES = (
    ("opportunity", "opportunity.json"),
    ("user_visits", "user_visits.json"),
    ("user_data", "user_data.json"),
    ("completed_works", "completed_works.json"),
    ("completed_module", "completed_module.json"),
)


class _Drive(Protocol):
    def create_folder(self, name: str, parent_id: str) -> str:
        ...

    def upload_file(self, folder_id: str, filename: str, content: bytes) -> str:
        ...


@dataclass(frozen=True)
class UploadResult:
    folder_id: str
    folder_url: str
    record_counts: dict[str, int]


def _folder_url(folder_id: str) -> str:
    """Build the human-openable Drive URL for a folder ID.

    The Drive API doesn't return webViewLink unless requested in the create
    response, but the URL pattern is stable and works for both My Drive and
    shared drives, so we just synthesize it.
    """
    return f"https://drive.google.com/drive/folders/{folder_id}"


def upload_fixtures(*, drive: _Drive, opportunity_id: int, fixtures: dict[str, Any]) -> UploadResult:
    parent_id = getattr(settings, "LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID", "")
    if not parent_id:
        raise RuntimeError("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID is not set.")

    folder_name = f"opp-{opportunity_id}-{timezone.now():%Y%m%d-%H%M%S}-generated"
    folder_id = drive.create_folder(folder_name, parent_id=parent_id)

    counts: dict[str, int] = {}
    for key, filename in _FILES:
        payload = fixtures[key]
        drive.upload_file(folder_id, filename, json.dumps(payload).encode())
        counts[key] = len(payload) if isinstance(payload, list) else 1

    app_structure = fixtures.get("app_structure")
    if app_structure:
        drive.upload_file(folder_id, "app_structure.json", json.dumps(app_structure).encode())
        counts["app_structure"] = 1

    return UploadResult(folder_id=folder_id, folder_url=_folder_url(folder_id), record_counts=counts)


def upload_and_register(
    *,
    drive: _Drive,
    opportunity_id: int,
    opportunity_name: str,
    fixtures: dict[str, Any],
) -> UploadResult:
    result = upload_fixtures(drive=drive, opportunity_id=opportunity_id, fixtures=fixtures)
    SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults={
            "label": opportunity_name,
            "gdrive_folder_id": result.folder_id,
            "enabled": True,
        },
    )
    invalidate_cache()
    return result
