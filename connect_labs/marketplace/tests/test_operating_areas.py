"""Where an organisation works, as points on the network map.

The head-office point answers "where is its desk"; this answers "where does it
work", which is what someone scanning the map for partners in Borno is asking.
All data invented; boundaries are unit squares, not real geometry.
"""

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.marketplace import queries
from connect_labs.marketplace.testing import make_partner
from connect_labs.pulse.hq_location import operating_areas

_id = iter(range(1, 10_000))


def _boundary(iso3, level, name, x, y=0, source=AdminBoundary.Source.GEOBOUNDARIES):
    square = Polygon(((x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1), (x, y)))
    return AdminBoundary.objects.create(
        iso_code=iso3,
        admin_level=level,
        name=name,
        boundary_id=f"b{next(_id)}",
        geometry=MultiPolygon(square),
        source=source,
    )


@pytest.fixture
def boundaries(db):
    _boundary("NGA", 0, "Nigeria", 0, 0)
    _boundary("NGA", 1, "Borno", 10)
    _boundary("NGA", 1, "Yobe", 12)
    _boundary("NGA", 1, "Kano", 14)
    _boundary("KEN", 0, "Kenya", 30)
    _boundary("KEN", 1, "Turkana", 32)


@pytest.mark.django_db
class TestOperatingAreas:
    def test_every_named_region_is_a_point(self, boundaries):
        areas = operating_areas(["Nigeria"], "Borno, Yobe and Adamawa states")
        assert [(a["label"], a["precision"], a["iso3"]) for a in areas] == [
            ("Borno", "region", "NGA"),
            ("Yobe", "region", "NGA"),
        ]
        assert areas[0]["lon"] == pytest.approx(10.5)

    def test_a_country_with_no_region_named_is_drawn_as_the_country(self, boundaries):
        areas = operating_areas(["Nigeria", "Kenya"], "Borno")
        assert [(a["label"], a["precision"]) for a in areas] == [("Borno", "region"), ("Kenya", "country")]

    def test_no_regions_at_all_gives_one_point_per_country(self, boundaries):
        areas = operating_areas(["Nigeria", "Kenya"], "")
        assert [a["precision"] for a in areas] == ["country", "country"]

    def test_a_region_held_by_two_sources_is_drawn_once(self, boundaries):
        _boundary("NGA", 1, "Borno", 10, source=AdminBoundary.Source.GEOPODE)
        assert [a["label"] for a in operating_areas(["Nigeria"], "Borno")] == ["Borno"]

    def test_no_country_means_no_points(self, boundaries):
        """It cannot guess a country from regions alone."""
        assert operating_areas([], "Borno") == []


@pytest.mark.django_db
class TestTheMapDrawsThem:
    def _areas(self, *specs):
        return [{"lat": 0.5, "lon": x, "precision": p, "label": label, "iso3": iso3} for label, x, p, iso3 in specs]

    def test_one_point_per_area(self, db):
        org = make_partner("Lakeshore Health Trust", "LHT", countries=["Nigeria", "Kenya"], lat=9.0, lon=7.0)
        org.marketplace_profile.operating_areas = self._areas(
            ("Borno", 10.5, "region", "NGA"), ("Yobe", 12.5, "region", "NGA"), ("Kenya", 30.5, "country", "KEN")
        )
        org.marketplace_profile.save()
        points = queries.map_points(queries.all_rows_with_rounds(), set())
        assert [p["place"] for p in points] == ["Borno", "Yobe", "Kenya"]
        assert {p["name"] for p in points} == {"Lakeshore Health Trust"}

    def test_a_country_filter_draws_only_that_country(self, db):
        org = make_partner("Lakeshore Health Trust", "LHT", countries=["Nigeria", "Kenya"])
        org.marketplace_profile.operating_areas = self._areas(
            ("Borno", 10.5, "region", "NGA"), ("Kenya", 30.5, "country", "KEN")
        )
        org.marketplace_profile.save()
        points = queries.map_points(queries.all_rows_with_rounds(), set(), ["Kenya"])
        assert [p["place"] for p in points] == ["Kenya"]

    def test_without_areas_it_falls_back_to_the_head_office(self, db):
        make_partner("Unresolved Trust", "UT", countries=["Kenya"], lat=1.0, lon=36.0, location_precision="city")
        points = queries.map_points(queries.all_rows_with_rounds(), set())
        assert [(p["lat"], p["precision"]) for p in points] == [(1.0, "city")]
