"""Assign synthetic image entries to visits that have MUAC measurements."""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any

from .fields import _set_nested
from .manifest import ImageConfig

logger = logging.getLogger(__name__)

# Exact paths this module used to require. Kept only as documentation of the
# three shapes that were hardcoded here: eligibility is now decided by
# _has_muac, which recognises MUAC wherever a manifest actually puts it.
_LEGACY_MUAC_PATHS = [
    ("form", "case", "update", "soliciter_muac_cm"),
    ("form", "subcase_0", "case", "update", "soliciter_muac"),
    ("form", "muac_group", "muac_display_group_1", "soliciter_muac_cm"),
]


def _has_muac(form_json: dict) -> bool:
    """Does this visit carry a MUAC measurement anywhere in its form_json?

    Matched on the FIELD NAME (any key containing "muac", case-insensitive)
    rather than on an allowlist of three exact paths. The allowlist silently
    produced zero images for every manifest that names its MUAC field
    anything else -- which is all of the current ones: the PAR and
    nutrition-demo manifests measure at
    ``form.service_delivery.muac_group.soliciter_muac``, so despite each
    declaring a full ``image_config`` (pool sizes, per-FLW bad rates), their
    opportunities were generated with no images at all. Nothing failed; the
    photos just weren't there, which surfaces much later as an image audit
    with nothing to audit.

    The match is on the LEAF field's own name, not on any ancestor: a group
    named ``muac_group`` is structure, and a visit that entered that group
    without recording a reading has no measurement to photograph. Attaching an
    image there would invent data the visit doesn't have.
    """
    return _find_muac_leaf(form_json)


def _find_muac_leaf(node: Any) -> bool:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                if _find_muac_leaf(value):
                    return True
            elif "muac" in str(key).lower() and value is not None and value != "":
                return True
        return False
    if isinstance(node, list):
        return any(_find_muac_leaf(item) for item in node)
    return False


def _legacy_blob_id(image_index: int, stock_count: int) -> str:
    return f"synth-muac-{(image_index % stock_count) + 1:03d}"


def _pool_blob_id(image_index: int, pool_count: int, pool_tag: str) -> str:
    return f"synth-muac-{pool_tag}-{(image_index % pool_count) + 1:03d}"


def assign_visit_images(
    visits: list[dict[str, Any]],
    config: ImageConfig,
    rng: random.Random,
) -> dict[str, int]:
    """Mutate visits in-place: add synthetic image entries to MUAC visits.

    Returns ``{"eligible_visits", "images_assigned"}`` so the caller can
    surface the count instead of a generation that quietly produced none.

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
    eligible = assigned = 0

    # Per-pool round-robin counters (used in two-pool mode).
    good_index = 0
    bad_index = 0
    # Per-FLW round-robin counter (used in legacy mode).
    legacy_index = 0

    for visit in visits:
        fj = visit.get("form_json") or {}
        if not _has_muac(fj):
            continue
        eligible += 1
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
        assigned += 1

    # A manifest that declares image_config and gets NOTHING is always a
    # mistake -- almost certainly its visits carry no MUAC field for images to
    # hang off. Saying so here is the whole difference between "my audit has no
    # photos, why?" a week later and a one-line answer at generation time.
    if not assigned:
        logger.warning(
            "[SyntheticImages] image_config is set but NO images were assigned "
            "to %d visit(s): %d had a MUAC measurement to attach one to. Check "
            "that the manifest's cohort generates a MUAC field.",
            len(visits),
            eligible,
        )
    else:
        logger.info("[SyntheticImages] assigned %d image(s) across %d MUAC visit(s)", assigned, eligible)
    return {"eligible_visits": eligible, "images_assigned": assigned}
