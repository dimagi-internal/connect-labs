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
    assert b"mapbox-gl.js" in resp.content and b"var MAPBOX_TOKEN" in resp.content


@pytest.mark.django_db
def test_a_bad_request_is_a_400_with_the_reason(client, person):
    resp = client.post(
        reverse("microplans:buildings_reached_run"), data=json.dumps({"opps": "1"}), content_type="application/json"
    )
    assert resp.status_code == 400 and "facility" in resp.json()["message"]


@pytest.mark.django_db
def test_a_run_reads_as_the_person_and_returns_counts_only(client, person):
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
    assert out["areas"][0]["district"] == "D1" and out["areas"][0]["cells"]["size_m"] == 100.0
    assert out["rollup"][0]["center"] == [7.0, 13.5]
    assert [r["label"] for r in out["rollup"]] == ["D1"]
    assert "hh1" not in resp.content.decode() and "13.50005" not in resp.content.decode()


@pytest.mark.django_db
def test_the_load_step_loads_what_is_missing_in_its_own_request(client, person):
    """Loading and reading in one request read an empty campaign on a cold cache: the loader writes inside the
    request's transaction (ATOMIC_REQUESTS) and the Data query reads over another connection. The page therefore
    loads first and reads second, and a run never loads."""

    class Space:
        class selection:
            opportunities = [type("O", (), {"id": 1})(), type("O", (), {"id": 2})()]

    called = []
    with (
        patch.object(page.spaces, "from_request", lambda caller, **k: Space()),
        patch.object(page.service, "warm", lambda caller, opps: called.append(len(opps)) or [{"opportunity_id": 1}]),
    ):
        resp = client.post(
            reverse("microplans:buildings_reached_load"),
            data=json.dumps({"opps": "1,2"}),
            content_type="application/json",
        )
    assert resp.status_code == 200 and resp.json()["opportunities"] == [1, 2] and called == [2]


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
            reverse("microplans:buildings_reached_load"),
            data=json.dumps({"opps": "9"}),
            content_type="application/json",
        )
    assert resp.status_code == 400 and "no Connect token" in resp.json()["message"]


@pytest.mark.django_db
def test_a_run_never_loads(client, person):
    class Space:
        class selection:
            opportunities = [type("O", (), {"id": 1})()]

    def boom(*a, **k):
        raise AssertionError("a run must not load")

    with (
        patch.object(page.spaces, "from_request", lambda caller, **k: Space()),
        patch.object(page.service, "warm", boom),
        patch.object(page.read, "read_households", lambda *a, **k: []),
        patch.object(
            page.check,
            "default_fetch",
            lambda f: lambda lon, lat, r: __import__("pandas").DataFrame(
                {"lon": [7.0], "lat": [13.5], "area_m2": [50.0]}
            ),
        ),
    ):
        resp = client.post(
            reverse("microplans:buildings_reached_run"),
            data=json.dumps({"opps": "1", "facilities": "A1,7.0,13.5"}),
            content_type="application/json",
        )
    assert resp.status_code == 200 and resp.json()["households_total"] == 0


@pytest.mark.django_db
def test_the_page_carries_the_csrf_token_the_run_needs(django_user_model):
    """Labs keeps the CSRF token in the session and hides the cookie (CSRF_USE_SESSIONS, HttpOnly), so a script
    that reads ``document.cookie`` posts with no token and gets a 403. The page must render the token itself.
    The default test client skips CSRF checks, which is how that shipped; this one enforces them."""
    import re

    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    user = django_user_model.objects.create(username="csrf-reader", email="c@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "t", "expires_at": time.time() + 3600}
    session.save()
    run_url = reverse("microplans:buildings_reached_run")
    page_html = client.get(reverse("microplans:buildings_reached")).content.decode()
    token = re.search(r'var CSRF = "([^"]+)"', page_html).group(1)
    assert token and token != "{{ csrf_token }}"
    body = json.dumps({"opps": "1"})  # incomplete on purpose: a 400 means the token was accepted
    assert client.post(run_url, data=body, content_type="application/json").status_code == 403
    ok = client.post(run_url, data=body, content_type="application/json", HTTP_X_CSRFTOKEN=token)
    assert ok.status_code == 400 and ok.json()["error"] == "bad_request"


def _population_patches(rows, served_rows):
    import numpy as np

    from connect_labs.microplans.coverage import population

    class Space:
        class selection:
            opportunities = [type("O", (), {"id": 1})()]

    grid = population.Grid(np.full((6, 6), 800.0), 6.9, 13.6, 0.05, -0.05)
    return (
        patch.object(page.spaces, "from_request", lambda caller, **k: Space()),
        patch.object(page.read, "read_households", lambda *a, **k: rows),
        patch.object(page.read, "read_served", lambda *a, **k: served_rows),
        patch.object(page.population, "fetch_grid", lambda iso, bbox: grid),
    )


@pytest.mark.django_db
def test_the_population_lens_places_children_served_on_the_grid_and_returns_counts_only(client, person):
    rows = [["A1", f"hh{i}", "13.50005 7.00005 0.0 5.0", "contact_1"] for i in range(6)]
    served_rows = [[f"hh{i}", "contact_1", 2] for i in range(6)]
    a, b, c, d = _population_patches(rows, served_rows)
    body = {"opps": "1", "facilities": "A1,7.0,13.5,D1", "iso": "ner"}
    with a, b, c, d:
        resp = client.post(
            reverse("microplans:buildings_reached_population"), data=json.dumps(body), content_type="application/json"
        )
    assert resp.status_code == 200
    out = resp.json()
    assert out["iso"] == "NER" and out["cycles"] == [1] and out["quality"]["usable"] == 6
    assert out["districts"][0]["served"] == {"1": 12} and out["total"]["cells"] == 1
    text = resp.content.decode()
    assert "hh0" not in text and "13.50005" not in text


@pytest.mark.django_db
def test_the_population_lens_names_the_missing_country_in_words(client, person):
    resp = client.post(
        reverse("microplans:buildings_reached_population"),
        data=json.dumps({"opps": "1", "facilities": "A1,7.0,13.5"}),
        content_type="application/json",
    )
    assert resp.status_code == 400 and "three-letter code" in resp.json()["message"]


def test_the_served_read_shards_by_household_and_cycle_and_doubles_on_a_cut_read():
    """One row per household and cycle: shards sized by households alone overran the row cap on the live deploy."""
    from connect_labs.labsdata import service
    from connect_labs.microplans.coverage import read

    calls = []

    def query(caller, space, sql, max_rows=None, *, for_agent=False, shaped=True):
        calls.append(sql)
        if sql.startswith("SELECT count("):
            return {"rows": [[3000]], "truncated": False}
        return {"rows": [["hh", "contact_1", 1]], "truncated": len(calls) <= 2}  # the first served read is cut

    spec = check.CheckSpec.from_dict({"facilities": [{"id": "A", "lon": 7.0, "lat": 13.5}], "catchment_k": 60})
    with patch.object(service, "query", query):
        rows = read.read_served(None, None, spec)
    served = [c for c in calls if "count(DISTINCT" in c and "spaq_recu" in c.replace("'", "")]
    assert rows and len(check.shard_bounds(3000 * 8)) > 1
    assert len(served) > len(check.shard_bounds(3000 * 8))  # the cut read was retried on more shards
