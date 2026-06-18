"""Unit tests for build_plan_snapshot — no DB, fed a fake data-access object."""
import pytest

from commcare_connect.microplans.core.solicitation_snapshot import build_plan_snapshot


class _FakePlan:
    def __init__(self, pid, name, region, input_areas, work_areas):
        self.id = pid
        self.name = name
        self.region = region
        self.work_areas = work_areas
        self.data = {"input_areas": input_areas}


class _FakeGroup:
    def __init__(self, name, plan_ids):
        self.name = name
        self.plan_ids = plan_ids


class _FakeDA:
    def __init__(self, program_id, plans, group=None):
        self.program_id = program_id
        self._plans = {p.id: p for p in plans}
        self._group = group

    def list_plans(self):
        return list(self._plans.values())

    def get_plan(self, plan_id):
        return self._plans[plan_id]

    def get_group(self, group_id):
        return self._group


def _plan(pid, name="Ward", region="Lagos"):
    return _FakePlan(
        pid,
        name,
        region,
        input_areas=[
            {"name": "North", "arm": "intervention"},
            {"name": "South", "arm": "control"},
        ],
        work_areas=[{"id": "wa1"}, {"id": "wa2"}, {"id": "wa3"}],
    )


def test_group_snapshot_lists_every_member_plan():
    plans = [_plan(1, "Ikorodu"), _plan(2, "Ikeja")]
    da = _FakeDA(25, plans, group=_FakeGroup("Lagos Study", [1, 2]))
    snap = build_plan_snapshot(da, group_id=88)

    assert snap["source_program_id"] == 25
    assert snap["source_group_id"] == 88
    assert snap["source_plan_ids"] == [1, 2]
    assert [p["plan_id"] for p in snap["plans"]] == [1, 2]
    first = snap["plans"][0]
    assert first["name"] == "Ikorodu"
    assert first["region"] == "Lagos"
    assert first["wards"] == ["North", "South"]
    assert sorted(first["arms"]) == ["control", "intervention"]
    assert first["work_area_count"] == 3
    assert "Lagos Study" in snap["suggested_title"]


def test_single_plan_snapshot_has_null_group():
    da = _FakeDA(25, [_plan(1, "Ikorodu")])
    snap = build_plan_snapshot(da, plan_id=1)
    assert snap["source_group_id"] is None
    assert snap["source_plan_ids"] == [1]
    assert [p["plan_id"] for p in snap["plans"]] == [1]
    assert "Ikorodu" in snap["suggested_title"]


def test_requires_exactly_one_selector():
    da = _FakeDA(25, [_plan(1)])
    with pytest.raises(ValueError):
        build_plan_snapshot(da)
    with pytest.raises(ValueError):
        build_plan_snapshot(da, group_id=1, plan_id=1)


def test_arms_omitted_when_single_arm():
    p = _FakePlan(1, "Solo", "Lagos", input_areas=[{"name": "Only"}], work_areas=[])
    da = _FakeDA(25, [p])
    snap = build_plan_snapshot(da, plan_id=1)
    entry = snap["plans"][0]
    assert "arms" not in entry
    assert entry["work_area_count"] == 0
    assert entry["wards"] == ["Only"]
