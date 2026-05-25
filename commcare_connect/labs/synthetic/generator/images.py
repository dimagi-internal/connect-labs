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


def _legacy_blob_id(image_index: int, stock_count: int) -> str:
    return f"synth-muac-{(image_index % stock_count) + 1:03d}"


def _pool_blob_id(image_index: int, pool_count: int, pool_tag: str) -> str:
    return f"synth-muac-{pool_tag}-{(image_index % pool_count) + 1:03d}"


def assign_visit_images(
    visits: list[dict[str, Any]],
    config: ImageConfig,
    rng: random.Random,
) -> None:
    """Mutate visits in-place: add synthetic image entries to MUAC visits.

    Two modes:

    - **Legacy**: ``good_image_count is None``. Round-robin from the
      uncategorized pool (``muac_NNN.jpg``). Preserves prior behavior for
      any opp manifest that hasn't opted into the good/bad split.
    - **Two-pool**: ``good_image_count`` set. Each MUAC visit lands in either
      the good pool or the bad pool based on the FLW's bad-rate
      (``flw_bad_rates[username]`` falls back to ``default_bad_rate``).
      Pools round-robin independently so a small bad set still spreads.
    """
    use_pools = config.good_image_count is not None
    legacy_count = config.stock_image_count

    # Per-pool round-robin counters (used in two-pool mode).
    good_index = 0
    bad_index = 0
    # Per-FLW round-robin counter (used in legacy mode).
    legacy_index = 0

    for visit in visits:
        fj = visit.get("form_json") or {}
        if not _has_muac(fj):
            continue
        if rng.random() > config.probability:
            continue

        if use_pools:
            username = visit.get("username") or ""
            bad_rate = config.flw_bad_rates.get(username, config.default_bad_rate)
            # Coin-flip per visit. When bad_rate is 0 the FLW always gets a
            # good photo; when 1.0 they always get a bad one. Anything in
            # between lets you tune how much evidence the audit "finds" on
            # that worker without the rest of the cohort looking compromised.
            pick_bad = rng.random() < bad_rate
            if pick_bad and config.bad_image_count:
                blob_id = _pool_blob_id(bad_index, config.bad_image_count, "bad")
                bad_index += 1
            else:
                blob_id = _pool_blob_id(good_index, config.good_image_count, "good")
                good_index += 1
        else:
            blob_id = _legacy_blob_id(legacy_index, legacy_count)
            legacy_index += 1

        filename = f"muac_photo_{uuid.UUID(int=rng.getrandbits(128)).hex[:12]}.jpg"
        visit["images"] = [{"blob_id": blob_id, "name": filename}]
        _set_nested(visit["form_json"], config.question_path, filename)
