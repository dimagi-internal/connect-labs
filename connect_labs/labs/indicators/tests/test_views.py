"""End-to-end tests for the targeting surface.

These exercise the path a person actually takes: load the page, move the
threshold, download the answer. The export tests are the ones that matter most —
a CSV that leaves without its provenance is the failure mode this whole design
exists to prevent.
"""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from django.urls import reverse

from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="tester", password="pw")  # noqa: S106


@pytest.fixture
def client_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def africa():
    """Two countries: one wholly above a threshold of 80, one split.

    Real ISO codes, because the map and the selection are both scoped to Africa
    — a fixture using invented codes would test nothing.
    """
    make_boundary("NER", 0, "Highland", "NER-0")
    a1 = make_boundary("NER", 1, "North", "NER-1", x=2)
    a2 = make_boundary("NER", 1, "South", "NER-2", x=4)

    make_boundary("NGA", 0, "Mixedland", "NGA-0", x=6)
    b1 = make_boundary("NGA", 1, "Hot", "NGA-1", x=8)
    b2 = make_boundary("NGA", 1, "Cool", "NGA-2", x=10)

    for b, rate, births in [(a1, 120, 11_000), (a2, 95, 22_000), (b1, 140, 33_000), (b2, 30, 44_000)]:
        set_value(b, "u5mr", rate, source=Source.DHS)
        set_value(b, "births", births, source=Source.DERIVED)
        set_value(b, "pop_u5", births * 5, source=Source.WORLDPOP)
        set_value(b, "pop_total", births * 30, source=Source.WORLDPOP)
    return {"a1": a1, "a2": a2, "b1": b1, "b2": b2}


class TestPage:
    def test_page_requires_login(self, client):
        resp = client.get(reverse("targeting:index"))
        assert resp.status_code in (302, 301)

    def test_page_renders(self, client_in, africa):
        resp = client_in.get(reverse("targeting:index"))
        assert resp.status_code == 200
        assert b"Intervention targeting" in resp.content


class TestSelectionApi:
    def test_returns_births_above_threshold(self, client_in, africa):
        resp = client_in.get(reverse("targeting:selection"), {"threshold": 80})
        data = resp.json()

        # Niger rolls up (both regions above); Nigeria contributes only its hot region.
        assert data["totals"]["births"] == 11_000 + 22_000 + 33_000
        assert data["counts"]["countries"] == 2
        assert data["counts"]["units"] == 3

    def test_threshold_is_echoed_in_both_units(self, client_in, africa):
        data = client_in.get(reverse("targeting:selection"), {"threshold": 80}).json()
        assert data["threshold"] == 80
        assert data["threshold_pct"] == 8.0

    def test_raising_threshold_reduces_births(self, client_in, africa):
        low = client_in.get(reverse("targeting:selection"), {"threshold": 50}).json()
        high = client_in.get(reverse("targeting:selection"), {"threshold": 130}).json()
        assert high["totals"]["births"] < low["totals"]["births"]

    def test_rolled_up_row_is_labelled(self, client_in, africa):
        data = client_in.get(reverse("targeting:selection"), {"threshold": 80}).json()
        rolled = [r for r in data["rows"] if r["whole_country"]]
        assert len(rolled) == 1
        assert rolled[0]["name"] == "Highland"
        assert rolled[0]["units_covered"] == 2

    def test_bad_threshold_falls_back_rather_than_500s(self, client_in, africa):
        resp = client_in.get(reverse("targeting:selection"), {"threshold": "not-a-number"})
        assert resp.status_code == 200


class TestMapApi:
    def test_returns_features_with_indicator_values(self, client_in, africa):
        data = client_in.get(reverse("targeting:map_data")).json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 4  # ADM1 units only; both countries have them

        props = {f["properties"]["name"]: f["properties"] for f in data["features"]}
        assert props["Hot"]["u5mr"] == 140.0
        assert props["Hot"]["births"] == 33_000

    def test_country_without_regions_falls_back_to_its_outline(self, client_in, africa):
        make_boundary("MLI", 0, "Regionless", "MLI-0", x=12)
        data = client_in.get(reverse("targeting:map_data")).json()
        names = {f["properties"]["name"] for f in data["features"]}
        assert "Regionless" in names


class TestDownload:
    def test_zip_carries_table_and_methodology_together(self, client_in, africa):
        resp = client_in.get(reverse("targeting:download"), {"threshold": 80})
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/zip"

        z = zipfile.ZipFile(io.BytesIO(resp.content))
        assert sorted(z.namelist()) == ["METHODOLOGY.md", "targeting_selection.csv"]

    def test_csv_rows_match_the_selection(self, client_in, africa):
        resp = client_in.get(reverse("targeting:download"), {"threshold": 80, "format": "csv"})
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))
        assert len(rows) == 2  # one rolled-up country, one region
        assert {r["Country"] for r in rows} == {"Highland", "Mixedland"}

    def test_methodology_names_sources_and_the_derivation(self, client_in, africa):
        resp = client_in.get(reverse("targeting:download"), {"threshold": 80})
        doc = zipfile.ZipFile(io.BytesIO(resp.content)).read("METHODOLOGY.md").decode()

        assert "DHS Program" in doc
        assert "WorldPop" in doc
        assert "births = population aged 0-1" in doc
        # The weighting rule is the thing most likely to be misunderstood.
        assert "weighted by `births`" in doc
        # And the caveat that mortality is not measured at the row's own level.
        assert "measured at ADM1 at best" in doc

    def test_methodology_states_the_threshold_in_both_units(self, client_in, africa):
        resp = client_in.get(reverse("targeting:download"), {"threshold": 80})
        doc = zipfile.ZipFile(io.BytesIO(resp.content)).read("METHODOLOGY.md").decode()
        assert "80 per 1,000 live births" in doc
        assert "8%" in doc

    def test_inherited_rows_say_where_the_number_came_from(self, client_in):
        country = make_boundary("TCD", 0, "Inheritland", "TCD-0", x=20)
        region = make_boundary("TCD", 1, "Only", "TCD-1", x=22)
        set_value(country, "u5mr", 150, source=Source.IGME)
        set_value(region, "births", 5_000, source=Source.DERIVED)

        resp = client_in.get(reverse("targeting:download"), {"threshold": 80, "format": "csv"})
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))

        assert len(rows) == 1
        assert "Inheritland" in rows[0]["U5MR measured at"]
        assert rows[0]["U5MR measured at"] != "this area"
