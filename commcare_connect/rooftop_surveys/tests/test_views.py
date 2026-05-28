"""View tests for the rooftop_surveys setup flow.

generate_frame is patched out — it hits Overture S3, which isn't a unit-test
dependency. We assert the view's request handling: auth gate, payload
validation, error mapping, and the response envelope.
"""

from __future__ import annotations

import json
import time

import pytest
from django.urls import reverse

from commcare_connect.rooftop_surveys.sampling.frame import FrameResult

pytestmark = pytest.mark.django_db


def _login(client, django_user_model):
    user = django_user_model.objects.create(username="tester", email="t@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "test-token", "expires_at": time.time() + 3600}
    session.save()
    return user


def test_setup_requires_login(client):
    resp = client.get(reverse("rooftop_surveys:setup", kwargs={"opp_id": 123}))
    assert resp.status_code == 302
    assert "/labs/login/" in resp["Location"]


def test_setup_renders_with_context(client, django_user_model, settings):
    settings.MAPBOX_TOKEN = "pk.test"
    _login(client, django_user_model)
    resp = client.get(reverse("rooftop_surveys:setup", kwargs={"opp_id": 123}))
    assert resp.status_code == 200
    assert resp.context["opp_id"] == 123
    assert resp.context["mapbox_token"] == "pk.test"
    assert "rooftop-map" in resp.content.decode()


def test_preview_rejects_empty_areas(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("rooftop_surveys:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": [], "config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.json()["status"] == "error"


def test_preview_rejects_malformed_body(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("rooftop_surveys:preview_frame", kwargs={"opp_id": 123}),
        data="not json",
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_preview_maps_sampling_failure_to_502(client, django_user_model, monkeypatch):
    _login(client, django_user_model)

    def boom(*a, **k):
        raise RuntimeError("overture down")

    monkeypatch.setattr("commcare_connect.rooftop_surveys.sampling.frame.generate_frame", boom)
    resp = client.post(
        reverse("rooftop_surveys:preview_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": [{"arm": "intervention", "geometry": {"type": "Point", "coordinates": [0, 0]}}]}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert "overture down" in resp.json()["detail"]


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
    monkeypatch.setattr("commcare_connect.rooftop_surveys.sampling.frame.generate_frame", lambda areas, config: fake)
    resp = client.post(
        reverse("rooftop_surveys:preview_frame", kwargs={"opp_id": 123}),
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

    class FakeDA:
        def __init__(self, *a, **k):
            pass

        def save_area(self, areas, config, name=""):
            return FakeRecord(11)

        def save_frame(self, area_record_id, pins, hulls, stats):
            assert area_record_id == 11
            return FakeRecord(22)

    monkeypatch.setattr("commcare_connect.rooftop_surveys.data_access.RooftopDataAccess", FakeDA)
    resp = client.post(
        reverse("rooftop_surveys:save_frame", kwargs={"opp_id": 123}),
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


def test_save_frame_rejects_missing_pins(client, django_user_model):
    _login(client, django_user_model)
    resp = client.post(
        reverse("rooftop_surveys:save_frame", kwargs={"opp_id": 123}),
        data=json.dumps({"areas": []}),  # no "pins"
        content_type="application/json",
    )
    assert resp.status_code == 400
