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
