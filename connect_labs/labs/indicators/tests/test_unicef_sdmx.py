"""The UNICEF SDMX loader.

These pin the two things that go wrong silently: the positional-index
encoding, which misattributes values rather than raising, and vintage mixing,
which produces a biased number wearing a national label.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators.models import Source
from connect_labs.labs.indicators.sources import unicef_sdmx as U
from connect_labs.labs.indicators.tests.test_resolve import make_boundary

pytestmark = pytest.mark.django_db


def _payload(observations, dimensions):
    return {
        "data": {
            "structure": {"dimensions": {"observation": dimensions}},
            "dataSets": [{"observations": observations}],
        }
    }


DIMS = [
    {"id": "REF_AREA", "values": [{"name": "Bong"}, {"name": "Nimba"}, {"name": "Ghost"}]},
    {"id": "INDICATOR", "values": [{"name": "Open defecation"}, {"name": "Something else"}]},
    {"id": "REF_AREA_PARENT", "values": [{"name": "Liberia"}]},
    {"id": "ADMIN_LEVEL", "values": [{"name": "Administrative level 1"}]},
    {"id": "TIME_PERIOD", "values": [{"name": "2012"}, {"name": "2019"}]},
    {"id": "DATA_SOURCE_MAIN", "values": [{"name": "MICS"}]},
]


class TestDecode:
    def test_positional_indices_resolve_to_the_right_dimension_values(self):
        """The encoding is positional, and getting it wrong does not raise —
        it silently attributes one region's value to another."""
        payload = _payload({"1:0:0:0:1:0": [42.0]}, DIMS)
        rows = list(U.decode(payload))

        assert len(rows) == 1
        assert rows[0]["REF_AREA"] == "Nimba"
        assert rows[0]["INDICATOR"] == "Open defecation"
        assert rows[0]["TIME_PERIOD"] == "2019"
        assert rows[0]["_value"] == 42.0

    def test_an_observation_with_no_value_is_skipped(self):
        """SDMX is a sparse map: a missing value means no observation, not an
        observation of nothing."""
        payload = _payload({"0:0:0:0:0:0": [None]}, DIMS)
        assert list(U.decode(payload)) == []


class TestVintageIsHeldPerCountry:
    """UNICEF mixes tessellations within a country, and the latest row per
    AREA silently combines them.

    Tunisia carries seven economic regions from MICS 2018 and a handful of
    individual governorates from MICS 2012. Keeping the latest of each matched
    only the governorates -- the four poorest interior ones -- and rolled up to
    14.5% open defecation against a true national figure near zero. Partial
    matching does not leave a gap; it produces a biased number that looks
    complete.
    """

    def test_only_the_latest_series_survives(self):
        records = [
            {"REF_AREA": "Bong", "REF_AREA_PARENT": "Liberia", "TIME_PERIOD": "2019", "_value": 1.0},
            {"REF_AREA": "Nimba", "REF_AREA_PARENT": "Liberia", "TIME_PERIOD": "2019", "_value": 2.0},
            # An older round covering a DIFFERENT set of areas.
            {"REF_AREA": "Old Region", "REF_AREA_PARENT": "Liberia", "TIME_PERIOD": "2012", "_value": 9.0},
        ]
        kept = U._latest_series_per_country(records, "TIME_PERIOD")

        assert set(kept) == {"Bong", "Nimba"}
        assert "Old Region" not in kept

    def test_countries_keep_their_own_vintages(self):
        """One country's newer survey must not evict another's older one."""
        records = [
            {"REF_AREA": "Bong", "REF_AREA_PARENT": "Liberia", "TIME_PERIOD": "2019", "_value": 1.0},
            {"REF_AREA": "Kassala", "REF_AREA_PARENT": "Sudan", "TIME_PERIOD": "2014", "_value": 2.0},
        ]
        kept = U._latest_series_per_country(records, "TIME_PERIOD")
        assert set(kept) == {"Bong", "Kassala"}


class TestLoad:
    def test_it_matches_areas_to_boundaries_and_drops_the_rest(self):
        make_boundary("LBR", 0, "Liberia", "LBR-0")
        make_boundary("LBR", 1, "Bong", "LBR-1")
        make_boundary("LBR", 1, "Nimba", "LBR-2", x=2)

        payload = _payload(
            {
                "0:0:0:0:1:0": [12.5],  # Bong
                "1:0:0:0:1:0": [30.0],  # Nimba
                "2:0:0:0:1:0": [99.0],  # "Ghost" — no such boundary
            },
            DIMS,
        )
        rows = U.load("WASH_HOUSEHOLD_SUBNAT", "Open defecation", "open_defecation", payload=payload)

        by_name = {r.boundary.name: r.value for r in rows}
        assert by_name == {"Bong": 12.5, "Nimba": 30.0}
        assert all(r.source == Source.UNICEF_SDMX for r in rows)
        assert all(r.year == 2019 for r in rows)

    def test_the_indicator_is_matched_exactly_not_by_substring(self):
        """The WASH dataflow carries four 'improved drinking water' indicators
        differing only in their tail. A substring match takes whichever comes
        first, which is not a choice anyone made."""
        make_boundary("LBR", 0, "Liberia", "LBR-0")
        make_boundary("LBR", 1, "Bong", "LBR-1")
        payload = _payload({"0:1:0:0:1:0": [12.5]}, DIMS)  # "Something else"

        rows = U.load("WASH_HOUSEHOLD_SUBNAT", "Open defecation", "open_defecation", payload=payload)
        assert rows == []

    def test_a_country_we_hold_no_boundaries_for_is_skipped_not_guessed(self):
        payload = _payload({"0:0:0:0:1:0": [12.5]}, DIMS)
        assert U.load("WASH_HOUSEHOLD_SUBNAT", "Open defecation", "open_defecation", payload=payload) == []
