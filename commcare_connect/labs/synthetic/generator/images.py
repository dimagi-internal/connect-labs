"""Assign synthetic image entries to visits that have MUAC measurements."""

from __future__ import annotations

import random
import uuid
from typing import Any

from .fields import _set_nested
from .manifest import ImageConfig

_MUAC_PATHS = [
    ("form", "case", "update", "soliciter_muac_cm"),
    ("form", "subcase_0", "case", "update", "soliciter_muac"),
    ("form", "muac_group", "muac_display_group_1", "soliciter_muac_cm"),
]


def _has_muac(form_json: dict) -> bool:
    for path_parts in _MUAC_PATHS:
        node = form_json
        for part in path_parts:
            if not isinstance(node, dict):
                break
            node = node.get(part)
        else:
            if node is not None:
                return True
    return False


def assign_visit_images(
    visits: list[dict[str, Any]],
    config: ImageConfig,
    rng: random.Random,
) -> None:
    """Mutate visits in-place: add synthetic image entries to MUAC visits."""
    stock_count = config.stock_image_count
    image_index = 0

    for visit in visits:
        fj = visit.get("form_json") or {}
        if not _has_muac(fj):
            continue
        if rng.random() > config.probability:
            continue

        image_num = (image_index % stock_count) + 1
        image_index += 1
        blob_id = f"synth-muac-{image_num:03d}"
        filename = f"muac_photo_{uuid.UUID(int=rng.getrandbits(128)).hex[:12]}.jpg"

        visit["images"] = [{"blob_id": blob_id, "name": filename}]
        _set_nested(visit["form_json"], config.question_path, filename)
