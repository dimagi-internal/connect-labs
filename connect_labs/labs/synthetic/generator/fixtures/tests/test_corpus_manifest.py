"""A corpus must describe itself, and the two corpora must answer the same questions.

The scale corpus has 83 ground-truth readings. Pasting those into every opportunity
manifest is how the copy on Drive and the copy in the YAML drift apart, so the
corpus carries them and an opp manifest only names it.
"""

import pytest

from connect_labs.labs.synthetic.generator.fixtures import corpus_manifest as cm


def test_both_corpora_load_and_answer_the_same_questions():
    for corpus in ("muac", "kmc-scale"):
        man = cm.load_corpus(corpus)
        for key in (
            "corpus",
            "description",
            "measurement",
            "reviewers",
            "ground_truth",
            "pools",
            "trajectories",
            "images",
            "folder",
        ):
            assert key in man, f"{corpus} manifest is missing {key!r}"
        assert man["pools"]["good"]["count"] > 0
        assert man["ground_truth"]["kind"] in (cm.GROUND_TRUTH_NONE, cm.GROUND_TRUTH_PER_IMAGE)


def test_an_unknown_corpus_raises_rather_than_generating_nothing():
    """A silent empty manifest produces an opp with no images and no explanation."""
    with pytest.raises(cm.CorpusManifestError, match="no manifest for corpus"):
        cm.load_corpus("nosuchcorpus")


def test_muac_declares_no_ground_truth_and_scale_declares_per_image():
    """The distinction that decides whether weight-matching is even meaningful."""
    assert not cm.requires_ground_truth("muac")
    assert cm.readings_for("muac") == {}
    assert cm.requires_ground_truth("kmc-scale")
    assert len(cm.readings_for("kmc-scale")) >= 80


def test_every_scale_reading_is_keyed_by_the_blob_id_the_image_server_serves():
    for blob_id, reading in cm.readings_for("kmc-scale").items():
        assert blob_id.startswith("synth-kmc-scale-")
        assert 500 < reading < 8000, f"{blob_id} reading {reading} is not an infant weight in g"


def test_bad_pool_carries_no_reading():
    """A bad-pool visit fails on the PHOTO, not on the arithmetic."""
    assert not [b for b in cm.readings_for("kmc-scale") if "-bad-" in b]


def test_dial_images_carry_a_band_and_digital_images_do_not():
    """A dial reviewer accepts a RANGE; a value meant to fail must clear it (#1563)."""
    bands = cm.bands_for("kmc-scale")
    assert bands, "the kmc-scale corpus has dial images and must record their accepted bands"
    for blob_id, (lo, hi) in bands.items():
        assert hi > lo, f"{blob_id} band is not an interval"
        assert hi - lo >= 100, f"{blob_id} band narrower than the dial's 100 g resolution"
    assert not cm.bands_for("muac")


def test_pool_sizes_match_the_images_actually_listed():
    """The counts a manifest advertises are what a generator round-robins over."""
    for corpus in ("muac", "kmc-scale"):
        good, bad = cm.pool_sizes(corpus)
        imgs = cm.load_corpus(corpus)["images"].values()
        assert good == sum(1 for i in imgs if i["pool"] == "good")
        assert bad == sum(1 for i in imgs if i["pool"] == "bad")


def test_scale_trajectories_are_ordered_real_series_and_muac_has_none():
    series = cm.trajectories("kmc-scale")
    assert len(series) == 20
    for subject, visits in series.items():
        seqs = [v["visit_seq"] for v in visits]
        assert seqs == sorted(seqs), f"{subject} is out of visit order"
        assert len(visits) >= 3
        assert all(v["reading_grams"] for v in visits)
    # Cross-sectional corpus: weight-matching against it would be meaningless.
    assert cm.trajectories("muac") == {}
