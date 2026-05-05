"""Compose engine output with the GDrive uploader and SyntheticOpportunity registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache


_FILES = (
    ("opportunity", "opportunity.json"),
    ("user_visits", "user_visits.json"),
    ("user_data", "user_data.json"),
    ("completed_works", "completed_works.json"),
    ("completed_module", "completed_module.json"),
)


class _Drive(Protocol):
    def create_folder(self, name: str, parent_id: str) -> str: ...
    def upload_file(self, folder_id: str, filename: str, content: bytes) -> str: ...


@dataclass(frozen=True)
class UploadResult:
    folder_id: str
    record_counts: dict[str, int]


def upload_and_register(
    *,
    drive: _Drive,
    opportunity_id: int,
    opportunity_name: str,
    fixtures: dict[str, Any],
) -> UploadResult:
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

    SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults={
            "label": opportunity_name,
            "gdrive_folder_id": folder_id,
            "enabled": True,
        },
    )
    invalidate_cache()

    return UploadResult(folder_id=folder_id, record_counts=counts)
