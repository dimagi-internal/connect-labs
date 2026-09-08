"""The chain that has to hold for a demo to be clickable.

Phase 1 measures the source and writes a bundle. Phase 2 replays it and layers on
the images. These pin the seam between them: a bundle carries no image_config (the
real opportunity says nothing about photo corpora), the spec does, and replaying a
bundle WITH the spec produces cases someone can open.
"""

import pytest
import yaml

from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.generator.fixtures import corpus_manifest as cm

SPEC = "connect_labs/labs/synthetic/cohorts/kmc.yaml"


@pytest.fixture(scope="module")
def spec():
    with open(SPEC) as fh:
        return CohortSpec.from_yaml(fh.read())


def test_the_kmc_spec_declares_images_without_touching_the_cohort(spec):
    """probability 0.0 — the corpus must not smear across ~38k mirrored visits."""
    ic = spec.image_config
    assert ic and ic["corpus"] == "kmc-scale"
    assert ic["probability"] == 0.0
    assert ic["showcase"], "a spec with images but no showcase gives nothing to open"


def test_showcase_paths_match_the_paths_the_audit_will_be_configured_with(spec):
    """The reviewer is wired by PATH: image_path + comparison_field. If the
    generator writes elsewhere the audit finds nothing and the failure is silent."""
    ic = spec.image_config
    assert ic["question_path"] == "form.anthropometric.upload_weight_image"
    assert ic["reading_path"] == "form.anthropometric.child_weight_visit"


def test_every_declared_trajectory_exists_in_the_corpus(spec):
    """A typo here would surface as a missing demo case at render time."""
    series = cm.trajectories(spec.image_config["corpus"])
    for case in spec.image_config["showcase"]:
        assert case["trajectory"] in series, f"{case['name']} names an unknown trajectory"


def test_the_demo_shows_both_a_rising_and_a_faltering_curve(spec):
    """The point of the demo. Asserted on the real source values, not on labels."""
    series = cm.trajectories(spec.image_config["corpus"])
    gains = {}
    for case in spec.image_config["showcase"]:
        vals = [p["reading_grams"] for p in series[case["trajectory"]]]
        gains[case["name"]] = vals[-1] - vals[0]
    assert max(gains.values()) > 500, f"no clearly rising case: {gains}"
    assert min(gains.values()) < 150, f"no clearly faltering case: {gains}"


def test_the_demo_shows_a_clean_worker_and_a_suspect_one(spec):
    """'users that look like they passed or failed' — outcomes must not be mixed
    within one FLW, or neither worker reads as clean or suspect."""
    by_flw = {}
    for case in spec.image_config["showcase"]:
        by_flw.setdefault(case["flw"], set()).add(case["outcome"] == "pass")
    assert any(v == {True} for v in by_flw.values()), "no all-clean worker"
    assert any(v == {False} for v in by_flw.values()), "no all-failing worker"


def test_both_failure_modes_are_represented(spec):
    outcomes = {c["outcome"] for c in spec.image_config["showcase"]}
    assert {"fail_number", "fail_photo"} <= outcomes


def test_a_spec_roundtrips_its_image_config(spec):
    """to_yaml is what a resolved spec is written back as between the phases."""
    again = CohortSpec.from_yaml(spec.to_yaml())
    assert again.image_config == spec.image_config


def test_a_spec_without_images_stays_none():
    """Every other cohort must be unaffected — images are opt-in."""
    s = CohortSpec.from_yaml(yaml.safe_dump({"opportunity_ids": [1, 2]}))
    assert s.image_config is None
    assert "image_config" not in s.to_yaml()
