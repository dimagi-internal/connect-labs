"""Tests for the tiered execution-gap indicators and the optional cluster-aware
filter (see core/indicators.py's module docstring for the design) — the core
logic the design brief specifically wanted real pytest coverage for instead of
manual live-browser checks."""

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


def _no_filter(**overrides):
    """A global_config with the cluster-aware filter off, so evaluate_run
    tests can isolate the unconditional per-WA floor without also needing a
    corroborating neighbor/FLW."""
    cfg = {"cluster_aware_filter_enabled": False}
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# wa_rate / wa_numerator_denominator
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

    @pytest.mark.parametrize("status", ["visited", "Visited", "inaccessible", "expected_visit_reached"])
    def test_concluded_status_matching_is_case_insensitive(self, status):
        # Real production bug, found live 2026-09-11: the real `wa_status`
        # case property uses lowercase "visited" for a completed WA, not
        # "VISITED" -- confirmed against a real CHC deliver app's own form
        # logic. The comparison must not be a case-sensitive exact match.
        wa = _wa("wa-1", status=status, approved_hsd_count=5, expected_visit_count=10)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) == pytest.approx(0.5)

    def test_blank_status_is_not_concluded(self):
        # The other half of the same bug: a genuinely missing/blank status
        # (e.g. a field-path mismatch upstream) must NOT be silently treated
        # as concluded -- it should behave exactly like "not yet visited".
        wa = _wa("wa-1", status="", approved_hsd_count=5, expected_visit_count=10)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) is None

    def test_gated_by_min_evc_floor(self):
        wa = _wa("wa-1", expected_visit_count=3, approved_hsd_count=0)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {"min_evc_floor": 5}) is None

    def test_not_gated_when_expected_meets_floor(self):
        wa = _wa("wa-1", expected_visit_count=5, approved_hsd_count=2)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {"min_evc_floor": 5}) is not None

    def test_zero_hsd_is_excluded_entirely(self):
        # Not-yet-visited, inaccessible, and NCF are all already covered
        # elsewhere -- a concluded WA with zero HSD visits shouldn't also
        # show up as an EVC "shortfall" (it never had any HSD delivery to
        # fall short on).
        wa = _wa("wa-1", status="VISITED", approved_hsd_count=0, expected_visit_count=10)
        assert ind.wa_rate(wa, ind.EVC_SHORTFALL, {}) is None


class TestWaNumeratorDenominator:
    def test_matches_wa_rate_for_evc_shortfall(self):
        wa = _wa("wa-1", approved_hsd_count=5, expected_visit_count=10)
        assert ind.wa_numerator_denominator(wa, ind.EVC_SHORTFALL, {}) == (5, 10)

    def test_gated_out_returns_none_same_as_wa_rate(self):
        wa = _wa("wa-1", status="NOT_VISITED")
        assert ind.wa_numerator_denominator(wa, ind.EVC_SHORTFALL, {}) is None

    def test_dq_pair_is_given_over_hsd_count(self):
        wa = _wa("wa-1", approved_hsd_count=8, deworming_given=3)
        assert ind.wa_numerator_denominator(wa, ind.DEWORMING, {}) == (3, 8)

    def test_ncf_raises_since_it_has_no_rate(self):
        with pytest.raises(ValueError):
            ind.wa_numerator_denominator(_wa("wa-1"), ind.NCF_INACCESSIBLE, {})


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


# ---------------------------------------------------------------------------
# Spatial neighbor graph
# ---------------------------------------------------------------------------


class TestHaversineDistance:
    def test_same_point_is_zero(self):
        assert ind.haversine_distance_m(12.0, 8.0, 12.0, 8.0) == 0.0

    def test_known_short_distance_is_reasonable(self):
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


class TestNcfAffected:
    def test_true_when_any_ncf_or_inaccessible_visit(self):
        wa = _wa("a", building_count=10, approved_ncf_count=1, approved_inaccessible_count=0)
        assert ind._ncf_affected(wa, {"min_building_count": 1}) is True

    def test_false_when_neither(self):
        wa = _wa("a", building_count=10, approved_ncf_count=0, approved_inaccessible_count=0)
        assert ind._ncf_affected(wa, {"min_building_count": 1}) is False

    def test_none_when_gated_out_by_min_building_count(self):
        wa = _wa("a", building_count=0, approved_ncf_count=1)
        assert ind._ncf_affected(wa, {"min_building_count": 1}) is None


class TestNcfNeighborAffectedCount:
    def test_counts_only_affected_neighbors(self):
        by_id = {
            "b": _wa("b", building_count=10, approved_ncf_count=1, approved_inaccessible_count=0),  # affected
            "c": _wa("c", building_count=10, approved_ncf_count=0, approved_inaccessible_count=0),  # not affected
            "d": _wa(
                "d", building_count=0, approved_ncf_count=1, approved_inaccessible_count=0
            ),  # gated out, doesn't count
        }
        count = ind.ncf_neighbor_affected_count(_wa("a"), ["b", "c", "d"], by_id, {"min_building_count": 1})
        assert count == 1

    def test_missing_neighbor_id_ignored(self):
        count = ind.ncf_neighbor_affected_count(_wa("a"), ["ghost"], {}, {"min_building_count": 1})
        assert count == 0


class TestFlaggedNeighborCount:
    """The unified cluster-aware corroboration signal for EVC/deworming/MUAC/
    vaccination — a raw count of neighbors themselves floor-flagged on the
    same indicator, not an averaged rate (matches NCF's existing mechanism)."""

    def test_counts_only_flagged_neighbors(self):
        by_id = {
            "b": _wa("b", approved_hsd_count=1, expected_visit_count=10),  # 0.1, flagged
            "c": _wa("c", approved_hsd_count=9, expected_visit_count=10),  # 0.9, not flagged
        }
        count = ind.flagged_neighbor_count(_wa("a"), ["b", "c"], by_id, ind.EVC_SHORTFALL, 0.5, {})
        assert count == 1

    def test_missing_neighbor_id_ignored(self):
        count = ind.flagged_neighbor_count(_wa("a"), ["ghost"], {}, ind.EVC_SHORTFALL, 0.5, {})
        assert count == 0

    def test_neighbor_gated_out_does_not_count(self):
        by_id = {"b": _wa("b", status="NOT_VISITED")}
        count = ind.flagged_neighbor_count(_wa("a"), ["b"], by_id, ind.EVC_SHORTFALL, 0.5, {})
        assert count == 0


# ---------------------------------------------------------------------------
# evaluate_run — the main entry point
# ---------------------------------------------------------------------------


class TestEvaluateRunFloor:
    """ "This WA only" is now an unconditional floor — every candidate here
    uses the cluster-aware filter turned OFF to isolate that floor."""

    def test_disabled_indicator_never_flags(self):
        was = [_wa("a", approved_hsd_count=0, expected_visit_count=10)]  # would fail EVC shortfall
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": False, "threshold": 0.5}}, _no_filter())
        assert candidates == []

    def test_floor_flags_on_own_rate_alone(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10)]  # 0.1, below 0.5
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert len(candidates) == 1
        assert candidates[0]["triggered_indicators"] == [ind.EVC_SHORTFALL]
        assert candidates[0]["severity_count"] == 1

    def test_passing_wa_is_excluded_entirely(self):
        was = [_wa("a", approved_hsd_count=9, expected_visit_count=10)]  # 0.9, not below 0.5
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert candidates == []

    def test_detail_carries_own_numerator_denominator(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10)]
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        detail = candidates[0]["detail"][ind.EVC_SHORTFALL]
        assert detail["own_numerator"] == 1
        assert detail["own_denominator"] == 10

    def test_candidate_carries_building_count_and_source_for_phase_3(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10, building_count=7, boundary={"type": "Point"})]
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert len(candidates) == 1
        assert candidates[0]["building_count"] == 7
        assert candidates[0]["expected_visit_count"] == 10
        assert candidates[0]["source"] == ind.SOURCE_EXISTING_WA
        assert candidates[0]["boundary"] == {"type": "Point"}

    def test_union_or_across_multiple_indicators(self):
        # approved_hsd_count=5 clears the default min_hsd_visits_floor (5) so
        # deworming is actually computed, not gated out.
        was = [_wa("a", approved_hsd_count=5, expected_visit_count=20, deworming_given=0)]
        candidates = ind.evaluate_run(
            was,
            {
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
                ind.DEWORMING: {"enabled": True, "threshold": 0.5},
            },
            _no_filter(),
        )
        assert len(candidates) == 1
        assert set(candidates[0]["triggered_indicators"]) == {ind.EVC_SHORTFALL, ind.DEWORMING}
        assert candidates[0]["severity_count"] == 2

    def test_ncf_floor_is_presence_not_a_ratio(self):
        was = [_wa("a", approved_ncf_count=1, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(was, {ind.NCF_INACCESSIBLE: {"enabled": True}}, _no_filter())
        assert len(candidates) == 1
        assert candidates[0]["triggered_indicators"] == [ind.NCF_INACCESSIBLE]

    def test_ncf_not_flagged_when_no_ncf_or_inaccessible_visit(self):
        was = [_wa("a", approved_ncf_count=0, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(was, {ind.NCF_INACCESSIBLE: {"enabled": True}}, _no_filter())
        assert candidates == []

    def test_ncf_detail_names_which_signal_fired(self):
        was = [_wa("a", approved_ncf_count=1, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(was, {ind.NCF_INACCESSIBLE: {"enabled": True}}, _no_filter())
        detail = candidates[0]["detail"][ind.NCF_INACCESSIBLE]
        assert detail["own_ncf_form"] is True
        assert detail["own_inaccessible_form"] is False

    def test_ncf_corroboration_is_informational_when_filter_disabled(self):
        # With the filter off, NCF's own neighbor distance/count settings
        # still compute an informational "is this part of a cluster" signal,
        # but nothing is ever dropped on account of it.
        was = [_wa("a", lat=12.0, lon=8.0, approved_ncf_count=1, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(
            was, {ind.NCF_INACCESSIBLE: {"enabled": True}}, _no_filter(min_affected_neighbors_ncf=1)
        )
        detail = candidates[0]["detail"][ind.NCF_INACCESSIBLE]
        assert detail["affected_neighbor_count"] == 0
        assert detail["corroborated"] is False
        assert len(candidates) == 1  # still a candidate -- never gated on this

    def test_evc_corroboration_is_informational_when_filter_disabled(self):
        # Filter is off, so this isolated WA still survives -- but its
        # corroboration data is still computed for display.
        was = [_wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10)]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}},
            _no_filter(evc_min_neighbor_count=3),
        )
        detail = candidates[0]["detail"][ind.EVC_SHORTFALL]
        assert detail["flagged_neighbor_count"] == 0
        assert detail["corroborated"] is False
        assert len(candidates) == 1


class TestEvaluateRunTier:
    def test_evc_only_is_tier_1(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10)]
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert candidates[0]["tier"] == 1

    def test_ncf_only_is_tier_1(self):
        was = [_wa("a", approved_ncf_count=1, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(was, {ind.NCF_INACCESSIBLE: {"enabled": True}}, _no_filter())
        assert candidates[0]["tier"] == 1

    def test_dq_only_is_tier_2(self):
        was = [_wa("a", approved_hsd_count=10, deworming_given=0)]
        candidates = ind.evaluate_run(was, {ind.DEWORMING: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert candidates[0]["tier"] == 2

    def test_evc_plus_dq_is_still_tier_1(self):
        was = [_wa("a", approved_hsd_count=1, expected_visit_count=10, deworming_given=0)]
        candidates = ind.evaluate_run(
            was,
            {
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
                ind.DEWORMING: {"enabled": True, "threshold": 0.5},
            },
            _no_filter(),
        )
        assert candidates[0]["tier"] == 1


class TestEvaluateRunClusterAwareFilter:
    """The optional post-hoc filter — only affects whether a floor-flagged WA
    SURVIVES, never prunes triggered_indicators."""

    def test_isolated_outlier_is_dropped_when_filter_enabled(self):
        # "a" has a bad rate, but no neighbors at all to corroborate it.
        was = [_wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10)]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}},
            {"cluster_aware_filter_enabled": True, "evc_neighbor_distance_m": 250, "evc_min_neighbor_count": 1},
        )
        assert candidates == []

    def test_isolated_outlier_survives_when_filter_disabled(self):
        was = [_wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10)]
        candidates = ind.evaluate_run(was, {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}}, _no_filter())
        assert len(candidates) == 1

    def test_real_cluster_survives_the_filter(self):
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("c", lat=12.001, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("d", lat=12.0015, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}},
            {"cluster_aware_filter_enabled": True, "evc_neighbor_distance_m": 250, "evc_min_neighbor_count": 3},
        )
        assert len(candidates) == 4
        assert candidates[0]["detail"][ind.EVC_SHORTFALL]["corroborated"] is True

    def test_too_few_flagged_neighbors_is_dropped(self):
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=1, expected_visit_count=10),
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5}},
            {"cluster_aware_filter_enabled": True, "evc_neighbor_distance_m": 250, "evc_min_neighbor_count": 3},
        )
        assert candidates == []

    def test_ncf_is_dropped_when_isolated_and_filter_enabled(self):
        # The filter now applies to NCF/inaccessible just like every other
        # indicator -- "a" is the only WA (no neighbors at all), so it has
        # nothing to corroborate it and gets dropped.
        was = [_wa("a", lat=12.0, lon=8.0, approved_ncf_count=1, approved_inaccessible_count=0)]
        candidates = ind.evaluate_run(
            was,
            {ind.NCF_INACCESSIBLE: {"enabled": True}},
            {"cluster_aware_filter_enabled": True, "ncf_neighbor_distance_m": 200, "min_affected_neighbors_ncf": 1},
        )
        assert candidates == []

    def test_ncf_survives_the_filter_when_neighbors_corroborate(self):
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_ncf_count=1, approved_inaccessible_count=0),
            _wa("b", lat=12.0005, lon=8.0, approved_ncf_count=1, approved_inaccessible_count=0),  # ~55m away
        ]
        candidates = ind.evaluate_run(
            was,
            {ind.NCF_INACCESSIBLE: {"enabled": True}},
            {"cluster_aware_filter_enabled": True, "ncf_neighbor_distance_m": 250, "min_affected_neighbors_ncf": 1},
        )
        assert {c["wa_id"] for c in candidates} == {"a", "b"}
        detail = next(c for c in candidates if c["wa_id"] == "a")["detail"][ind.NCF_INACCESSIBLE]
        assert detail["corroborated"] is True

    def test_wa_with_ncf_and_uncorroborated_evc_is_dropped_when_neither_corroborates(self):
        # "a" triggers on both NCF and EVC, but is the only WA -- no
        # neighbors for either indicator to corroborate against -- so the
        # whole WA is dropped now that NCF is no longer a blanket exemption.
        # approved_hsd_count=1 (not 0) so EVC's floor still applies -- a
        # zero-HSD WA is excluded from EVC entirely (see TestEvaluateRunFloor
        # ::test_zero_hsd_is_excluded_from_evc).
        was = [
            _wa(
                "a",
                lat=12.0,
                lon=8.0,
                approved_hsd_count=1,
                approved_ncf_count=1,
                approved_inaccessible_count=0,
                expected_visit_count=10,
            )
        ]
        candidates = ind.evaluate_run(
            was,
            {
                ind.NCF_INACCESSIBLE: {"enabled": True},
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
            },
            {
                "cluster_aware_filter_enabled": True,
                "ncf_neighbor_distance_m": 200,
                "min_affected_neighbors_ncf": 1,
                "evc_neighbor_distance_m": 200,
                "evc_min_neighbor_count": 1,
            },
        )
        assert candidates == []

    def test_wa_survives_via_ncf_corroboration_even_when_evc_does_not_unpruned(self):
        # "a" triggers on NCF (corroborated by "b", also NCF-affected) AND
        # EVC (isolated -- "b" isn't EVC-flagged) -- since AT LEAST ONE
        # indicator corroborates, "a" survives with BOTH indicators still
        # listed, nothing pruned (same "Option 3" rule as every indicator).
        # approved_hsd_count=1 (not 0) so EVC's floor still applies.
        was = [
            _wa(
                "a",
                lat=12.0,
                lon=8.0,
                approved_hsd_count=1,
                approved_ncf_count=1,
                approved_inaccessible_count=0,
                expected_visit_count=10,
            ),
            _wa(
                "b",
                lat=12.0005,
                lon=8.0,
                approved_hsd_count=9,
                approved_ncf_count=1,
                approved_inaccessible_count=0,
                expected_visit_count=10,
            ),
        ]
        candidates = ind.evaluate_run(
            was,
            {
                ind.NCF_INACCESSIBLE: {"enabled": True},
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
            },
            {
                "cluster_aware_filter_enabled": True,
                "ncf_neighbor_distance_m": 250,
                "min_affected_neighbors_ncf": 1,
                "evc_neighbor_distance_m": 250,
                "evc_min_neighbor_count": 3,
            },
        )
        a = next(c for c in candidates if c["wa_id"] == "a")
        assert set(a["triggered_indicators"]) == {ind.NCF_INACCESSIBLE, ind.EVC_SHORTFALL}
        assert a["detail"][ind.NCF_INACCESSIBLE]["corroborated"] is True
        assert a["detail"][ind.EVC_SHORTFALL]["corroborated"] is False

    def test_one_corroborating_indicator_keeps_the_whole_wa_unpruned(self):
        # "a" triggers on EVC (corroborated by neighbors) AND deworming
        # (isolated, no dq neighbors at all) -- since AT LEAST ONE indicator
        # corroborates, the WA survives with BOTH indicators still listed,
        # nothing pruned (the "Option 3" severity/pruning decision).
        was = [
            _wa("a", lat=12.0, lon=8.0, approved_hsd_count=5, expected_visit_count=20, deworming_given=0),
            _wa("b", lat=12.0005, lon=8.0, approved_hsd_count=5, expected_visit_count=20, deworming_given=5),
            _wa("c", lat=12.001, lon=8.0, approved_hsd_count=5, expected_visit_count=20, deworming_given=5),
        ]
        candidates = ind.evaluate_run(
            was,
            {
                ind.EVC_SHORTFALL: {"enabled": True, "threshold": 0.5},
                ind.DEWORMING: {"enabled": True, "threshold": 0.5},
            },
            {
                "cluster_aware_filter_enabled": True,
                "evc_neighbor_distance_m": 250,
                "evc_min_neighbor_count": 2,
                "tier2_neighbor_distance_m": 250,
                "tier2_min_neighbor_count": 2,
            },
        )
        a = next(c for c in candidates if c["wa_id"] == "a")
        assert set(a["triggered_indicators"]) == {ind.EVC_SHORTFALL, ind.DEWORMING}
        assert a["severity_count"] == 2
        assert a["detail"][ind.EVC_SHORTFALL]["corroborated"] is True
        assert a["detail"][ind.DEWORMING]["corroborated"] is False
