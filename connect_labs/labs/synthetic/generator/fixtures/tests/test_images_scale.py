"""Scale-photo corpus: the reading has to be GROUND TRUTH, not decoration.

MUAC and scale photos are judged by different kinds of agent, and the difference
decides what a corpus must carry.

`muac_overzoom` judges the PICTURE alone — its bad pool is categorised by image
defect (framing, tape_usage, equipment) and nothing about it depends on what the
tape read. `scale_validation` and `scale_dial_read` do something else entirely:
they post the photo together with the reading entered on the form and return
match / no-match. So a scale photo whose true value is unknown makes every verdict
an accident of which image the round-robin happened to land on — a demo that looks
like a working AI review and means nothing.

These tests pin the property that prevents that: a GOOD-pool visit's entered value
equals its photo's value, and a BAD-pool visit's does not.
"""

import random

from connect_labs.labs.synthetic.generator.fixtures.images import assign_visit_images
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig

WEIGHT_PATH = "form.anthropometric.child_weight_visit"
PHOTO_PATH = "form.anthropometric.upload_weight_image"

READINGS = {
    "synth-scale-good-001": 1535.0,
    "synth-scale-good-002": 2010.0,
    "synth-scale-bad-001": 1720.0,
}


def _scale_config(**over):
    kw = dict(
        question_path=PHOTO_PATH,
        corpus="scale",
        measurement_field_match="weight",
        probability=1.0,
        good_image_count=2,
        bad_image_count=1,
        readings=READINGS,
        reading_path=WEIGHT_PATH,
    )
    kw.update(over)
    return ImageConfig(**kw)


def _visits(n, username="asha"):
    return [
        {
            "id": f"v{i}",
            "username": username,
            "form_json": {"form": {"anthropometric": {"child_weight_visit": 1400 + i}}},
        }
        for i in range(n)
    ]


def test_a_good_pool_visit_is_written_the_value_its_photo_shows():
    visits = _visits(6)
    assign_visit_images(visits, _scale_config(default_bad_rate=0.0), random.Random(1))
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert blob.startswith("synth-scale-good-")
        entered = v["form_json"]["form"]["anthropometric"]["child_weight_visit"]
        assert entered == READINGS[blob], "entered weight must equal the photo's reading"


def test_a_bad_pool_visit_is_written_a_value_its_photo_contradicts():
    """The population an agreement reviewer exists to catch."""
    visits = _visits(6)
    stats = assign_visit_images(visits, _scale_config(default_bad_rate=1.0), random.Random(1))
    assert stats["reading_mismatches"] == 6
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert blob.startswith("synth-scale-bad-")
        entered = v["form_json"]["form"]["anthropometric"]["child_weight_visit"]
        assert entered != READINGS[blob]
        assert entered == round(READINGS[blob] * 1.35, 3)


def test_blob_ids_and_filenames_carry_the_corpus_not_a_hardcoded_muac():
    visits = _visits(3)
    assign_visit_images(visits, _scale_config(default_bad_rate=0.0), random.Random(2))
    assert all(v["images"][0]["blob_id"].startswith("synth-scale-") for v in visits)
    assert all(v["images"][0]["name"].startswith("scale_photo_") for v in visits)
    assert all(
        v["form_json"]["form"]["anthropometric"]["upload_weight_image"].startswith("scale_photo_") for v in visits
    )


def test_eligibility_follows_the_corpus_field_not_the_word_muac():
    """A KMC weight visit has no 'muac' anywhere in it. Under the old hardcoded matcher
    every one of these was skipped and the opp generated with no photos at all."""
    visits = _visits(4)
    stats = assign_visit_images(visits, _scale_config(default_bad_rate=0.0), random.Random(3))
    assert stats["eligible_visits"] == 4 and stats["images_assigned"] == 4


def test_a_visit_with_no_measurement_gets_no_photo():
    """Nothing to photograph — attaching one would invent data the visit does not have."""
    visits = [{"id": "v0", "username": "asha", "form_json": {"form": {"notes": "none"}}}]
    stats = assign_visit_images(visits, _scale_config(), random.Random(4))
    assert stats["eligible_visits"] == 0 and stats["images_assigned"] == 0
    assert "images" not in visits[0]


def test_without_readings_nothing_is_overwritten():
    """A corpus with no ground truth must leave the cohort's own value alone rather than
    silently substituting one — that is the MUAC case and it must keep working."""
    visits = _visits(3)
    before = [v["form_json"]["form"]["anthropometric"]["child_weight_visit"] for v in visits]
    stats = assign_visit_images(
        visits, _scale_config(readings={}, reading_path=None, default_bad_rate=1.0), random.Random(5)
    )
    after = [v["form_json"]["form"]["anthropometric"]["child_weight_visit"] for v in visits]
    assert before == after
    assert stats["reading_mismatches"] == 0


def test_readings_without_a_path_is_refused_at_manifest_load():
    """Ground truth with nowhere to write it is a silent no-op — the exact failure this
    whole file exists to prevent — so it fails loudly at config time instead."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="reading_path"):
        ImageConfig(question_path=PHOTO_PATH, corpus="scale", readings=READINGS)


# ---------------------------------------------------------------------------
# Weight-matched selection (#1558)
#
# Everything above pins "the entered value must agree with the photo". These pin
# the other half, which round-robin quietly broke: the value they agree ON must
# still be the one the COHORT generated. A KMC cohort sets mirror: true precisely
# to reproduce a per-child weight-vs-age growth curve, and a photo drawn blind
# and written over that weight turns the curve back into noise while every count
# reports success.
# ---------------------------------------------------------------------------

# A pool spread across the birthweight-to-discharge range, so a near match exists
# for each point on a growth curve rather than only at one weight.
CURVE_READINGS = {
    "synth-scale-good-001": 1400.0,
    "synth-scale-good-002": 1600.0,
    "synth-scale-good-003": 1800.0,
    "synth-scale-good-004": 2000.0,
    "synth-scale-bad-001": 1750.0,
}


def _curve_config(**over):
    kw = dict(
        good_image_count=4,
        bad_image_count=1,
        readings=CURVE_READINGS,
        reading_match_tolerance=120.0,
    )
    kw.update(over)
    return _scale_config(**kw)


def _curve_visits(weights, username="asha"):
    return [
        {
            "id": f"v{i}",
            "username": username,
            "form_json": {"form": {"anthropometric": {"child_weight_visit": w}}},
        }
        for i, w in enumerate(weights)
    ]


def _entered(visit):
    return visit["form_json"]["form"]["anthropometric"]["child_weight_visit"]


def test_weight_matching_preserves_the_growth_curve():
    """The regression gate. A rising series must still rise after photos are attached.

    Under round-robin the same series comes back ordered by the pool's rotation
    instead, which is the defect: monotonically increasing weights are what the
    KMC longitudinal view plots.
    """
    weights = [1410.0, 1590.0, 1810.0, 1990.0]
    visits = _curve_visits(weights)
    stats = assign_visit_images(visits, _curve_config(), random.Random(11))

    assert stats["images_assigned"] == 4
    assert stats["unmatched_visits"] == 0
    after = [_entered(v) for v in visits]
    assert after == sorted(after), f"growth curve was scrambled: {after}"
    # Each visit kept its own weight to within the tolerance it was matched on.
    for before, now in zip(weights, after):
        assert abs(now - before) <= 120.0


def test_weight_matching_still_agrees_with_the_photo():
    """Curve preservation must not cost the AI-review guarantee — the entered value
    still equals the value visible in the photo that was attached."""
    visits = _curve_visits([1410.0, 1590.0, 1810.0])
    assign_visit_images(visits, _curve_config(), random.Random(12))
    for v in visits:
        assert _entered(v) == CURVE_READINGS[v["images"][0]["blob_id"]]


def test_a_failing_visit_is_written_a_value_its_photo_does_not_show():
    """The property that matters: a visit marked to fail disagrees with its photo, so an
    agreement reviewer has something real to catch.

    It does NOT have to come from the bad pool. Under weight matching the default is a
    readable, weight-matched photo of the RIGHT infant with the entered value pushed off
    it — the transcription/fraud case — because matching inside the bad pool cannot work:
    those frames carry no reading, so every planted mistake was silently skipped."""
    visits = _curve_visits([1740.0, 1760.0], username="fatima")
    stats = assign_visit_images(visits, _curve_config(flw_bad_rates={"fatima": 1.0}), random.Random(13))
    assert stats["reading_mismatches"] == 2
    assert stats["unmatched_visits"] == 0
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert _entered(v) != CURVE_READINGS[blob]


def test_a_weight_the_corpus_cannot_show_gets_no_photo():
    """A thin corpus must surface as a coverage gap, never as rewritten data.

    3200g is far outside a pool topping out at 2000g. The old behaviour attached
    a photo anyway and moved the visit to whatever that photo read.
    """
    visits = _curve_visits([3200.0])
    stats = assign_visit_images(visits, _curve_config(), random.Random(14))

    assert stats["unmatched_visits"] == 1
    assert stats["images_assigned"] == 0
    assert "images" not in visits[0]
    assert _entered(visits[0]) == 3200.0


def test_round_robin_is_unchanged_when_tolerance_is_unset():
    """MUAC and every existing manifest must not move: no tolerance, old behaviour."""
    visits = _visits(3)
    stats = assign_visit_images(visits, _scale_config(), random.Random(15))
    assert stats["images_assigned"] == 3
    assert stats["unmatched_visits"] == 0
    for v in visits:
        assert _entered(v) == READINGS[v["images"][0]["blob_id"]]


def test_tolerance_without_readings_is_refused_at_manifest_load():
    """A tolerance with nothing to match against would silently fall back to
    round-robin while the manifest claimed curve preservation."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="requires readings"):
        _scale_config(readings={}, reading_path=None, reading_match_tolerance=50.0)


# ---------------------------------------------------------------------------
# The two ways a visit is made to fail on purpose.
#
# Routing every failure into the BAD pool cannot work under weight matching:
# bad-pool images carry no reading, so the nearest-match finds nothing and the
# visit is skipped. An FLW with bad_rate 1.0 then produces a spotless record and
# the planted mistakes vanish without a word.
# ---------------------------------------------------------------------------


def _fail_config(**over):
    kw = dict(
        good_image_count=4,
        bad_image_count=1,
        readings=CURVE_READINGS,
        reading_match_tolerance=120.0,
        flw_bad_rates={"fatima": 1.0},
    )
    kw.update(over)
    return _scale_config(**kw)


def test_a_failing_visit_defaults_to_a_good_photo_with_a_wrong_number():
    """The payment-integrity case: right infant, readable photo, value that disagrees."""
    visits = _curve_visits([1410.0, 1590.0, 1810.0], username="fatima")
    stats = assign_visit_images(visits, _fail_config(bad_photo_share=0.0), random.Random(3))

    assert stats["images_assigned"] == 3
    assert stats["unmatched_visits"] == 0, "planted mistakes must not vanish as coverage gaps"
    assert stats["reading_mismatches"] == 3
    assert stats["bad_photo_visits"] == 0
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert blob.startswith("synth-scale-good-"), "a wrong NUMBER needs a readable photo"
        assert _entered(v) != CURVE_READINGS[blob]


def test_bad_photo_share_routes_some_failures_to_an_unusable_frame_instead():
    """The other defect: fails on the IMAGE, so the cohort's own weight is left alone."""
    weights = [1410.0] * 40
    visits = _curve_visits(weights, username="fatima")
    stats = assign_visit_images(visits, _fail_config(bad_photo_share=1.0), random.Random(4))

    assert stats["bad_photo_visits"] == len(visits)
    assert stats["unmatched_visits"] == 0
    for v, w in zip(visits, weights):
        assert v["images"][0]["blob_id"].startswith("synth-scale-bad-")
        assert _entered(v) == w, "a bad-photo visit must not have its weight rewritten"


def test_a_clean_worker_is_untouched_while_a_failing_one_is_planted():
    """Both in one run — the mix a reviewer demo actually needs."""
    clean = _curve_visits([1410.0, 1590.0, 1810.0], username="asha")
    dirty = _curve_visits([1410.0, 1590.0, 1810.0], username="fatima")
    stats = assign_visit_images(clean + dirty, _fail_config(bad_photo_share=0.0), random.Random(5))

    assert stats["reading_mismatches"] == 3
    for v in clean:
        assert _entered(v) == CURVE_READINGS[v["images"][0]["blob_id"]]
    for v in dirty:
        assert _entered(v) != CURVE_READINGS[v["images"][0]["blob_id"]]


def test_probability_zero_with_showcase_is_not_warned_about(caplog):
    """`probability: 0.0` + showcase is the supported "only the demo cases" config.

    Warning on it would train the reader to ignore a line that is usually real —
    the zero-assignment warning exists to catch a manifest that silently produced
    nothing (#1467), and this manifest produced exactly what it asked for.
    """
    import logging

    visits = _curve_visits([1410.0, 1590.0])
    cfg = _curve_config(
        probability=0.0,
        showcase=[{"name": "Demo", "trajectory": "normal_02", "flw": "flw_001"}],
    )
    with caplog.at_level(logging.WARNING):
        assign_visit_images(visits, cfg, random.Random(21))
    assert not [r for r in caplog.records if "NO images were assigned" in r.message]


def test_probability_zero_WITHOUT_showcase_still_warns(caplog):
    """The original alarm has to survive: images configured, nothing produced,
    and no showcase to explain it."""
    import logging

    visits = _curve_visits([1410.0, 1590.0])
    with caplog.at_level(logging.WARNING):
        assign_visit_images(visits, _curve_config(probability=0.0), random.Random(22))
    assert [r for r in caplog.records if "NO images were assigned" in r.message]
