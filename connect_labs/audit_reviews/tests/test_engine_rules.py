"""Rules the parity cases do not reach: thresholds in shapes the registry has grown since the port."""

from connect_labs.audit_reviews import engine


def _data(metric, programme_value):
    return {
        "metrics": {"m": metric},
        "runs": [{"run_id": 1, "period_end": "2026-11-22", "programme": {"m": {"value": programme_value, "n": 50}}}],
    }


MID2 = {"unit": "%", "direction": "mid2", "bands": [[4, 12], [2, 16]], "min_den": 10}


def test_a_range_measure_reads_its_scale_from_the_numbers_inside_its_ranges():
    # Values arrive as fractions (0.08 = 8%) while the ranges are in points.
    assert engine.detect_scales(_data(MID2, 0.08)) == {"m": 100}


def test_a_range_measure_is_graded_by_its_ranges():
    D = _data(MID2, 0.08)
    pf = engine.detect_scales(D)
    assert engine.grade(D, pf, "m", 0.08, 50) == "green"  # 8% in [4, 12]
    assert engine.grade(D, pf, "m", 0.03, 50) == "yellow"  # 3% in [2, 16], not [4, 12]
    assert engine.grade(D, pf, "m", 0.20, 50) == "red"
    assert engine.grade(D, pf, "m", 0.08, 5) == "insufficient"


def test_thresholds_it_cannot_read_give_no_colour_not_an_error():
    odd = {"unit": "%", "direction": "higher", "bands": [{"x": 1}, "y"], "min_den": 10}
    D = _data(odd, 0.5)
    pf = engine.detect_scales(D)
    assert engine.grade(D, pf, "m", 0.5, 50) == "unbanded"


def test_a_range_measure_and_count_measures_can_be_replayed():
    D = {
        "metrics": {
            "main": {"unit": "%", "direction": "higher", "bands": [50, 30], "min_den": 10},
            "deaths": MID2,
            "count": {"unit": "n", "direction": "none", "bands": None, "min_den": 1},
        },
        "knownMetrics": ["main"],
        "runs": [
            {
                "run_id": i,
                "period_end": f"2026-11-{8 + 7 * i:02d}",
                "programme": {
                    "main": {"band": "red", "value": 0.2, "n": 40},
                    "deaths": {"band": "yellow", "value": 0.03, "n": 40},
                    "count": {"band": "unbanded", "value": 40, "n": 40},
                },
                "llo": {},
                "opportunity": {},
                "worker": [],
            }
            for i in range(3)
        ],
    }
    cfg = engine.default_config(D)
    assert cfg["metrics"]["deaths"]["role"] == "detail" and "count" not in cfg["metrics"]
    world = engine.replay(D, cfg, {}, {"goLiveRunId": 2})
    assert world["pf"]["deaths"] == 100
    assert [p["cls"] for p in world["proposalsByRun"][2]] == ["open"]
    assert world["history"]["program"]["deaths"][-1]["state"] == "yellow"


def test_a_range_measure_with_malformed_ranges_has_no_colour():
    for bands in ([2, 12], [[12, 4], [2, 16]], [[4, 12], [2]], [[4, "12"], [2, 16]]):
        m = {**MID2, "bands": bands}
        D = _data(m, 0.08)
        assert engine.grade(D, {"m": 100}, "m", 0.08, 50) == "unbanded", bands
