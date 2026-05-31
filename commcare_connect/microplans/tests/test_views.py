"""View tests for the microplans setup flow.

generate_frame is patched out — it hits Overture S3, which isn't a unit-test
dependency. We assert the view's request handling: auth gate, payload
validation, error mapping, and the response envelope.
"""

from __future__ import annotations

import json
import time

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


def test_preview_maps_sampling_failure_to_502(client, django_user_model, monkeypatch):
    _login(client, django_user_model)

    def boom(*a, **k):
        raise RuntimeError("overture down")

    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", boom)
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": [{"arm": "intervention", "geometry": {"type": "Point", "coordinates": [0, 0]}}]}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    # generic message — the internal exception text must NOT leak to the client
    assert "overture down" not in resp.json()["detail"]
    assert "server logs" in resp.json()["detail"].lower()


def test_preview_bad_config_is_400_not_500(client, django_user_model):
    # A non-numeric config value must surface as 400 (config parsing is inside the
    # request-validation try), not crash with a 500.
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


def test_preview_maps_value_error_to_400(client, django_user_model, monkeypatch):
    _login(client, django_user_model)

    def too_big(*a, **k):
        raise ValueError("Area is too large (~5,000 km²); draw a smaller area.")

    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", too_big)
    resp = client.post(
        reverse("microplans:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": [{"arm": "intervention", "geometry": {"type": "Point", "coordinates": [0, 0]}}]}),
        content_type="application/json",
    )
    assert resp.status_code == 400  # actionable user error surfaces
    assert "too large" in resp.json()["detail"]


def test_preview_happy_path(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    fake = FrameResult(
        pins_geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [13.1, 11.8]},
                    "properties": {"role": "primary", "cluster": "C0", "arm": "intervention"},
                }
            ],
        },
        hulls_geojson={"type": "FeatureCollection", "features": []},
        stats=[
            {
                "arm": "intervention",
                "psus_selected": 1,
                "pins": 1,
                "primaries": 1,
                "alternates": 0,
                "after_filters": 10,
            }
        ],
    )
    monkeypatch.setattr("commcare_connect.microplans.sampling.frame.generate_frame", lambda areas, config: fake)
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
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["pins"]["features"][0]["properties"]["role"] == "primary"
    assert body["stats"][0]["psus_selected"] == 1


def test_preview_coverage_happy_path(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
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
        stats=[
            {
                "arm": "coverage",
                "strategy": "balanced",
                "after_filters": 42,
                "work_areas": 1,
                "min_buildings": 42,
                "median_buildings": 42,
                "max_buildings": 42,
            }
        ],
    )
    monkeypatch.setattr(
        "commcare_connect.microplans.coverage.frame.generate_coverage_frame", lambda areas, config: fake
    )
    resp = client.post(
        reverse("microplans:preview_coverage", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [
                    {
                        "arm": "coverage",
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                    }
                ],
                "config": {"strategy": "balanced"},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["areas"]["features"][0]["properties"]["building_count"] == 42
    assert body["stats"][0]["strategy"] == "balanced"


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
    def __init__(self, pid, mode, work_areas, name="", region="", status="draft", opportunity_id=None):
        self.id, self.mode, self.work_areas = pid, mode, work_areas
        self.name = name or f"Plan {pid}"
        self.region, self.status, self.opportunity_id = region, status, opportunity_id
        self.status_log = []
        self.created_at = "2026-05-28T00:00:00Z"
        # Real backing dict for `data` so tests can mutate e.g. plan.data["grouping"]
        # and have the mutation stick. Mirrors how the real proxy model exposes data.
        self._data = {
            "work_areas": self.work_areas,
            "status": self.status,
            "region": self.region,
            "opportunity_id": self.opportunity_id,
            "status_log": self.status_log,
            "mode": self.mode,
            "name": self.name,
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


class _FakeGroup:
    def __init__(self, gid, name, plan_ids, offered_to="", shared=False):
        self.id, self.name, self.plan_ids = gid, name, list(plan_ids)
        self.offered_to, self.shared = offered_to, shared


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

        def create_plan(self, region, name, mode, pins, hulls, input_areas=None, grouping=None):
            was = plan_lib.materialize_work_areas(mode, pins, hulls)
            pid = seq["plan"]
            seq["plan"] += 1
            plans[pid] = _FakeProgramPlan(pid, mode, was, name=name, region=region)
            return plans[pid]

        def regenerate_plan(self, pid, mode, pins, hulls, input_areas, grouping=None):
            was = plan_lib.materialize_work_areas(mode, pins, hulls, grouping=grouping)
            p = plans[int(pid)]
            p.work_areas = was
            p.mode = mode
            p.data["mode"] = mode
            p.data["work_areas"] = was
            p.data["input_areas"] = list(input_areas or [])
            p.data["grouping"] = dict(grouping or {})
            p.data["assignment"] = {}
            return p

        def apply_plan_edits(self, pid, wa_ids, action, params, actor):
            p = plans[int(pid)]
            for wa_id in wa_ids:
                wa = plan_lib.find(p.work_areas, wa_id)
                if wa is None:
                    raise ValueError(f"work area {wa_id!r} not found")
                plan_lib.apply_action(wa, action, params, actor)
            return p

        def transition_plan(self, pid, to, actor, opportunity_id=None):
            p = plans[int(pid)]
            data = dict(p.data)
            plan_lib.transition_plan(data, to, actor, opportunity_id=opportunity_id)
            p.status = data["status"]
            p.opportunity_id = data.get("opportunity_id")
            p.status_log = data.get("status_log", [])
            return p

        def create_group(self, name, plan_ids, offered_to=""):
            gid = seq["group"]
            seq["group"] += 1
            groups[gid] = _FakeGroup(gid, name, plan_ids, offered_to)
            return groups[gid]

        def list_groups(self):
            return list(groups.values())

        def get_group(self, gid):
            return groups[int(gid)]

        def update_group(self, gid, **fields):
            g = groups[int(gid)]
            for key in ("name", "offered_to", "shared"):
                if fields.get(key) is not None:
                    setattr(g, key, fields[key])
            if fields.get("plan_ids") is not None:
                g.plan_ids = [int(x) for x in fields["plan_ids"]]
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


def test_program_create_plan(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    plans, _ = _make_fake_program_da(monkeypatch, {}, {})
    resp = client.post(
        reverse("microplans:program_create_plan", kwargs={"program_id": 25}),
        data=json.dumps({"region": "Zaria", "name": "Zaria v1", "mode": "coverage", "coverage_areas": _HULL_FC}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    pid = resp.json()["plan_id"]
    assert plans[pid].region == "Zaria" and plans[pid].status == "draft"
    assert len(plans[pid].work_areas) == 2


def test_program_regenerate_replaces_work_areas(client, django_user_model, monkeypatch):
    # Regenerate wipes the work areas + resets grouping / assignment configs
    # — destructive equivalent of "create new plan with these settings", but
    # the plan keeps its id, name, region.
    _login(client, django_user_model)
    plans, _ = _seed_program_plans(monkeypatch)
    # Seed a CHW assignment so we can confirm it gets wiped
    plans[1].data["assignment"] = {"strategy": "minimax_spread", "workers": ["chw-1"]}
    plans[1].data["grouping"] = {"strategy": "bfs_adjacency", "max_buildings": 200}
    resp = client.post(
        reverse("microplans:program_plan_regenerate", kwargs={"program_id": 25, "plan_id": 1}),
        data=json.dumps(
            {
                "mode": "coverage",
                "coverage_areas": _HULL_FC,
                "input_areas": [{"geometry": _HULL_FC["features"][0]["geometry"]}],
                "grouping": {"strategy": "bbox", "target_size": 30},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # New work areas were generated
    assert len(body["work_areas"]) == 2  # _HULL_FC has 2 features
    # Grouping config got replaced
    assert plans[1].data["grouping"] == {"strategy": "bbox", "target_size": 30}
    # Assignment is wiped
    assert plans[1].data["assignment"] == {}
    # Plan identity preserved
    assert plans[1].id == 1


def test_program_create_plan_page_renders(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:program_create_plan_page", kwargs={"program_id": 25}))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert resp.context["program_id"] == 25
    assert resp.context["plan_id"] is None
    # Unified template: new-plan page shows the "New microplan" header + the
    # Create plan button (vs. the per-plan "Microplan review" + "Apply
    # geographic frame" button on the existing-plan flow).
    assert "New microplan" in body
    assert "Create plan" in body


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
