"""WA Revisit R2's selection rules (core/r2.py) -- pure functions over
synthetic per-WA rows, plus its two fetch helpers with the network mocked."""

from __future__ import annotations

from unittest import mock

from connect_labs.mopup.core import r2
from connect_labs.mopup.core.visits import aggregate_visits_by_wa


def _row(wa_id="wa-1", **overrides):
    row = {
        "wa_id": wa_id,
        "wa_name": f"WA {wa_id}",
        "wag_name": "GRP-1",
        "ward": "Kushi",
        "lga": "Shongom",
        "state": "Gombe",
        "flw_username": "submitter-1",
        "flw_name": "Submitter One",
        "owner_username": "owner-1",
        "lat": 9.46306,
        "lon": 8.00182,
        "boundary": {"type": "Polygon", "coordinates": []},
        "status": "VISITED",
        "connect_status": "VISITED",
        "building_count": 4,
        "expected_visit_count": 9,
        "rejected_hsd_count": 0,
        "rejected_ncf_count": 0,
        "owner_suspended": 0,
        "in_r1": 0,
    }
    row.update(overrides)
    return row


ALL_ON = {key: {"enabled": True} for key in r2.R2_INDICATORS}


class TestDefaults:
    def test_rejected_rule_defaults_on_for_isodaf_and_jhf_only(self):
        assert r2.default_indicator_configs(2154)[r2.REJECTED_VISIT]["enabled"] is True  # JHF
        assert r2.default_indicator_configs(2156)[r2.REJECTED_VISIT]["enabled"] is True  # ISODAF
        assert r2.default_indicator_configs(2155)[r2.REJECTED_VISIT]["enabled"] is False  # EHA
        assert r2.default_indicator_configs(2157)[r2.REJECTED_VISIT]["enabled"] is False  # SOLINA

    def test_suspended_and_inaccessible_default_on_everywhere(self):
        for opp in (2154, 2155, 2156, 2157, None):
            cfg = r2.default_indicator_configs(opp)
            assert cfg[r2.SUSPENDED_FLW]["enabled"] is True
            assert cfg[r2.MICROPLAN_INACCESSIBLE]["enabled"] is True

    def test_merge_overrides_per_key_and_drops_unknown_keys(self):
        merged = r2.merged_indicator_configs(
            {r2.REJECTED_VISIT: {"enabled": True}, "evc_shortfall": {"enabled": True, "threshold": 0.5}}, 2155
        )
        assert set(merged) == set(r2.R2_INDICATORS)
        assert merged[r2.REJECTED_VISIT]["enabled"] is True

    def test_global_config_defaults_to_excluding_r1(self):
        assert r2.merged_global_config(None) == {"exclude_r1": True}
        assert r2.merged_global_config({"exclude_r1": False, "cluster_aware_filter_enabled": True}) == {
            "exclude_r1": False
        }

    def test_r1_pairs_cover_all_four_llos(self):
        assert r2.R1_OPPORTUNITY_BY_ORIGINAL == {2154: 2304, 2155: 2305, 2156: 2291, 2157: 2306}


class TestRules:
    def test_suspended_worker_selects_even_a_completed_work_area(self):
        candidates, _ = r2.evaluate_run([_row(owner_suspended=1, status="EXPECTED_VISIT_REACHED")], ALL_ON)
        assert [c["triggered_indicators"] for c in candidates] == [[r2.SUSPENDED_FLW]]
        assert candidates[0]["detail"][r2.SUSPENDED_FLW] == {"worker": "owner-1"}

    def test_any_rejected_visit_selects_even_with_approved_visits(self):
        rows = [
            _row("wa-hsd", rejected_hsd_count=1, approved_hsd_count=12),
            _row("wa-ncf", rejected_ncf_count=1),
            _row("wa-clean", approved_hsd_count=12),
        ]
        candidates, _ = r2.evaluate_run(rows, ALL_ON)
        by_id = {c["wa_id"]: c for c in candidates}
        assert set(by_id) == {"wa-hsd", "wa-ncf"}
        assert by_id["wa-hsd"]["detail"][r2.REJECTED_VISIT] == {"rejected_hsd": 1, "rejected_ncf": 0}

    def test_inaccessible_status_selects_but_a_pending_request_does_not(self):
        rows = [
            _row("wa-in", connect_status="INACCESSIBLE"),
            _row("wa-req", connect_status="REQUEST_FOR_INACCESSIBLE"),
        ]
        candidates, _ = r2.evaluate_run(rows, ALL_ON)
        assert [c["wa_id"] for c in candidates] == ["wa-in"]

    def test_a_disabled_rule_never_selects(self):
        cfg = {**ALL_ON, r2.REJECTED_VISIT: {"enabled": False}}
        candidates, _ = r2.evaluate_run([_row(rejected_hsd_count=3)], cfg)
        assert candidates == []

    def test_several_rules_are_all_listed_in_rule_order(self):
        row = _row(owner_suspended=1, rejected_ncf_count=1, connect_status="INACCESSIBLE")
        candidates, _ = r2.evaluate_run([row], ALL_ON)
        assert candidates[0]["triggered_indicators"] == r2.R2_INDICATORS
        assert candidates[0]["severity_count"] == 3

    def test_candidate_carries_what_the_lock_and_hand_off_need(self):
        candidates, _ = r2.evaluate_run([_row(owner_suspended=1)], ALL_ON)
        c = candidates[0]
        for key in ("wa_id", "ward", "lga", "state", "boundary", "building_count", "expected_visit_count", "source"):
            assert key in c
        assert c["tier"] == 1


class TestR1Exclusion:
    def test_work_area_already_in_r1_is_left_out_and_counted(self):
        rows = [_row("wa-r1", owner_suspended=1, in_r1=1), _row("wa-new", owner_suspended=1)]
        candidates, extra = r2.evaluate_run(rows, ALL_ON)
        assert [c["wa_id"] for c in candidates] == ["wa-new"]
        assert extra == {r2.EXCLUDED_IN_R1: 1, "r1_matched": 1}

    def test_r1_match_with_no_rule_is_not_counted_as_left_out(self):
        _, extra = r2.evaluate_run([_row(in_r1=1)], ALL_ON)
        assert extra == {r2.EXCLUDED_IN_R1: 0, "r1_matched": 1}

    def test_exclusion_can_be_switched_off(self):
        candidates, extra = r2.evaluate_run([_row(owner_suspended=1, in_r1=1)], ALL_ON, {"exclude_r1": False})
        assert [c["wa_id"] for c in candidates] == ["wa-1"]
        assert candidates[0]["in_r1"] is True
        assert extra[r2.EXCLUDED_IN_R1] == 0


class TestAnnotateRows:
    def test_owner_suspended_uses_the_assigned_worker(self):
        rows = [_row(owner_username="bad"), _row("wa-2", owner_username="good")]
        r2.annotate_rows(rows, suspended_usernames={"bad"}, r1_centroid_keys=set())
        assert [r["owner_suspended"] for r in rows] == [1, 0]

    def test_falls_back_to_the_last_submitter_with_no_owner(self):
        rows = [_row(owner_username="", flw_username="bad")]
        r2.annotate_rows(rows, suspended_usernames={"bad"}, r1_centroid_keys=set())
        assert rows[0]["owner_suspended"] == 1

    def test_in_r1_matches_the_exact_centroid_despite_float_noise(self):
        # Real pair on program 217: original 2157 WA 00690d66... and R1 2306
        # WA WT6KLDU share this centroid exactly.
        lon, lat = 8.001819283466496, 9.463060048485453
        keys = {r2.centroid_key(lon, lat)}
        rows = [
            _row("same", lon=lon + 1e-12, lat=lat - 1e-12),
            # The next 50 m grid cell over is a different work area.
            _row("neighbour", lon=8.002274, lat=lat),
            _row("no-geometry", lon=None, lat=None),
        ]
        r2.annotate_rows(rows, suspended_usernames=set(), r1_centroid_keys=keys)
        assert [r["in_r1"] for r in rows] == [1, 0, 0]


class TestFetches:
    def test_fetch_suspended_usernames_reads_the_user_data_export(self):
        records = [
            {"username": "a", "suspended": True},
            {"username": "b", "suspended": False},
            {"username": "c", "suspended": "true"},
            {"username": "", "suspended": True},
            {"username": "d"},
        ]
        client = mock.MagicMock()
        client.__enter__.return_value.fetch_all.return_value = records
        with mock.patch(
            "connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client
        ) as factory:
            assert r2.fetch_suspended_usernames(2156, "tok") == {"a", "c"}
        assert factory.call_args.kwargs["opportunity_id"] == 2156
        client.__enter__.return_value.fetch_all.assert_called_once_with("/export/opportunity/2156/user_data/")

    def test_fetch_r1_centroid_keys_includes_work_areas_with_no_case_yet(self):
        class Row:
            def __init__(self, **computed):
                self.computed = computed

        class Pipeline:
            def stream_analysis_ignore_events(self, config, opportunity_id, force_refresh=False):
                self.opportunity_id = opportunity_id
                return mock.Mock(
                    rows=[
                        # Freshly uploaded R1 work area: UNASSIGNED, no case.
                        Row(wa_case_id=None, centroid='{"type": "Point", "coordinates": [8.0018, 9.4630]}'),
                        Row(wa_case_id="c-1", centroid={"type": "Point", "coordinates": [8.1, 9.5]}),
                        Row(wa_case_id=None, centroid=None),
                    ]
                )

        pipeline = Pipeline()
        keys = r2.fetch_r1_centroid_keys(2306, pipeline=pipeline)
        assert pipeline.opportunity_id == 2306
        assert keys == {r2.centroid_key(8.0018, 9.4630), r2.centroid_key(8.1, 9.5)}


class TestRejectedVisitCounts:
    def test_rejected_hsd_and_ncf_are_counted_alongside_the_existing_totals(self):
        visits = [
            {"wa_case_id": "wa-1", "form_name": "Health Service Delivery", "status": "rejected"},
            {"wa_case_id": "wa-1", "form_name": "Health Service Delivery", "status": "approved"},
            {"wa_case_id": "wa-1", "form_name": "No Children Found", "status": "rejected"},
            {"wa_case_id": "wa-2", "form_name": "No Children Found", "status": "approved"},
            {"wa_case_id": "wa-3", "form_name": "Health Service Delivery", "status": "duplicate"},
        ]
        agg = aggregate_visits_by_wa(visits)
        assert (agg["wa-1"]["rejected_hsd_count"], agg["wa-1"]["rejected_ncf_count"]) == (1, 1)
        # Unchanged for R1: a rejected visit still counts in the totals.
        assert (agg["wa-1"]["approved_hsd_count"], agg["wa-1"]["approved_ncf_count"]) == (2, 1)
        assert (agg["wa-2"]["rejected_hsd_count"], agg["wa-2"]["rejected_ncf_count"]) == (0, 0)
        assert "wa-3" not in agg
