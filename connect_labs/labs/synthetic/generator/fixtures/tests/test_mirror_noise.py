"""The mirror pool that ships in a manifest holds no real case exactly.

A pool series is one real case: its first-visit date, its visit timing, its values.
The profiler perturbs it before it enters a manifest: the case moves in time (its
internal timing and ages kept exact) and its numbers are scaled by a small per-case
factor. Outcome sequences are kept, since they are the reason to mirror at all.
"""

import datetime as dt

import pytest
import yaml
from django.test import override_settings

from connect_labs.labs.synthetic.generator.fixtures.mirror import (
    MIRROR_START_SHIFT_DAYS,
    MIRROR_VALUE_JITTER,
    perturb_transplant_pool,
)
from connect_labs.labs.synthetic.generator.fixtures.profiler import _mirror_noise_seed, profile


def _pool():
    return [
        {
            "owner": "flw_001",
            "start_date": f"2026-01-{e + 1:02d}",
            "visits": [
                {
                    "day": 7 * i,
                    "values": {"form.weight": 1500.0 + 100 * i + e, "form.muac": 11.5 + 0.1 * i, "form.count": 2.0},
                    "dates": {"form.dob": -3},
                    "cats": {"form.alive": "yes" if i < 2 else "no"},
                    "form": "Visit",
                }
                for i in range(3)
            ]
            + [{"day": 21, "values": {"form.birth_weight": 1400.0 + e}}],
        }
        for e in range(20)
    ]


def test_every_case_moves_in_time_but_keeps_its_own_timing():
    real = _pool()
    noisy = perturb_transplant_pool(real, seed=1)
    for before, after in zip(real, noisy):
        shift = (dt.date.fromisoformat(after["start_date"]) - dt.date.fromisoformat(before["start_date"])).days
        assert shift != 0 and abs(shift) <= MIRROR_START_SHIFT_DAYS
        assert [v["day"] for v in after["visits"]] == [v["day"] for v in before["visits"]]
        assert [v.get("dates") for v in after["visits"]] == [v.get("dates") for v in before["visits"]]


def test_outcomes_form_names_and_owners_are_kept():
    real = _pool()
    noisy = perturb_transplant_pool(real, seed=1)
    for before, after in zip(real, noisy):
        assert after["owner"] == before["owner"]
        assert [v.get("cats") for v in after["visits"]] == [v.get("cats") for v in before["visits"]]
        assert [v.get("form") for v in after["visits"]] == [v.get("form") for v in before["visits"]]


def test_numbers_move_by_at_most_the_jitter_and_keep_their_shape():
    real = _pool()
    noisy = perturb_transplant_pool(real, seed=1)
    changed = 0
    for before, after in zip(real, noisy):
        weights = []
        for vb, va in zip(before["visits"], after["visits"]):
            for path, value in vb["values"].items():
                new = va["values"][path]
                assert abs(new - value) <= value * MIRROR_VALUE_JITTER + 0.5
                changed += new != value
            if "form.weight" in va["values"]:
                weights.append(va["values"]["form.weight"])
                assert va["values"]["form.weight"].is_integer()  # recorded whole, stays whole
                assert round(va["values"]["form.muac"], 2) == va["values"]["form.muac"]
                assert va["values"]["form.count"] == 2.0  # a small whole number cannot move
        assert weights == sorted(weights)  # a rising curve still rises
    assert changed > 40  # the real numbers do not survive


def test_values_stay_inside_the_range_the_source_observed():
    real = _pool()
    lo = min(v["values"]["form.weight"] for s in real for v in s["visits"] if "form.weight" in v["values"])
    hi = max(v["values"]["form.weight"] for s in real for v in s["visits"] if "form.weight" in v["values"])
    for seed in range(5):
        for s in perturb_transplant_pool(real, seed=seed):
            for v in s["visits"]:
                if "form.weight" in v["values"]:
                    assert lo <= v["values"]["form.weight"] <= hi


def test_frozen_paths_are_replayed_exactly():
    real = _pool()
    noisy = perturb_transplant_pool(real, seed=1, frozen_paths={"form.weight"})
    assert [v["values"].get("form.weight") for s in noisy for v in s["visits"]] == [
        v["values"].get("form.weight") for s in real for v in s["visits"]
    ]


def test_the_noise_is_deterministic_in_its_seed():
    assert perturb_transplant_pool(_pool(), seed=7) == perturb_transplant_pool(_pool(), seed=7)
    assert perturb_transplant_pool(_pool(), seed=7) != perturb_transplant_pool(_pool(), seed=8)


def test_the_default_seed_is_keyed_on_the_server_secret_not_the_opp_id_alone():
    """Seeding from the opp id (which the manifest carries) would let anyone replay and subtract the noise."""
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
def pool():
    manifest = yaml.safe_load(
        profile(
            opportunity_id=10_009,
            user_visits=_source(),
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=_app(),
            mirror=True,
            noise_seed=3,
        )
    )
    return manifest["beneficiary_cohorts"][0]["longitudinal"]["transplant_pool"]


def test_the_saved_profile_holds_no_real_first_visit_date(pool):
    # Real cases start 40 days apart, further than any shift, so a shifted start can
    # never land on a real one by coincidence.
    real_starts = {(dt.date(2026, 1, 1) + dt.timedelta(days=40 * e)).isoformat() for e in range(8)}
    assert len(pool) == 8
    assert not ({series["start_date"] for series in pool} & real_starts)


def test_the_saved_profile_holds_no_real_weight_series(pool):
    real_series = {tuple(1500.0 + 100 * i + 13 * e for i in range(3)) for e in range(8)}
    shipped = {tuple(v["values"]["form.weight"] for v in s["visits"]) for s in pool}
    assert not (shipped & real_series)


def test_an_app_computed_whole_number_is_replayed_exactly(pool):
    """age is `calculate`d from the dob, whose offset the shift keeps, so it stays exact
    (unfrozen, 60 would move: 3% of it rounds to a whole day or two)."""
    for series in pool:
        assert [v["values"]["form.age"] for v in series["visits"]] == [40.0, 60.0, 80.0]
