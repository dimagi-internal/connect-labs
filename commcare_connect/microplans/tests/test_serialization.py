"""Unit tests for the extracted serialization helpers.

These exercise the plan shapers directly (no HTTP request / view), which is the
point of pulling them out of views.py.
"""

from commcare_connect.microplans import serialization


class _Plan:
    """Duck-typed stand-in for a PlanRecord (only the attrs the shapers read)."""

    def __init__(self, *, id=1, mode="coverage", name="", region="", status="draft", work_areas=None, data=None):
        self.id, self.mode, self.name, self.region, self.status = id, mode, name, region, status
        self.work_areas = work_areas or []
        self.created_at = ""
        self.data = data or {}


def _poly(x):
    return {"type": "Polygon", "coordinates": [[[x, 0], [x + 1, 0], [x + 1, 1], [x, 1], [x, 0]]]}


def test_plan_to_json_has_expected_shape():
    out = serialization.plan_to_json(_Plan(id=7, mode="coverage", work_areas=[]))
    assert out["status"] == "ok" and out["plan_id"] == 7 and out["mode"] == "coverage"
    assert set(out) >= {"work_areas", "summary", "kpis", "grouping", "assignment"}


def test_plan_summary_row_flags_unassigned_plan():
    row = serialization.plan_summary_row(_Plan(id=3, name="", region="Kano"))
    assert row["plan_id"] == 3 and row["name"] == "Plan 3" and row["region"] == "Kano"
    # No worker assignment yet → assigned is False (UI shows area count, not travel).
    assert row["assigned"] is False


def test_plan_lookup_geometry_unions_cells_when_no_input_areas():
    plan = _Plan(work_areas=[{"geometry": _poly(0)}, {"geometry": _poly(2)}])
    geom = serialization.plan_lookup_geometry(plan)
    assert geom is not None and not geom.is_empty


def test_plan_lookup_geometry_none_when_no_geometry():
    plan = _Plan(work_areas=[{"geometry": None}, {}])
    assert serialization.plan_lookup_geometry(plan) is None


def test_plan_to_json_rounds_geometry_coords():
    wa = [{"geometry": {"type": "Point", "coordinates": [13.123456789, 11.987654321]}, "building_count": 5}]
    out = serialization.plan_to_json(_Plan(work_areas=wa))
    assert out["work_areas"][0]["geometry"]["coordinates"] == [13.123457, 11.987654]
    # non-geometry properties pass through untouched
    assert out["work_areas"][0]["building_count"] == 5


def test_slim_work_areas_does_not_mutate_source():
    wa = [{"geometry": {"type": "Point", "coordinates": [13.123456789, 11.987654321]}}]
    serialization.slim_work_areas(wa)
    assert wa[0]["geometry"]["coordinates"] == [13.123456789, 11.987654321]  # source unchanged


def test_slim_work_areas_tolerates_missing_geometry():
    # rows without geometry (or non-dict) must pass through, not raise
    assert serialization.slim_work_areas([{"building_count": 1}, {"geometry": None}]) == [
        {"building_count": 1},
        {"geometry": None},
    ]
