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
from django.test import override_settings
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
    make_boundary("NER", 0, "Highland", "NER-0")  # boundary name is ignored
    a1 = make_boundary("NER", 1, "North", "NER-1", x=2)
    a2 = make_boundary("NER", 1, "South", "NER-2", x=4)

    make_boundary("NGA", 0, "Mixedland", "NGA-0", x=6)
    b1 = make_boundary("NGA", 1, "Hot", "NGA-1", x=8)
    b2 = make_boundary("NGA", 1, "Cool", "NGA-2", x=10)

    for b, rate, births in [(a1, 120, 11_000), (a2, 95, 22_000), (b1, 140, 33_000), (b2, 30, 44_000)]:
        # The default subnational method reads IGME's small-area model, so the
        # fixture supplies that; the DHS row alongside keeps the survey methods
        # answerable from the same fixture.
        set_value(b, "u5mr", rate, source=Source.IGME_SUBNATIONAL)
        set_value(b, "u5mr", rate, source=Source.DHS)
        set_value(b, "births", births, source=Source.DERIVED)
        set_value(b, "pop_u5", births * 5, source=Source.WORLDPOP)
        set_value(b, "pop_total", births * 30, source=Source.WORLDPOP)
    return {"a1": a1, "a2": a2, "b1": b1, "b2": b2}


class TestPage:
    def test_page_requires_login_when_deployed(self, client):
        # Django forces DEBUG=False under test settings, so this is the
        # deployed behaviour.
        resp = client.get(reverse("targeting:index"))
        assert resp.status_code in (302, 301)

    def test_page_is_open_locally(self, client, africa):
        # Locally the page carries only public open data and nothing
        # user-specific, so it must not demand a Connect OAuth round trip —
        # which is unusable on a laptop with an expired CLI token.
        with override_settings(DEBUG=True):
            resp = client.get(reverse("targeting:index"))
        assert resp.status_code == 200

    def test_apis_are_open_locally(self, client, africa):
        with override_settings(DEBUG=True):
            assert client.get(reverse("targeting:selection"), {"threshold": 80}).status_code == 200
            assert client.get(reverse("targeting:map_data")).status_code == 200
            assert client.get(reverse("targeting:download"), {"threshold": 80}).status_code == 200

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
        # The curated country name wins over the boundary file's label.
        assert rolled[0]["name"] == "Niger"
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

    def test_a_subnational_map_omits_a_country_with_no_regional_data(self, client_in, africa):
        # It used to fall back to the country outline. That painted a national
        # figure where the legend promises regional detail, so the country is
        # now simply absent from a subnational map.
        make_boundary("MLI", 0, "Regionless", "MLI-0", x=12)
        data = client_in.get(reverse("targeting:map_data"), {"method": "subnational_survey"}).json()
        names = {f["properties"]["name"] for f in data["features"]}
        assert "Regionless" not in names

    def test_a_national_map_draws_one_shape_per_country(self, client_in, africa):
        from connect_labs.labs.admin_boundaries.models import AdminBoundary

        # The shared fixture only carries survey values on regions; a national
        # method needs a national estimate to have anything to draw.
        for iso in ("NER", "NGA"):
            adm0 = AdminBoundary.objects.get(iso_code=iso, admin_level=0)
            set_value(adm0, "u5mr", 100, source=Source.IGME)

        data = client_in.get(reverse("targeting:map_data"), {"method": "national_igme"}).json()

        assert {f["properties"]["level"] for f in data["features"]} == {0}
        assert len(data["features"]) == 2


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
        assert {r["Country"] for r in rows} == {"Niger", "Nigeria"}

    def test_methodology_names_sources_and_the_derivation(self, client_in, africa):
        resp = client_in.get(reverse("targeting:download"), {"threshold": 80})
        doc = zipfile.ZipFile(io.BytesIO(resp.content)).read("METHODOLOGY.md").decode()

        assert "UN IGME" in doc
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

    def test_a_subnational_method_does_not_borrow_the_national_figure(self, client_in):
        # A region with no survey of its own must not quietly inherit its
        # country's number when the user asked for subnational detail.
        country = make_boundary("TCD", 0, "Inheritland", "TCD-0", x=20)
        region = make_boundary("TCD", 1, "Only", "TCD-1", x=22)
        set_value(country, "u5mr", 150, source=Source.IGME)
        set_value(region, "births", 5_000, source=Source.DERIVED)

        resp = client_in.get(
            reverse("targeting:download"),
            {"threshold": 80, "format": "csv", "method": "subnational_survey"},
        )
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))

        assert rows == []

    def test_the_national_method_answers_that_country_directly(self, client_in):
        country = make_boundary("TCD", 0, "Inheritland", "TCD-0", x=20)
        region = make_boundary("TCD", 1, "Only", "TCD-1", x=22)
        set_value(country, "u5mr", 150, source=Source.IGME)
        set_value(region, "births", 5_000, source=Source.DERIVED)

        resp = client_in.get(
            reverse("targeting:download"),
            {"threshold": 80, "format": "csv", "method": "national_igme"},
        )
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))

        assert len(rows) == 1
        assert rows[0]["Country"] == "Chad"
        assert rows[0]["Est. annual births"] == "5000"


class TestMissingBirthsSurfacing:
    def test_selection_api_sends_null_not_zero(self, client_in):
        make_boundary("TCD", 0, "Chad", "TCD-0", x=30)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=32)
        set_value(r, "u5mr", 150, source=Source.IGME_SUBNATIONAL)
        set_value(r, "pop_u5", 400_000, source=Source.WORLDPOP)

        data = client_in.get(reverse("targeting:selection"), {"threshold": 80}).json()
        row = data["rows"][0]

        assert row["births"] is None
        assert row["births_partial"] is True
        assert data["coverage"]["births"] == {"with_value": 0, "of": 1}

    def test_csv_leaves_missing_births_blank(self, client_in):
        make_boundary("TCD", 0, "Chad", "TCD-0", x=30)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=32)
        set_value(r, "u5mr", 150, source=Source.IGME_SUBNATIONAL)

        resp = client_in.get(reverse("targeting:download"), {"threshold": 80, "format": "csv"})
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))

        assert rows[0]["Est. annual births"] == ""
        assert rows[0]["Births complete for all regions"] == "no"

    def test_methodology_says_the_total_is_a_floor(self, client_in):
        make_boundary("TCD", 0, "Chad", "TCD-0", x=30)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=32)
        set_value(r, "u5mr", 150, source=Source.IGME_SUBNATIONAL)

        resp = client_in.get(reverse("targeting:download"), {"threshold": 80})
        doc = zipfile.ZipFile(io.BytesIO(resp.content)).read("METHODOLOGY.md").decode()

        assert "floor, not a measurement" in doc
        assert "1 of 1" in doc


class TestSourceColumns:
    """Source, year and link are separate columns.

    They used to be one cell reading "NG2024DHS", which told a reader nothing
    and led nowhere.
    """

    def test_selection_splits_name_year_and_link(self, client_in):
        make_boundary("TCD", 0, "Chad", "TCD-0", x=30)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=32)
        v = set_value(r, "u5mr", 150, year=2019, source=Source.IGME_SUBNATIONAL)
        v.source_ref = "Chad DHS 2019"
        v.source_url = "https://dhsprogram.com/methodology/survey/survey-display-123.cfm"
        v.save()

        row = client_in.get(reverse("targeting:selection"), {"threshold": 80}).json()["rows"][0]

        assert row["source_name"] == "UN IGME (subnational model)"
        assert row["source_detail"] == "Chad DHS 2019"
        assert row["year"] == 2019
        assert row["source_url"].endswith("survey-display-123.cfm")

    def test_a_rolled_up_row_names_every_source_it_mixes(self):
        from connect_labs.labs.indicators.views import source_name

        # A country row whose regions were not all measured the same way must
        # not present one source as though it covered them all.
        assert source_name("dhs+igme") == "DHS Program + UN IGME (via UNICEF SDMX)"
        assert source_name("dhs") == "DHS Program"
        assert source_name("") == ""

    def test_csv_carries_the_link(self, client_in):
        make_boundary("TCD", 0, "Chad", "TCD-0", x=30)
        r = make_boundary("TCD", 1, "Region", "TCD-1", x=32)
        v = set_value(r, "u5mr", 150, source=Source.IGME_SUBNATIONAL)
        v.source_url = "https://dhsprogram.com/x.cfm"
        v.save()

        resp = client_in.get(reverse("targeting:download"), {"threshold": 80, "format": "csv"})
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))

        assert rows[0]["U5MR source"] == "UN IGME (subnational model)"
        assert rows[0]["U5MR source link"] == "https://dhsprogram.com/x.cfm"

    def test_row_values_are_escaped_before_reaching_innerHTML(self, client_in):
        # Source text is server data, but the table builds HTML by hand.
        js = open("connect_labs/static/indicators/targeting.js", encoding="utf-8").read()
        assert "function esc(" in js
        assert "esc(r.source_name" in js
        assert "esc(r.source_url)" in js
