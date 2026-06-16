"""Tests for the pure arm-comparability helper (study groups + single-plan reuse)."""

from __future__ import annotations


def _square(x0, y0, s=0.1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s], [x0, y0]]]}


def test_arm_comparability_computes_area_density_and_matched():
    from commcare_connect.microplans.core.comparability import arm_comparability

    out = arm_comparability(
        [
            {"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)},
            {"arm": "control", "building_count": 110, "geometry": _square(8.3, 11.0)},
        ]
    )
    arms = {a["arm"]: a for a in out["arms"]}
    assert set(arms) == {"intervention", "control"}
    assert arms["intervention"]["area_km2"] > 0
    assert arms["intervention"]["density_per_km2"] > 0
    # near-equal counts + same-size areas → matched (within 1.5x)
    assert out["matched"] is True


def test_arm_comparability_flags_not_matched_when_counts_far_apart():
    from commcare_connect.microplans.core.comparability import arm_comparability

    out = arm_comparability(
        [
            {"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)},
            {"arm": "control", "building_count": 1000, "geometry": _square(8.3, 11.0)},
        ]
    )
    assert out["matched"] is False
    assert out["reasons"]  # explains why (building counts differ N×)


def test_arm_comparability_matched_none_with_one_arm():
    from commcare_connect.microplans.core.comparability import arm_comparability

    out = arm_comparability([{"arm": "intervention", "building_count": 100, "geometry": _square(8.0, 11.0)}])
    assert out["matched"] is None  # nothing to compare against
    assert len(out["arms"]) == 1


# --- PSU-based comparability (the corrected metric: compares the SELECTED PSUs /
#     surveyed buildings via standardized mean difference, not whole-ward density) ---


def _arm(name, *, size, density, bldg_area, ward_density=0.0, n_psus=None):
    """An arm's stored sampling summary: (mean, sd) per metric + whole-ward context."""
    arm = {
        "arm": name,
        "psu_size": size,
        "psu_density": density,
        "bldg_area": bldg_area,
        "ward_density": ward_density,
    }
    if n_psus is not None:
        arm["n_psus"] = n_psus
    return arm


def test_psu_comparability_matched_on_settlement_structure_ignores_whole_ward():
    # Kauran-Mata-like: whole-ward density is 2.4x off (old metric would REJECT), but
    # the selected PSUs match on settlement density + size → matched on what matters.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), ward_density=200),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41), ward_density=83),
        ]
    )
    assert out["matched"] is True  # core metrics (size + density) within tolerance
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["psu_density"]["band"] == "good" and m["psu_size"]["band"] in ("good", "ok")


def test_psu_comparability_flags_settlement_density_mismatch():
    # Gora-like: settlements much denser/sparser than the intervention → not matched,
    # even if building counts are similar.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)),
            _arm("control", size=(60, 30), density=(2500, 1500), bldg_area=(130, 45)),
        ]
    )
    assert out["matched"] is False
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["psu_density"]["band"] == "imbalanced"
    assert any("density" in r for r in out["reasons"])


def test_psu_comparability_building_area_is_an_advisory_flag_not_a_gate():
    # Matched on size + density but building stock differs (high SMD on bldg_area):
    # headline stays matched, but a flag is surfaced for analysis-time adjustment.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 30)),
            _arm("control", size=(55, 21), density=(8100, 2550), bldg_area=(160, 32)),
        ]
    )
    assert out["matched"] is True  # size + density gate the headline
    m = {x["metric"]: x for x in out["metrics"]}
    assert m["bldg_area"]["band"] == "imbalanced"
    assert out["flags"]  # building-stock difference surfaced as an advisory flag


def test_psu_comparability_surfaces_sample_size_and_advisory_flag():
    # The panel states its own n (PSUs each SMD is computed over) and exposes
    # has_advisory so the template can render the demoted covariate block.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40), n_psus=8),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41), n_psus=8),
        ]
    )
    assert out["n_intervention"] == 8 and out["n_control"] == 8
    assert out["has_advisory"] is True  # PSU size + building footprint are always advisory rows
    assert all("n_psus" in a for a in out["arms"])


def test_psu_comparability_n_defaults_zero_for_legacy_stats():
    # Stats persisted before n_psus was threaded through must not crash; n reads 0
    # so the template hides the sample-size line rather than asserting "n = 0".
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)),
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41)),
        ]
    )
    assert out["n_intervention"] == 0 and out["n_control"] == 0


def test_psu_comparability_none_with_one_arm():
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu([_arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40))])
    assert out["matched"] is None


def test_psu_comparability_passes_name_through():
    # The shared panel renders each arm's display name (plan name on the group page,
    # ward name on the single-plan page), so the engine must echo it.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu

    out = arm_comparability_psu(
        [
            _arm("intervention", size=(53, 20), density=(8000, 2500), bldg_area=(120, 40)) | {"name": "Attakar"},
            _arm("control", size=(55, 21), density=(8200, 2600), bldg_area=(123, 41)) | {"name": "Gura"},
        ]
    )
    names = {a["arm"]: a["name"] for a in out["arms"]}
    assert names["intervention"] == "Attakar" and names["control"] == "Gura"


def test_psu_arms_from_stats_builds_comparison_input():
    # Shared assembly: a list of per-arm sampling_stats dicts → the arms input
    # arm_comparability_psu consumes. Reused by the single-plan endpoint + group page.
    from commcare_connect.microplans.core.comparability import arm_comparability_psu, psu_arms_from_stats

    stats = [
        {
            "arm": "intervention",
            "psu_size": (53, 20),
            "psu_density": (8000, 2500),
            "bldg_area": (120, 40),
            "n_psus": 8,
        },
        {"arm": "comparison", "psu_size": (55, 21), "psu_density": (8200, 2600), "bldg_area": (123, 41), "n_psus": 8},
    ]
    arms = psu_arms_from_stats(stats, names={"intervention": "Attakar", "comparison": "Gura"})
    assert {a["arm"] for a in arms} == {"intervention", "comparison"}
    out = arm_comparability_psu(arms)
    assert out["matched"] is True
    assert out["n_intervention"] == 8
    assert any(a["name"] == "Attakar" for a in out["arms"])


# --- Surrounding-ward control finder: distribution-overlap scoring -----------


def test_density_match_identical_distributions_high_overlap():
    from commcare_connect.microplans.core.comparability import density_distribution_match

    d = [100, 200, 300, 400, 500, 600, 700, 800]
    out = density_distribution_match(d, list(d))
    assert out["band"] == "good"
    assert out["overlap"] == 1.0
    assert out["median_gap_pct"] == 0.0
    assert out["n_ref"] == out["n_cand"] == len(d)


def test_density_match_same_mean_different_shape_is_not_good():
    """A uniform ward and a bimodal urban+rural ward can share a mean yet not
    overlap — the whole point of scoring the distribution, not the mean."""
    from commcare_connect.microplans.core.comparability import density_distribution_match

    uniform = [440, 450, 460, 455, 445, 448, 452, 458]  # tight around ~450
    bimodal = [100, 110, 90, 105, 800, 810, 790, 805]  # low + high, mean ~450
    out = density_distribution_match(uniform, bimodal)
    assert out["band"] in ("ok", "poor")
    assert out["overlap"] < 0.5


def test_density_match_disjoint_ranges_poor():
    from commcare_connect.microplans.core.comparability import density_distribution_match

    out = density_distribution_match([100, 120, 140, 160], [900, 950, 1000, 1050])
    assert out["band"] == "poor"
    assert out["overlap"] == 0.0


def test_density_match_insufficient_clusters():
    from commcare_connect.microplans.core.comparability import density_distribution_match

    out = density_distribution_match([100, 200], [300, 400, 500, 600])
    assert out["band"] == "insufficient"
    assert out["overlap"] is None
    assert out["smd"] is None


def test_rank_ward_matches_orders_best_first_errors_last():
    from commcare_connect.microplans.tasks import _rank_ward_matches

    rows = [
        {"name": "lo", "overlap": 0.3},
        {"name": "err", "overlap": None, "status": "error"},
        {"name": "hi", "overlap": 0.9},
    ]
    ranked = _rank_ward_matches(rows)
    assert [r["name"] for r in ranked] == ["hi", "lo", "err"]
