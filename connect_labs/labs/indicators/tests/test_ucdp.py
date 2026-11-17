"""Conflict deaths from UCDP GED: reading the file, placing events in territories, and the rate per 100,000."""

import io
import zipfile

import pytest
import shapely

from connect_labs.effectiveness.tests.helpers import boundary, value
from connect_labs.labs.indicators import measures, policy
from connect_labs.labs.indicators.models import IndicatorValue, Source
from connect_labs.labs.indicators.sources import base, derive, ucdp
from connect_labs.labs.indicators.sources.ucdp import Event

HEADER = "id,year,where_prec,latitude,longitude,best,low,high"


def zipped(rows):
    """A GED-shaped zip: the real file has 49 columns, the loader reads these seven."""
    csv_text = "\n".join([HEADER, *(",".join(str(c) for c in r) for r in rows)])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("GEDEvent_v26_1.csv", csv_text)
    return buf.getvalue()


def event(year=2025, lon=0.5, lat=0.5, best=10, low=5, high=20, prec=1):
    return Event(year, lon, lat, best, low, high, prec)


class TestReadingTheFile:
    def test_events_are_read_with_their_estimates_and_precision(self):
        got = ucdp.parse(zipped([(1, 2025, 1, 6.5, 3.25, 10, 4, 15)]))
        assert got == [Event(2025, 3.25, 6.5, 10.0, 4.0, 15.0, 1)]

    def test_only_the_asked_years_are_kept(self):
        got = ucdp.parse(zipped([(1, 2020, 1, 1, 1, 1, 1, 1), (2, 2025, 1, 1, 1, 1, 1, 1)]), years=range(2024, 2026))
        assert [e.year for e in got] == [2025]

    def test_an_event_with_no_coordinates_is_skipped_not_placed_at_zero(self):
        assert ucdp.parse(zipped([(1, 2025, 1, "", "", 5, 5, 5)])) == []

    def test_the_window_is_the_last_three_years_present(self):
        assert ucdp.latest_window([event(2021), event(2025), event(2024)]) == range(2023, 2026)

    def test_the_release_names_its_download(self):
        assert ucdp.url("26.1").endswith("/ged261-csv.zip")


def squares():
    return {"a": shapely.box(0, 0, 1, 1), "b": shapely.box(1, 0, 2, 1)}


class TestPlacement:
    def test_an_event_counts_in_the_territory_that_contains_it(self):
        got = ucdp.assign([event(lon=0.5, best=10), event(lon=1.5, best=7), event(lon=1.6, best=3)], squares(), 4)
        assert got["a"] == [10, 5, 20] and got["b"] == [10, 10, 40]

    def test_an_event_coded_less_precisely_than_the_limit_is_not_placed(self):
        got = ucdp.assign([event(prec=5), event(prec=4, best=3, low=3, high=3)], squares(), 4)
        assert got["a"] == [3, 3, 3]

    def test_an_event_in_no_territory_is_dropped(self):
        assert ucdp.assign([event(lon=9, lat=9)], squares(), 4) == {"a": [0, 0, 0], "b": [0, 0, 0]}

    def test_an_event_on_a_shared_edge_counts_once_in_the_first_territory(self):
        got = ucdp.assign([event(lon=1.0, best=8, low=8, high=8)], squares(), 4)
        assert (got["a"][0], got["b"][0]) == (8, 0)

    def test_no_events_or_no_territories_is_all_zero(self):
        assert ucdp.assign([], squares(), 4) == {"a": [0, 0, 0], "b": [0, 0, 0]}
        assert ucdp.assign([event()], {}, 4) == {}


@pytest.mark.django_db
class TestTheRows:
    @pytest.fixture
    def country(self):
        ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
        west = boundary(1, "West", "NGA-W", 0, 0)
        east = boundary(1, "East", "NGA-E", 1, 0)
        return {"ng": ng, "west": west, "east": east}

    def rows(self, country, events):
        window = range(2023, 2026)
        return {
            r.boundary.name: r
            for r in ucdp.rows_for(events, window, [country["ng"], country["west"], country["east"]])
        }

    def test_deaths_are_a_yearly_mean_over_the_window_with_ucdps_own_bounds(self, country):
        rows = self.rows(country, [event(2023, best=30, low=15, high=60), event(2025, best=30, low=15, high=60)])
        west = rows["West"]
        assert (west.value, west.ci_low, west.ci_high, west.year) == (20.0, 10.0, 40.0, 2025)
        assert west.source == Source.UCDP and west.license_code == "cc-by-4.0"

    def test_a_quiet_territory_is_a_true_zero_not_a_missing_value(self, country):
        assert self.rows(country, [event(lon=0.5, best=9, low=9, high=9)])["East"].value == 0.0

    def test_events_outside_the_window_are_ignored(self, country):
        assert self.rows(country, [event(2019, best=900)])["West"].value == 0.0

    def test_the_country_is_the_sum_of_its_territories(self, country):
        rows = self.rows(country, [event(lon=0.5, best=9, low=9, high=9), event(lon=1.5, best=6, low=6, high=6)])
        assert rows["Nigeria"].value == 5.0 == rows["West"].value + rows["East"].value

    def test_the_country_says_how_much_of_its_coded_deaths_could_be_placed(self, country):
        rows = self.rows(country, [event(best=30, low=30, high=30), event(prec=6, best=70, low=70, high=70)])
        assert rows["Nigeria"].extra["located_share"] == pytest.approx(0.3)
        assert "located_share" not in rows["West"].extra

    def test_a_country_with_no_coded_deaths_has_no_share_rather_than_a_division_by_zero(self, country):
        assert self.rows(country, [])["Nigeria"].extra["located_share"] is None


@pytest.mark.django_db
class TestTheRate:
    def test_the_rate_is_deaths_per_100000_people_with_the_bounds_carried_over(self):
        ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
        kano = boundary(1, "Kano", "NGA-KN", 0, 0)
        base.upsert(
            [
                base.Row("conflict_deaths", kano, 2025, 50.0, Source.UCDP, ci_low=25.0, ci_high=100.0),
                base.Row("conflict_deaths", ng, 2025, 80.0, Source.UCDP),
            ]
        )
        value(kano, "pop_total", 1_000_000.0, year=2025)
        got = {r.boundary.name: r for r in derive.load_conflict_rate()}
        assert got["Kano"].value == pytest.approx(5.0)
        assert (got["Kano"].ci_low, got["Kano"].ci_high) == (pytest.approx(2.5), pytest.approx(10.0))
        assert got["Nigeria"].value == pytest.approx(8.0)  # its one unit's population stands for the country's

    def test_a_country_divides_by_the_sum_of_its_units_populations_when_all_have_one(self):
        ng = boundary(0, "Nigeria", "NGA", 0, 0, size=2)
        a, b = boundary(1, "A", "NGA-A", 0, 0), boundary(1, "B", "NGA-B", 1, 0)
        base.upsert([base.Row("conflict_deaths", ng, 2025, 60.0, Source.UCDP)])
        value(a, "pop_total", 1_000_000.0, year=2025)
        assert [r.boundary.name for r in derive.load_conflict_rate(["NGA"])] == []  # B has no population yet
        value(b, "pop_total", 2_000_000.0, year=2025)
        (got,) = derive.load_conflict_rate(["NGA"])
        assert got.boundary.name == "Nigeria" and got.value == pytest.approx(2.0)
        assert "sum of its admin-1 units" in got.method

    def test_it_is_a_registered_policy_governed_measure(self):
        assert measures.get("conflict_rate").unit == "per 100,000 people a year"
        assert measures.get("conflict_rate").weight_by == "pop_total"
        assert [e.source for e in policy.POLICY["conflict_deaths"]] == [Source.UCDP]
        assert IndicatorValue._meta.get_field("source").choices  # the new source is a model choice (migration 0017)
