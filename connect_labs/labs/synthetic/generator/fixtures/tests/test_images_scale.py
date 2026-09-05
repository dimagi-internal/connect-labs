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
