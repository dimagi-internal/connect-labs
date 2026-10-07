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


# ---- GPS: the programme's shape, moved, and no real point ----------------------
# Invented coordinates: two workers about 30 km apart.

REAL = {"asha": (9.00, 7.00), "ben": (9.20, 7.20)}


def _km(a, b):
    import math

    (lat1, lon1), (lat2, lon2) = a, b
    return math.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * math.cos(math.radians((lat1 + lat2) / 2)))


def _source_with_gps():
    visits = _source()
    for n, visit in enumerate(visits):
        lat, lon = REAL[visit["username"]]
        # Each visit a little off the worker's middle, as real households are.
        visit["location"] = f"{lat + (n % 3 - 1) * 0.01:.5f} {lon + (n % 2) * 0.01:.5f} 400 6"
    return visits


@pytest.fixture
def geo_manifest():
    return yaml.safe_load(
        profile(
            opportunity_id=10_009,
            user_visits=_source_with_gps(),
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=_app(),
            case_timelines=True,
            noise_seed=3,
        )
    )


def _persona_of(manifest, username):
    # The profiler ranks workers by visit count, and both here have 12; read the mapping back
    # from where each real worker's centre landed rather than assuming the tie-break.
    centers = manifest["geography"]["persona_centers"]
    return min(centers, key=lambda p: _km(REAL[username], (centers[p][1], centers[p][0])))


def test_a_clone_carries_one_moved_point_per_worker_and_no_real_point(geo_manifest):
    centers = geo_manifest["geography"]["persona_centers"]
    assert set(centers) == {"flw_001", "flw_002"}
    real_points = {tuple(map(float, v["location"].split()[:2])) for v in _source_with_gps()}
    for username in REAL:
        lon, lat = centers[_persona_of(geo_manifest, username)]
        assert (lat, lon) not in real_points
        # Moved off the real map, but kept in the same district.
        assert 1.0 < _km(REAL[username], (lat, lon)) < 12.0
    assert "asha" not in yaml.dump(geo_manifest["geography"])


def test_a_clone_keeps_how_far_apart_the_workers_are(geo_manifest):
    centers = geo_manifest["geography"]["persona_centers"]
    (lon_a, lat_a), (lon_b, lat_b) = centers["flw_001"], centers["flw_002"]
    assert abs(_km((lat_a, lon_a), (lat_b, lon_b)) - _km(REAL["asha"], REAL["ben"])) < 3.0


def test_a_source_with_no_gps_gives_a_clone_with_none():
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
    assert "geography" not in manifest


def test_a_generated_clone_puts_each_workers_visits_around_that_worker(geo_manifest):
    from connect_labs.labs.synthetic.generator.fixtures.engine import generate
    from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest
    from connect_labs.labs.synthetic.generator.fixtures.schema_loader import parse_form_schema_from_app_json

    manifest = Manifest.from_yaml(yaml.dump(geo_manifest))
    out = generate(
        manifest=manifest,
        opportunity_detail={"name": "KMC", "id": 10_009},
        form_schema=parse_form_schema_from_app_json(_app(), app_type="deliver"),
    )
    centers = geo_manifest["geography"]["persona_centers"]
    visits = out["user_visits"]
    assert visits and all(v["location"] for v in visits)
    for visit in visits:
        lat, lon = map(float, visit["location"].split()[:2])
        lon_c, lat_c = centers[visit["username"]]
        assert _km((lat, lon), (lat_c, lon_c)) < 6.0
        assert visit["form_json"]["metadata"]["location"] == visit["location"]
