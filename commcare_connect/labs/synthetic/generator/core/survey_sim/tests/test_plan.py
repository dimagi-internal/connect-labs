"""Unit tests for the plan-grounded survey generator (pure, no DB)."""

import random

from commcare_connect.labs.synthetic.generator.core.survey_quality.stats import haversine_m
from commcare_connect.labs.synthetic.generator.core.survey_sim import SimParams, simulate_plan


def _work_areas(n_clusters=4, primaries=10, alternates=4, lat0=11.75, lon0=8.27):
    """A synthetic sampled-plan work-area list: clusters of ranked primary +
    alternate footprint centroids, spread on a small grid so each has a distinct
    location."""
    was = []
    for ci in range(n_clusters):
        for k in range(primaries):
            was.append(
                {
                    "wa_id": f"int-C{ci}-prim-{k}",
                    "lon": lon0 + 0.001 * ci + 0.0001 * k,
                    "lat": lat0 + 0.001 * ci,
                    "sample_type": "primary",
                    "cluster": f"C{ci}",
                    "order_in_cluster": k + 1,
                    "arm": "intervention",
                }
            )
        for k in range(alternates):
            was.append(
                {
                    "wa_id": f"int-C{ci}-alt-{k}",
                    "lon": lon0 + 0.001 * ci + 0.0001 * k,
                    "lat": lat0 + 0.001 * ci + 0.0005,
                    "sample_type": "alternate",
                    "cluster": f"C{ci}",
                    "order_in_cluster": k + 1,
                    "arm": "intervention",
                }
            )
    return was


def _params(**kw):
    base = dict(
        enumerators=["T1", "T2", "T3"],
        coverage_start=0.6,
        coverage_end=0.6,
        round_idx=0,
        n_rounds=1,
        arm="treatment",
        gps_within_15m=1.0,
        gps_near_m=(1.0, 13.0),
        gps_far_m=(16.0, 55.0),
        primary_rate={"mean": 0.85, "variance": 0.05},
    )
    base.update(kw)
    return SimParams.from_dict(base)


def test_one_record_per_primary_slot_all_primary_form():
    was = _work_areas(n_clusters=4, primaries=10, alternates=4)
    recs = simulate_plan(was, _params(), random.Random(1))
    assert len(recs) == 40  # 4 clusters x 10 primaries
    assert all(r["form_type"] == "primary" for r in recs)
    assert all(r["arm"] == "treatment" for r in recs)


def test_gps_is_grounded_on_a_real_centroid():
    """assigned_lat/lon must equal some work-area centroid, and gps_offset_m must
    be the real distance from the capture to that assigned centroid."""
    was = _work_areas()
    centroids = {(round(w["lat"], 6), round(w["lon"], 6)) for w in was}
    recs = simulate_plan(was, _params(), random.Random(2))
    for r in recs:
        assert (r["assigned_lat"], r["assigned_lon"]) in centroids
        d = haversine_m(r["lat"], r["lon"], r["assigned_lat"], r["assigned_lon"])
        assert abs(d - r["gps_offset_m"]) < 1.5  # rounding tolerance


def test_primary_rate_one_means_all_primary():
    was = _work_areas()
    recs = simulate_plan(was, _params(primary_rate={"mean": 1.0, "variance": 0.0}), random.Random(3))
    assert {r["sample_type"] for r in recs} == {"primary"}


def test_primary_rate_zero_means_all_alternate_when_available():
    was = _work_areas(alternates=4)
    recs = simulate_plan(was, _params(primary_rate={"mean": 0.0, "variance": 0.0}), random.Random(4))
    assert {r["sample_type"] for r in recs} == {"alternate"}


def test_no_alternates_falls_back_to_primary():
    was = _work_areas(alternates=0)
    recs = simulate_plan(was, _params(primary_rate={"mean": 0.0, "variance": 0.0}), random.Random(5))
    assert {r["sample_type"] for r in recs} == {"primary"}


def test_flagged_surveyor_has_lower_primary_rate():
    was = _work_areas(n_clusters=6, primaries=20, alternates=8)
    params = _params(
        enumerators=["T1", "T2", "T3", "T4", "T5", "T6"],
        primary_rate={"mean": 0.95, "variance": 0.01, "flagged_mean": 0.4, "flagged_id": "T6"},
    )
    recs = simulate_plan(was, params, random.Random(6))

    def primary_share(sid):
        rs = [r for r in recs if r["enumerator_id"] == sid]
        return sum(1 for r in rs if r["sample_type"] == "primary") / len(rs)

    assert primary_share("T6") < 0.6
    for good in ("T1", "T2", "T3", "T4", "T5"):
        assert primary_share(good) > 0.85


def test_substituted_alternate_is_in_same_cluster():
    was = _work_areas()
    by_id = {w["wa_id"]: w for w in was}
    recs = simulate_plan(was, _params(primary_rate={"mean": 0.5, "variance": 0.1}), random.Random(7))
    for r in recs:
        assert by_id[r["work_area_id"]]["cluster"] == r["cluster"]


def test_gps_within_15m_param_controls_offsets():
    was = _work_areas()
    near = simulate_plan(was, _params(gps_within_15m=1.0), random.Random(8))
    far = simulate_plan(was, _params(gps_within_15m=0.0), random.Random(8))
    assert all(r["gps_offset_m"] <= 15 for r in near)
    assert all(r["gps_offset_m"] > 15 for r in far)


def test_deterministic_for_same_seed():
    was = _work_areas()
    a = simulate_plan(was, _params(), random.Random(99))
    b = simulate_plan(was, _params(), random.Random(99))
    assert a == b


def test_ward_geom_stamps_in_ward():
    was = _work_areas(n_clusters=1, primaries=6, alternates=0, lat0=0.0, lon0=0.0)
    # A small polygon around (0,0) that contains the cluster centroid but not far offsets.
    ward = {
        "type": "Polygon",
        "coordinates": [[[-0.01, -0.01], [0.01, -0.01], [0.01, 0.01], [-0.01, 0.01], [-0.01, -0.01]]],
    }
    recs = simulate_plan(was, _params(), random.Random(10), ward_geom=ward)
    assert all(isinstance(r["in_ward"], bool) for r in recs)
    assert all(r["in_ward"] for r in recs)  # near captures stay inside the small ward
