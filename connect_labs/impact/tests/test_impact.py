"""The impact engine end to end: the models' own formulas, the place layer, and one delivery valued.

Places and figures are invented; the model specs are the real vendored ones.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.urls import reverse
from django.utils import timezone

from connect_labs.impact import baseline, engine, places, result
from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import policy
from connect_labs.labs.indicators.models import IndicatorValue, License
from connect_labs.pulse import cea, costs
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseWork

SUMMARY = json.loads((cea.DATA).read_text(encoding="utf-8"))


# --------------------------------------------------------------------- engine
class TestEngine:
    @pytest.mark.parametrize("model", engine.MODELS)
    def test_every_column_the_spec_worked_out_is_reproduced(self, model):
        s = engine.spec(model)
        base = engine.defaults(model)
        want = s["results"]
        got = {"Your inputs": engine.run(model)}
        for j, sc in enumerate(s["scenarios"]):
            got[f"Scenario {j + 1}: {sc['name']}"] = engine.run(model, sc["sets"])
        for j in (0, 1):
            got[f"Check {j + 1}"] = engine.run(model, column=f"check{j}")
        assert base  # the spec carries its inputs
        for col, rows in want.items():
            for key, v in rows.items():
                assert got[col][key] == pytest.approx(v, rel=1e-12, abs=1e-12), (col, key)

    def test_the_grey_columns_give_givewells_published_multiples(self):
        assert round(engine.run("chc", column="check0")["full_x"], 2) == 6.12
        assert round(engine.run("chc", column="check1")["full_x"], 2) == 12.22
        assert round(engine.run("kc", column="check0")["x"], 1) == 6.0
        assert round(engine.run("kc", column="check1")["x"], 1) == 12.5
        assert round(engine.run("mbw", column="check0")["x"], 2) == 17.73
        assert round(engine.run("mbw", column="check1")["x"], 2) == 13.54

    def test_the_per_country_summary_is_derived_from_the_specs(self):
        # pulse/data/cea_models.json is written by `impact_vendor` from the specs, never typed.
        for iso2, c in SUMMARY["countries"].items():
            for m in engine.MODELS:
                r = result.model_country_result(m, c["iso3"])
                assert r["x"] == pytest.approx(c[m]["x"], rel=1e-12), (iso2, m)
                assert r["price"] == pytest.approx(c[m]["unit_cost"], rel=1e-12)
                assert r["deaths_per_100k"] == pytest.approx(c[m]["deaths_per_100k"], rel=1e-12)

    def test_kc_risk_is_the_models_13_percent_everywhere(self):
        xs = {c["kc"]["x"] for c in SUMMARY["countries"].values()}
        assert len(xs) == 1
        assert engine.defaults("kc")["post_ref"] == 0.13

    def test_a_formula_can_only_do_arithmetic(self):
        with pytest.raises(ValueError):
            engine.evaluate("__import__('os').system('true')", {})
        assert engine.evaluate("IF(i_a>1,MIN(i_a,3),0)", {"i_a": 5}) == 3


# ---------------------------------------------------------------- place layer
def _square(x0, y0, size=1.0):
    return MultiPolygon(
        Polygon(((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0))), srid=4326
    )


def _boundary(level, name, bid, x0, y0, size=1.0, iso="NGA"):
    return AdminBoundary.objects.create(
        iso_code=iso,
        admin_level=level,
        name=name,
        boundary_id=bid,
        geometry=_square(x0, y0, size),
        source=AdminBoundary.Source.GEOBOUNDARIES,
    )


def _value(boundary, indicator, value, year=2023, source=None):
    source = source or policy.sources(indicator)[0]
    return IndicatorValue.objects.create(
        indicator=indicator,
        boundary=boundary,
        iso_code=boundary.iso_code,
        admin_level=boundary.admin_level,
        year=year,
        value=value,
        source=source,
        source_ref=f"{source}-{year}",
        license_code=License.OPEN_API,
        retrieved_at=timezone.now(),
    )


@pytest.fixture
def nigeria(db):
    """A country of two states side by side: Kano (high mortality, low ORS) and Lagos."""
    ng = _boundary(0, "Nigeria", "NGA", 0, 0, size=2)
    kano = _boundary(1, "Kano", "NGA-KN", 0, 0)
    lagos = _boundary(1, "Lagos", "NGA-LA", 1, 0)
    # Area mortality is IGME's subnational model, levelled to IGME's national estimate.
    _value(ng, "u5mr", 100.0, source="igme")
    _value(kano, "u5mr", 150.0, source="igme_subnational")
    _value(lagos, "u5mr", 50.0, source="igme_subnational")
    _value(kano, "ors_coverage", 20.0)
    _value(lagos, "ors_coverage", 60.0)
    _value(ng, "nmr", 30.0, source="igme")
    _value(kano, "nmr", 40.0, source="igme_subnational")
    return {"ng": ng, "kano": kano, "lagos": lagos}


@pytest.mark.django_db
class TestBaseline:
    def test_an_area_takes_its_own_figure_and_says_where_it_came_from(self, nigeria):
        v = baseline.Baselines([nigeria["kano"]]).for_area("chc", "NGA", nigeria["kano"])
        assert v["ors"].value == pytest.approx(0.20)
        assert v["ors"].level == "state or province"
        assert v["ors"].measured_at == "Kano"
        assert v["ors"].country_value == engine.country("chc", "NGA")["inputs"]["ors"]

    def test_diarrhoea_deaths_scale_with_the_areas_child_mortality_as_the_model_does(self, nigeria):
        country = engine.country("chc", "NGA")["inputs"]["deaths"]
        v = baseline.Baselines([nigeria["kano"], nigeria["lagos"]])
        assert v.for_area("chc", "NGA", nigeria["kano"])["deaths"].value == pytest.approx(country * 1.5)
        assert v.for_area("chc", "NGA", nigeria["lagos"])["deaths"].value == pytest.approx(country * 0.5)

    def test_with_nothing_local_it_takes_the_models_country_figure_not_another_sources(self, nigeria):
        # Labs holds a national vitamin A figure, but the model's own country figure wins at country level,
        # so a place with nothing more local is valued exactly as the published per-country result.
        _value(nigeria["ng"], "vitamin_a_coverage", 90.0)
        v = baseline.Baselines([nigeria["kano"]]).for_area("chc", "NGA", nigeria["kano"])
        assert v["vas"].value == engine.country("chc", "NGA")["inputs"]["vas"]
        assert v["vas"].level == "model country figure"
        # An input with no recipe at all says so.
        assert "no area figure" in v["worm"].how

    def test_mbw_scales_the_countrys_newborn_mortality_by_the_areas(self, nigeria):
        v = baseline.Baselines([nigeria["kano"]]).for_area("mbw", "NGA", nigeria["kano"])
        country = engine.country("mbw", "NGA")["inputs"]["nmr"]
        assert v["nmr"].value == pytest.approx(country * 40 / 30)
        assert v["nmr"].level == "state or province"
        assert v["imr"].level == "model country figure"  # no area figure for infant mortality here

    def test_a_ratio_is_only_taken_within_one_series(self, nigeria):
        # A DHS survey figure for the area has no national DHS figure to compare with here.
        lagos = nigeria["lagos"]
        IndicatorValue.objects.filter(boundary=lagos, indicator="u5mr").delete()
        _value(lagos, "u5mr", 40.0, source="dhs")
        v = baseline.Baselines([lagos]).for_area("chc", "NGA", lagos)
        assert v["deaths"].level == "model country figure"

    def test_kc_has_no_place_inputs(self, nigeria):
        assert baseline.Baselines([nigeria["kano"]]).for_area("kc", "NGA", nigeria["kano"]) == {}


# ------------------------------------------------------------- one delivery
def _opp(oid, service="chc", country="NG"):
    return PulseOpportunity.objects.create(
        opportunity_id=oid,
        name=f"Opp {oid}",
        org_slug=f"org-{oid}",
        service_slug=service,
        country=country,
        currency="USD",
        usd_rate=Decimal("1"),
        end_date=dt.date(2026, 1, 1),
    )


_seq = [0]


def _visit(oid, lat, lon, status="approved"):
    _seq[0] += 1
    PulseEvent.objects.create(
        connect_visit_id=70000 + _seq[0],
        opportunity_id=oid,
        field_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
        sync_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
        lat=lat,
        lon=lon,
        status=status,
    )


def _work(oid, usd, who):
    _seq[0] += 1
    PulseWork.objects.create(
        work_key=f"imp-w{_seq[0]}",
        opportunity_id=oid,
        worker_hash="w",
        payment_unit_id=1,
        beneficiary_key=who,
        status="approved",
        approved_count=1,
        created_ts=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
        usd_to_worker=Decimal(str(usd)),
        usd_to_org=Decimal("0"),
    )


@pytest.fixture(autouse=True)
def _fresh():
    costs.invalidate()
    yield
    costs.invalidate()


@pytest.mark.django_db
class TestPlaces:
    def test_visits_are_placed_in_their_area_and_shared_by_count(self, nigeria):
        _opp(1)
        for _ in range(3):
            _visit(1, 0.5, 0.5)  # Kano
        _visit(1, 0.5, 1.5)  # Lagos
        _visit(1, 0.5, 1.6, status="rejected")  # not approved: not counted
        _visit(1, 5.0, 5.0)  # outside every area
        assert places.opportunity_places([1]) == {}  # nothing stored until the timer runs
        places.refresh({1: "NGA"})
        p = places.opportunity_places([1])[1]
        assert [(a.boundary.name, a.visits) for a in p.areas] == [("Kano", 3), ("Lagos", 1)]
        assert p.areas[0].share == pytest.approx(0.75)
        assert (p.visits, p.unplaced) == (5, 1)
        from connect_labs.pulse.models import PulseScalar

        # The level used is stored with the placement, so a page can say which it was.
        assert PulseScalar.objects.get(key=places.SCALAR_KEY).value["opps"]["1"]["level"] == 1


@pytest.mark.django_db
class TestEndToEnd:
    def test_a_campaign_in_kano_is_valued_with_kanos_figures(self, nigeria):
        _opp(1)
        for i in range(10):
            _visit(1, 0.5, 0.5)
            _work(1, 1.0, f"child-{i}")
        from connect_labs.impact.tasks import refresh_opportunity_places

        assert refresh_opportunity_places() == 1  # the timed task places every modelled opportunity
        row = next(r for r in cea.ranking()["rows"] if r["id"] == 1)
        country = result.model_country_result("chc", "NGA")
        # Kano: more child deaths and less ORS than Nigeria as a whole, so more to avert.
        assert row["model_x"] > country["x"]
        # ORS, diarrhoea deaths and child mortality are Kano's own; the rest are Nigeria's.
        assert row["baseline_level"] == "state or province · 3 of 9"
        assert row["x"] == pytest.approx(row["model_x"] * country["price"] / 1.0)

    def test_without_placed_visits_it_is_valued_at_the_countrys_figures(self, nigeria):
        _opp(2)
        for i in range(10):
            _work(2, 1.0, f"child-{i}")
        row = next(r for r in cea.ranking()["rows"] if r["id"] == 2)
        assert row["model_x"] == pytest.approx(result.model_country_result("chc", "NGA")["x"])
        assert row["baseline_level"] == "country"

    def test_the_end_to_end_page_shows_every_step(self, nigeria, client, django_user_model):
        _opp(3)
        for i in range(4):
            _visit(3, 0.5, 0.5)
            _work(3, 1.0, f"child-{i}")
        places.refresh({3: "NGA"})
        client.force_login(django_user_model.objects.create_user(username="u", email="u@x.org", password="x"))
        url = reverse("pulse:cea_opp", args=[3])
        page = client.get(url)
        assert page.status_code == 200
        body = page.content.decode()
        for text in ("Where it was delivered", "Baseline in those places", "The model, run there", "Kano", "Result"):
            assert text in body
        data = client.get(url + "?format=json").json()
        assert data["result"]["areas"][0]["name"] == "Kano"
        assert data["result"]["areas"][0]["inputs"]["ors"]["value"] == pytest.approx(0.20)
        assert any(c["key"] == "full_x" for c in data["result"]["areas"][0]["chain"])
        assert client.get(reverse("pulse:cea_opp", args=[999])).status_code == 404
