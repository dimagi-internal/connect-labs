"""Tests for planning-phase work-area editing (pure; no DB)."""

from __future__ import annotations

from commcare_connect.microplans.core import plan

# a 2-cluster coverage frame (hulls)
_HULLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.0, 6.0], [3.1, 6.0], [3.1, 6.1], [3.0, 6.1], [3.0, 6.0]]],
            },
            "properties": {"arm": "coverage", "cluster": "C0", "building_count": 100, "expected_visit_count": 100},
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.2, 6.0], [3.3, 6.0], [3.3, 6.1], [3.2, 6.1], [3.2, 6.0]]],
            },
            "properties": {"arm": "coverage", "cluster": "C1", "building_count": 80, "expected_visit_count": 80},
        },
    ],
}
_EMPTY = {"type": "FeatureCollection", "features": []}


def _materialize():
    return plan.materialize_work_areas("coverage", _EMPTY, _HULLS)


class TestMaterialize:
    def test_one_work_area_per_cluster(self):
        was = _materialize()
        assert len(was) == 2
        w = was[0]
        assert w["status"] == plan.STATUS_UNASSIGNED
        # Coverage cells are auto-bucketed into spatial super-cells at materialize
        # time so the LLO sees groups out of the box (instead of "every cell is in
        # the same default group"). With only 2 cells the super-grid is 1×1 → both
        # land in "group-1".
        assert w["work_area_group"].startswith("group-")
        assert w["opportunity_access"] is None
        assert w["building_count"] == 100 and w["expected_visit_count"] == 100
        assert w["audit"] == []
        assert 3.0 <= w["centroid"][0] <= 3.1  # centroid inside the polygon
        assert len({w["id"] for w in was}) == 2  # ids unique

    def test_sampling_pin_defaults_one_visit(self):
        pins = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [3.05, 6.05]},
                    "properties": {"arm": "intervention", "cluster": "C0", "role": "primary", "order_in_cluster": 1},
                },
            ],
        }
        was = plan.materialize_work_areas("sampling", pins, _EMPTY)
        assert len(was) == 1 and was[0]["expected_visit_count"] == 1


class TestActions:
    def test_exclude_sets_fields_and_audits_phase_planning(self):
        wa = _materialize()[0]
        plan.apply_action(
            wa, "exclude", {"reason": "lake, not a settlement"}, actor="llo_user", now="2026-05-28T00:00:00Z"
        )
        assert wa["status"] == plan.STATUS_EXCLUDED
        assert wa["excluded_reason"] == "lake, not a settlement"
        assert wa["excluded_by"] == "llo_user"
        assert len(wa["audit"]) == 1
        ev = wa["audit"][0]
        assert ev["phase"] == "planning" and ev["actor"] == "llo_user" and ev["action"] == "exclude"
        # audit mirrors Connect pghistory: old->new over the tracked fields that changed
        assert ev["changes"]["status"] == ["UNASSIGNED", "EXCLUDED"]
        assert ev["changes"]["excluded_reason"] == ["", "lake, not a settlement"]
        assert set(ev["changes"]).issubset(set(plan.TRACKED_FIELDS))

    def test_resize_regroup_reassign_audit(self):
        wa = _materialize()[0]
        plan.apply_action(wa, "resize", {"expected_visit_count": 60}, "u")
        plan.apply_action(wa, "regroup", {"work_area_group": "north"}, "u")
        plan.apply_action(wa, "reassign", {"opportunity_access": "flw-7"}, "u")
        assert wa["expected_visit_count"] == 60
        assert wa["work_area_group"] == "north"
        assert wa["opportunity_access"] == "flw-7"
        assert [e["action"] for e in wa["audit"]] == ["resize", "regroup", "reassign"]
        assert wa["audit"][0]["changes"]["expected_visit_count"] == [100, 60]
        assert wa["audit"][2]["changes"]["opportunity_access"] == [None, "flw-7"]

    def test_noop_edit_records_no_audit(self):
        wa = _materialize()[0]
        # Re-set the work_area_group to its current auto-assigned value: no change → no audit.
        plan.apply_action(wa, "regroup", {"work_area_group": wa["work_area_group"]}, "u")
        assert wa["audit"] == []

    def test_unexclude_clears(self):
        wa = _materialize()[0]
        plan.apply_action(wa, "exclude", {"reason": "x"}, "u")
        plan.apply_action(wa, "unexclude", {}, "u")
        assert wa["status"] == plan.STATUS_UNASSIGNED and wa["excluded_reason"] == "" and wa["excluded_by"] == ""

    def test_unknown_action_raises(self):
        import pytest

        with pytest.raises(ValueError):
            plan.apply_action(_materialize()[0], "nuke", {}, "u")


class TestSummaryAndExport:
    def test_summary_loads_and_excludes(self):
        was = _materialize()
        plan.apply_action(was[0], "reassign", {"opportunity_access": "flw-1"}, "u")
        plan.apply_action(was[1], "exclude", {"reason": "invalid"}, "u")
        s = plan.summarize(was)
        assert s["total"] == 2 and s["active"] == 1 and s["excluded"] == 1
        assert s["buildings_active"] == 100  # the excluded 80 dropped
        assert s["by_worker"]["flw-1"]["work_areas"] == 1
        assert s["by_worker"]["flw-1"]["buildings"] == 100

    def test_export_skips_excluded_and_uses_edits(self):
        was = _materialize()
        plan.apply_action(was[0], "regroup", {"work_area_group": "ward-A"}, "u")
        plan.apply_action(was[0], "resize", {"expected_visit_count": 55}, "u")
        plan.apply_action(was[1], "exclude", {"reason": "invalid"}, "u")
        payloads = plan.to_workarea_payloads(was, lga="Eti Osa", state="Lagos")
        assert len(payloads) == 1  # excluded one dropped
        p = payloads[0]
        assert p.ward == "ward-A"  # group -> ward
        assert p.expected_visit_count == 55
        assert "POLYGON" in p.boundary_wkt
        assert p.case_properties["lga"] == "Eti Osa"


class TestHardening:
    def test_resize_rejects_non_numeric(self):
        import pytest

        wa = _materialize()[0]
        with pytest.raises(ValueError):
            plan.apply_action(wa, "resize", {"expected_visit_count": "abc"}, "u")
        with pytest.raises(ValueError):
            plan.apply_action(wa, "resize", {}, "u")  # missing key

    def test_string_fields_capped(self):
        wa = _materialize()[0]
        plan.apply_action(wa, "exclude", {"reason": "x" * 900}, "u")
        plan.apply_action(wa, "regroup", {"work_area_group": "g" * 400}, "u")
        assert len(wa["excluded_reason"]) == 500  # Connect max_length
        assert len(wa["work_area_group"]) == 255


class TestKpis:
    def _poly(self, lon, lat, d=0.01):
        return {
            "type": "Polygon",
            "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]],
        }

    def _was(self):
        # 3 areas; we'll assign two to flw-A (far apart) and one to flw-B
        feats = [
            {
                "type": "Feature",
                "geometry": self._poly(3.0, 6.0),
                "properties": {"arm": "coverage", "cluster": "C0", "building_count": 100, "expected_visit_count": 100},
            },
            {
                "type": "Feature",
                "geometry": self._poly(3.5, 6.0),
                "properties": {"arm": "coverage", "cluster": "C1", "building_count": 90, "expected_visit_count": 90},
            },
            {
                "type": "Feature",
                "geometry": self._poly(3.05, 6.0),
                "properties": {"arm": "coverage", "cluster": "C2", "building_count": 60, "expected_visit_count": 60},
            },
        ]
        return plan.materialize_work_areas(
            "coverage", {"type": "FeatureCollection", "features": []}, {"type": "FeatureCollection", "features": feats}
        )

    def test_dimension_falls_back_to_group_before_assignment(self):
        # Coverage cells auto-group via BFS adjacency at materialize time. The
        # three test cells are >50km apart, well beyond the 100m buffer, so they
        # each land in their own group.
        k = plan.plan_kpis(self._was())
        assert k["dimension"] == "group"  # nothing assigned yet
        assert all(t["name"].startswith("group-") for t in k["territories"])
        assert k["plan"]["territory_count"] == 3

    def test_per_worker_spread_is_territory_diameter(self):
        was = self._was()
        # flw-A gets C0 (lon 3.0) + C1 (lon 3.5) -> ~55km apart; flw-B gets C2
        plan.apply_action(was[0], "reassign", {"opportunity_access": "flw-A"}, "u")
        plan.apply_action(was[1], "reassign", {"opportunity_access": "flw-A"}, "u")
        plan.apply_action(was[2], "reassign", {"opportunity_access": "flw-B"}, "u")
        k = plan.plan_kpis(was)
        assert k["dimension"] == "worker"
        terr = {t["name"]: t for t in k["territories"]}
        assert terr["flw-A"]["spread_km"] > 40  # 0.5deg lon at ~6N ~ 55km
        assert terr["flw-B"]["spread_km"] == 0.0  # single area -> diameter 0
        assert k["plan"]["max_spread_km"] == terr["flw-A"]["spread_km"]
        assert terr["flw-A"]["buildings"] == 190 and terr["flw-B"]["buildings"] == 60

    def test_population_balance_and_exclusion(self):
        was = self._was()
        for w in was:
            w["population"] = {"c0": 1000, "c1": 500, "c2": 300}.get(w["properties"]["cluster"].lower(), 0)
        plan.apply_action(was[0], "reassign", {"opportunity_access": "A"}, "u")
        plan.apply_action(was[1], "reassign", {"opportunity_access": "B"}, "u")
        plan.apply_action(was[2], "exclude", {"reason": "lake"}, "u")
        k = plan.plan_kpis(was)
        assert k["plan"]["has_population"] is True
        # active pops: A=1000, B=500; target=750 -> imbalance=(1000-500)/750*100
        assert k["plan"]["pop_imbalance_pct"] == round((1000 - 500) / 750 * 100, 1)
        assert k["excluded"]["count"] == 1 and k["excluded"]["buildings"] == 60
        # coverage = active buildings / (active+excluded) = 190/250
        assert k["coverage_pct"] == round(100 * 190 / 250, 1)


class TestHaversine:
    def test_identical_is_zero_and_known_distance(self):
        assert plan._haversine_km([3.0, 6.0], [3.0, 6.0]) == 0.0
        # 1 degree of latitude ~ 111 km
        d = plan._haversine_km([3.0, 6.0], [3.0, 7.0])
        assert 110 < d < 112

    def test_diameter_no_domain_error_on_dense_points(self):
        cents = [[3.0 + i * 1e-7, 6.0] for i in range(50)]  # near-identical points
        assert plan._territory_diameter_km(cents) >= 0.0  # must not raise math domain error


class TestLifecycle:
    def test_valid_path_draft_to_deployed(self):
        d = {"status": plan.PLAN_DRAFT}
        plan.transition_plan(d, plan.PLAN_IN_REVIEW, "u")
        plan.transition_plan(d, plan.PLAN_APPROVED, "u")
        plan.transition_plan(d, plan.PLAN_DEPLOYED, "u", opportunity_id=1882)
        assert d["status"] == plan.PLAN_DEPLOYED and d["opportunity_id"] == 1882
        assert [e["to"] for e in d["status_log"]] == ["in_review", "approved", "deployed"]
        assert d["status_log"][-1]["phase"] == "deploy"
        assert d["status_log"][0]["phase"] == "planning"

    def test_illegal_transition_raises(self):
        import pytest

        d = {"status": plan.PLAN_DRAFT}
        with pytest.raises(ValueError):
            plan.transition_plan(d, plan.PLAN_DEPLOYED, "u", opportunity_id=1)  # draft can't jump to deployed

    def test_deploy_requires_opportunity(self):
        import pytest

        d = {"status": plan.PLAN_APPROVED}
        with pytest.raises(ValueError):
            plan.transition_plan(d, plan.PLAN_DEPLOYED, "u")  # no opp bound

    def test_archive_and_restore(self):
        d = {"status": plan.PLAN_IN_REVIEW}
        plan.transition_plan(d, plan.PLAN_ARCHIVED, "u")
        assert d["status"] == plan.PLAN_ARCHIVED
        plan.transition_plan(d, plan.PLAN_DRAFT, "u")
        assert d["status"] == plan.PLAN_DRAFT


class TestRecordModels:
    """Exercise the REAL proxy-record construction path (no mocking) — the gap that
    let two prod-only bugs ship: `.to_dict()` (should be `.to_api_dict()`) and a
    read-only `opportunity_id`/`program_id` property shadowing the base instance
    attribute (broke instantiation entirely)."""

    def _plan_api_data(self):
        # Shape returned by the production /export/labs_record/ API for a program-scoped
        # plan: top-level opportunity_id is null (scoped by program_id); the deploy-bound
        # opp lives inside data.
        return {
            "id": 501,
            "experiment": "133",
            "type": "microplan_plan",
            "opportunity_id": None,
            "program_id": 133,
            "data": {
                "status": "deployed",
                "region": "Kano North LGA",
                "name": "Kano North v2",
                "mode": "coverage",
                "work_areas": [{"id": "cov-c0", "status": "UNASSIGNED"}],
                "opportunity_id": "1742",
                "status_log": [{"to": "deployed"}],
            },
        }

    def test_plan_record_instantiates_from_api_data(self):
        from commcare_connect.microplans.core.models import RooftopPlanRecord

        rec = RooftopPlanRecord(self._plan_api_data())  # must not raise (property-shadow regression)
        assert rec.id == 501
        assert rec.program_id == 133  # base instance attr, not shadowed
        assert rec.opportunity_id is None  # base record-level field
        assert rec.data.get("opportunity_id") == "1742"  # deploy-bound opp lives in data
        assert rec.status == "deployed" and rec.region == "Kano North LGA"
        assert len(rec.work_areas) == 1

    def test_plan_record_round_trips_through_to_api_dict(self):
        # data_access constructs typed records via `record.to_api_dict()`; verify the
        # LocalLabsRecord → to_api_dict() → RooftopPlanRecord round-trip works.
        from commcare_connect.labs.models import LocalLabsRecord
        from commcare_connect.microplans.core.models import RooftopPlanRecord

        base = LocalLabsRecord(self._plan_api_data())
        rec = RooftopPlanRecord(base.to_api_dict())
        assert rec.id == 501 and rec.program_id == 133 and rec.status == "deployed"

    def test_group_record_instantiates_from_api_data(self):
        from commcare_connect.labs.models import LocalLabsRecord
        from commcare_connect.microplans.core.models import RooftopPlanGroupRecord

        api_data = {
            "id": 9,
            "experiment": "133",
            "type": "microplan_plan_group",
            "opportunity_id": None,
            "program_id": 133,
            "data": {"name": "For Hilltop Health", "plan_ids": [1, 2], "offered_to": "Hilltop", "shared": True},
        }
        rec = RooftopPlanGroupRecord(api_data)  # must not raise
        assert rec.program_id == 133 and rec.name == "For Hilltop Health"
        assert rec.plan_ids == [1, 2] and rec.shared is True
        # round-trip too
        rec2 = RooftopPlanGroupRecord(LocalLabsRecord(api_data).to_api_dict())
        assert rec2.plan_ids == [1, 2]
