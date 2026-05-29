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
        assert w["work_area_group"] == "coverage"  # default group = arm
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
        plan.apply_action(wa, "regroup", {"work_area_group": "coverage"}, "u")  # same as default
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
