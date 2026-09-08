"""Assign synthetic image entries to visits that carry a measurement to photograph."""

from __future__ import annotations

import logging
import random
import uuid
from typing import Any

from . import corpus_manifest as cm
from .fields import _get_nested, _set_nested
from .manifest import ImageConfig

logger = logging.getLogger(__name__)


def failing_value(true_reading: float, blob_id: str, bands: dict, factor: float) -> float:
    """A value that DISAGREES with the photo -- and stays disagreeing.

    A dial photo is reviewed against a RANGE, not a point, so a multiplier off the
    recorded midpoint can land back INSIDE the accepted band and be passed. The
    planted error then reads as a case the reviewer cleared, which is worse than
    having planted nothing. Push clear of the band's upper edge (#1563).

    Shared with `showcase.py`: both paths plant the same kind of mistake against the
    same reviewer, and having two copies is how one of them stayed wrong.
    """
    wrong = true_reading * factor
    band = bands.get(blob_id)
    if band:
        lo, hi = band
        if lo <= wrong <= hi:
            wrong = hi + max(hi - lo, 1.0) * 0.5
    return round(wrong, 3)


# Exact paths this module used to require. Kept only as documentation of the
# three shapes that were hardcoded here: eligibility is now decided by
# _has_muac, which recognises MUAC wherever a manifest actually puts it.
_LEGACY_MUAC_PATHS = [
    ("form", "case", "update", "soliciter_muac_cm"),
    ("form", "subcase_0", "case", "update", "soliciter_muac"),
    ("form", "muac_group", "muac_display_group_1", "soliciter_muac_cm"),
]


def _has_measurement(form_json: dict, field_match: str) -> bool:
    """Does this visit carry the measurement this corpus photographs?

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
    return _find_measurement_leaf(form_json, field_match)


def _find_measurement_leaf(node: Any, field_match: str) -> bool:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                if _find_measurement_leaf(value, field_match):
                    return True
            elif field_match in str(key).lower() and value is not None and value != "":
                return True
        return False
    if isinstance(node, list):
        return any(_find_measurement_leaf(item, field_match) for item in node)
    return False


def _legacy_blob_id(image_index: int, stock_count: int, corpus: str) -> str:
    return f"synth-{corpus}-{(image_index % stock_count) + 1:03d}"


def _pool_blob_id(image_index: int, pool_count: int, pool_tag: str, corpus: str) -> str:
    return f"synth-{corpus}-{pool_tag}-{(image_index % pool_count) + 1:03d}"


def _nearest_blob(
    target: float,
    pool_count: int,
    pool_tag: str,
    corpus: str,
    readings: dict[str, float],
    tolerance: float,
) -> tuple[str | None, float | None]:
    """Pick the pool image whose true reading is closest to ``target``.

    Returns ``(blob_id, true_reading)``, or ``(None, None)`` when the pool has
    nothing within ``tolerance`` -- the caller must then leave the visit without
    a photo rather than attach one and overwrite the cohort's value, which is the
    whole defect this function exists to remove (#1558).

    Ties break on the LOWER blob index so the choice is deterministic for a given
    corpus; a synthetic run has to be reproducible from its seed.
    """
    best_id: str | None = None
    best_reading: float | None = None
    best_delta: float | None = None
    for i in range(pool_count):
        blob_id = _pool_blob_id(i, pool_count, pool_tag, corpus)
        reading = readings.get(blob_id)
        if reading is None:
            continue
        delta = abs(reading - target)
        if best_delta is None or delta < best_delta:
            best_id, best_reading, best_delta = blob_id, reading, delta
    if best_delta is None or best_delta > tolerance:
        return None, None
    return best_id, best_reading


def assign_visit_images(
    visits: list[dict[str, Any]],
    config: ImageConfig,
    rng: random.Random,
) -> dict[str, int]:
    """Mutate visits in-place: add synthetic image entries to MUAC visits.

    Returns ``{"eligible_visits", "images_assigned", "reading_mismatches",
    "unmatched_visits", "no_reading_value_visits", "bad_photo_visits"}`` so the
    caller can surface the counts instead of a generation that quietly produced
    none. ``reading_mismatches`` is how many
    visits were given an entered value that disagrees with their photo — the
    population an agreement reviewer should catch. ``unmatched_visits`` is how
    many were left photo-less because the corpus had nothing near their weight,
    and ``no_reading_value_visits`` how many had no numeric value at
    ``reading_path`` at all (both weight-matched mode only). Those two are
    counted apart on purpose: the first is a thin corpus, the second is a
    misconfigured path, and only one of them is fixed by adding photos.

    Two modes:

    - **Legacy**: ``good_image_count is None``. Round-robin from the
      uncategorized pool (``muac_NNN.jpg``). Preserves prior behavior for
      any opp manifest that hasn't opted into the good/bad split.
    - **Two-pool**: ``good_image_count`` set. Each MUAC visit lands in either
      the good pool or the bad pool based on the FLW's bad-rate
      (``flw_bad_rates[username]`` falls back to ``default_bad_rate``).
      Pools round-robin independently so a small bad set still spreads.
    - **Weight-matched two-pool**: as above, plus ``reading_match_tolerance``.
      The bad-rate coin-flip still picks the POOL, but within it the photo is
      chosen to fit the weight the cohort already generated rather than drawn
      round-robin and then written over it. This is what keeps a longitudinal
      cohort's growth curve intact while still giving the agreement reviewers a
      photo whose value genuinely matches the entered one (#1558).
    """
    use_pools = config.good_image_count is not None
    # Weight-matched selection is opt-in and pool-only: the legacy uncategorized
    # pool has no ground truth to match against. Guarded on reading_path too so a
    # partial config degrades to the old behaviour rather than crashing (#1558).
    match_weight = bool(
        use_pools and config.reading_match_tolerance is not None and config.readings and config.reading_path
    )
    legacy_count = config.stock_image_count
    eligible = assigned = mismatched = unmatched = bad_photos = no_reading_value = 0

    # Per-pool round-robin counters (used in two-pool mode).
    good_index = 0
    bad_index = 0
    # Per-FLW round-robin counter (used in legacy mode).
    legacy_index = 0

    corpus = config.corpus
    field_match = config.field_match
    # Only dial images carry one; a point-read digital photo has no band and
    # falls through to the plain multiplier. Readings can also be supplied by hand
    # for a corpus that has no manifest of its own, so a missing one is not an
    # error here -- it just means there are no bands to respect.
    try:
        bands = cm.bands_for(corpus) if config.readings else {}
    except cm.CorpusManifestError:
        bands = {}

    for visit in visits:
        fj = visit.get("form_json") or {}
        if not _has_measurement(fj, field_match):
            continue
        eligible += 1
        if rng.random() > config.probability:
            continue

        fails_on_photo = False
        if use_pools:
            username = visit.get("username") or ""
            bad_rate = config.flw_bad_rates.get(username, config.default_bad_rate)
            # Coin-flip per visit. When bad_rate is 0 the FLW always gets a
            # good photo; when 1.0 they always get a bad one. Anything in
            # between lets you tune how much evidence the audit "finds" on
            # that worker without the rest of the cohort looking compromised.
            pick_bad = rng.random() < bad_rate
            use_bad_pool = bool(pick_bad and config.bad_image_count)
            pool_tag = "bad" if use_bad_pool else "good"
            pool_count = config.bad_image_count if use_bad_pool else config.good_image_count
            from_bad_pool = use_bad_pool

            if match_weight:
                # Choose the photo to fit the weight the cohort already generated,
                # instead of drawing one blind and then writing over that weight.
                #
                # A FAILING visit fails in one of two ways, and they need different
                # photos. Matching inside the BAD pool cannot work -- bad-pool images
                # carry no reading by design -- so routing every failure there made
                # deliberate mistakes silently vanish: the nearest-match found nothing,
                # the visit was skipped, and an FLW with bad_rate 1.0 produced a clean
                # record. The two modes:
                #
                #   wrong NUMBER  (default) -- a good, weight-matched photo of the right
                #       infant, with the entered value pushed off it. This is the
                #       transcription/fraud case the agreement reviewer exists to catch,
                #       and the one that maps to payment integrity.
                #   bad PHOTO (bad_photo_share) -- an unreadable frame from the bad pool.
                #       Nothing to match on, so the cohort's own weight is left intact;
                #       the visit fails on the image, not the arithmetic.
                target = _get_nested(fj, config.reading_path)
                if not isinstance(target, (int, float)) or isinstance(target, bool):
                    # No usable cohort value to match against. Skipping is the
                    # honest outcome: attaching a photo here would reintroduce the
                    # overwrite this mode exists to prevent.
                    #
                    # Counted SEPARATELY from `unmatched`, which means "the corpus
                    # has no photo near this weight". This one means "there is no
                    # weight here at all" -- a config/form-shape problem, not a
                    # coverage one. Folding them together sent a reader off to
                    # widen a corpus that was already fine (#1602).
                    no_reading_value += 1
                    continue
                if use_bad_pool and rng.random() < config.bad_photo_share:
                    # Fail on the IMAGE. No reading to match, so take the next bad
                    # frame round-robin and leave the cohort's weight alone.
                    blob_id = _pool_blob_id(bad_index, config.bad_image_count, "bad", corpus)
                    bad_index += 1
                    bad_photos += 1
                    from_bad_pool = True
                    fails_on_photo = True
                    pool_tag, pool_count = "bad", config.bad_image_count
                    matched = True
                else:
                    # Fail (or pass) on the NUMBER: always a good, weight-matched photo.
                    # from_bad_pool still carries "this visit should fail", which the
                    # write-back below turns into a value that disagrees with the photo.
                    pool_tag, pool_count = "good", config.good_image_count
                    blob_id, _matched_reading = _nearest_blob(
                        float(target),
                        pool_count,
                        pool_tag,
                        corpus,
                        config.readings,
                        config.reading_match_tolerance,
                    )
                    matched = blob_id is not None
                if not matched:
                    # The corpus has no photo showing anything near this weight.
                    # Leave the visit photo-less and count it -- a thin corpus must
                    # surface as a coverage gap, not as silently rewritten data.
                    unmatched += 1
                    continue
            elif use_bad_pool:
                blob_id = _pool_blob_id(bad_index, pool_count, "bad", corpus)
                bad_index += 1
            else:
                blob_id = _pool_blob_id(good_index, pool_count, "good", corpus)
                good_index += 1
        else:
            blob_id = _legacy_blob_id(legacy_index, legacy_count, corpus)
            legacy_index += 1
            from_bad_pool = False

        # A visit that fails on the IMAGE keeps the weight the cohort generated:
        # there is nothing to disagree with, and rewriting it would quietly move a
        # child off its own growth curve to no purpose. Tracked explicitly rather
        # than inferred from "the bad pool has no reading", which is a property of
        # the corpus and not something this code should rely on.
        filename = f"{corpus}_photo_{uuid.UUID(int=rng.getrandbits(128)).hex[:12]}.jpg"
        visit["images"] = [{"blob_id": blob_id, "name": filename}]
        _set_nested(visit["form_json"], config.question_path, filename)

        # Make the entered value AGREE with the photo (good pool) or deliberately
        # DISAGREE with it (bad pool). Without this the reviewer compares a number
        # from an unrelated image against whatever the cohort happened to draw, and
        # every match/no-match verdict is an accident of the round-robin.
        true_reading = None if fails_on_photo else config.readings.get(blob_id)
        if true_reading is not None and config.reading_path:
            entered = (
                failing_value(true_reading, blob_id, bands, config.bad_reading_factor)
                if from_bad_pool
                else true_reading
            )
            _set_nested(visit["form_json"], config.reading_path, round(entered, 3))
            if from_bad_pool:
                mismatched += 1
        assigned += 1

    # A manifest that declares image_config and gets NOTHING is always a
    # mistake -- almost certainly its visits carry no MUAC field for images to
    # hang off. Saying so here is the whole difference between "my audit has no
    # photos, why?" a week later and a one-line answer at generation time.
    if not assigned and not eligible:
        logger.warning(
            "[SyntheticImages] image_config is set but NO images were assigned: none of "
            "%d visit(s) recorded a '%s' measurement to attach one to. Check that the "
            "manifest's cohort generates a matching field.",
            len(visits),
            field_match,
        )
    elif not assigned and not config.showcase:
        # Different cause, so a different message: the visits DO have MUAC
        # readings and every one was still skipped, which points at the config
        # (probability, or an empty pool) rather than the cohort's fields.
        #
        # Suppressed when the manifest declares showcase cases, because
        # `probability: 0.0` + showcase is the SUPPORTED way to photograph only
        # the named demo cases and leave the cohort alone. Warning on a correct
        # config is worse than not warning at all: it teaches the reader to skip
        # a line that is usually real. The `not eligible` branch above still
        # fires either way, so the genuine silent no-op is still caught.
        logger.warning(
            "[SyntheticImages] image_config is set and %d of %d visit(s) had a '%s' "
            "measurement, but NO images were assigned. Check probability=%s and the "
            "pool sizes (stock=%s, good=%s, bad=%s).",
            eligible,
            len(visits),
            field_match,
            config.probability,
            config.stock_image_count,
            config.good_image_count,
            config.bad_image_count,
        )
    else:
        logger.info(
            "[SyntheticImages] assigned %d '%s' image(s) across %d eligible visit(s); "
            "%d carry a deliberately mismatched reading",
            assigned,
            corpus,
            eligible,
            mismatched,
        )
    if match_weight and no_reading_value:
        logger.warning(
            "[SyntheticImages] %d of %d eligible visit(s) had NO numeric value at reading_path "
            "%r, so no photo could be matched to them. This is a CONFIG/FORM-SHAPE problem, not "
            "a corpus one: check that reading_path names the field this cohort actually writes "
            "its %s into. Widening the corpus will not help.",
            no_reading_value,
            eligible,
            config.reading_path,
            field_match,
        )
    if match_weight and unmatched:
        # A coverage gap is the one failure mode weight-matching introduces, so it
        # gets its own line rather than hiding inside a lower assigned count. The
        # fix is more photos across the range, not a wider tolerance.
        logger.warning(
            "[SyntheticImages] weight-matched selection left %d of %d eligible visit(s) without a "
            "photo: no '%s' image within +/-%s of the visit's own reading. Widen the corpus's weight "
            "coverage rather than the tolerance.",
            unmatched,
            eligible,
            corpus,
            config.reading_match_tolerance,
        )
    return {
        "eligible_visits": eligible,
        "images_assigned": assigned,
        "reading_mismatches": mismatched,
        "unmatched_visits": unmatched,
        "no_reading_value_visits": no_reading_value,
        "bad_photo_visits": bad_photos,
    }
