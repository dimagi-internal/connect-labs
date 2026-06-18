"""Tests for the microplans read MCP tools against a labs-only program."""

from __future__ import annotations

import pytest

import commcare_connect.mcp.tools.microplans  # noqa: F401 — trigger @register
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.tool_registry import get_tool
from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

OPP = 10_088  # labs-only opp floor is 10_000
PROG = OPP  # labs-only program id IS the backing opp id (positive, >= floor)


@pytest.fixture(autouse=True)
def _allow_access(monkeypatch):
    from commcare_connect.mcp.tools import synthetic as syn

    monkeypatch.setattr(syn, "_require_opportunity_access", lambda user, opportunity_id: None)


def _pt(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def _pins():
    feats = []
    for i in range(3):  # 3 primaries in cluster C0
        feats.append(
            _pt(
                8.0 + 0.001 * i,
                9.0,
                {"sample_type": "primary", "cluster": "C0", "order_in_cluster": i + 1, "arm": "intervention"},
            )
        )
    for i in range(2):  # 2 alternates in cluster C0
        feats.append(
            _pt(
                8.0 + 0.001 * i,
                9.001,
                {"sample_type": "alternate", "cluster": "C0", "order_in_cluster": i + 1, "arm": "intervention"},
            )
        )
    return {"type": "FeatureCollection", "features": feats}


_EMPTY = {"type": "FeatureCollection", "features": []}


@pytest.fixture
def study(db):
    SyntheticOpportunity.objects.create(
        opportunity_id=OPP, label="VM", program_name="VM Synthetic", gdrive_folder_id="f", labs_only=True
    )
    da = ProgramPlanDataAccess(PROG, access_token="labs-local")
    tse = da.create_plan(region="Tse", name="Tse", mode="sampling", pins=_pins(), hulls=_EMPTY)
    danto = da.create_plan(region="Danto", name="Danto", mode="sampling", pins=_pins(), hulls=_EMPTY)
    group = da.create_group(
        name="R1 — Tse × Danto",
        plan_ids=[tse.id, danto.id],
        kind="study",
        arms={str(tse.id): "intervention", str(danto.id): "control"},
    )
    return {"tse": tse.id, "danto": danto.id, "group": group.id}


@pytest.mark.django_db
def test_list_plans_returns_plans_and_group_arms(study, user):
    out = get_tool("microplans_list_plans").handler(user=user, program_id=PROG)
    assert {p["name"] for p in out["plans"]} == {"Tse", "Danto"}
    assert {p["region"] for p in out["plans"]} == {"Tse", "Danto"}
    assert all(p["phase"] == "sampled" for p in out["plans"])
    assert len(out["groups"]) == 1
    g = out["groups"][0]
    assert g["kind"] == "study"
    assert set(g["plan_ids"]) == {study["tse"], study["danto"]}
    assert g["arm_for"][str(study["tse"])] == "intervention"
    assert g["arm_for"][str(study["danto"])] == "control"


@pytest.mark.django_db
def test_plan_work_areas_are_compact_and_carry_sample_type(study, user):
    out = get_tool("microplans_plan_work_areas").handler(user=user, program_id=PROG, plan_id=study["tse"])
    was = out["work_areas"]
    assert out["n"] == 5  # 3 primary + 2 alternate
    types = sorted(w["sample_type"] for w in was)
    assert types == ["alternate", "alternate", "primary", "primary", "primary"]
    for w in was:
        assert w["cluster"] == "C0"
        assert isinstance(w["lon"], float) and isinstance(w["lat"], float)
        assert w["order_in_cluster"] is not None


@pytest.mark.django_db
def test_delete_plan_removes_it(study, user):
    out = get_tool("microplans_delete_plan").handler(user=user, program_id=PROG, plan_id=study["tse"])
    assert out == {"program_id": PROG, "plan_id": study["tse"], "deleted": True}
    remaining = {p["id"] for p in get_tool("microplans_list_plans").handler(user=user, program_id=PROG)["plans"]}
    assert study["tse"] not in remaining
    assert study["danto"] in remaining


@pytest.mark.django_db
def test_delete_plan_refuses_plan_not_in_program(study, user):
    from commcare_connect.mcp.tool_registry import MCPToolError

    with pytest.raises(MCPToolError):
        get_tool("microplans_delete_plan").handler(user=user, program_id=PROG, plan_id=999_999)


@pytest.mark.django_db
def test_delete_group_removes_container_not_member_plans(study, user):
    out = get_tool("microplans_delete_group").handler(user=user, program_id=PROG, group_id=study["group"])
    assert out == {"program_id": PROG, "group_id": study["group"], "deleted": True}
    listed = get_tool("microplans_list_plans").handler(user=user, program_id=PROG)
    assert listed["groups"] == []
    # member plans survive the group delete
    assert {p["id"] for p in listed["plans"]} == {study["tse"], study["danto"]}


@pytest.mark.django_db
def test_transition_plan_advances_status(study, user):
    out = get_tool("microplans_transition_plan").handler(
        user=user, program_id=PROG, plan_id=study["tse"], to="in_review"
    )
    assert out["plan_status"] == "in_review"
    out = get_tool("microplans_transition_plan").handler(
        user=user, program_id=PROG, plan_id=study["tse"], to="approved"
    )
    assert out["plan_status"] == "approved"


@pytest.mark.django_db
def test_transition_plan_rejects_illegal_jump(study, user):
    from commcare_connect.mcp.tool_registry import MCPToolError

    # draft -> approved skips in_review: illegal
    with pytest.raises(MCPToolError):
        get_tool("microplans_transition_plan").handler(user=user, program_id=PROG, plan_id=study["tse"], to="approved")


@pytest.mark.django_db
def test_transition_deploy_requires_opportunity_id(study, user):
    from commcare_connect.mcp.tool_registry import MCPToolError

    for to in ("in_review", "approved"):
        get_tool("microplans_transition_plan").handler(user=user, program_id=PROG, plan_id=study["tse"], to=to)
    with pytest.raises(MCPToolError):
        get_tool("microplans_transition_plan").handler(user=user, program_id=PROG, plan_id=study["tse"], to="deployed")
