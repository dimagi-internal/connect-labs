"""Tests for the microplans bulk-create MCP tools (enqueue / status / param schema)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

import connect_labs.mcp.tools.microplans_create  # noqa: F401 — triggers @register
from connect_labs.mcp.tool_registry import MCPToolError, get_tool

OPP = 10_091  # labs-only opp floor is 10_000
PROG = OPP


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="mc", password="p")


@pytest.fixture(autouse=True)
def _allow_access(monkeypatch):
    """Labs-only program access is gated on synthetic-opp visibility; stub it open."""
    from connect_labs.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", lambda user, opportunity_id: None)


@pytest.fixture
def synthetic_opp(db):
    """Register the backing opp so PROG resolves as labs-only (no Connect token needed)."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=OPP, label="X", program_name="X", gdrive_folder_id="f", labs_only=True
    )


@pytest.mark.django_db
def test_bulk_create_enqueues_and_returns_run_id(user, synthetic_opp, monkeypatch):
    from connect_labs.microplans import tasks

    captured = {}

    def fake_delay(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        return SimpleNamespace(id="task-xyz")

    monkeypatch.setattr(tasks.bulk_create_plans_task, "delay", fake_delay)

    out = get_tool("microplans_bulk_create_plans").handler(
        user=user,
        program_id=PROG,
        boundary_ids=["w1", "w2", "w3"],
        mode="coverage",
        cell_size_m=125,
        coverage_config={"min_confidence": 0.5, "exclude_isolated_singletons": True},
        grouping={"strategy": "bbox", "target_size": 20},
    )
    assert out["task_id"] == "task-xyz"
    assert out["run_id"].startswith("bulk-")
    assert out["n_wards"] == 3
    # The full coverage parameter surface + run_id/actor reach the task verbatim.
    kw = captured["kwargs"]
    assert kw["coverage_config"] == {"min_confidence": 0.5, "exclude_isolated_singletons": True}
    assert kw["run_id"] == out["run_id"]
    assert kw["actor"] == "mc"
    # plans_input carries one boundary per ward.
    plans_input = captured["args"][1]
    assert [p["boundary_id"] for p in plans_input] == ["w1", "w2", "w3"]


@pytest.mark.django_db
def test_bulk_create_rejects_empty_boundary_ids(user, synthetic_opp):
    with pytest.raises(MCPToolError) as exc:
        get_tool("microplans_bulk_create_plans").handler(user=user, program_id=PROG, boundary_ids=[])
    assert exc.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db
def test_bulk_create_status_maps_states(user, monkeypatch):
    import connect_labs.mcp.tools.microplans_create as mc

    # A SUCCESS result surfaces the final rows + run_id.
    fake = SimpleNamespace(
        state="SUCCESS",
        info={},
        result={
            "results": [{"boundary_id": "w1", "status": "ok", "plan_id": 7}],
            "created": 1,
            "total": 1,
            "run_id": "bulk-abc",
        },
    )
    monkeypatch.setattr(mc, "AsyncResult", lambda tid: fake, raising=False)
    # AsyncResult is imported inside the handler, so patch the celery symbol it binds.
    monkeypatch.setattr("celery.result.AsyncResult", lambda tid: fake)
    out = get_tool("microplans_bulk_create_status").handler(user=user, task_id="task-xyz")
    assert out["state"] == "completed"
    assert out["created"] == 1 and out["run_id"] == "bulk-abc"
    assert out["results"][0]["plan_id"] == 7


@pytest.mark.django_db
def test_coverage_param_schema_reflects_dataclasses(user):
    out = get_tool("microplans_coverage_param_schema").handler(user=user)
    cov_fields = {f["name"] for f in out["coverage_config"]["fields"]}
    # Every CoverageConfig knob is discoverable — not just cell_size_m.
    assert {"cell_size_m", "min_confidence", "sources", "exclude_isolated_singletons", "population"} <= cov_fields
    group_fields = {f["name"] for f in out["grouping"]["fields"]}
    assert {"strategy", "target_size", "max_buildings", "buffer_distance_m"} <= group_fields
    assert set(out["grouping"]["strategies"]) == {"bbox", "bfs_adjacency"}
    assign_fields = {f["name"] for f in out["assignment"]["fields"]}
    assert {"strategy", "workers", "restarts", "seed"} <= assign_fields
    # Defaults + help are present so the surface is self-describing.
    cell = next(f for f in out["coverage_config"]["fields"] if f["name"] == "cell_size_m")
    assert cell["default"] == 100.0 and cell["help"]


_EMPTY = {"type": "FeatureCollection", "features": []}


def _coverage_cell():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 0.001], [0.001, 0.001], [0, 0]]]},
                "properties": {
                    "building_count": 4,
                    "expected_visit_count": 4,
                    "cluster": "0-0",
                    "area_id": 0,
                    "ward": "W",
                    "cell_size_m": 100,
                    "roof_area_m2": 120.0,
                    "dist_to_multi_m": 0.0,
                },
            }
        ],
    }


@pytest.mark.django_db
def test_coverage_capture_round_trips_through_read_tools(user):
    """Real LabsRecord round-trip: create_plan persists coverage_config/coverage_stats/
    run_meta and the read tools surface them (closes the mocked-DA gap)."""
    import connect_labs.mcp.tools.microplans  # noqa: F401 — register read tools
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

    SyntheticOpportunity.objects.create(
        opportunity_id=OPP, label="X", program_name="X", gdrive_folder_id="f", labs_only=True
    )
    da = ProgramPlanDataAccess(PROG, access_token="labs-local")
    stats = [{"work_areas": 1, "retained_buildings": 4, "per_area": [{"ward": "W", "work_areas": 1}]}]
    plan = da.create_plan(
        region="W",
        name="W",
        mode="coverage",
        pins=_EMPTY,
        hulls=_coverage_cell(),
        coverage_config={"cell_size_m": 100, "min_confidence": 0.5},
        coverage_stats=stats,
        run_meta={"run_id": "bulk-rt", "index": 0},
    )

    out = get_tool("microplans_plan_work_areas").handler(user=user, program_id=PROG, plan_id=plan.id)
    assert out["coverage_config"] == {"cell_size_m": 100, "min_confidence": 0.5}
    assert out["coverage_stats"] == stats
    assert out["run_meta"]["run_id"] == "bulk-rt"

    listed = get_tool("microplans_list_plans").handler(user=user, program_id=PROG)
    assert listed["plans"][0]["run_id"] == "bulk-rt"
