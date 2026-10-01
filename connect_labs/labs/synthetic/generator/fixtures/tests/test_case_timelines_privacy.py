"""The case-timelines pool that ships in a manifest holds no real case.

The profiler measures each real case (mirror.profile_entity_structure), fits models
to those measurements (case_model.py), and ships cases SAMPLED from the models. These
tests run the real profiler on a small source and check the saved pool against it.
"""

import datetime as dt

import pytest
import yaml
from django.test import override_settings

from connect_labs.labs.synthetic.generator.fixtures.profiler import _mirror_noise_seed, profile


def test_the_default_seed_is_keyed_on_the_server_secret_not_the_opp_id_alone():
    """A seed derivable from the manifest (it carries the opp id) would let anyone replay the sampling."""
    with override_settings(SECRET_KEY="one"):
        a = _mirror_noise_seed(874)
        assert _mirror_noise_seed(874) == a
        assert _mirror_noise_seed(875) != a
    with override_settings(SECRET_KEY="two"):
        assert _mirror_noise_seed(874) != a


def _source():
    visits = []
    for e in range(8):
        for i in range(3):
            visits.append(
                {
                    "username": "asha" if e % 2 else "ben",
                    "visit_date": (dt.date(2026, 1, 1) + dt.timedelta(days=40 * e + 7 * i)).isoformat(),
                    "status": "approved",
                    "entity_id": f"ent_{e}",
                    "form_json": {"form": {"weight": 1500.0 + 100 * i + 13 * e, "age": float(40 + 20 * i)}},
                }
            )
    return visits


def _app():
    return {
        "learn_app": None,
        "deliver_app": {
            "modules": [
                {
                    "forms": [
                        {
                            "questions": [
                                {"value": "/data/weight", "type": "Decimal", "options": []},
                                {"value": "/data/age", "type": "Int", "options": [], "calculate": "today() - dob"},
                            ]
                        }
                    ]
                }
            ]
        },
    }


@pytest.fixture
def longitudinal():
    manifest = yaml.safe_load(
        profile(
            opportunity_id=10_009,
            user_visits=_source(),
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=_app(),
            case_timelines=True,
            noise_seed=3,
        )
    )
    return manifest["beneficiary_cohorts"][0]["longitudinal"]


@pytest.fixture
def pool(longitudinal):
    return longitudinal["transplant_pool"]


def test_the_pool_is_modelled_not_mirrored(longitudinal):
    assert longitudinal["mode"] == "modelled"


def test_each_worker_keeps_its_caseload(pool):
    # 4 cases each for the two workers, 3 visits each, as in the source.
    assert sorted(len(s["visits"]) for s in pool) == [3] * 8
    assert sorted(sum(1 for s in pool if s["owner"] == o) for o in {s["owner"] for s in pool}) == [4, 4]


def test_the_saved_profile_holds_no_real_first_visit_date(pool):
    # Real cases start 40 days apart, further than any smoothing, so a sampled start
    # can never land on a real one by coincidence.
    real_starts = {(dt.date(2026, 1, 1) + dt.timedelta(days=40 * e)).isoformat() for e in range(8)}
    assert not ({series["start_date"] for series in pool} & real_starts)


def test_the_saved_profile_holds_no_real_weight_series(pool):
    real_series = {tuple(1500.0 + 100 * i + 13 * e for i in range(3)) for e in range(8)}
    shipped = {tuple(v["values"]["form.weight"] for v in s["visits"]) for s in pool}
    assert not (shipped & real_series)


def test_growth_is_modelled_from_the_source(pool):
    # Every source case grows 100 g a week; every sampled case grows the same way.
    for series in pool:
        weights = [v["values"]["form.weight"] for v in series["visits"]]
        assert all(b > a for a, b in zip(weights, weights[1:]))


def test_an_app_computed_whole_number_keeps_its_relationship_to_the_visit(pool):
    """age is `calculate`d, so it is rebuilt from the case's own timing, not jittered."""
    for series in pool:
        assert [v["values"]["form.age"] for v in series["visits"]] == [40.0, 60.0, 80.0]
