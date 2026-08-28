"""DHS loader tests that need no network."""

from __future__ import annotations

import pytest

from connect_labs.labs.indicators.sources.dhs import _latest_survey_per_country


def rec(code, year, label, value=100):
    return {
        "DHS_CountryCode": code,
        "SurveyYear": year,
        "CharacteristicLabel": label,
        "Value": value,
    }


class TestLatestSurveyPerCountry:
    def test_only_the_most_recent_survey_survives(self):
        got = _latest_survey_per_country([rec("NG", 2013, "Kano"), rec("NG", 2024, "Kano"), rec("NG", 2024, "Lagos")])
        assert set(got) == {("NG", "Kano"), ("NG", "Lagos")}
        assert got[("NG", "Kano")]["SurveyYear"] == 2024

    def test_labels_from_an_older_taxonomy_are_dropped_entirely(self):
        # Kenya's pre-2010 provinces must not ride alongside its 2022 counties;
        # keeping both would double-count the same population.
        got = _latest_survey_per_country(
            [rec("KE", 2008, "Coast"), rec("KE", 2022, "Mombasa"), rec("KE", 2022, "Kilifi")]
        )
        assert ("KE", "Coast") not in got
        assert len(got) == 2

    def test_each_country_keeps_its_own_latest_year(self):
        got = _latest_survey_per_country([rec("NG", 2024, "Kano"), rec("ML", 2023, "Segou"), rec("ML", 2018, "Segou")])
        assert got[("NG", "Kano")]["SurveyYear"] == 2024
        assert got[("ML", "Segou")]["SurveyYear"] == 2023

    def test_empty_input(self):
        assert _latest_survey_per_country([]) == {}


class TestWorldPopDecomposition:
    """The service rejects MultiPolygons and anything over 100,000 km².

    Both are handled by cutting the boundary into acceptable pieces and summing.
    That is only valid if the pieces are disjoint and cover the original, so
    these tests check exactly that.
    """

    def test_multipolygon_is_exploded_into_polygons(self):
        from django.contrib.gis.geos import MultiPolygon, Polygon

        from connect_labs.labs.indicators.sources.worldpop import _explode

        a = Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)))
        b = Polygon(((5, 5), (6, 5), (6, 6), (5, 6), (5, 5)))
        parts = _explode(MultiPolygon(a, b, srid=4326))

        assert len(parts) == 2
        assert all(p.geom_type == "Polygon" for p in parts)

    def test_a_small_polygon_is_left_whole(self):
        from django.contrib.gis.geos import Polygon

        from connect_labs.labs.indicators.sources.worldpop import _split_to_limit

        small = Polygon(((0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5), (0, 0)), srid=4326)
        assert len(_split_to_limit(small)) == 1

    def test_an_oversized_polygon_is_split_into_pieces_under_the_cap(self):
        from django.contrib.gis.geos import Polygon

        from connect_labs.labs.indicators.sources.worldpop import MAX_AREA_KM2, _area_km2, _split_to_limit

        # ~10 degrees square in the Sahara — several hundred thousand km².
        big = Polygon(((0, 15), (10, 15), (10, 25), (0, 25), (0, 15)), srid=4326)
        assert _area_km2(big) > MAX_AREA_KM2

        pieces = _split_to_limit(big)

        assert len(pieces) > 1
        assert all(_area_km2(p) <= MAX_AREA_KM2 for p in pieces)

    def test_pieces_tile_the_original_without_gaps_or_overlap(self):
        from django.contrib.gis.geos import Polygon

        from connect_labs.labs.indicators.sources.worldpop import _area_km2, _split_to_limit

        big = Polygon(((0, 15), (10, 15), (10, 25), (0, 25), (0, 15)), srid=4326)
        pieces = _split_to_limit(big)

        # Summed piece area must equal the whole: gaps would lose population,
        # overlaps would double-count it.
        summed = sum(_area_km2(p) for p in pieces)
        assert summed == pytest.approx(_area_km2(big), rel=1e-6)

    def test_island_specks_are_dropped_by_area_and_the_loss_is_reported(self):
        from django.contrib.gis.geos import Polygon

        from connect_labs.labs.indicators.sources.worldpop import _select_pieces

        mainland = Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), srid=4326)
        specks = [
            Polygon(
                ((5 + i, 5), (5.001 + i, 5), (5.001 + i, 5.001), (5 + i, 5.001), (5 + i, 5)),
                srid=4326,
            )
            for i in range(30)
        ]

        kept, omitted = _select_pieces([mainland, *specks])

        assert kept[0].equals(mainland)
        assert len(kept) < 31
        # Something was left out, and it is a knowable, tiny amount.
        assert 0 < omitted < 0.005

    def test_nothing_is_dropped_when_pieces_are_comparable(self):
        from django.contrib.gis.geos import Polygon

        from connect_labs.labs.indicators.sources.worldpop import _select_pieces

        halves = [
            Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), srid=4326),
            Polygon(((2, 0), (3, 0), (3, 1), (2, 1), (2, 0)), srid=4326),
        ]
        kept, omitted = _select_pieces(halves)

        assert len(kept) == 2
        assert omitted == pytest.approx(0.0, abs=1e-9)


class TestRateLimitHandling:
    """A quota refusal must stop the run, not be retried.

    Learned the hard way: repeated restarts of the WorldPop backfill exhausted
    its undocumented daily quota, and each subsequent failure then spent three
    retries deepening the hole — 463 boundaries returned nothing.
    """

    def test_a_429_raises_rate_limited_without_retrying(self, monkeypatch):
        from connect_labs.labs.indicators.sources import base

        calls = {"n": 0}

        class Resp:
            status_code = 429
            text = "Your application is sending too many requests per day."

        def fake_post(*a, **kw):
            calls["n"] += 1
            return Resp()

        monkeypatch.setattr(base.requests, "post", fake_post)

        with pytest.raises(base.RateLimited):
            base.http_json_post("https://example.test/x", {"a": "b"}, retries=3)

        # Once, not three times.
        assert calls["n"] == 1

    def test_an_ordinary_error_still_retries(self, monkeypatch):
        from connect_labs.labs.indicators.sources import base

        calls = {"n": 0}

        def fake_post(*a, **kw):
            calls["n"] += 1
            raise ConnectionError("boom")

        monkeypatch.setattr(base.requests, "post", fake_post)
        monkeypatch.setattr(base.time, "sleep", lambda *_: None)

        with pytest.raises(RuntimeError):
            base.http_json_post("https://example.test/x", {"a": "b"}, retries=3)

        assert calls["n"] == 3
