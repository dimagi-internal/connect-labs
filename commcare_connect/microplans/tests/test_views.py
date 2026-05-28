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
