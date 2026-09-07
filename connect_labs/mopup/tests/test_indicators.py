"""Tests for the §5 execution-gap indicators and §6 cluster-aware detection —
the core logic the design brief specifically wanted real pytest coverage for
instead of manual live-browser checks."""

from __future__ import annotations

import pytest

from connect_labs.mopup.core import indicators as ind


def _wa(wa_id, **overrides):
    base = {
        "wa_id": wa_id,
        "ward": "Sabon Gari",
        "lga": "Rano",
        "state": "Kano",
        "flw_username": "flw-1",
        "lat": 12.0,
        "lon": 8.0,
        "status": "VISITED",
        "building_count": 10,
        "expected_visit_count": 10,
        "approved_hsd_count": 8,
        "approved_ncf_count": 1,
        "approved_inaccessible_count": 1,
        "deworming_given": 8,
        "muac_given": 8,
        "vaccination_given": 8,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# wa_rate
# ---------------------------------------------------------------------------


class TestWaRateEvcShortfall:
    def test_basic_ratio(self):
        wa = _wa("wa-1", approved_hsd_count=5, expected_visit_count=10)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) == pytest.approx(0.5)

    def test_zero_expected_returns_none_not_zerodivision(self):
        wa = _wa("wa-1", expected_visit_count=0)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) is None

    @pytest.mark.parametrize("status", ["NOT_VISITED", "REQUEST_FOR_INACCESSIBLE"])
    def test_excluded_by_default_for_not_yet_visited_statuses(self, status):
        wa = _wa("wa-1", status=status)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) is None

    @pytest.mark.parametrize("status", ["NOT_VISITED", "REQUEST_FOR_INACCESSIBLE"])
    def test_override_includes_not_yet_visited(self, status):
        wa = _wa("wa-1", status=status, approved_hsd_count=5, expected_visit_count=10)
        rate = ind.wa_rate(wa, ind.EVC_SHORTFALL, {"include_not_yet_visited": True})
        assert rate == pytest.approx(0.5)

    @pytest.mark.parametrize("status", ["VISITED", "EXPECTED_VISIT_REACHED", "INACCESSIBLE"])
    def test_concluded_statuses_always_computed(self, status):
        wa = _wa("wa-1", status=status, approved_hsd_count=5, expected_visit_count=10)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) == pytest.approx(0.5)


class TestWaRateNcfInaccessible:
    def test_basic_rate(self):
        wa = _wa("wa-1", approved_hsd_count=7, approved_ncf_count=2, approved_inaccessible_count=1)
        # (2+1) / (7+2+1) = 0.3
        assert ind.wa_rate(wa, ind.NCF_INACCESSIBLE, {}) == pytest.approx(0.3)

    def test_gated_by_min_building_count(self):
        wa = _wa("wa-1", building_count=2)
        assert ind.wa_rate(wa, ind.NCF_INACCESSIBLE, {"min_building_count": 5}) is None

    def test_not_gated_when_building_count_meets_floor(self):
        wa = _wa("wa-1", building_count=5)
        assert ind.wa_rate(wa, ind.NCF_INACCESSIBLE, {"min_building_count": 5}) is not None

    def test_zero_total_visits_returns_none(self):
        wa = _wa("wa-1", approved_hsd_count=0, approved_ncf_count=0, approved_inaccessible_count=0)
        assert ind.wa_rate(wa, ind.NCF_INACCESSIBLE, {}) is None


class TestWaRateDataQuality:
    @pytest.mark.parametrize(
        "key,field",
        [(ind.DEWORMING, "deworming_given"), (ind.MUAC, "muac_given"), (ind.VACCINATION, "vaccination_given")],
    )
    def test_basic_rate(self, key, field):
        wa = _wa("wa-1", approved_hsd_count=10, **{field: 4})
        assert ind.wa_rate(wa, key, {}) == pytest.approx(0.4)

    @pytest.mark.parametrize("key", [ind.DEWORMING, ind.MUAC, ind.VACCINATION])
    def test_gated_by_min_hsd_visits_floor(self, key):
        wa = _wa("wa-1", approved_hsd_count=3)
        assert ind.wa_rate(wa, key, {"min_hsd_visits_floor": 10}) is None

    def test_unknown_indicator_raises(self):
        with pytest.raises(ValueError):
            ind.wa_rate(_wa("wa-1"), "not_a_real_indicator", {})


# ---------------------------------------------------------------------------
# is_flagged
# ---------------------------------------------------------------------------


class TestIsFlagged:
    def test_none_rate_never_flags(self):
        assert ind.is_flagged(None, 0.5, ind.EVC_SHORTFALL) is False

    def test_below_direction(self):
        assert ind.is_flagged(0.3, 0.5, ind.EVC_SHORTFALL) is True
        assert ind.is_flagged(0.5, 0.5, ind.EVC_SHORTFALL) is False
        assert ind.is_flagged(0.7, 0.5, ind.EVC_SHORTFALL) is False

    def test_above_direction(self):
        assert ind.is_flagged(0.7, 0.5, ind.NCF_INACCESSIBLE) is True
        assert ind.is_flagged(0.5, 0.5, ind.NCF_INACCESSIBLE) is False
        assert ind.is_flagged(0.3, 0.5, ind.NCF_INACCESSIBLE) is False


# ---------------------------------------------------------------------------
# Spatial neighbor graph (§6a)
# ---------------------------------------------------------------------------


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        assert ind.haversine_distance_m(12.0, 8.0, 12.0, 8.0) == 0.0

    def test_known_short_distance_is_reasonable(self):
        # ~0.001 degrees latitude is roughly 111m.
        d = ind.haversine_distance_m(12.0, 8.0, 12.001, 8.0)
        assert 100 < d < 120


class TestBuildNeighborGraph:
    def test_close_work_areas_are_mutual_neighbors(self):
        was = [_wa("a", lat=12.0, lon=8.0), _wa("b", lat=12.0005, lon=8.0)]  # ~55m apart
        graph = ind.build_neighbor_graph(was, distance_m=200)
        assert graph["a"] == ["b"]
        assert graph["b"] == ["a"]

    def test_far_work_areas_are_not_neighbors(self):
        was = [_wa("a", lat=12.0, lon=8.0), _wa("b", lat=13.0, lon=9.0)]
        graph = ind.build_neighbor_graph(was, distance_m=200)
        assert graph["a"] == []
        assert graph["b"] == []

    def test_missing_coordinates_are_skipped_not_crashed(self):
        was = [_wa("a", lat=None, lon=None), _wa("b", lat=12.0, lon=8.0)]
        graph = ind.build_neighbor_graph(was, distance_m=200)
        assert graph == {"a": [], "b": []}


class TestNeighborhoodRate:
    def test_averages_qualifying_neighbors(self):
        by_id = {
            "b": _wa("b", approved_hsd_count=5, expected_visit_count=10),  # 0.5
            "c": _wa("c", approved_hsd_count=3, expected_visit_count=10),  # 0.3
        }
        rate = ind.neighborhood_rate(_wa("a"), ["b", "c"], by_id, ind.EVC_SHORTFALL, {}, min_neighbors=2)
        assert rate == pytest.approx(0.4)

    def test_too_few_qualifying_neighbors_returns_none(self):
        by_id = {"b": _wa("b", approved_hsd_count=5, expected_visit_count=10)}
        rate = ind.neighborhood_rate(_wa("a"), ["b"], by_id, ind.EVC_SHORTFALL, {}, min_neighbors=3)
        assert rate is None

    def test_neighbors_with_ungated_rate_excluded_from_average_and_count(self):
        by_id = {
            "b": _wa("b", building_count=0),  # gated out for NCF (min_building_count default 1)
            "c": _wa(
                "c", building_count=10, approved_hsd_count=7, approved_ncf_count=2, approved_inaccessible_count=1
            ),
        }
        rate = ind.neighborhood_rate(_wa("a"), ["b", "c"], by_id, ind.NCF_INACCESSIBLE, {}, min_neighbors=1)
        assert rate == pytest.approx(0.3)
        # with min_neighbors=2, only "c" qualifies -> not enough
        rate2 = ind.neighborhood_rate(_wa("a"), ["b", "c"], by_id, ind.NCF_INACCESSIBLE, {}, min_neighbors=2)
        assert rate2 is None


# ---------------------------------------------------------------------------
# Within-FLW clustering (§6b)
# ---------------------------------------------------------------------------


class TestFlwPortfolioRate:
    def test_averages_other_was_excluding_self(self):
        flw_was = [
            _wa("a", approved_hsd_count=10, deworming_given=1),  # self, excluded
            _wa("b", approved_hsd_count=10, deworming_given=2),  # 0.2
            _wa("c", approved_hsd_count=10, deworming_given=4),  # 0.4
        ]
        rate = ind.flw_portfolio_rate(flw_was[0], flw_was, ind.DEWORMING, {}, min_portfolio_size=2)
        assert rate == pytest.approx(0.3)

    def test_too_few_others_returns_none(self):
        flw_was = [_wa("a"), _wa("b", approved_hsd_count=10, deworming_given=2)]
        rate = ind.flw_portfolio_rate(flw_was[0], flw_was, ind.DEWORMING, {}, min_portfolio_size=3)
        assert rate is None


# ---------------------------------------------------------------------------
# Whole-FLW average (§6, three-way view)
# ---------------------------------------------------------------------------


class TestFlwAverageRate:
    def test_evc_blends_concluded_only_by_default(self):
        flw_was = [
            _wa("a", status="VISITED", approved_hsd_count=5, expected_visit_count=10),
            _wa("b", status="NOT_VISITED", approved_hsd_count=0, expected_visit_count=10),
        ]
        # only "a" counts: 5/10 = 0.5
        assert ind.flw_average_rate(flw_was, ind.EVC_SHORTFALL, {}) == pytest.approx(0.5)

    def test_evc_override_includes_not_yet_visited(self):
        flw_was = [
            _wa("a", status="VISITED", approved_hsd_count=5, expected_visit_count=10),
            _wa("b", status="NOT_VISITED", approved_hsd_count=0, expected_visit_count=10),
        ]
        rate = ind.flw_average_rate(flw_was, ind.EVC_SHORTFALL, {"include_not_yet_visited": True})
        assert rate == pytest.approx(5 / 20)

    def test_ncf_excludes_wa_below_min_building_count(self):
        flw_was = [
            _wa("a", building_count=10, approved_hsd_count=7, approved_ncf_count=2, approved_inaccessible_count=1),
            _wa("b", building_count=0, approved_hsd_count=0, approved_ncf_count=5, approved_inaccessible_count=0),
        ]
        rate = ind.flw_average_rate(flw_was, ind.NCF_INACCESSIBLE, {"min_building_count": 1})
        assert rate == pytest.approx(0.3)  # only "a" counted

    def test_dq_excludes_wa_below_min_hsd_floor(self):
        flw_was = [
            _wa("a", approved_hsd_count=10, muac_given=4),
            _wa("b", approved_hsd_count=1, muac_given=0),
        ]
        rate = ind.flw_average_rate(flw_was, ind.MUAC, {"min_hsd_visits_floor": 5})
        assert rate == pytest.approx(0.4)  # only "a" counted

    def test_zero_denominator_returns_none(self):
        flw_was = [_wa("a", status="NOT_VISITED", expected_visit_count=10)]
        assert ind.flw_average_rate(flw_was, ind.EVC_SHORTFALL, {}) is None


# ---------------------------------------------------------------------------
# evaluate_run — the main entry point
# ---------------------------------------------------------------------------


class TestEvaluateRun:
    def test_disabled_indicator_never_flags(self):
        was = [_wa("a", approved_hsd_count=0, expected_visit_count=10)]  # would fail EVC shortfall
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": False, "threshold": 0.5}})
        assert candidates == []

    def test_wa_only_granularity_flags_on_own_rate_alone(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10)]  # 0.1, below 0.5
        candidates = ind.evaluate_run(
            was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY}}
        )
        assert len(candidates) == 1
        assert candidates[0]["triggered_indicators"] == [ind.EVC_SHORTFALL]
        assert candidates[0]["severity_count"] == 1

    def test_passing_wa_is_excluded_entirely(self):
        was = [_wa("a", approved_hsd_count=9, expected_visit_count=10)]  # 0.9, not below 0.5
        candidates = ind.evaluate_run(
            was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY}}
        )
        assert candidates == []

    def test_union_or_across_multiple_indicators(self):
        # Fails EVC shortfall AND has a low deworming rate -> both trigger.
        was = [
            _wa(
                "a",
                approved_hsd_count=1,
                expected_visit_count=10,  # EVC 0.1, fails
                deworming_given=0,  # deworming 0/1, fails
            )
        ]
        candidates = ind.evaluate_run(
            was,
            {
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY},
                ind.DEWORMING: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_WA_ONLY},
            },
        )
        assert len(candidates) == 1
        assert set(candidates[0]["triggered_indicators"]) == {ind.EVC_SHORTFALL, ind.DEWORMING}
        assert candidates[0]["severity_count"] == 2

    def test_cluster_aware_isolated_outlier_is_not_flagged(self):
        # "a" has a bad rate, but its neighbors are all fine -> not a real cluster.
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10),  # 0.1
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=9, expected_visit_count=10),  # 0.9
            _wa("c", lat=12.001, lon=8.0, approved_hsd_count=9, expected_visit_count=10),  # 0.9
            _wa("d", lat=12.0015, lon=8.0, approved_hsd_count=9, expected_visit_count=10),  # 0.9
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_CLUSTER_AWARE}},
            {"neighbor_distance_m": 250, "min_neighbor_count": 3},
        )
        assert candidates == []

    def test_cluster_aware_real_cluster_is_flagged(self):
        # "a" and its neighbors are ALL bad -> a genuine cluster.
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("c", lat=12.001, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("d", lat=12.0015, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_CLUSTER_AWARE}},
            {"neighbor_distance_m": 250, "min_neighbor_count": 3},
        )
        assert len(candidates) == 4
        detail = candidates[0]["detail"][ind.EVC_SHORTFALL]
        assert detail["is_isolated_outlier"] is False

    def test_cluster_aware_too_few_neighbors_does_not_flag(self):
        # "a" is bad but has only 1 neighbor (min_neighbor_count=3) -> can't trust the neighborhood.
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_CLUSTER_AWARE}},
            {"neighbor_distance_m": 250, "min_neighbor_count": 3},
        )
        assert candidates == []

    def test_flw_average_flags_whole_portfolio_uniformly(self):
        was = [
            _wa("a", flw_username="bad-flw", approved_hsd_count=1, expected_visit_count=10),
            _wa("b", flw_username="bad-flw", approved_hsd_count=2, expected_visit_count=10),
        ]
        candidates = ind.evaluate_run(
            was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_FLW_AVERAGE}}
        )
        assert {c["wa_id"] for c in candidates} == {"a", "b"}

    def test_dq_cluster_aware_uses_flw_portfolio_not_spatial_neighbors(self):
        # Two WAs far apart (no spatial neighbors) but same FLW, both bad on deworming.
        was = [
            _wa("a", lat=12.0, lon=8.0, flw_username="flw-x", approved_hsd_count=10, deworming_given=0),
            _wa("b", lat=20.0, lon=20.0, flw_username="flw-x", approved_hsd_count=10, deworming_given=1),
            _wa("c", lat=30.0, lon=30.0, flw_username="flw-x", approved_hsd_count=10, deworming_given=0),
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.DEWORMING: {"enabled": True, "threshold": 0.5, "granularity": ind.GRANULARITY_CLUSTER_AWARE}},
            {"min_neighborhood_size": 2},
        )
        assert len(candidates) == 3

    def test_unknown_granularity_raises(self):
        was = [_wa("a")]
        with pytest.raises(ValueError):
            ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5, "granularity": "bogus"}})
