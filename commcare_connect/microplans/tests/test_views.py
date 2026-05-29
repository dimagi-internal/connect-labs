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


def test_setup_requires_login(client):
    resp = client.get(reverse("microplans:setup", kwargs={"opp_id": 123}))
    assert resp.status_code == 302
    assert "/labs/login/" in resp["Location"]


def test_setup_renders_with_context(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:setup", kwargs={"opp_id": 123}))
    assert resp.status_code == 200
    assert resp.context["opp_id"] == 123
    assert resp.context["mapbox_token"] == "pk.test"
    assert "rooftop-map" in resp.content.decode()


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
                "areas": [{"arm": "coverage", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
                "config": {"buildings_per_cluster": "abc"},
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


def test_save_frame_persists_area_and_frame(client, django_user_model, monkeypatch):
    _login(client, django_user_model)

    class FakeRecord:
        def __init__(self, rid):
            self.id = rid

    captured = {}

    class FakeDA:
        def __init__(self, *a, **k):
            pass

        def save_area(self, areas, config, name="", mode="sampling"):
            captured["area_mode"] = mode
            return FakeRecord(11)

        def save_frame(self, area_record_id, pins, hulls, stats, mode="sampling"):
            assert area_record_id == 11
            captured["frame_mode"] = mode
            captured["pins"] = pins
            captured["hulls"] = hulls
            return FakeRecord(22)

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.RooftopDataAccess", FakeDA)
    resp = client.post(
        reverse("microplans:save_frame", kwargs={"opp_id": 123}),
        data=json.dumps(
            {
                "areas": [{"arm": "intervention", "geometry": {"type": "Point", "coordinates": [0, 0]}}],
                "pins": {"type": "FeatureCollection", "features": []},
                "hulls": {"type": "FeatureCollection", "features": []},
                "stats": [],
                "config": {},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["area_record_id"] == 11
    assert body["frame_record_id"] == 22
    assert captured["area_mode"] == "sampling" and captured["frame_mode"] == "sampling"


def test_save_coverage_routes_areas_to_hulls(client, django_user_model, monkeypatch):
    _login(client, django_user_model)

    class FakeRecord:
        def __init__(self, rid):
            self.id = rid

    captured = {}

    class FakeDA:
        def __init__(self, *a, **k):
            pass

        def save_area(self, areas, config, name="", mode="sampling"):
            captured["area_mode"] = mode
            return FakeRecord(1)

        def save_frame(self, area_record_id, pins, hulls, stats, mode="sampling"):
            captured.update(frame_mode=mode, pins=pins, hulls=hulls)
            return FakeRecord(2)

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.RooftopDataAccess", FakeDA)
    cov = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"building_count": 5}}]}
    resp = client.post(
        reverse("microplans:save_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"mode": "coverage", "areas": [], "coverage_areas": cov, "stats": [], "config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert captured["frame_mode"] == "coverage" and captured["area_mode"] == "coverage"
    # coverage polygons land in hulls; pins stays empty
    assert captured["hulls"] == cov
    assert captured["pins"]["features"] == []


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


def test_coverage_csv_export(client, django_user_model):
    _login(client, django_user_model)
    cov = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[13.0, 11.0], [13.1, 11.0], [13.1, 11.1], [13.0, 11.1], [13.0, 11.0]]],
                },
                "properties": {"arm": "coverage", "cluster": "C1", "building_count": 80},
            }
        ],
    }
    resp = client.post(
        reverse("microplans:work_areas_csv", kwargs={"opp_id": 123}),
        data=json.dumps({"mode": "coverage", "coverage_areas": cov, "lga": "Maiduguri", "state": "Borno"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Area Slug" in body and "Boundary" in body
    assert "POLYGON" in body  # cluster hull WKT
    assert "80" in body  # building/expected-visit count


def test_save_frame_rejects_missing_pins(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("microplans:save_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": []}),  # no "pins"
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_work_areas_csv_export(client, django_user_model):
    _login(client, django_user_model)
    pins = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [13.155, 11.832]},
                "properties": {"arm": "intervention", "cluster": "C1", "role": "primary", "order_in_cluster": 1},
            }
        ],
    }
    resp = client.post(
        reverse("microplans:work_areas_csv", kwargs={"opp_id": 123}),
        data=json.dumps({"pins": pins, "lga": "Maiduguri", "state": "Borno"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    body = resp.content.decode()
    assert "Area Slug" in body and "Centroid" in body and "Boundary" in body
    assert "13.155 11.832" in body
    assert "Maiduguri" in body


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


class _FakePlan:
    def __init__(self, pid, mode, work_areas):
        self.id, self.mode, self.work_areas = pid, mode, work_areas
        self.data = {"work_areas": work_areas}


def _make_fake_da(monkeypatch, store):
    """A RooftopDataAccess stand-in that runs the real plan logic over an in-memory store."""
    from commcare_connect.microplans.core import plan as plan_lib

    class _Frame:
        mode, pins, hulls = "coverage", _EMPTY_FC, _HULL_FC

    class _Api:
        def get_record_by_id(self, rid, model_class=None):
            return _Frame()

    class FakeDA:
        def __init__(self, *a, **k):
            self.labs_api = _Api()

        def materialize_plan(self, frame, name=""):
            was = plan_lib.materialize_work_areas(frame.mode, frame.pins, frame.hulls)
            store[1] = _FakePlan(1, frame.mode, was)
            return store[1]

        def get_plan(self, pid):
            return store[int(pid)]

        def apply_plan_edit(self, pid, wa_id, action, params, actor):
            p = store[int(pid)]
            wa = plan_lib.find(p.work_areas, wa_id)
            if wa is None:
                raise ValueError(f"work area {wa_id!r} not found")
            plan_lib.apply_action(wa, action, params, actor)
            return p

    monkeypatch.setattr("commcare_connect.microplans.core.data_access.RooftopDataAccess", FakeDA)
    return store


def test_materialize_plan(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_da(monkeypatch, {})
    resp = client.post(
        reverse("microplans:plan_materialize", kwargs={"opp_id": 1}),
        data=json.dumps({"frame_record_id": 42}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["plan_id"] == 1
    assert len(body["work_areas"]) == 2
    assert body["summary"]["active"] == 2 and body["summary"]["excluded"] == 0


def test_plan_edit_exclude_with_reason(client, django_user_model, monkeypatch):
    user = _login(client, django_user_model)
    store = _make_fake_da(monkeypatch, {})
    from commcare_connect.microplans.core import plan as plan_lib

    store[1] = _FakePlan(1, "coverage", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC))
    wa_id = store[1].work_areas[0]["id"]
    resp = client.post(
        reverse("microplans:plan_edit", kwargs={"opp_id": 1, "plan_id": 1}),
        data=json.dumps({"action": "exclude", "wa_id": wa_id, "reason": "lake"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["excluded"] == 1
    wa = next(w for w in body["work_areas"] if w["id"] == wa_id)
    assert wa["status"] == "EXCLUDED" and wa["excluded_reason"] == "lake"
    ev = wa["audit"][-1]
    assert ev["phase"] == "planning" and ev["actor"] == user.username and ev["action"] == "exclude"


def test_plan_edit_bulk_reassign(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.core import plan as plan_lib

    store = _make_fake_da(monkeypatch, {})
    store[1] = _FakePlan(1, "coverage", plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC))
    ids = [w["id"] for w in store[1].work_areas]
    resp = client.post(
        reverse("microplans:plan_edit", kwargs={"opp_id": 1, "plan_id": 1}),
        data=json.dumps({"action": "reassign", "wa_ids": ids, "opportunity_access": "flw-3"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert all(w["opportunity_access"] == "flw-3" for w in resp.json()["work_areas"])


def test_plan_edit_bad_action_is_400(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    _make_fake_da(monkeypatch, {})
    resp = client.post(
        reverse("microplans:plan_edit", kwargs={"opp_id": 1, "plan_id": 1}),
        data=json.dumps({"action": "delete_everything", "wa_id": "x"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_plan_csv_skips_excluded(client, django_user_model, monkeypatch):
    _login(client, django_user_model)
    from commcare_connect.microplans.core import plan as plan_lib

    store = _make_fake_da(monkeypatch, {})
    was = plan_lib.materialize_work_areas("coverage", _EMPTY_FC, _HULL_FC)
    plan_lib.apply_action(was[1], "exclude", {"reason": "invalid"}, "u")
    store[1] = _FakePlan(1, "coverage", was)
    resp = client.post(
        reverse("microplans:plan_csv", kwargs={"opp_id": 1, "plan_id": 1}),
        data=json.dumps({"lga": "Eti Osa", "state": "Lagos"}),
        content_type="application/json",
    )
    assert resp.status_code == 200 and resp["Content-Type"] == "text/csv"
    body = resp.content.decode()
    assert "Area Slug" in body
    assert body.count("POLYGON") == 1  # only the non-excluded area exported


def test_review_page_renders(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("microplans:review", kwargs={"opp_id": 1, "plan_id": 7}))
    assert resp.status_code == 200
    assert resp.context["plan_id"] == 7
    body = resp.content.decode()
    assert "Microplan review" in body and "review-map" in body
