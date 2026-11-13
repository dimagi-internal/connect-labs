"""The uploaded-household-CSV path and the denominators panel (pure: no Django needed for the first block)."""

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from connect_labs.microplans.coverage import population as P
from connect_labs.microplans.coverage import upload
from connect_labs.microplans.coverage.request import BadRequest, parse_facilities, parse_targets

HEADER = "facility_id,household_id,lat,lon,cycle,children_served,accuracy_m\n"


def csv(*lines):
    return (HEADER + "\n".join(lines) + "\n").encode()


def test_upload_rows_become_households_with_a_point_cycles_and_children_served():
    rows, served, summary = upload.parse(
        csv("F1,h1,13.5,7.0,1,2,10", "F1,h1,13.5,7.0,cycle 2,1,10", "F2,h2,13.4,7.1,1,0,5", "F2,h3,,,1,1,")
    )
    by = {r[1]: r for r in rows}
    assert by["h1"][0] == "F1" and by["h1"][3] == "1,cycle 2" and by["h1"][2].startswith("13.5000000 7.0000000")
    assert by["h3"][2] is None  # a household with no usable point still counts, unplaced
    assert served == {"h1": {1: 2, 2: 1}, "h2": {1: 0}, "h3": {1: 1}}
    assert summary["households"] == 3 and summary["bad_rows"] == 0


def test_the_template_parses_and_its_example_comment_is_ignored():
    rows, served, summary = upload.parse(upload.TEMPLATE.encode())
    assert summary["households"] == 2 and served["hh-0001"] == {1: 2, 2: 1}


@pytest.mark.parametrize(
    "data, fragment",
    [
        (b"a,b\n1,2\n", "missing column"),
        (HEADER.encode(), "no rows"),
        (csv(*["F1,,13.5,7.0,1,x,5"] * 6), "unreadable"),
    ],
)
def test_a_wrong_shaped_file_says_what_to_fix(data, fragment):
    with pytest.raises(upload.UploadError) as err:
        upload.parse(data)
    assert fragment in str(err.value)


def test_a_few_dirty_rows_are_counted_not_fatal():
    good = ["F1,h%d,13.5,7.0,1,1,5" % i for i in range(20)]
    rows, served, summary = upload.parse(csv(*good, "F1,h99,13.5,7.0,1,-3,5"))
    assert summary["bad_rows"] == 1 and summary["bad_row_examples"] == [22] and "h99" not in served


def test_an_oversize_file_is_refused_with_a_way_out():
    with pytest.raises(upload.UploadError) as err:
        upload.parse(b"x" * (upload.MAX_BYTES + 1))
    assert "split it by district" in str(err.value)


def test_the_optional_fifth_facility_column_is_a_target_and_must_be_a_number():
    assert parse_targets("A,7,13,D1,250;B,7,13,D2;C,7,13,D2, 1 000") == {"A": 250.0, "C": 1000.0}
    assert parse_facilities("A,7,13,D1,250")[1] == {"A": "D1"}
    with pytest.raises(BadRequest):
        parse_targets("A,7,13,D1,lots")


# --- denominators ------------------------------------------------------------------------------------------


def lens_fixture():
    values = np.full((10, 10), 1000.0)
    grid = P.crop(values, 8.0, 14.0, 0.01, -0.01, (8.0, 13.9, 8.1, 14.0), pad_cells=0)
    placed = pd.DataFrame(
        {
            "grp": ["A"] * 6,
            "hh": [f"h{i}" for i in range(6)],
            "lon": [8.025] * 6,
            "lat": [13.975] * 6,
            "cycles": [{1}] * 6,
        }
    )
    served = {f"h{i}": {1: 2} for i in range(6)}
    return grid, placed, served, P.population_lens(placed, served, grid, {"A": "D1"})


def unit(geom, name="Dept X"):
    return SimpleNamespace(geometry=geom, name=name, level=2, parent_name="Region Y")


def test_a_unit_denominator_sums_the_grid_cells_inside_the_polygon():
    grid, *_ = lens_fixture()
    assert P.eligible_in(grid, box(8.0, 13.95, 8.05, 14.0)) == 25 * 1000 * 0.95


def test_denominators_put_squares_unit_and_target_side_by_side_with_their_range():
    grid, placed, served, lens = lens_fixture()
    out = P.denominators(placed, served, grid, [unit(box(8.0, 13.95, 8.05, 14.0))], lens, {"A": "D1"}, {"A": 20})
    (d,) = out["districts"]
    assert d["unit"]["eligible"] == 23750.0 and d["unit"]["units"][0]["name"] == "Dept X"
    assert d["unit"]["touched_share"] == pytest.approx(950 / 23750)  # the campaign's square holds 4% of the unit
    assert d["target"]["share"][1] == pytest.approx(12 / 20)
    # the campaign's squares hold only 4% of the unit, so the unit stays out of the range
    assert d["range_excludes"] == ["unit"]
    low, high = d["range"][1]
    assert low == pytest.approx(12 / 950) and high == pytest.approx(12 / 20)


def test_a_unit_the_campaign_mostly_covers_is_in_the_range():
    grid, placed, served, lens = lens_fixture()
    (d,) = P.denominators(placed, served, grid, [unit(box(8.02, 13.97, 8.03, 13.98))], lens, {"A": "D1"}, {})[
        "districts"
    ]
    assert d["unit"]["touched_share"] >= P.UNIT_FAIR_TOUCHED_SHARE and d["range_excludes"] == []
    assert d["range"][1][0] <= d["unit"]["share"][1] <= d["range"][1][1]


def test_no_unit_found_leaves_the_other_denominators_standing():
    grid, placed, served, lens = lens_fixture()
    (d,) = P.denominators(placed, served, grid, [], lens, {"A": "D1"}, {})["districts"]
    assert d["unit"]["eligible"] is None and d["target"] is None
    assert d["range"][1] == [d["squares"]["share"][1]] * 2


# --- the endpoints (Django) -----------------------------------------------------------------------------------


pytestmark_db = pytest.mark.django_db


@pytest.fixture
def person(client, django_user_model):
    user = django_user_model.objects.create(username="uploader", email="u@example.com")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "t", "expires_at": time.time() + 3600}
    session.save()
    return user


def _village_csv():
    lines = [f"F1,h{i},{13.5 + (i % 5) * 0.0001:.5f},{7.0 + (i // 5) * 0.0001:.5f},1,2,5" for i in range(30)]
    return csv(*lines)


@pytest.mark.django_db
def test_the_template_downloads_as_a_csv(client, person):
    from django.urls import reverse

    resp = client.get(reverse("microplans:buildings_reached_template"))
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    assert b"facility_id,household_id" in resp.content


@pytest.mark.django_db
def test_an_upload_runs_the_buildings_lens_in_memory_and_returns_counts_only(client, person):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse

    from connect_labs.microplans.coverage import page

    buildings = pd.DataFrame({"lon": [7.0, 7.0003, 7.0006], "lat": [13.5, 13.5003, 13.5006], "area_m2": [50.0] * 3})
    with patch.object(page.check, "default_fetch", lambda f: lambda lon, lat, r: buildings):
        resp = client.post(
            reverse("microplans:buildings_reached_upload"),
            {
                "file": SimpleUploadedFile("hh.csv", _village_csv()),
                "facilities": "F1,7.0,13.5,D1",
                "lens": "buildings",
            },
        )
    assert resp.status_code == 200
    out = resp.json()
    assert out["households_total"] == 30 and out["upload"]["households"] == 30 and out["rollup"][0]["label"] == "D1"
    assert "h0" not in resp.content.decode()


@pytest.mark.django_db
def test_an_upload_without_a_file_or_with_a_bad_one_says_why(client, person):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse

    url = reverse("microplans:buildings_reached_upload")
    assert "Choose a CSV" in client.post(url, {"facilities": "F1,7.0,13.5"}).json()["message"]
    bad = client.post(url, {"file": SimpleUploadedFile("x.csv", b"a,b\n1,2\n"), "facilities": "F1,7.0,13.5"})
    assert bad.status_code == 400 and "missing column" in bad.json()["message"]


@pytest.mark.django_db
def test_the_population_lens_from_an_upload_needs_the_country_and_returns_denominators(client, person):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse

    from connect_labs.microplans.coverage import page

    url = reverse("microplans:buildings_reached_upload")
    data = {
        "file": SimpleUploadedFile("hh.csv", _village_csv()),
        "facilities": "F1,7.0,13.5,D1,500",
        "lens": "population",
    }
    assert "three-letter code" in client.post(url, data).json()["message"]

    grid = P.Grid(np.full((8, 8), 900.0), 6.9, 13.7, 0.05, -0.05)
    box_unit = SimpleNamespace(geometry=box(6.9, 13.3, 7.3, 13.7), name="Dept X", level=2, parent_name="R")
    with (
        patch.object(page.population, "fetch_grid", lambda iso, bbox: grid),
        patch("connect_labs.labs.admin_boundaries.placement.load_units", lambda *a, **k: [box_unit]),
    ):
        resp = client.post(
            url,
            {
                "file": SimpleUploadedFile("hh.csv", _village_csv()),
                "facilities": "F1,7.0,13.5,D1,500",
                "lens": "population",
                "iso": "ner",
            },
        )
    assert resp.status_code == 200
    out = resp.json()
    d = out["denominators"]["districts"][0]
    assert out["iso"] == "NER" and d["target"]["value"] == 500.0 and d["unit"]["units"][0]["name"] == "Dept X"
    assert json.dumps(out).count("h0") == 0
