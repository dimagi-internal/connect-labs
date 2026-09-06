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
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.africa import ISO_CODES
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

    def test_csv_names_the_method_that_produced_each_row(self, client_in):
        """The CSV is the copy that leaves the building, without the page beside it.

        A row must never travel under a method heading that did not produce it.
        This used to be a labelling problem: a region with no survey inherited
        IGME's national figure and rode out under "Survey as measured", so the
        column had to be per-row rather than per-download. The eligibility rule
        made it a stronger guarantee — such a row is not produced at all — and
        this test now pins both halves.
        """
        country = make_boundary("COD", 0, "DR Congo", "COD-0", x=40)
        measured = make_boundary("COD", 1, "Haut-Katanga", "COD-1-1", x=42)
        ineligible = make_boundary("COD", 1, "North Kivu", "COD-1-2", x=44)
        set_value(measured, "u5mr", 150, source=Source.DHS)
        # IGME is not an eligible source for a survey method, so North Kivu has
        # nothing it may inherit and must be absent rather than mislabelled.
        set_value(country, "u5mr", 140, source=Source.IGME)
        # A third region below the threshold, so the country is not rolled up
        # into a single row and the per-row labels stay visible.
        set_value(make_boundary("COD", 1, "Kinshasa", "COD-1-3", x=46), "u5mr", 50, source=Source.DHS)

        resp = client_in.get(
            reverse("targeting:download"),
            {"threshold": 80, "format": "csv", "method": "subnational_survey"},
        )
        rows = {r["Area"]: r for r in csv.DictReader(io.StringIO(resp.content.decode()))}

        assert rows["Haut-Katanga"]["U5MR method"] == "Survey as measured"
        assert ineligible.name not in rows

    def test_a_legitimately_inherited_row_says_where_it_was_measured(self, client_in):
        """Inheritance is still allowed inside the method, and is still labelled.

        A region taking its country's *survey* figure is a real answer, not a
        substitution — but the reader has to be able to see that it was measured
        one level up.
        """
        country = make_boundary("TZA", 0, "Tanzania", "TZA-0", x=50)
        make_boundary("TZA", 1, "Borrowing", "TZA-1-1", x=52)
        make_boundary("TZA", 1, "Low", "TZA-1-2", x=54)
        set_value(country, "u5mr", 150, source=Source.DHS)
        set_value(make_boundary("TZA", 1, "Measured", "TZA-1-3", x=56), "u5mr", 20, source=Source.DHS)

        resp = client_in.get(
            reverse("targeting:download"),
            {"threshold": 80, "format": "csv", "method": "subnational_survey"},
        )
        rows = {r["Area"]: r for r in csv.DictReader(io.StringIO(resp.content.decode()))}

        assert "Borrowing" in rows
        assert rows["Borrowing"]["U5MR measured at"] == "Tanzania (ADM0)"

    def test_row_values_are_escaped_before_reaching_innerHTML(self, client_in):
        """Source text is server data, but the table builds HTML by hand.

        Scanning the whole directory rather than one named file, so a module
        added later is covered without anyone remembering to widen this.
        """
        from pathlib import Path

        root = Path("connect_labs/static/indicators/targeting")
        js = "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.glob("*.js")))

        assert "function esc(" in js
        assert "util.esc(r.source_name" in js
        assert "util.esc(r.source_url)" in js
        # The map tooltip also builds HTML from server strings.
        assert "util.esc(p.name)" in js


class TestRowMethodLabel:
    """The Method column names what produced the row, not what was asked for."""

    def test_a_row_from_the_selected_method_keeps_its_label(self):
        from connect_labs.labs.indicators import methods
        from connect_labs.labs.indicators.views import _row_method_label

        selected = methods.get("subnational_survey")
        row = SimpleNamespace(source="dhs")

        assert _row_method_label(row, selected) == selected.label

    def test_an_inherited_row_names_the_method_that_answered(self):
        """DR Congo's provinces are IGME's national figure applied downward.

        Labelling them "Survey as measured" contradicted the logic column beside
        them and hid the one thing a reader needs to weigh the row.
        """
        from connect_labs.labs.indicators import methods
        from connect_labs.labs.indicators.views import _row_method_label

        row = SimpleNamespace(source="igme")

        label = _row_method_label(row, methods.get("subnational_survey"))

        assert label == methods.get("national_igme").label

    def test_a_rolled_up_row_names_every_method_beneath_it(self):
        from connect_labs.labs.indicators import methods
        from connect_labs.labs.indicators.views import _row_method_label

        row = SimpleNamespace(source="dhs+igme")

        label = _row_method_label(row, methods.get("subnational_survey"))

        assert label == "Survey as measured + National estimate (UN IGME)"


class TestThresholdIsReadInItsOwnUnit:
    """A threshold means what the indicator's unit says it means.

    The surface divided every threshold by ten to get a percentage, which is
    right for a rate per 1,000 and wrong for the fourteen indicators already
    measured in percent — a 50% sanitation threshold rendered as 5.0%.
    """

    def test_a_per_1000_rate_has_a_percent_reading(self):
        assert measures.percent_equivalent("u5mr", 80) == 8.0

    def test_an_indicator_already_in_percent_has_none(self):
        assert measures.percent_equivalent("improved_sanitation", 50) is None
        assert measures.percent_equivalent("stunting", 30) is None

    def test_the_api_reports_it_that_way(self, client_in):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "improved_sanitation", 20)

        resp = client_in.get(
            reverse("targeting:selection"),
            {"indicator": "improved_sanitation", "threshold": 50, "resolution": "subnational"},
        )

        assert resp.json()["threshold_pct"] is None


class TestDefaultMethodCanAnswer:
    """The default adapts to the indicator; an explicit choice does not."""

    def test_the_default_avoids_a_method_with_no_data_for_this_indicator(self, client_in):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        # Only the survey path carries sanitation; IGME publishes mortality.
        set_value(region, "improved_sanitation", 20, source=Source.DHS)

        resp = client_in.get(
            reverse("targeting:selection"),
            {"indicator": "improved_sanitation", "threshold": 50, "resolution": "subnational"},
        )
        body = resp.json()

        assert body["method"] != "subnational_igme"
        assert body["counts"]["units"] == 1

    def test_an_explicit_method_is_honoured_even_when_it_cannot_answer(self, client_in):
        """Silently substituting would hide the very thing worth learning."""
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "improved_sanitation", 20, source=Source.DHS)

        resp = client_in.get(
            reverse("targeting:selection"),
            {
                "indicator": "improved_sanitation",
                "threshold": 50,
                "resolution": "subnational",
                "method": "subnational_igme",
            },
        )
        body = resp.json()

        assert body["method"] == "subnational_igme"
        assert body["counts"]["units"] == 0
        assert "Nigeria" in body["countries_unsupported"]


class TestMethodologyOnThePage:
    """The workings are readable before anyone unzips anything."""

    def test_the_page_serves_the_same_text_the_download_ships(self, client_in):
        from connect_labs.labs.indicators import export
        from connect_labs.labs.indicators.resolve import select_above

        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "u5mr", 150)

        params = {"indicator": "u5mr", "threshold": 80, "method": "subnational_survey"}
        resp = client_in.get(reverse("targeting:methodology"), params)
        body = resp.json()

        expected = export.to_methodology(
            select_above(indicator="u5mr", threshold=80.0, iso_codes=ISO_CODES, method="subnational_survey")
        )
        # Same function, not a second copy — a page that paraphrased its own
        # methodology could drift from the file a funder was sent.
        assert body["markdown"].splitlines()[3:] == expected.splitlines()[3:]
        assert "<h2>" in body["html"]

    def test_a_coverage_indicator_reads_the_other_way(self, client_in):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        region = make_boundary("NGA", 1, "Kano", "NGA-1-1", x=2)
        set_value(region, "improved_sanitation", 20, source=Source.DHS)

        resp = client_in.get(
            reverse("targeting:methodology"),
            {"indicator": "improved_sanitation", "threshold": 50, "resolution": "subnational"},
        )
        md = resp.json()["markdown"]

        assert "falls below" in md
        assert "50% of population" in md
        # The per-1,000 aside must not appear on an indicator that has no such reading.
        assert "of live births)" not in md


class TestTemplateRenders:
    def test_no_template_comment_leaks_into_the_page(self, client_in):
        """Django's {# #} is single-line only; a multi-line one renders as text."""
        resp = client_in.get(reverse("targeting:index"))

        assert b"{#" not in resp.content


class TestScopeAndLevel:
    """The controls that turn a continental scan into a country argument.

    The surface had none of these. Every question it could be asked was
    continental, at whichever level happened to carry a value, with counts
    frozen at the year they were measured — so the analysis a funder actually
    wants ("ORS in Liberia, by county, delivered in 2027") could be produced
    through the MCP tools and not through the page. These test the four things
    that were missing, because each of them changes the answer rather than the
    presentation.
    """

    def test_scoping_to_a_country_drops_the_others(self, client_in, africa):
        both = client_in.get(reverse("targeting:selection"), {"threshold": 50}).json()
        one = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER"}).json()

        assert {r["iso"] for r in both["rows"]} == {"NER", "NGA"}
        assert {r["iso"] for r in one["rows"]} == {"NER"}
        assert one["scope"]["iso_codes"] == ["NER"]
        assert one["scope"]["whole_continent"] is False

    def test_an_unknown_iso_falls_back_to_the_continent(self, client_in, africa):
        """Rather than returning an empty selection.

        An empty answer reads as "nowhere qualifies", which is a finding. A
        misspelled country code is not a finding.
        """
        r = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "XXX"}).json()
        assert r["scope"]["whole_continent"] is True

    def test_ranking_the_parts_unrolls_a_wholly_selected_country(self, client_in, africa):
        rolled = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER"}).json()
        ranked = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER", "rollup": "0"}).json()

        assert len(rolled["rows"]) == 1 and rolled["rows"][0]["whole_country"]
        # Ranked, not alphabetised: the point of unrolling is to see which
        # part is biggest, so the rows come back ordered by the quantity a
        # programme would be sized on.
        assert [r["name"] for r in ranked["rows"]] == ["South", "North"]
        assert ranked["rolled_up"] is False

    def test_the_costing_narrows_with_the_table(self, client_in, africa):
        """A cost computed continent-wide beside a one-country table is not a
        caveat, it is a second answer in the same panel."""
        both = client_in.get(
            reverse("targeting:scenario"), {"threshold": 50, "basis": "birth", "unit_cost": 10}
        ).json()
        one = client_in.get(
            reverse("targeting:scenario"), {"threshold": 50, "basis": "birth", "unit_cost": 10, "iso": "NER"}
        ).json()
        assert one["units"] < both["units"]

    def test_the_map_follows_the_pinned_level(self, client_in, africa):
        r = client_in.get(reverse("targeting:map_data"), {"iso": "NER", "admin_level": 0}).json()
        assert {f["properties"]["level"] for f in r["features"]} == {0}

    def test_districts_are_not_drawn_across_the_continent(self, client_in, africa):
        """The limit is the map's, not the analysis's: 47,000 polygons is a
        download, not a map. The table will still rank them."""
        r = client_in.get(reverse("targeting:map_data"), {"admin_level": 2}).json()
        assert all(f["properties"]["level"] == 1 for f in r["features"])

    def test_scope_reports_which_levels_are_measured(self, client_in, africa):
        """Boundary depth and measurement depth are different facts, and the
        difference is the whole trap: a level where nothing is measured is the
        same information on a finer grid, and every unit ties."""
        r = client_in.get(reverse("targeting:scope"), {"iso": "NER"}).json()
        assert [c["iso"] for c in r["countries"] if c["iso"] == "NER"] == ["NER"]
        assert r["depth"]["1"]["measured"] == 2
        assert r["depth"]["1"]["inherited"] == 0


class TestDeliveryYear:
    def test_counts_are_carried_and_rates_are_not(self, client_in, africa):
        for b in africa.values():
            set_value(b, "pop_growth_rate", 3.0, source=Source.WORLDBANK)

        now = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER"}).json()
        later = client_in.get(
            reverse("targeting:selection"), {"threshold": 50, "iso": "NER", "target_year": 2030}
        ).json()

        assert later["projected_to"] == 2030
        assert later["totals"]["births"] > now["totals"]["births"]
        # The rate is left exactly as measured: nothing here models how
        # mortality moves, and a projected rate would be invention.
        assert later["totals"]["u5mr"] == now["totals"]["u5mr"]

    def test_a_country_with_no_growth_series_is_named(self, client_in, africa):
        r = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER", "target_year": 2030}).json()
        assert "Niger" in r["projected_without_rate"]

    def test_an_implausible_year_is_ignored_rather_than_obeyed(self, client_in, africa):
        r = client_in.get(reverse("targeting:selection"), {"threshold": 50, "target_year": "9999"}).json()
        assert r["projected_to"] is None


class TestAnnualBasis:
    """A survey measures a fortnight. Quoting that as a year is a twentyfold
    error, and this page has made it in print."""

    def test_the_annual_basis_resolves_to_a_different_measure(self):
        from connect_labs.labs.indicators import interventions

        fortnight = interventions.measure_for(interventions.UnitBasis.DISEASE_CASE, "ors_coverage")
        annual = interventions.measure_for(interventions.UnitBasis.CASE_YEAR, "ors_coverage")
        assert fortnight != annual
        assert annual == "ors_coverage_gap_annual"

    def test_the_annual_basis_is_declined_where_it_cannot_be_derived(self):
        """No recall window and episode duration means no honest conversion.
        Multiplying by an assumed 26 would be an invention in the same units as
        a measurement."""
        from connect_labs.labs.indicators import interventions

        assert interventions.measure_for(interventions.UnitBasis.CASE_YEAR, "u5mr") is None

    def test_an_annual_basis_is_offered_for_an_ors_question(self, client_in):
        r = client_in.get(reverse("targeting:interventions"), {"indicator": "ors_coverage"}).json()
        by_code = {b["code"]: b for b in r["bases"]}
        assert by_code["case_year"]["available_for_indicator"] is True
        assert by_code["case_year"]["measure"] == "ors_coverage_gap_annual"

    def test_a_mortality_question_is_offered_no_annual_basis(self, client_in):
        r = client_in.get(reverse("targeting:interventions"), {"indicator": "u5mr"}).json()
        by_code = {b["code"]: b for b in r["bases"]}
        assert by_code["case_year"]["available_for_indicator"] is False


class TestTheDownloadIsTheQuestionOnScreen:
    """A .zip that answers a different question from the page it came from is
    the failure this whole surface exists to prevent — and it is invisible,
    because both halves look right on their own."""

    def test_the_download_carries_scope_level_year_and_rollup(self, client_in, africa):
        for b in africa.values():
            set_value(b, "pop_growth_rate", 3.0, source=Source.WORLDBANK)

        page = client_in.get(
            reverse("targeting:selection"),
            {"threshold": 50, "iso": "NER", "admin_level": 1, "rollup": "0", "target_year": 2030},
        ).json()
        csv_text = client_in.get(
            reverse("targeting:download"),
            {
                "threshold": 50,
                "iso": "NER",
                "admin_level": 1,
                "rollup": "0",
                "target_year": 2030,
                "format": "csv",
            },
        ).content.decode()
        rows = list(csv.DictReader(io.StringIO(csv_text)))

        assert len(rows) == len(page["rows"]) == 2
        assert {r["name"] for r in page["rows"]} == {c["Area"] for c in rows}

    def test_the_methodology_is_produced_for_the_same_selection(self, client_in, africa):
        """It used to be built continent-wide whatever the page showed."""
        scoped = client_in.get(
            reverse("targeting:methodology"), {"threshold": 50, "iso": "NER", "rollup": "0"}
        ).json()["markdown"]
        whole = client_in.get(reverse("targeting:methodology"), {"threshold": 50, "rollup": "0"}).json()["markdown"]
        assert "across **1 countries**" in scoped
        assert "across **2 countries**" in whole

    def test_the_csv_carries_the_annual_figure_beside_the_fortnight_one(self, client_in):
        """The .zip is the copy read without the page beside it."""
        make_boundary("LBR", 0, "Liberia", "LBR-0")
        b = make_boundary("LBR", 1, "Bong", "LBR-1")
        set_value(b, "ors_coverage", 41.0, source=Source.DHS)
        set_value(b, "pop_u5", 60_000, source=Source.WORLDPOP)
        # What the derivation writes: the fortnight count and its annualised
        # sibling, x19.9 apart.
        set_value(b, "ors_coverage_gap", 5_000, source=Source.DERIVED)
        set_value(b, "ors_coverage_gap_annual", 99_700, source=Source.DERIVED)

        text = client_in.get(
            reverse("targeting:download"),
            {
                "indicator": "ors_coverage",
                "threshold": 80,
                "iso": "LBR",
                "method": "subnational_survey",
                "format": "csv",
            },
        ).content.decode()
        rows = list(csv.DictReader(io.StringIO(text)))

        assert [r["Area"] for r in rows] == ["Bong"]
        fortnight = float(rows[0]["Children with untreated diarrhoea"] or 0)
        annual = float(rows[0]["Unreached per year (annualised)"])
        assert annual == 99_700
        # A different question in the same units, not a rounding difference.
        assert fortnight != annual


class TestTheUrlCarriesTheQuestion:
    """A selection you cannot send to someone has to be described in prose,
    and every figure here ends up in an argument somebody else must check.

    These assert the server half of the contract: the parameters the page
    writes into its address bar are the ones the API reads back. The browser
    half — parsing them on load — is exercised by driving the page.
    """

    def test_a_link_reproduces_the_selection_it_was_copied_from(self, client_in, africa):
        for b in africa.values():
            set_value(b, "pop_growth_rate", 3.0, source=Source.WORLDBANK)

        link = {
            "indicator": "u5mr",
            "method": "subnational_igme",
            "iso": "NER",
            "admin_level": 1,
            "target_year": 2030,
            "rollup": "0",
            "threshold": 50,
        }
        first = client_in.get(reverse("targeting:selection"), link).json()
        again = client_in.get(reverse("targeting:selection"), link).json()

        assert [r["name"] for r in first["rows"]] == [r["name"] for r in again["rows"]]
        assert first["totals"] == again["totals"]
        assert first["pinned_level"] == 1
        assert first["projected_to"] == 2030
        assert first["rolled_up"] is False
        assert first["scope"]["iso_codes"] == ["NER"]

    def test_a_link_that_omits_everything_is_still_a_valid_question(self, client_in, africa):
        """The bare page and a stripped link have to mean the same thing."""
        bare = client_in.get(reverse("targeting:selection")).json()
        assert bare["scope"]["whole_continent"] is True
        assert bare["projected_to"] is None
        assert bare["pinned_level"] is None

    def test_a_country_without_growth_is_named_not_coded(self, client_in, africa):
        """Every sibling caveat on this page lists country names."""
        r = client_in.get(reverse("targeting:selection"), {"threshold": 50, "iso": "NER", "target_year": 2030}).json()
        assert r["projected_without_rate"] == ["Niger"]


class TestIndicatorGrouping:
    """The menu's grouping is registry data, not markup.

    Fifty-two indicators in a flat list is a scroll bar with a catalogue
    hidden inside it. The grouping is what makes the catalogue visible — so it
    lives where the measures live, can be reported by the MCP tools, and
    cannot drift from the list of things you are allowed to target.
    """

    def test_every_targetable_measure_has_exactly_one_group(self):
        seen: dict[str, str] = {}
        for group, codes in measures.GROUPS.items():
            for code in codes:
                assert code not in seen, f"{code} is in both {seen.get(code)!r} and {group!r}"
                seen[code] = group

        assert set(seen) == set(measures.TARGETABLE)

    def test_targetable_is_derived_from_the_groups(self):
        """Not hand-maintained beside them. A measure added to one list and
        forgotten in the other is the drift this ordering prevents."""
        assert measures.TARGETABLE == tuple(c for codes in measures.GROUPS.values() for c in codes)

    def test_every_grouped_code_is_a_real_measure(self):
        for code in measures.TARGETABLE:
            assert code in measures.MEASURES, f"{code} is grouped but not registered"

    def test_group_of_answers_and_declines(self):
        assert measures.group_of("u5mr") == "Child survival"
        assert measures.group_of("iptp3") == "Malaria"
        # Registered but deliberately not targetable, so it has no menu home.
        assert measures.group_of("rain_peak_month") is None
        assert measures.group_of("not_a_measure") is None

    def test_the_api_carries_the_group_on_every_indicator(self, client_in):
        r = client_in.get(reverse("targeting:methods"), {"indicator": "u5mr"}).json()

        assert r["groups"] == list(measures.GROUPS)
        ungrouped = [i["code"] for i in r["indicators"] if not i.get("group")]
        assert ungrouped == [], f"the menu would file these under 'Other': {ungrouped}"

    def test_group_order_is_the_registry_order_not_alphabetical(self):
        """The menu reads groups in this order because 'Child survival' leads
        for a reason, not because C sorts early."""
        groups = list(measures.GROUPS)
        assert groups[0] == "Child survival"
        assert groups != sorted(groups)


class TestDownloadFilenameNamesTheQuestion:
    def test_a_coverage_selection_is_lt_not_gt(self, client_in):
        """An ORS selection of the counties UNDER 90% arrived as
        `targeting_ors_coverage_gt90.zip` — the opposite of its contents."""
        from connect_labs.labs.indicators import export
        from connect_labs.labs.indicators.resolve import Selection

        sel = Selection(
            indicator="ors_coverage",
            threshold=90,
            year=None,
            areas=[],
            totals={},
            coverage={},
            countries_fully_above=[],
            countries_partly_above=[],
            skipped_no_data=[],
        )
        assert export.filename_stem(sel) == "targeting_ors_coverage_lt90"

    def test_a_burden_selection_is_still_gt(self):
        from connect_labs.labs.indicators import export
        from connect_labs.labs.indicators.resolve import Selection

        sel = Selection(
            indicator="u5mr",
            threshold=80,
            year=None,
            areas=[],
            totals={},
            coverage={},
            countries_fully_above=[],
            countries_partly_above=[],
            skipped_no_data=[],
        )
        assert export.filename_stem(sel) == "targeting_u5mr_gt80"
