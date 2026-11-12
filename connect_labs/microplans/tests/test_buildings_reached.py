"""The buildings-reached page: what it parses, how it rolls up, and what the view answers."""

import json
import time
from unittest.mock import patch

import pytest
from django.urls import reverse

from connect_labs.microplans.coverage import check, page
from connect_labs.microplans.coverage.request import BadRequest, build_spec, parse_facilities, parse_ids


def test_facilities_parse_with_and_without_a_district():
    facilities, labels = parse_facilities("A1,7.1,13.5,D1;A2,7.2,13.6\nA3, 7.3 ,13.7, D2 ")
    assert [f["id"] for f in facilities] == ["A1", "A2", "A3"]
    assert facilities[2] == {"id": "A3", "lon": 7.3, "lat": 13.7}
    assert labels == {"A1": "D1", "A3": "D2"}


@pytest.mark.parametrize(
    "text",
    ["", "A1,7.1", "A1,x,13.5", "A1,13.5,200", "A1,7.1,13.5,D1,extra"],
)
def test_a_bad_facility_list_is_refused_with_a_reason(text):
    with pytest.raises(BadRequest):
        parse_facilities(text)


def test_opportunities_parse_and_are_required():
    assert parse_ids("10184, 10185,x") == [10184, 10185]
    with pytest.raises(BadRequest):
        parse_ids("")


def test_the_spec_takes_defaults_and_overrides():
    spec, labels, opps = build_spec(
        {
            "opps": "1",
            "facilities": "A,7,13,D",
            "max_dist_m": "50",
            "gps_paths": "form.a, form.b",
            "cycle_path": "form.c",
        }
    )
    assert spec.max_dist_m == 50.0 and spec.catchment_k == 260
    assert spec.gps_paths == ("form.a", "form.b") and spec.cycle_path == "form.c"
    assert labels == {"A": "D"} and opps == [1]


def test_a_distance_out_of_range_or_a_bad_path_is_a_bad_request():
    with pytest.raises(BadRequest):
        build_spec({"opps": "1", "facilities": "A,7,13", "max_dist_m": "9000"})
    with pytest.raises(BadRequest):
        build_spec({"opps": "1", "facilities": "A,7,13", "household_path": "form.a; drop table"})


def _result():
    def cyc(b, r):
        return {"households_visited": 10, "households_unplaced": 1, "buildings_reached": r, "share": r / b}

    def area(g, b, cycles):
        return {
            "group": g,
            "buildings": b,
            "households": 10,
            "data_quality": {"no_point": 1, "null_island": 0, "off_area": 0, "poor_accuracy": 1, "usable": 8},
            "cycles": cycles,
        }

    return {
        "areas": [
            area("A1", 100, {1: cyc(100, 50)}),
            area("A2", 100, {1: cyc(100, 70), 2: cyc(100, 90)}),
            area("B1", 50, {1: cyc(50, 25)}),
        ]
    }


def test_rollup_gathers_areas_under_a_label_and_a_cycle_is_out_of_the_areas_that_ran_it():
    out = {r["label"]: r for r in check.rollup(_result(), {"A1": "D1", "A2": "D1", "B1": "D2"})}
    d1 = out["D1"]
    assert d1["areas"] == 2 and d1["buildings"] == 200
    assert d1["cycles"][1]["share"] == 0.6  # 120 of 200
    assert d1["cycles"][2]["share"] == 0.9  # only A2 ran cycle 2: 90 of its 100, not of 200
    assert d1["data_quality"]["usable"] == 16 and d1["households"] == 20
    assert out["D2"]["cycles"][1]["share"] == 0.5


def test_an_area_with_no_label_is_not_dropped():
    out = check.rollup(_result(), {"A1": "D1"})
    assert {r["label"] for r in out} == {"D1", "(none)"}


# --- the view ----------------------------------------------------------------


@pytest.fixture
def person(client, django_user_model):
    user = django_user_model.objects.create(username="reader", email="r@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "t", "expires_at": time.time() + 3600}
    session.save()
    return user


@pytest.mark.django_db
def test_the_page_and_its_endpoint_need_a_login(client):
    assert client.get(reverse("microplans:buildings_reached")).status_code == 302
    assert (
        client.post(
            reverse("microplans:buildings_reached_run"), data="{}", content_type="application/json"
        ).status_code
        == 302
    )


@pytest.mark.django_db
def test_the_page_renders_for_a_person(client, person):
    resp = client.get(reverse("microplans:buildings_reached"))
    assert resp.status_code == 200 and b"Buildings reached" in resp.content


@pytest.mark.django_db
def test_a_bad_request_is_a_400_with_the_reason(client, person):
    resp = client.post(
        reverse("microplans:buildings_reached_run"), data=json.dumps({"opps": "1"}), content_type="application/json"
    )
    assert resp.status_code == 400 and "facility" in resp.json()["message"]


@pytest.mark.django_db
def test_a_run_loads_what_is_missing_reads_as_the_person_and_returns_counts_only(client, person):
    rows = [["A1", "hh1", "13.50005 7.00005 0.0 5.0", "contact_1"]]
    seen = {}

    def read_households(caller, space, spec, agent=False):
        seen["caller_user"], seen["agent"] = caller.user, agent
        return rows

    class Space:
        class selection:
            opportunities = [type("O", (), {"id": 1})()]

    body = {"opps": "1", "facilities": "A1,7.0,13.5,D1", "catchment_k": 10}
    with (
        patch.object(page.spaces, "from_request", lambda caller, **k: Space()),
        patch.object(page.service, "warm", lambda caller, opps: [{"opportunity_id": 1, "loaded_visits": 5}]),
        patch.object(page.read, "read_households", read_households),
        patch.object(
            page.check,
            "default_fetch",
            lambda f: lambda lon, lat, r: __import__("pandas").DataFrame(
                {"lon": [7.0, 7.0001], "lat": [13.5, 13.5], "area_m2": [50.0, 50.0]}
            ),
        ),
    ):
        resp = client.post(
            reverse("microplans:buildings_reached_run"), data=json.dumps(body), content_type="application/json"
        )
    assert resp.status_code == 200
    out = resp.json()
    assert seen == {"caller_user": person, "agent": False}
    assert out["opportunities"] == [1] and out["total"]["buildings"] == 2
    assert [r["label"] for r in out["rollup"]] == ["D1"]
    assert "hh1" not in resp.content.decode() and "13.50005" not in resp.content.decode()


@pytest.mark.django_db
def test_a_load_that_failed_is_said_not_answered_with_zeros(client, person):
    class Space:
        class selection:
            opportunities = []

    with (
        patch.object(page.spaces, "from_request", lambda caller, **k: Space()),
        patch.object(page.service, "warm", lambda caller, opps: [{"opportunity_id": 9, "error": "no Connect token"}]),
    ):
        resp = client.post(
            reverse("microplans:buildings_reached_run"),
            data=json.dumps({"opps": "9", "facilities": "A1,7,13"}),
            content_type="application/json",
        )
    assert resp.status_code == 400 and "no Connect token" in resp.json()["message"]
