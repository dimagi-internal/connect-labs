"""A showcase case has to be openable, longitudinal, and honest about its outcome.

The probabilistic image knobs answer "does the audit find anything". These pin the
other question — "show me the faltering-growth case" — which a spread cannot answer
because it contains no case you can name.
"""

import datetime as dt

import pytest

from connect_labs.labs.synthetic.generator.fixtures import corpus_manifest as cm
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig
from connect_labs.labs.synthetic.generator.fixtures.showcase import ShowcaseError, build_showcase_visits

CORPUS = "kmc-scale"
WEIGHT = "form.anthropometric.child_weight_visit"
PHOTO = "form.anthropometric.upload_weight_image"


def _cfg(**over):
    kw = dict(
        question_path=PHOTO,
        corpus=CORPUS,
        measurement_field_match="weight",
        reading_path=WEIGHT,
        probability=0.0,
        good_image_count=83,
        bad_image_count=10,
    )
    kw.update(over)
    return ImageConfig(**kw)


def _build(cases, **kw):
    return build_showcase_visits(_cfg(showcase=cases), opportunity_id=10015, start_date=dt.date(2026, 3, 2), **kw)


def _weight(v):
    return v["form_json"]["form"]["anthropometric"]["child_weight_visit"]


def test_no_showcase_declared_changes_nothing():
    assert build_showcase_visits(_cfg(), opportunity_id=1, start_date=dt.date(2026, 1, 1)) == []


def test_a_case_replays_a_real_trajectory_with_a_photo_on_every_visit():
    """Longitudinal by construction — the demo needs a series, not one frame."""
    visits = _build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}])
    series = cm.trajectories(CORPUS)["normal_02"]
    assert len(visits) == len(series)
    assert all(len(v["images"]) == 1 for v in visits)
    assert [_weight(v) for v in visits] == [p["reading_grams"] for p in series]
    # and it is a rising series, which is the story being shown
    assert _weight(visits[-1]) > _weight(visits[0])


def test_good_and_insufficient_gain_are_real_source_properties():
    """Not fabricated: the contrast comes from which real infant is replayed."""
    good = _build([{"name": "Steady Gain", "trajectory": "normal_02", "flw": "flw_001"}])
    poor = _build([{"name": "Faltering", "trajectory": "slow_03", "flw": "flw_001"}])
    good_gain = _weight(good[-1]) - _weight(good[0])
    poor_gain = _weight(poor[-1]) - _weight(poor[0])
    assert good_gain > 500, good_gain
    assert poor_gain < 150, poor_gain


def test_a_passing_case_enters_exactly_what_its_photo_shows():
    visits = _build([{"name": "Clean", "trajectory": "normal_05", "flw": "flw_001", "outcome": "pass"}])
    readings = cm.readings_for(CORPUS)
    for v in visits:
        assert _weight(v) == readings[v["images"][0]["blob_id"]]


def test_fail_number_keeps_a_readable_photo_but_enters_the_wrong_value():
    """The payment-integrity case: right infant, readable frame, wrong number."""
    visits = _build([{"name": "Typo", "trajectory": "normal_05", "flw": "flw_007", "outcome": "fail_number"}])
    readings = cm.readings_for(CORPUS)
    for v in visits:
        blob = v["images"][0]["blob_id"]
        assert "-good-" in blob, "a wrong NUMBER still needs a readable photo"
        assert _weight(v) != readings[blob]


def test_fail_photo_uses_an_unusable_frame_and_leaves_the_weight_alone():
    visits = _build([{"name": "Blurred", "trajectory": "slow_10", "flw": "flw_007", "outcome": "fail_photo"}])
    series = cm.trajectories(CORPUS)["slow_10"]
    assert [_weight(v) for v in visits] == [p["reading_grams"] for p in series]
    assert all("-bad-" in v["images"][0]["blob_id"] for v in visits)
    # a multi-visit case must not repeat one frame
    assert len({v["images"][0]["blob_id"] for v in visits}) == len(visits)


def test_a_case_is_findable_and_stable_across_runs():
    """The whole point: hand someone a name, and it is the same case next week."""
    a = _build([{"name": "KMC Demo — Faltering Growth", "trajectory": "slow_03", "flw": "flw_001"}])
    b = _build([{"name": "KMC Demo — Faltering Growth", "trajectory": "slow_03", "flw": "flw_001"}])
    assert {v["entity_name"] for v in a} == {"KMC Demo — Faltering Growth"}
    assert len({v["entity_id"] for v in a}) == 1, "one case, one entity"
    assert [v["entity_id"] for v in a] == [v["entity_id"] for v in b], "ids must be reproducible"


def test_outcomes_group_by_worker_so_an_FLW_reads_clean_or_suspect():
    visits = _build(
        [
            {"name": "Clean A", "trajectory": "normal_02", "flw": "flw_001", "outcome": "pass"},
            {"name": "Clean B", "trajectory": "slow_03", "flw": "flw_001", "outcome": "pass"},
            {"name": "Bad A", "trajectory": "normal_05", "flw": "flw_007", "outcome": "fail_number"},
        ]
    )
    readings = cm.readings_for(CORPUS)
    by_flw = {}
    for v in visits:
        blob = v["images"][0]["blob_id"]
        agrees = _weight(v) == readings.get(blob)
        by_flw.setdefault(v["username"], []).append(agrees)
    assert all(by_flw["flw_001"]), "the clean worker's whole caseload agrees"
    assert not any(by_flw["flw_007"]), "the suspect worker's does not"


def test_showcase_visits_are_ordinary_approved_work():
    """over_limit / rejected visits never reach review — a demo case must be
    reviewable, so the AI reviewer (not Connect's status) decides its fate."""
    visits = _build([{"name": "Clean", "trajectory": "normal_02", "flw": "flw_001"}])
    assert all(v["status"] == "approved" and not v["flagged"] for v in visits)


def test_an_unknown_trajectory_fails_loudly_and_names_what_exists():
    with pytest.raises(ShowcaseError, match="does not carry"):
        _build([{"name": "Nope", "trajectory": "normal_99", "flw": "flw_001"}])
