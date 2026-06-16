"""View tests for the microplans setup flow.

Cold map generation (Overture S3 fetch + clustering) is offloaded to Celery —
the preview views validate synchronously then enqueue, returning 202
{task_id, poll_url}; PreviewStatusView reports progress/result. So the view
tests assert the auth gate, synchronous payload/config validation, and the
enqueue envelope; the generation work itself is exercised against the task
functions (with generate_frame/generate_coverage_frame patched out — they hit
Overture S3, which isn't a unit-test dependency); and the lifecycle mapping is
exercised against PreviewStatusView with AsyncResult mocked.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from django.urls import reverse

from commcare_connect.microplans.sampling.frame import FrameResult

pytestmark = pytest.mark.django_db


def _login(client, django_user_model):
    user = django_user_model.objects.create(username="tester", email="t@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()
    return user


# --- synchronous request validation (runs before anything is enqueued) --------


def test_preview_rejects_empty_areas(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": [], "config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_preview_rejects_malformed_body(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data="not json",
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_preview_bad_config_is_400_not_500(client, django_user_model):
    # A non-numeric config value must surface as 400 (config is validated in the
    # request-validation try, before enqueue), not crash or become a failed task.
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [{"arm": "intervention", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
                "config": {"target_clusters": "abc"},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_preview_coverage_bad_config_is_400(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:preview_coverage", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [{"geometry": {"type": "Point", "coordinates": [0, 0]}}],
                "config": {"cell_size_m": "abc"},  # non-numeric cell size → 400
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 400


# --- enqueue: a valid request returns 202 + a pollable task id -----------------


def _fake_delay(task_id):
    return lambda *a, **k: SimpleNamespace(id=task_id)


def test_preview_frame_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import generate_frame_task

    monkeypatch.setattr(generate_frame_task, "delay", _fake_delay("frame-task-1"))
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [
                    {
                        "arm": "intervention",
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                    }
                ],
                "config": {"target_clusters": 1},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["task_id"] == "frame-task-1"
    assert "frame-task-1" in body["poll_url"]


def test_compare_surrounding_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import compare_surrounding_wards_task

    monkeypatch.setattr(compare_surrounding_wards_task, "delay", _fake_delay("compare-task-1"))
    resp = client.post(
        reverse("microplans:compare_surrounding", kwargs={"opp_id": 123}),
        data=json.dumps({"selected": {"boundary_id": "NGA-ward-1", "name": "Attakar"}, "config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["task_id"] == "compare-task-1"
    assert reverse("microplans:compare_surrounding_status", kwargs={"task_id": "compare-task-1"}) == body["poll_url"]


def test_compare_surrounding_rejects_missing_boundary(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:compare_surrounding", kwargs={"opp_id": 123}),
        data=json.dumps({"selected": {}, "config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_preview_coverage_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import generate_coverage_task

    monkeypatch.setattr(generate_coverage_task, "delay", _fake_delay("coverage-task-1"))
    resp = client.post(
        reverse("microplans:preview_coverage", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}],
                "config": {"strategy": "balanced"},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "coverage-task-1"
    assert "coverage-task-1" in body["poll_url"]


def test_preview_footprints_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import fetch_footprints_task

    monkeypatch.setattr(fetch_footprints_task, "delay", _fake_delay("fp-task-1"))
    resp = client.post(
        reverse("microplans:preview_footprints", kwargs={"opp_id": 123}),
        data=json.dumps(
            {"areas": [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 202
    assert resp.json()["task_id"] == "fp-task-1"


# --- the task bodies produce the same response envelope the views used to ------


def test_generate_frame_task_returns_envelope(monkeypatch):
    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)
    fake = FrameResult(
        pins_geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [13.1, 11.8]},
                    "properties": {"sample_type": "primary", "cluster": "C0", "arm": "intervention"},
                }
            ],
        },
        hulls_geojson={"type": "FeatureCollection", "features": []},
        stats=[{"arm": "intervention", "psus_selected": 1}],
    )
    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", lambda areas, config: fake)
    from commcare_connect.microplans.tasks import generate_frame_task

    out = generate_frame_task.run([{"arm": "intervention"}], {"target_clusters": 1})
    assert out["status"] == "ok"
    assert out["pins"]["features"][0]["properties"]["sample_type"] == "primary"
    assert out["stats"][0]["psus_selected"] == 1


def test_generate_frame_task_maps_value_error_to_envelope(monkeypatch):
    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)

    def too_big(*a, **k):
        raise ValueError("Area is too large (~5,000 km²); draw a smaller area.")

    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", too_big)
    from commcare_connect.microplans.tasks import generate_frame_task

    out = generate_frame_task.run([{"arm": "intervention"}], {})
    assert out["status"] == "error"
    assert "too large" in out["detail"]


def test_generate_frame_task_propagates_unexpected(monkeypatch):
    # An unexpected failure must NOT be swallowed — it propagates so Celery marks
    # the task FAILURE and the status view returns a generic message.
    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)

    def boom(*a, **k):
        raise RuntimeError("overture down")

    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", boom)
    from commcare_connect.microplans.tasks import generate_frame_task

    with pytest.raises(RuntimeError):
        generate_frame_task.run([{"arm": "intervention"}], {})


def test_fetch_footprints_task_emits_polygons_with_centroid_fallback(monkeypatch):
    # The building-overlay preview must return the real footprint POLYGON when the
    # cache has it (with_geom=True), only falling back to a centroid Point for rows
    # with no stored geometry. Regression: it used to always emit Points, so the
    # new-plan overlay showed dots instead of buildings.
    import pandas as pd

    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)
    poly = {"type": "Polygon", "coordinates": [[[0, 0], [0.001, 0], [0.001, 0.001], [0, 0]]]}
    df = pd.DataFrame(
        [
            {"lon": 0.0005, "lat": 0.0005, "area_m2": 80, "confidence": 0.9, "dataset": "x", "geom_json": poly},
            {"lon": 0.002, "lat": 0.002, "area_m2": 60, "confidence": 0.8, "dataset": "x", "geom_json": None},
        ]
    )
    captured = {}

    def fake_fetch(geom, **kwargs):
        captured.update(kwargs)
        return df

    monkeypatch.setattr("commcare_connect.microplans.core.footprints.fetch_buildings", fake_fetch)
    from commcare_connect.microplans.tasks import fetch_footprints_task

    out = fetch_footprints_task.run(
        [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}]
    )
    assert captured.get("with_geom") is True  # asked the cache for real geometry
    feats = out["footprints"]["features"]
    assert feats[0]["geometry"]["type"] == "Polygon"  # real footprint
    assert feats[1]["geometry"]["type"] == "Point"  # centroid fallback for no-geom row
    assert out["count"] == 2


def test_generate_coverage_task_returns_envelope(monkeypatch):
    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)
    from commcare_connect.microplans.coverage.frame import CoverageFrameResult

    fake = CoverageFrameResult(
        areas_geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": []},
                    "properties": {"building_count": 42, "expected_visit_count": 42},
                }
            ],
        },
        stats=[{"arm": "coverage", "strategy": "balanced", "work_areas": 1}],
    )
    monkeypatch.setattr(
        "commcare_connect.microplans.coverage.frame.generate_coverage_frame", lambda areas, config: fake
    )
    from commcare_connect.microplans.tasks import generate_coverage_task

    out = generate_coverage_task.run([{"arm": "coverage"}], {"strategy": "balanced"})
    assert out["status"] == "ok"
    assert out["areas"]["features"][0]["properties"]["building_count"] == 42
    assert out["stats"][0]["strategy"] == "balanced"


# --- status polling maps Celery lifecycle to a stable client contract ----------


def _patch_async_result(monkeypatch, fake):
    # The view does `from celery.result import AsyncResult` at call time, so
    # patching the source symbol is enough.
    monkeypatch.setattr("celery.result.AsyncResult", lambda task_id: fake)


def test_preview_status_requires_login(client):
    resp = client.get(reverse("microplans:preview_status", kwargs={"task_id": "abc"}))
    assert resp.status_code in (302, 403)


def test_preview_status_running(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _patch_async_result(
        monkeypatch,
        SimpleNamespace(state="PROGRESS", info={"message": "Fetching building footprints…"}, result=None),
    )
    resp = client.get(reverse("microplans:preview_status", kwargs={"task_id": "abc"}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running"
    assert "Fetching" in body["message"]


def test_preview_status_completed_passes_envelope(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    envelope = {"status": "ok", "areas": {"type": "FeatureCollection", "features": []}, "stats": []}
    _patch_async_result(monkeypatch, SimpleNamespace(state="SUCCESS", info=envelope, result=envelope))
    resp = client.get(reverse("microplans:preview_status", kwargs={"task_id": "abc"}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "completed"
    assert body["result"]["status"] == "ok"


def test_preview_status_failed_hides_internals(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    err = RuntimeError("overture down — secret internals")
    _patch_async_result(monkeypatch, SimpleNamespace(state="FAILURE", info=err, result=err))
    resp = client.get(reverse("microplans:preview_status", kwargs={"task_id": "abc"}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "failed"
    assert "overture down" not in body["detail"]
    assert "server logs" in body["detail"].lower()


# ---- planning-phase plan review/edit endpoints ----

_HULL_FC = {
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
_EMPTY_FC = {"type": "FeatureCollection", "features": []}


# ============================================================================
# Program layer: portfolio workspace, program-scoped creation/review, lifecycle
# transitions, and plan groups. Fakes run the real plan_lib logic over a store.
# ============================================================================


class _FakeProgramPlan:
    def __init__(
        self,
        pid,
        mode,
        work_areas,
        name="",
        region="",
        status="draft",
        opportunity_id=None,
        lga="",
        state="",
        input_areas=None,
        sampling_stats=None,
        psu_hulls=None,
    ):
        self.id, self.mode, self.work_areas = pid, mode, work_areas
        self.name = name or f"Plan {pid}"
        self.region, self.status, self.opportunity_id = region, status, opportunity_id
        self.lga, self.state = (lga or region), state
        self.status_log = []
        self.created_at = "2026-05-28T00:00:00Z"
        # Real backing dict for `data` so tests can mutate e.g. plan.data["grouping"]
        # and have the mutation stick. Mirrors how the real proxy model exposes data.
        self._data = {
            "work_areas": self.work_areas,
            "status": self.status,
            "region": self.region,
            "lga": self.lga,
            "state": self.state,
            "opportunity_id": self.opportunity_id,
            "status_log": self.status_log,
            "mode": self.mode,
            "name": self.name,
            "input_areas": list(input_areas or []),
            "sampling_stats": list(sampling_stats or []),
            "psu_hulls": psu_hulls or {},
        }

    @property
    def data(self):
        # Sync mutable fields back into the dict on every access so callers that
        # mutate p.status etc. through the attribute API still see fresh values.
        self._data.update(
            {
                "work_areas": self.work_areas,
                "status": self.status,
                "opportunity_id": self.opportunity_id,
                "status_log": self.status_log,
                "mode": self.mode,
            }
        )
        return self._data

    @property
    def phase(self):
        # Mirror PlanRecord.phase: boundary-only until work areas are generated.
        return "sampled" if self.work_areas else "boundary"


class _FakeGroup:
    def __init__(self, gid, name, plan_ids, offered_to="", shared=False, kind="bundle", arms=None, status="defining"):
        self.id, self.name, self.plan_ids = gid, name, list(plan_ids)
        self.offered_to, self.shared = offered_to, shared
        self.kind = kind
        self.arms = {str(k): v for k, v in (arms or {}).items()}
        self.sampling_config = {}
        self.status = status

    def arm_for(self, plan_id):
        return self.arms.get(str(plan_id))


def _make_fake_program_da(monkeypatch, plans=None, groups=None):
    """A ProgramPlanDataAccess stand-in over an in-memory store, using real plan_lib."""
    from commcare_connect.microplans.core import plan as plan_lib

    plans = plans if plans is not None else {}
    groups = groups if groups is not None else {}
    seq = {"plan": (max(plans) if plans else 0) + 1, "group": (max(groups) if groups else 0) + 1}

    class FakeDA:
        def __init__(self, program_id, *a, **k):
            self.program_id = int(program_id)

        def list_plans(self):
            return list(plans.values())

        def get_plan(self, pid):
            return plans[int(pid)]

        def create_plan(
            self, region, name, mode, pins, hulls, input_areas=None, grouping=None, lga="", state="", stats=None
        ):
            was = plan_lib.materialize_work_areas(mode, pins, hulls)
            pid = seq["plan"]
            seq["plan"] += 1
            plans[pid] = _FakeProgramPlan(
                pid,
                mode,
                was,
                name=name,
                region=region,
                lga=lga,
                state=state,
                input_areas=input_areas,
                sampling_stats=stats,
                psu_hulls=(hulls if mode == "sampling" else None),
            )
            return plans[pid]

        def regenerate_plan(self, pid, mode, pins, hulls, input_areas, grouping=None, base_revision=None, stats=None):
            was = plan_lib.materialize_work_areas(mode, pins, hulls, grouping=grouping)
            p = plans[int(pid)]
            p.work_areas = was
            p.mode = mode
            p.data["mode"] = mode
            p.data["work_areas"] = was
            p.data["input_areas"] = list(input_areas or [])
            p.data["grouping"] = dict(grouping or {})
            p.data["assignment"] = {}
            if mode == "sampling" and hulls is not None:
                p.data["psu_hulls"] = hulls
            if stats is not None:
                p.data["sampling_stats"] = stats
            return p

        def apply_plan_edits(self, pid, wa_ids, action, params, actor, base_revision=None):
            p = plans[int(pid)]
            for wa_id in wa_ids:
                wa = plan_lib.find(p.work_areas, wa_id)
                if wa is None:
                    raise ValueError(f"work area {wa_id!r} not found")
                plan_lib.apply_action(wa, action, params, actor)
            return p

        def transition_plan(self, pid, to, actor, opportunity_id=None, base_revision=None):
            p = plans[int(pid)]
            data = dict(p.data)
            plan_lib.transition_plan(data, to, actor, opportunity_id=opportunity_id)
            p.status = data["status"]
            p.opportunity_id = data.get("opportunity_id")
            p.status_log = data.get("status_log", [])
            return p

        def create_group(self, name, plan_ids, offered_to="", kind="bundle", arms=None, sampling_config=None):
            gid = seq["group"]
            seq["group"] += 1
            groups[gid] = _FakeGroup(gid, name, plan_ids, offered_to, kind=kind, arms=arms)
            if sampling_config:
                groups[gid].sampling_config = dict(sampling_config)
            return groups[gid]

        def list_groups(self):
            return list(groups.values())

        def get_group(self, gid):
            return groups[int(gid)]

        def update_group(self, gid, **fields):
            g = groups[int(gid)]
            for key in ("name", "offered_to", "shared", "kind", "status"):
                if fields.get(key) is not None:
                    setattr(g, key, fields[key])
            if fields.get("plan_ids") is not None:
                g.plan_ids = [int(x) for x in fields["plan_ids"]]
            if fields.get("arms") is not None:
                g.arms = {str(k): v for k, v in fields["arms"].items()}
            return g

        def add_plan_to_group(self, gid, plan_id):
            g = groups[int(gid)]
            plan_id = int(plan_id)
            if plan_id not in g.plan_ids:
                g.plan_ids.append(plan_id)
            return g

        def remove_plan_from_group(self, gid, plan_id):
            g = groups[int(gid)]
            plan_id = int(plan_id)
            g.plan_ids = [p for p in g.plan_ids if p != plan_id]
            g.arms = {k: v for k, v in g.arms.items() if k != str(plan_id)}
            return g

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", FakeDA)
    return plans, groups


def _seed_program_plans(monkeypatch):
    from commcare_connect.microplans.core import plan as plan_lib

    plans = {
        1: _FakeProgramPlan(
            1,
            "coverage",
            plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC),
            name="Kano North",
            region="Kano North LGA",
        ),
        2: _FakeProgramPlan(
            2,
            "coverage",
            plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC),
            name="Kano South",
            region="Kano South LGA",
            status="approved",
        ),
    }
    return _make_fake_program_da(monkeypatch, plans, {})


def test_program_workspace_requires_login(client):
    resp = client.get(reverse("microplans:program_workspace", kwargs={"program_id": 25}))
    assert resp.status_code == 302 and "/labs/login/" in resp["Location"]


def test_program_workspace_renders(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:program_workspace", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    assert resp.context["program_id"] == 25
    assert "Microplan portfolio" in resp.content.decode()


def test_program_plans_json(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _seed_program_plans(monkeypatch)
    resp = client.get(reverse("microplans:program_plans", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and len(body["plans"]) == 2
    row = next(p for p in body["plans"] if p["plan_id"] == 2)
    assert row["status"] == "approved" and row["region"] == "Kano South LGA"
    assert "max_spread_km" in row and "coverage_pct" in row
    assert "draft" in body["transitions"] and "status_labels" in body


def test_program_plans_json_groups_carry_kind(client, django_user_model, monkeypatch):
    # The workspace cards branch on kind (study vs bundle) to pick the
    # "Open study" / manage link, so the JSON must surface it.
    _login(client, django_user_model)
    plan = _FakeProgramPlan(501, "sampling", [], name="Madobi ward")
    groups = {7: _FakeGroup(7, "Kano study", [501], kind="study", status="defining")}
    _make_fake_program_da(monkeypatch, {501: plan}, groups)
    resp = client.get(reverse("microplans:program_plans", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    g = next(g for g in resp.json()["groups"] if g["group_id"] == 7)
    assert g["kind"] == "study" and g["status"] == "defining"


def test_program_create_plan(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, _ = _make_fake_program_da(monkeypatch, {}, {})
    resp = client.post(
        reverse("microplans:program_create_plan", kwargs={"program_id": 25}),
        data=json.dumps(
            {"region": "Zaria", "name": "Zaria v1", "mode": "coverage", "coverage_areas": _HULL_FC, "state": "Kaduna"}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    pid = resp.json()["plan_id"]
    assert plans[pid].region == "Zaria" and plans[pid].status == "draft"
    assert len(plans[pid].work_areas) == 2
    # lga/state captured at creation for the Connect import (lga defaults to region)
    assert plans[pid].data["lga"] == "Zaria"
    assert plans[pid].data["state"] == "Kaduna"


def test_program_create_sampling_plan_returns_overlay_and_urls(client, django_user_model, monkeypatch):
    # create-in-place + created==opened consistency: the create response carries the
    # plan's sampling overlay (input_areas + psu_hulls + sampling_stats) AND its
    # plan-scoped URLs, so the client hydrates the page without a reload and a
    # reopened plan replays the same boundaries/hulls/Sample-details.
    _login(client, django_user_model)
    plans, _ = _make_fake_program_da(monkeypatch, {}, {})
    input_areas = [{"arm": "intervention", "geometry": _ward(8.3)}]
    resp = client.post(
        reverse("microplans:program_create_plan", kwargs={"program_id": 25}),
        data=json.dumps(
            {
                "region": "Attakar",
                "name": "Kaura study",
                "mode": "sampling",
                "hulls": _HULL_FC,
                "input_areas": input_areas,
                "stats": [{"arm": "intervention", "psus_selected": 12}],
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["plan_id"]
    # overlay round-trips in the response (for in-place hydrate)...
    assert body["input_areas"] == input_areas
    assert body["psu_hulls"]["features"] == _HULL_FC["features"]
    assert body["sampling_stats"][0]["psus_selected"] == 12
    # ...and is persisted so a later open replays identically...
    pid = body["plan_id"]
    assert plans[pid].data["input_areas"] == input_areas
    assert plans[pid].data["psu_hulls"]["features"] == _HULL_FC["features"]
    # ...and the plan-scoped URLs are returned so the page adopts them without a reload.
    for k in ("review", "plan", "regenerate", "footprints", "regroup", "reassign", "csv", "edit"):
        assert k in body["urls"] and str(body["plan_id"]) in body["urls"][k]


def test_program_plan_csv_defaults_lga_state_and_flags_readiness(client, django_user_model, monkeypatch):
    """The Connect-import CSV defaults LGA/State from the plan (LGA falls back to
    region) and flags via response headers whether Connect will accept the file."""
    _login(client, django_user_model)
    from commcare_connect.microplans.core import plan as plan_lib

    was = plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC)
    # Plan created before State was captured: region present, no state.
    no_state = _FakeProgramPlan(1, "coverage", was, name="Zaria v1", region="Zaria LGA")
    # Plan with both labels.
    ready = _FakeProgramPlan(
        2, "coverage", was, name="Kano", region="Kano North LGA", lga="Kano North LGA", state="Kano"
    )
    _make_fake_program_da(monkeypatch, {1: no_state, 2: ready}, {})

    # No-state plan: LGA column filled from region, State blank, NOT Connect-ready.
    r1 = client.post(
        reverse("microplans:program_plan_csv", kwargs={"program_id": 25, "plan_id": 1}),
        data="{}",
        content_type="application/json",
    )
    assert r1.status_code == 200
    assert r1["X-Microplan-Connect-Ready"] == "false"
    assert "State" in r1["X-Microplan-Missing"]
    body1 = r1.content.decode()
    assert "Zaria LGA" in body1  # LGA defaulted from region

    # Ready plan: both labels present → Connect-ready, no missing header.
    r2 = client.post(
        reverse("microplans:program_plan_csv", kwargs={"program_id": 25, "plan_id": 2}),
        data="{}",
        content_type="application/json",
    )
    assert r2.status_code == 200
    assert r2["X-Microplan-Connect-Ready"] == "true"
    body2 = r2.content.decode()
    assert "Kano North LGA" in body2 and "Kano" in body2

    # Explicit body values override the plan.
    r3 = client.post(
        reverse("microplans:program_plan_csv", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"lga": "Override LGA", "state": "Override State"}),
        content_type="application/json",
    )
    assert r3["X-Microplan-Connect-Ready"] == "true"
    assert "Override LGA" in r3.content.decode()


def test_study_member_plan_csv_export_is_arm_blind(client, django_user_model, monkeypatch):
    """A study plan exports to its own opportunity's CSV with NO arm anywhere — the
    arm lives only on the group, so execution stays blind (S5)."""
    _login(client, django_user_model)
    from commcare_connect.microplans.core import plan as plan_lib

    was = plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC)
    plan = _FakeProgramPlan(501, "sampling", was, name="Madobi", region="Madobi", lga="Madobi", state="Kano")
    groups = {7: _FakeGroup(7, "Study", [501], kind="study", arms={"501": "intervention"})}
    _make_fake_program_da(monkeypatch, {501: plan}, groups)

    r = client.post(
        reverse("microplans:program_plan_csv", kwargs={"program_id": 25, "plan_id": 501}),
        data="{}",
        content_type="application/json",
    )
    assert r.status_code == 200
    body = r.content.decode().lower()
    assert "arm" not in body and "intervention" not in body and "control" not in body


def test_program_regenerate_enqueues(client, django_user_model, monkeypatch):
    # Regenerate is Celery-offloaded now: the view validates + enqueues (202 + a
    # pollable task id). The destructive logic itself is exercised against the DA
    # (test_data_access_contract) and the dispatcher task (below).
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import apply_plan_mutation_task

    monkeypatch.setattr(apply_plan_mutation_task, "delay", _fake_delay("regen-1"))
    resp = client.post(
        reverse("microplans:program_plan_regenerate", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"mode": "coverage", "coverage_areas": _HULL_FC, "grouping": {"strategy": "bbox"}}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "regen-1" and "regen-1" in body["poll_url"]


def test_program_regroup_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import apply_plan_mutation_task

    monkeypatch.setattr(apply_plan_mutation_task, "delay", _fake_delay("regrp-1"))
    resp = client.post(
        reverse("microplans:program_plan_regroup", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"strategy": "bbox", "target_size": 30, "revision": 2}),
        content_type="application/json",
    )
    assert resp.status_code == 202 and resp.json()["task_id"] == "regrp-1"


def test_program_reassign_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import apply_plan_mutation_task

    monkeypatch.setattr(apply_plan_mutation_task, "delay", _fake_delay("reasg-1"))
    resp = client.post(
        reverse("microplans:program_plan_reassign", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"strategy": "round_robin", "workers": "a,b", "revision": 2}),
        content_type="application/json",
    )
    assert resp.status_code == 202 and resp.json()["task_id"] == "reasg-1"


def test_program_mutation_requires_token(client, django_user_model):
    user = django_user_model.objects.create(username="mut-notoken", email="mn@example.com")
    client.force_login(user)  # no labs_oauth
    resp = client.post(
        reverse("microplans:program_plan_regroup", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"strategy": "bbox"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_apply_plan_mutation_task_dispatches_and_returns_plan_json(monkeypatch):
    from commcare_connect.microplans import tasks

    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)
    seen = {}

    class FakeDA:
        def __init__(self, pid, access_token=None):
            seen["pid"], seen["token"] = pid, access_token

        def regroup_plan(self, plan_id, grouping, actor, base_revision=None):
            seen["call"] = ("regroup", plan_id, grouping, actor, base_revision)
            return "PLAN"

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", FakeDA)
    monkeypatch.setattr(
        "commcare_connect.microplans.serialization.plan_to_json", lambda p: {"status": "ok", "plan": p}
    )

    out = tasks.apply_plan_mutation_task.run(
        "regroup", 25, 1, {"grouping": {"strategy": "bbox"}, "revision": 3}, "act", "tok"
    )
    assert out == {"status": "ok", "plan": "PLAN"}
    assert seen["call"] == ("regroup", 1, {"strategy": "bbox"}, "act", 3)
    assert seen["token"] == "tok"


def test_apply_plan_mutation_task_maps_conflict(monkeypatch):
    from commcare_connect.microplans import tasks
    from commcare_connect.microplans.core.data_access import StalePlanError

    monkeypatch.setattr("commcare_connect.microplans.tasks.set_task_progress", lambda *a, **k: None)

    class FakeDA:
        def __init__(self, *a, **k):
            pass

        def regenerate_plan(self, *a, **k):
            raise StalePlanError("This plan changed since you opened it (r0→r3).")

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", FakeDA)
    out = tasks.apply_plan_mutation_task.run("regenerate", 1, 2, {"revision": 0}, "act", "tok")
    assert out["status"] == "conflict" and "changed" in out["detail"]


def test_program_create_plan_page_renders(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:program_create_plan_page", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert resp.context["program_id"] == 25
    assert resp.context["plan_id"] is None
    # Unified template: new-plan page shows the click-to-edit plan-name title
    # (placeholder "Untitled microplan") + the "Create work areas" button (vs. the
    # per-plan "Apply geographic frame" button on the existing-plan flow). The
    # title became an editable input in #412.
    assert "Untitled microplan" in body
    assert "Create work areas" in body  # new-plan affordance (plan_id-branched button text)


def test_program_transition_advances_status(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, _ = _seed_program_plans(monkeypatch)
    resp = client.post(
        reverse("microplans:program_plan_transition", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"to": "in_review"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["plan_status"] == "in_review" and plans[1].status == "in_review"


def test_program_deploy_requires_opportunity(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _seed_program_plans(monkeypatch)  # plan 2 is approved
    resp = client.post(
        reverse("microplans:program_plan_transition", kwargs={"program_id": 25, "plan_id": 2}),
        data=json.dumps({"to": "deployed"}),
        content_type="application/json",
    )
    assert resp.status_code == 400  # deploy without an opportunity_id is rejected


def test_program_deploy_binds_opportunity(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, _ = _seed_program_plans(monkeypatch)  # plan 2 is approved
    resp = client.post(
        reverse("microplans:program_plan_transition", kwargs={"program_id": 25, "plan_id": 2}),
        data=json.dumps({"to": "deployed", "opportunity_id": "555"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["plan_status"] == "deployed" and body["opportunity_id"] == "555"


def test_program_illegal_transition_is_400(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _seed_program_plans(monkeypatch)  # plan 1 is draft; draft->deployed is illegal
    resp = client.post(
        reverse("microplans:program_plan_transition", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"to": "deployed", "opportunity_id": "9"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_program_group_create_and_share(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, groups = _seed_program_plans(monkeypatch)
    resp = client.post(
        reverse("microplans:program_group_create", kwargs={"program_id": 25}),
        data=json.dumps({"name": "For Acme LLO", "plan_ids": [1, 2], "offered_to": "Acme"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    gid = resp.json()["group_id"]
    assert groups[gid].name == "For Acme LLO" and groups[gid].plan_ids == [1, 2]

    share = client.get(reverse("microplans:program_group_share", kwargs={"program_id": 25, "group_id": gid}))
    assert share.status_code == 200
    body = share.content.decode()
    assert "For Acme LLO" in body and "Acme" in body
    assert len(share.context["entries"]) == 2
    assert all("kpis" in e and "status_label" in e and "review_url" in e for e in share.context["entries"])
    assert all("composite" not in e for e in share.context["entries"])


def test_program_group_share_escapes_plan_names(client, django_user_model, monkeypatch):
    # A malicious plan name must be HTML-escaped in the server-rendered share page.
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    plans = {
        1: _FakeProgramPlan(
            1,
            "coverage",
            plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC),
            name="<script>alert('xss')</script>",
            region="<b>R</b>",
        )
    }
    groups = {3: _FakeGroup(3, "G", [1], offered_to="Acme")}
    _make_fake_program_da(monkeypatch, plans, groups)
    resp = client.get(reverse("microplans:program_group_share", kwargs={"program_id": 25, "group_id": 3}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "<script>alert('xss')</script>" not in body
    assert "&lt;script&gt;" in body


def test_program_group_create_requires_name_and_plans(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_program_da(monkeypatch, {}, {})
    resp = client.post(
        reverse("microplans:program_group_create", kwargs={"program_id": 25}),
        data=json.dumps({"name": "", "plan_ids": []}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_program_group_share_toggle(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    groups = {7: _FakeGroup(7, "G", [1], shared=False)}
    _make_fake_program_da(monkeypatch, {}, groups)
    resp = client.post(
        reverse("microplans:program_group_update", kwargs={"program_id": 25, "group_id": 7}),
        data=json.dumps({"shared": True}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["shared"] is True and groups[7].shared is True


def test_program_review_page_uses_program_urls(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:program_review", kwargs={"program_id": 25, "plan_id": 3}))
    assert resp.status_code == 200
    assert resp.context["plan_id"] == 3
    # the edit URL the page posts to must be the program-scoped route
    assert resp.context["edit_url"] == reverse("microplans:program_plan_edit", kwargs={"program_id": 25, "plan_id": 3})


def test_program_group_create_study_allows_empty_and_sets_kind(client, django_user_model, monkeypatch):
    """A study group is created empty (you add wards after) with kind=study."""
    _login(client, django_user_model)
    _plans, groups = _make_fake_program_da(monkeypatch, {}, {})
    resp = client.post(
        reverse("microplans:program_group_create", kwargs={"program_id": 25}),
        data=json.dumps({"name": "Madobi CHC study", "plan_ids": [], "kind": "study"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    gid = resp.json()["group_id"]
    assert groups[gid].kind == "study"
    assert groups[gid].plan_ids == []


def test_program_group_manage_page_renders_study(client, django_user_model, monkeypatch):
    """The group management page lists members with arm + phase; a study shows arm badges."""
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    plans = {
        501: _FakeProgramPlan(
            501,
            "sampling",
            plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC),
            name="Madobi ward",
            region="Madobi",
        ),
        502: _FakeProgramPlan(502, "sampling", [], name="Gora ward", region="Gora"),  # boundary-only
    }
    groups = {
        7: _FakeGroup(7, "Madobi CHC study", [501, 502], kind="study", arms={"501": "intervention", "502": "control"})
    }
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.get(reverse("microplans:program_group_page", kwargs={"program_id": 25, "group_id": 7}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Madobi CHC study" in body
    assert "Study" in body  # study badge
    assert "intervention" in body and "control" in body  # arm badges
    assert "sampled" in body and "boundary only" in body  # phase badges
    entries = resp.context["entries"]
    assert {e["plan_id"]: e["arm"] for e in entries} == {501: "intervention", 502: "control"}
    assert {e["plan_id"]: e["phase"] for e in entries} == {501: "sampled", 502: "boundary"}
    # The add-path links must carry ?group=<gid> so plans created there file into THIS group.
    assert resp.context["bulk_create_url"].endswith("?group=7")
    assert resp.context["new_plan_url"].endswith("?group=7")


def test_program_group_manage_remove_plan_drops_plan_and_arm(client, django_user_model, monkeypatch):
    """POST remove_plan_id to the group endpoint drops the plan and its arm."""
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    plans = {
        501: _FakeProgramPlan(
            501, "sampling", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC), name="Madobi"
        ),
        502: _FakeProgramPlan(502, "sampling", [], name="Gora"),
    }
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study", arms={"501": "intervention", "502": "control"})}
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.post(
        reverse("microplans:program_group_update", kwargs={"program_id": 25, "group_id": 7}),
        data=json.dumps({"remove_plan_id": 502}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert groups[7].plan_ids == [501]
    assert groups[7].arm_for(502) is None
    assert groups[7].arm_for(501) == "intervention"


def test_program_group_generate_enqueues_with_group_id(client, django_user_model, monkeypatch):
    """POST generate → 202, enqueues generate_group_samples_task for the group."""
    _login(client, django_user_model)
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study")}
    _make_fake_program_da(monkeypatch, {}, groups)
    from commcare_connect.microplans.tasks import generate_group_samples_task

    captured = {}

    def fake_delay(*args, **kwargs):
        captured["args"] = args
        return SimpleNamespace(id="gen-1")

    monkeypatch.setattr(generate_group_samples_task, "delay", fake_delay)
    resp = client.post(reverse("microplans:program_group_generate", kwargs={"program_id": 25, "group_id": 7}))
    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "gen-1" and "gen-1" in body["poll_url"]
    assert captured["args"][0] == 25 and captured["args"][1] == 7


def test_program_group_map_overlays_member_plans_by_arm(client, django_user_model, monkeypatch):
    """The group map view assembles each member plan's work-area GeoJSON, tagged by arm + color."""
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    plans = {
        501: _FakeProgramPlan(
            501, "sampling", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC), name="Madobi"
        ),
        502: _FakeProgramPlan(
            502, "sampling", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC), name="Gora"
        ),
    }
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study", arms={"501": "intervention", "502": "control"})}
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.get(reverse("microplans:program_group_map", kwargs={"program_id": 25, "group_id": 7}))
    assert resp.status_code == 200
    layers = {layer["plan_id"]: layer for layer in resp.context["plan_layers"]}
    assert set(layers) == {501, 502}
    assert layers[501]["arm"] == "intervention" and layers[502]["arm"] == "control"
    # distinct colors per arm; each layer carries a GeoJSON FeatureCollection of its work areas
    assert layers[501]["color"] != layers[502]["color"]
    fc = layers[501]["geojson"]
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) >= 1
    assert fc["features"][0]["properties"]["arm"] == "intervention"


def test_program_group_map_includes_saved_psu_settlement_hulls(client, django_user_model, monkeypatch):
    """When a plan saved its PSU hulls, the map overlays the SELECTED settlements
    (polygons) alongside the sampled pins — so the surveyed settlements are visible."""
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    hull = {"type": "Polygon", "coordinates": [[[8.2, 11.0], [8.21, 11.0], [8.21, 11.01], [8.2, 11.0]]]}
    psu_hulls = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": hull, "properties": {"cluster": 1}}],
    }
    plans = {
        501: _FakeProgramPlan(
            501,
            "sampling",
            plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC),
            name="Madobi",
            psu_hulls=psu_hulls,
        ),
        502: _FakeProgramPlan(
            502, "sampling", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC), name="Gora"
        ),
    }
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study", arms={"501": "intervention", "502": "control"})}
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.get(reverse("microplans:program_group_map", kwargs={"program_id": 25, "group_id": 7}))
    assert resp.status_code == 200
    layers = {layer["plan_id"]: layer for layer in resp.context["plan_layers"]}
    feats = layers[501]["geojson"]["features"]
    settlements = [
        f for f in feats if f["geometry"]["type"] == "Polygon" and f["properties"].get("feature") == "settlement"
    ]
    assert len(settlements) == 1  # the saved PSU hull is rendered as a settlement polygon
    assert settlements[0]["properties"]["arm"] == "intervention"


def _pins(n, lon=8.3, lat=11.8):
    # Sampling work areas are PINS (Point geometry), not polygons.
    return [
        {"id": f"p{i}", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "building_count": 1}
        for i in range(n)
    ]


def _ward(x0):
    return {
        "type": "Polygon",
        "coordinates": [[[x0, 11.7], [x0 + 0.2, 11.7], [x0 + 0.2, 11.9], [x0, 11.9], [x0, 11.7]]],
    }


def test_program_group_manage_comparability_uses_psu_smd_not_whole_ward(client, django_user_model, monkeypatch):
    """Corrected comparability compares the SELECTED PSUs (settlement density, PSU
    size, building footprint) via SMD — not whole-ward geography — so a larger,
    sparser control ward that matches on settlement structure reads as matched."""
    _login(client, django_user_model)
    # Madobi (intervention) vs a Kauran-Mata-like control: very different WARDS
    # (the control ward is sparser) but the SELECTED PSUs match closely.
    iv_stats = [{"psu_size": [53, 20], "psu_density": [8000, 2500], "bldg_area": [120, 40], "after_filters": 4569}]
    ct_stats = [{"psu_size": [55, 21], "psu_density": [8200, 2600], "bldg_area": [123, 41], "after_filters": 4137}]
    plans = {
        501: _FakeProgramPlan(
            501,
            "sampling",
            _pins(100),
            name="Madobi",
            input_areas=[{"kind": "draw", "geometry": _ward(8.2)}],
            sampling_stats=iv_stats,
        ),
        502: _FakeProgramPlan(
            502,
            "sampling",
            _pins(110),
            name="Kauran Mata",
            input_areas=[{"kind": "draw", "geometry": _ward(8.5)}],
            sampling_stats=ct_stats,
        ),
    }
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study", arms={"501": "intervention", "502": "control"})}
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.get(reverse("microplans:program_group_page", kwargs={"program_id": 25, "group_id": 7}))
    assert resp.status_code == 200
    comp = resp.context["comparability"]
    metrics = {m["metric"]: m for m in comp["metrics"]}
    assert set(metrics) == {"psu_density", "psu_size", "bldg_area"}
    assert metrics["psu_density"]["band"] == "good"  # settlements match closely
    assert comp["matched"] is True  # matched on the core (settlement) metrics
    # whole-ward density is echoed as context only (each arm carries it)
    assert all("ward_density" in a for a in comp["arms"])
    assert "Arm comparability" in resp.content.decode()


def test_program_group_assign_arm(client, django_user_model, monkeypatch):
    """POST arms to the group endpoint assigns each plan's arm (labs-side study metadata)."""
    from commcare_connect.microplans.core import plan as plan_lib

    _login(client, django_user_model)
    plans = {
        501: _FakeProgramPlan(
            501, "sampling", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC), name="Madobi"
        ),
        502: _FakeProgramPlan(502, "sampling", [], name="Gora"),
    }
    groups = {7: _FakeGroup(7, "Study", [501, 502], kind="study")}  # no arms yet
    _make_fake_program_da(monkeypatch, plans, groups)

    resp = client.post(
        reverse("microplans:program_group_update", kwargs={"program_id": 25, "group_id": 7}),
        data=json.dumps({"arms": {"501": "intervention", "502": "control"}}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert groups[7].arm_for(501) == "intervention"
    assert groups[7].arm_for(502) == "control"


def test_program_create_plan_into_group_adds_membership(client, django_user_model, monkeypatch):
    """Creating a plan with group_id drops it into that group (the editor 'add to group' path)."""
    _login(client, django_user_model)
    groups = {7: _FakeGroup(7, "Madobi CHC study", [], kind="study")}
    _make_fake_program_da(monkeypatch, {}, groups)

    resp = client.post(
        reverse("microplans:program_create_plan", kwargs={"program_id": 25}),
        data=json.dumps({"name": "Gora ward", "region": "Gora", "mode": "sampling", "group_id": 7}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    pid = resp.json()["plan_id"]
    assert groups[7].plan_ids == [pid]


def test_program_create_plan_without_group_id_is_unchanged(client, django_user_model, monkeypatch):
    """No group_id → plain create, no membership side effect."""
    _login(client, django_user_model)
    groups = {7: _FakeGroup(7, "Study", [], kind="study")}
    _make_fake_program_da(monkeypatch, {}, groups)

    resp = client.post(
        reverse("microplans:program_create_plan", kwargs={"program_id": 25}),
        data=json.dumps({"name": "Standalone", "region": "X", "mode": "sampling"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert groups[7].plan_ids == []


def test_program_compare_json(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _seed_program_plans(monkeypatch)  # plans 1 (approved/assigned) + 2 (approved/unassigned)
    resp = client.get(reverse("microplans:program_plan_compare", kwargs={"program_id": 25}) + "?plans=1,2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and len(body["plans"]) == 2
    assert all("kpis" in p for p in body["plans"])
    assert "weights" not in body and all("composite" not in p for p in body["plans"])


def test_program_compare_json_bad_ids_400(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_program_da(monkeypatch, {}, {})
    resp = client.get(reverse("microplans:program_plan_compare", kwargs={"program_id": 25}) + "?plans=abc")
    assert resp.status_code == 400


def test_program_compare_uses_list_plans_not_per_id(client, django_user_model, monkeypatch):
    """N+1 guard: compare must fetch the program once (list_plans), not get_plan per id."""
    _login(client, django_user_model)

    class CountDA:
        def __init__(self, *a, **k):
            pass

        def list_plans(self):
            return [_FakeProgramPlan(1, "coverage", []), _FakeProgramPlan(2, "coverage", [])]

        def get_plan(self, pid):
            raise AssertionError("compare must not call get_plan per id (N+1)")

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", CountDA)
    resp = client.get(reverse("microplans:program_plan_compare", kwargs={"program_id": 1}) + "?plans=2,1")
    assert resp.status_code == 200
    assert [e["plan_id"] for e in resp.json()["plans"]] == [2, 1]  # requested order preserved


def test_program_plan_delete_foreign_record_returns_404(client, django_user_model, monkeypatch):
    """Deleting a plan that isn't in this program (RecordNotInProgramError) → 404,
    not a silent cross-tenant delete."""
    _login(client, django_user_model)
    from commcare_connect.microplans.core.data_access import RecordNotInProgramError

    class RefuseDA:
        def __init__(self, *a, **k):
            pass

        def delete_plan(self, plan_id):
            raise RecordNotInProgramError(f"plan {plan_id} is not in program")

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", RefuseDA)
    resp = client.post(reverse("microplans:program_plan_delete", kwargs={"program_id": 1, "plan_id": 999}))
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_program_compare_page_renders(client, django_user_model):
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:program_compare_page", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Compare plans" in body and "Program #25" in body
    # the page wires its data fetches to the program-scoped endpoints
    assert resp.context["compare_url"] == reverse("microplans:program_plan_compare", kwargs={"program_id": 25})


def test_program_plan_get_and_edit(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, _ = _seed_program_plans(monkeypatch)
    got = client.get(reverse("microplans:program_plan", kwargs={"program_id": 25, "plan_id": 1}))
    assert got.status_code == 200 and "kpis" in got.json()
    wa_id = plans[1].work_areas[0]["id"]
    edited = client.post(
        reverse("microplans:program_plan_edit", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps({"action": "exclude", "wa_id": wa_id, "reason": "river"}),
        content_type="application/json",
    )
    assert edited.status_code == 200
    wa = next(w for w in edited.json()["work_areas"] if w["id"] == wa_id)
    assert wa["status"] == "EXCLUDED" and wa["audit"][-1]["phase"] == "planning"


def test_program_plan_get_returns_404_when_plan_missing(client, django_user_model, monkeypatch):
    # A stale/deleted plan id: the labs-only backend's get_plan returns None (not an
    # exception) — e.g. after a study re-seed renumbers plans. The view must 404, not
    # 500 on plan_to_json(None).
    _login(client, django_user_model)

    class _NoneDA:
        def __init__(self, *a, **k):
            pass

        def get_plan(self, pid):
            return None

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", _NoneDA)
    resp = client.get(reverse("microplans:program_plan", kwargs={"program_id": 25, "plan_id": 999}))
    assert resp.status_code == 404, resp.content


# ---------------------------------------------------------------------------
# Service-delivery GPS overlay views
# ---------------------------------------------------------------------------
def _login_with_opps(client, django_user_model, opp_ids):
    user = _login(client, django_user_model)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token",
        "expires_at": time.time() + 3600,
        "organization_data": {"opportunities": [{"id": oid, "name": f"Opp {oid}"} for oid in opp_ids]},
    }
    session.save()
    return user


def test_derive_boundary_returns_polygon(client, django_user_model):
    _login(client, django_user_model)
    coords = [[36.82 + i * 0.001, -1.29 + j * 0.001] for i in range(4) for j in range(4)]
    resp = client.post(
        reverse("microplans:derive_boundary", kwargs={"opp_id": 123}),
        data=json.dumps({"coords": coords, "method": "concave", "buffer_m": 20}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["boundary"]["type"] == "Feature"
    assert body["boundary"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert body["point_count"] == len(coords)


def test_derive_boundary_rejects_empty(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:derive_boundary", kwargs={"opp_id": 123}),
        data=json.dumps({"coords": []}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_preview_service_delivery_scopes_to_allowed_opps(client, django_user_model):
    _login_with_opps(client, django_user_model, [100])
    # 999 is not in the user's org data -> filtered out -> 403
    resp = client.post(
        reverse("microplans:preview_service_delivery", kwargs={"opp_id": 100}),
        data=json.dumps({"opp_ids": [999]}),
        content_type="application/json",
    )
    assert resp.status_code == 403


def test_preview_service_delivery_merges_colored_layers(client, django_user_model, monkeypatch):
    _login_with_opps(client, django_user_model, [100, 200])

    def fake_fetch(opp_id, request=None, access_token=None, pipeline_id=None):
        return {
            "points": [{"lon": 36.82, "lat": -1.29, "username": f"flw{opp_id}", "status": "approved"}],
            "stats": {"opportunity_id": opp_id, "total_rows": 1, "with_gps": 1, "gps_pct": 100.0},
            "error": None,
        }

    monkeypatch.setattr("commcare_connect.microplans.service_delivery.points.fetch_points", fake_fetch)
    resp = client.post(
        reverse("microplans:preview_service_delivery", kwargs={"opp_id": 100}),
        data=json.dumps({"opp_ids": [100, 200]}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["count"] == 2
    assert len(body["layers"]) == 2
    # distinct colors per opp
    colors = {L["opportunity_id"]: L["color"] for L in body["layers"]}
    assert colors[100] != colors[200]
    # each feature tagged with its opp + color
    feats = body["points"]["features"]
    assert {f["properties"]["opportunity_id"] for f in feats} == {100, 200}
    assert body["sampled"] is False and body["total"] == 2


def test_preview_service_delivery_caps_overlay_points(client, django_user_model, monkeypatch):
    from commcare_connect.microplans.service_delivery.points import MAX_OVERLAY_POINTS

    _login_with_opps(client, django_user_model, [100, 200])
    per_opp = MAX_OVERLAY_POINTS  # 2 opps × cap → comfortably over the limit

    def fake_fetch(opp_id, request=None, access_token=None, pipeline_id=None):
        pts = [{"lon": 36.82 + i * 1e-5, "lat": -1.29, "status": "approved"} for i in range(per_opp)]
        return {"points": pts, "stats": {"opportunity_id": opp_id}, "error": None}

    monkeypatch.setattr("commcare_connect.microplans.service_delivery.points.fetch_points", fake_fetch)
    resp = client.post(
        reverse("microplans:preview_service_delivery", kwargs={"opp_id": 100}),
        data=json.dumps({"opp_ids": [100, 200]}),
        content_type="application/json",
    )
    body = resp.json()
    assert body["sampled"] is True
    assert body["total"] == 2 * per_opp  # honest about how many there were
    assert body["count"] <= MAX_OVERLAY_POINTS  # bounded, no silent truncation
    assert len(body["points"]["features"]) == body["count"]


def test_program_plan_footprints_sets_cache_control(client, django_user_model, monkeypatch):
    import pandas as pd

    _login(client, django_user_model)
    plan = _FakeProgramPlan(
        5, "coverage", [{"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0.01, 0], [0.01, 0.01], [0, 0]]]}}]
    )
    _make_fake_program_da(monkeypatch, {5: plan}, {})
    monkeypatch.setattr(
        "commcare_connect.microplans.core.footprints.fetch_buildings",
        lambda area, min_confidence=None, with_geom=False: pd.DataFrame([{"lon": 0.005, "lat": 0.005}]),
    )
    resp = client.get(reverse("microplans:program_plan_footprints", kwargs={"program_id": 1, "plan_id": 5}))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "max-age" in resp.headers.get("Cache-Control", "")


# --- arm comparability (two-arm study guardrail) ------------------------------


def _square(lon, lat, d=0.02):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]],
    }


def test_arm_comparability_renders_shared_smd_panel(client, django_user_model):
    # Single-plan path reuses the SAME PSU/SMD engine + panel markup as the group
    # page: POST the plan's per-arm sampling_stats, get back the rendered partial.
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:arm_comparability", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "stats": [
                    {
                        "arm": "intervention",
                        "psu_size": [53, 20],
                        "psu_density": [8000, 2500],
                        "bldg_area": [120, 40],
                        "n_psus": 8,
                    },
                    {
                        "arm": "comparison",
                        "psu_size": [55, 21],
                        "psu_density": [8200, 2600],
                        "bldg_area": [123, 41],
                        "n_psus": 8,
                    },
                ],
                "names": {"intervention": "Attakar", "comparison": "Gura"},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["matched"] is True
    # the rendered, shared comparability partial (same one the group page includes)
    assert "Arm comparability" in body["html"]
    assert "settlement density" in body["html"]
    assert "SMD" in body["html"]
    assert "Attakar" in body["html"] and "Gura" in body["html"]


def test_arm_comparability_density_mismatch_not_matched(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:arm_comparability", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "stats": [
                    {"arm": "intervention", "psu_size": [53, 20], "psu_density": [8000, 2500], "bldg_area": [120, 40]},
                    {"arm": "comparison", "psu_size": [60, 30], "psu_density": [2500, 1500], "bldg_area": [130, 45]},
                ]
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["matched"] is False
    assert "out of tolerance" in body["html"]


def test_arm_comparability_one_arm_returns_empty_panel(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:arm_comparability", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "stats": [
                    {"arm": "intervention", "psu_size": [53, 20], "psu_density": [8000, 2500], "bldg_area": [120, 40]}
                ]
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "ok"
    assert body["matched"] is None
    assert body["html"] == ""


def test_boundary_viewport_bbox_snaps_to_grid():
    """Viewport bbox snaps outward to the SNAP_DEG grid so pans reuse one cache key
    (and the snapped bbox drives the query too, keeping results correct)."""
    from commcare_connect.microplans.views import BoundaryViewportView

    poly = BoundaryViewportView._parse_bbox("8.41,11.93,8.46,11.97")
    assert tuple(round(x, 4) for x in poly.extent) == (8.40, 11.90, 8.50, 12.00)
    # already-on-grid stays put; degenerate/invalid → None
    assert BoundaryViewportView._parse_bbox("8.5,11.5,8.5,11.6") is None  # minx == maxx
    assert BoundaryViewportView._parse_bbox("nope") is None


# --- bulk-create: gridding (#5) + Celery offload (#4) -------------------------


def test_initial_plan_hulls_coverage_grids_via_frame(monkeypatch):
    """Coverage is sampled at creation: a ward is gridded into many cells, not one feature."""
    from commcare_connect.microplans import tasks

    cells = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": []},
                "properties": {"building_count": 5},
            }
            for _ in range(3)
        ],
    }
    monkeypatch.setattr(
        "commcare_connect.microplans.coverage.frame.generate_coverage_frame",
        lambda areas, config: SimpleNamespace(areas_geojson=cells, stats=[]),
    )
    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [0, 0.01], [0.01, 0.01], [0, 0]]]}
    out = tasks._initial_plan_hulls(geometry, "coverage", 100)
    assert len(out["features"]) == 3  # gridded — NOT a single whole-ward cell


def test_initial_plan_hulls_sampling_is_empty_boundary_only():
    """Sampling is two-step: the plan starts boundary-only (no hulls — the PSU sample
    is drawn later), so there are no work-area hulls at creation."""
    from commcare_connect.microplans import tasks

    geometry = {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}
    out = tasks._initial_plan_hulls(geometry, "sampling", 100)
    assert out["features"] == []


def test_bulk_create_enqueues(client, django_user_model, monkeypatch):
    _login(client, django_user_model)  # sets labs_oauth.access_token
    from commcare_connect.microplans.tasks import bulk_create_plans_task

    monkeypatch.setattr(bulk_create_plans_task, "delay", _fake_delay("bulk-1"))
    resp = client.post(
        reverse("microplans:program_bulk_create", kwargs={"program_id": 1}),
        data=json.dumps({"plans": [{"boundary_id": "b", "name": "B"}], "mode": "coverage", "cell_size_m": 150}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "bulk-1" and "bulk-1" in body["poll_url"]


def test_bulk_create_threads_group_id_to_task(client, django_user_model, monkeypatch):
    """A group_id in the bulk request reaches the task so created plans join the group."""
    _login(client, django_user_model)
    from commcare_connect.microplans.tasks import bulk_create_plans_task

    captured = {}

    def fake_delay(*args, **kwargs):
        captured["args"], captured["kwargs"] = args, kwargs
        return SimpleNamespace(id="bulk-g")

    monkeypatch.setattr(bulk_create_plans_task, "delay", fake_delay)
    resp = client.post(
        reverse("microplans:program_bulk_create", kwargs={"program_id": 1}),
        data=json.dumps({"plans": [{"boundary_id": "b", "name": "B"}], "mode": "sampling", "group_id": 7}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    assert captured["kwargs"].get("group_id") == 7 or 7 in captured["args"]


def test_bulk_create_requires_token(client, django_user_model):
    user = django_user_model.objects.create(username="notoken", email="nt@example.com")
    client.force_login(user)  # NO labs_oauth in session
    resp = client.post(
        reverse("microplans:program_bulk_create", kwargs={"program_id": 1}),
        data=json.dumps({"plans": [{"boundary_id": "b"}], "mode": "coverage"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


def test_bulk_create_empty_plans_400(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:program_bulk_create", kwargs={"program_id": 1}),
        data=json.dumps({"plans": [], "mode": "coverage"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_bulk_create_status_running_carries_results(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    meta = {"results": [{"index": 0, "status": "ok", "plan_id": 5}], "created": 1, "total": 2}
    monkeypatch.setattr(
        "celery.result.AsyncResult", lambda tid: SimpleNamespace(state="PROGRESS", info=meta, result=None)
    )
    resp = client.get(reverse("microplans:bulk_create_status", kwargs={"task_id": "abc"}))
    body = resp.json()
    assert body["state"] == "running" and body["created"] == 1 and body["total"] == 2
    assert body["results"][0]["plan_id"] == 5


def test_bulk_create_status_completed(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    res = {"status": "ok", "results": [{"index": 0, "status": "ok", "plan_id": 5}], "created": 1, "total": 1}
    monkeypatch.setattr(
        "celery.result.AsyncResult", lambda tid: SimpleNamespace(state="SUCCESS", info=res, result=res)
    )
    resp = client.get(reverse("microplans:bulk_create_status", kwargs={"task_id": "abc"}))
    body = resp.json()
    assert body["state"] == "completed" and body["created"] == 1


class TestProgramMapSeed:
    """`_program_map_seed` opens the new-plan map over the program's footprint so
    the boundary layer loads + the country auto-detects (else cold-start dead end)."""

    @staticmethod
    def _plan(created_at, country=None, centroids=None):
        from types import SimpleNamespace

        input_areas = [{"name": "A", "country": country}] if country else []
        was = [{"centroid": c} for c in (centroids or [])]
        return SimpleNamespace(data={"created_at": created_at, "input_areas": input_areas, "work_areas": was})

    def test_centroids_and_country(self):
        from commcare_connect.microplans.views import _program_map_seed

        seed = _program_map_seed([self._plan("2026-05-01", "NGA", [[8.5, 12.0], [8.7, 12.2]])])
        assert seed == {"iso": "NGA", "lng": 8.6, "lat": 12.1, "zoom": 10}

    def test_newest_wins(self):
        from commcare_connect.microplans.views import _program_map_seed

        old = self._plan("2026-01-01", "KEN", [[36.8, -1.3]])
        new = self._plan("2026-06-01", "NGA", [[8.5, 12.0]])
        assert _program_map_seed([old, new])["iso"] == "NGA"

    def test_country_only_no_centroids(self):
        from commcare_connect.microplans.views import _program_map_seed

        seed = _program_map_seed([self._plan("2026-05-01", "NGA", [])])
        assert seed == {"iso": "NGA", "lng": None, "lat": None, "zoom": None}

    def test_empty_program_returns_none(self):
        from commcare_connect.microplans.views import _program_map_seed

        assert _program_map_seed([]) is None
        assert _program_map_seed([self._plan("2026-05-01", None, [])]) is None


# --- ProgramPlanView DELETE (hard-delete a plan) -----------------------------


def test_plan_delete_ok(client, django_user_model, monkeypatch):
    """DELETE on a plan calls delete_plan and returns ok."""
    _login(client, django_user_model)
    deleted = {}

    class FakeDA:
        def __init__(self, program_id, request=None):
            pass

        def delete_plan(self, plan_id):
            deleted["id"] = plan_id

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", FakeDA)
    resp = client.delete(reverse("microplans:program_plan", kwargs={"program_id": 133, "plan_id": 555}))
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "deleted": 555}
    assert deleted["id"] == 555


def test_plan_delete_not_in_program_is_404(client, django_user_model, monkeypatch):
    """A plan id outside this program (delete_plan raises RecordNotInProgramError) → 404."""
    from commcare_connect.microplans.core.data_access import RecordNotInProgramError

    _login(client, django_user_model)

    class FakeDA:
        def __init__(self, program_id, request=None):
            pass

        def delete_plan(self, plan_id):
            raise RecordNotInProgramError("nope")

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.ProgramPlanDataAccess", FakeDA)
    resp = client.delete(reverse("microplans:program_plan", kwargs={"program_id": 133, "plan_id": 999}))
    assert resp.status_code == 404
    assert resp.json()["status"] == "error"


def test_plan_delete_requires_login(client):
    resp = client.delete(reverse("microplans:program_plan", kwargs={"program_id": 133, "plan_id": 1}))
    assert resp.status_code in (301, 302, 403)


# --- State (ADM1) derivation for boundary-created plans -----------------------


def _state_square(cx, cy, d=0.1):
    return {
        "type": "Polygon",
        "coordinates": [[[cx - d, cy - d], [cx + d, cy - d], [cx + d, cy + d], [cx - d, cy + d], [cx - d, cy - d]]],
    }


def test_adm1_state_for_matches_containing_boundary(monkeypatch):
    """A plan polygon whose centroid falls inside an ADM1 boundary picks up its name."""
    from commcare_connect.microplans import views

    captured = {}

    class FakeQS:
        def filter(self, **kw):
            captured.update(kw)
            return self

        def first(self):
            return type("B", (), {"name": "Kano"})()

    class FakeManager:
        objects = FakeQS()

    monkeypatch.setattr("commcare_connect.labs.admin_boundaries.models.AdminBoundary", FakeManager, raising=False)
    state = views._adm1_state_for([{"geometry": _state_square(8.5, 12.0)}], None)
    assert state == "Kano"
    assert captured.get("admin_level") == 1
    assert "geometry__contains" in captured


def test_adm1_state_for_no_match_returns_empty(monkeypatch):
    from commcare_connect.microplans import views

    class FakeQS:
        def filter(self, **kw):
            return self

        def first(self):
            return None

    class FakeManager:
        objects = FakeQS()

    monkeypatch.setattr("commcare_connect.labs.admin_boundaries.models.AdminBoundary", FakeManager, raising=False)
    assert views._adm1_state_for([{"geometry": _state_square(0.0, 0.0)}], None) == ""


def test_adm1_state_for_no_geometry_returns_empty():
    from commcare_connect.microplans import views

    assert views._adm1_state_for([], None) == ""
    assert views._adm1_state_for(None, {"type": "FeatureCollection", "features": []}) == ""


def test_program_group_bulk_create_from_boundaries(client, django_user_model, monkeypatch):
    """Selecting N admin boundaries on the map creates N boundary-only ward-plans
    filed into the study — no work areas, no arm on the plans (study-groups model)."""
    _login(client, django_user_model)
    groups = {9: _FakeGroup(9, "Kano CHC rooftop impact study", [], kind="study")}
    plans, groups = _make_fake_program_da(monkeypatch, {}, groups)
    geom = _HULL_FC["features"][0]["geometry"]
    resp = client.post(
        reverse(
            "microplans:program_group_bulk_create_from_boundaries",
            kwargs={"program_id": 25, "group_id": 9},
        ),
        data=json.dumps(
            {
                "boundaries": [
                    {"name": "Madobi ward", "lga": "Madobi", "state": "Kano", "boundary_id": "b1", "geometry": geom},
                    {
                        "name": "Kauran Mata ward",
                        "lga": "Madobi",
                        "state": "Kano",
                        "boundary_id": "b2",
                        "geometry": geom,
                    },
                ]
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    plan_ids = resp.json()["plan_ids"]
    assert len(plan_ids) == 2
    # both filed into the study
    assert set(plan_ids) <= set(groups[9].plan_ids)
    assert len(groups[9].plan_ids) == 2
    # boundary-only: no work areas → phase boundary; no arm on the plan; labels captured
    for pid in plan_ids:
        p = plans[pid]
        assert p.work_areas == []
        assert p.phase == "boundary"
        assert p.state == "Kano"
        assert "arm" not in p.data
    names = {plans[pid].name for pid in plan_ids}
    assert names == {"Madobi ward", "Kauran Mata ward"}


def test_program_group_bulk_create_from_boundaries_rejects_empty(client, django_user_model, monkeypatch):
    """An empty boundary list is a 400 — nothing to create."""
    _login(client, django_user_model)
    groups = {9: _FakeGroup(9, "Study", [], kind="study")}
    _make_fake_program_da(monkeypatch, {}, groups)
    resp = client.post(
        reverse(
            "microplans:program_group_bulk_create_from_boundaries",
            kwargs={"program_id": 25, "group_id": 9},
        ),
        data=json.dumps({"boundaries": []}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_program_group_add_from_map_page_renders(client, django_user_model, monkeypatch):
    """The 'Add wards from map' page renders for a study and wires the bulk endpoint."""
    _login(client, django_user_model)
    groups = {9: _FakeGroup(9, "Kano CHC rooftop impact study", [], kind="study")}
    _make_fake_program_da(monkeypatch, {}, groups)
    resp = client.get(reverse("microplans:program_group_add_from_map", kwargs={"program_id": 25, "group_id": 9}))
    assert resp.status_code == 200
    body = resp.content.decode()
    # the bulk-create endpoint URL is wired into the page
    assert (
        reverse("microplans:program_group_bulk_create_from_boundaries", kwargs={"program_id": 25, "group_id": 9})
        in body
    )
    # the study name + the boundary-bulk-picker surface are wired in
    assert "Kano CHC rooftop impact study" in body
    assert "boundary_bulk_picker.js" in body and "Back to study" in body


def test_filter_demo_junk_opps_excludes_test_entries():
    """The delivery-points picker drops obvious test/QA/throwaway opportunities."""
    from commcare_connect.microplans.views import _filter_demo_junk_opps

    opps = [
        {"id": 1, "name": "Kano CHC Nutrition"},
        {"id": 2, "name": "[TO DELETE] Test program"},
        {"id": 3, "name": "DELETE-ME 4"},
        {"id": 4, "name": "Sokoto MNCH [TEST]"},
        {"id": 5, "name": "[DEMO] QA run"},
        {"id": 6, "name": "Lagos Immunization"},
        {"id": 7, "name": ""},  # unnamed → kept (we don't guess)
    ]
    kept = {o["id"] for o in _filter_demo_junk_opps(opps)}
    assert kept == {1, 6, 7}
