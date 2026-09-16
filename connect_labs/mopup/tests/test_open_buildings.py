"""Tests for the Google Open Buildings direct-fetch source (core.open_buildings).

Network (`_download_shard`) and S2 covering (`_s2_covering_tokens`) are mocked
for the caching/shape tests — we assert the cache behavior (mirrors
microplans/tests/test_footprints.py's pattern for the same reason: a miss
fetches once and persists rows, a hit reads from Postgres with zero network
calls) and the ward-boundary spatial filter (real shapely, not mocked). The
S2-shard-count guard is exercised against real `s2sphere` math (a genuinely
huge bounding box), not mocked, since that's the one thing actually worth
testing end to end without a network call."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from django.utils import timezone
from shapely.geometry import box

from connect_labs.mopup.core import open_buildings
from connect_labs.mopup.models import OpenBuildingsArea, OpenBuildingsBuilding

pytestmark = pytest.mark.django_db


def _raw_shard(rows):
    """rows: list of (lon, lat) tuples, matching _download_shard's output shape."""
    return pd.DataFrame(
        {
            "lat": [r[1] for r in rows],
            "lon": [r[0] for r in rows],
            "area_m2": [50.0] * len(rows),
            "confidence": [0.8] * len(rows),
            "full_plus_code": ["ABC"] * len(rows),
        }
    )


class TestFetchOpenBuildingsForWard:
    def test_miss_fetches_and_caches_then_hit_reads_from_postgres(self, monkeypatch):
        ward = box(0.0, 0.0, 0.01, 0.01)
        calls = {"n": 0}

        monkeypatch.setattr(open_buildings, "_s2_covering_tokens", lambda bounds: ["aaaa"])

        def fake_download(token, session):
            calls["n"] += 1
            return _raw_shard([(0.005, 0.005), (0.006, 0.006)])

        monkeypatch.setattr(open_buildings, "_download_shard", fake_download)

        df1 = open_buildings.fetch_open_buildings_for_ward(ward)
        assert calls["n"] == 1
        assert len(df1) == 2
        assert set(df1.columns) == {"lon", "lat", "area_m2", "confidence", "dataset"}
        assert (df1["dataset"] == open_buildings.DATASET_LABEL).all()
        assert OpenBuildingsArea.objects.count() == 1
        area = OpenBuildingsArea.objects.get()
        assert area.n_buildings == 2
        assert OpenBuildingsBuilding.objects.filter(area=area).count() == 2

        # Second call, same ward: cache hit — zero network calls.
        df2 = open_buildings.fetch_open_buildings_for_ward(ward)
        assert calls["n"] == 1  # _download_shard NOT called again
        assert len(df2) == 2
        assert OpenBuildingsArea.objects.count() == 1  # no duplicate area row

    def test_stale_cache_refetches_and_replaces(self, monkeypatch):
        ward = box(0.0, 0.0, 0.01, 0.01)
        calls = {"n": 0}

        monkeypatch.setattr(open_buildings, "_s2_covering_tokens", lambda bounds: ["aaaa"])

        def fake_download(token, session):
            calls["n"] += 1
            return _raw_shard([(0.005, 0.005)])

        monkeypatch.setattr(open_buildings, "_download_shard", fake_download)

        open_buildings.fetch_open_buildings_for_ward(ward)
        assert calls["n"] == 1
        area = OpenBuildingsArea.objects.get()
        stale_at = timezone.now() - open_buildings.CACHE_MAX_AGE - timedelta(days=1)
        OpenBuildingsArea.objects.filter(pk=area.pk).update(created_at=stale_at)

        open_buildings.fetch_open_buildings_for_ward(ward)
        assert calls["n"] == 2  # stale — re-fetched instead of reusing the old rows
        assert OpenBuildingsArea.objects.count() == 1  # replaced, not duplicated
        assert OpenBuildingsArea.objects.get().pk != area.pk

    def test_fresh_cache_within_max_age_is_not_refetched(self, monkeypatch):
        ward = box(0.0, 0.0, 0.01, 0.01)
        calls = {"n": 0}

        monkeypatch.setattr(open_buildings, "_s2_covering_tokens", lambda bounds: ["aaaa"])

        def fake_download(token, session):
            calls["n"] += 1
            return _raw_shard([(0.005, 0.005)])

        monkeypatch.setattr(open_buildings, "_download_shard", fake_download)

        open_buildings.fetch_open_buildings_for_ward(ward)
        area = OpenBuildingsArea.objects.get()
        fresh_at = timezone.now() - open_buildings.CACHE_MAX_AGE + timedelta(days=1)
        OpenBuildingsArea.objects.filter(pk=area.pk).update(created_at=fresh_at)

        open_buildings.fetch_open_buildings_for_ward(ward)
        assert calls["n"] == 1  # still within TTL — no re-fetch

    def test_404_shards_are_treated_as_empty_not_an_error(self, monkeypatch):
        ward = box(0.0, 0.0, 0.01, 0.01)
        monkeypatch.setattr(open_buildings, "_s2_covering_tokens", lambda bounds: ["aaaa", "bbbb"])
        monkeypatch.setattr(open_buildings, "_download_shard", lambda token, session: None)

        df = open_buildings.fetch_open_buildings_for_ward(ward)
        assert len(df) == 0
        assert OpenBuildingsArea.objects.get().n_buildings == 0


class TestFetchAndFilterToWard:
    """Real shapely filtering — no mocking of the spatial-containment logic
    itself, only the network fetch."""

    def test_drops_points_outside_the_ward_polygon(self, monkeypatch):
        ward = box(0.0, 0.0, 0.01, 0.01)
        monkeypatch.setattr(open_buildings, "_s2_covering_tokens", lambda bounds: ["aaaa"])
        monkeypatch.setattr(
            open_buildings,
            "_download_shard",
            lambda token, session: _raw_shard(
                [
                    (0.005, 0.005),  # inside
                    (0.5, 0.5),  # far outside — same shard's cell, different ward
                ]
            ),
        )

        result = open_buildings._fetch_and_filter_to_ward(ward)
        assert len(result) == 1
        assert result.iloc[0]["lon"] == pytest.approx(0.005)
        assert result.iloc[0]["lat"] == pytest.approx(0.005)


class TestS2CoveringTokens:
    def test_small_bbox_returns_a_few_tokens(self):
        tokens = open_buildings._s2_covering_tokens((8.4, 11.8, 8.7, 12.1))  # ~Kano-area bbox
        assert 1 <= len(tokens) <= open_buildings.MAX_SHARDS_PER_WARD

    def test_oversized_bbox_raises_value_error(self):
        # Roughly half of Africa -- genuinely spans far more than
        # MAX_SHARDS_PER_WARD level-6 S2 cells, real math, no mocking.
        with pytest.raises(ValueError, match="shards"):
            open_buildings._s2_covering_tokens((-20.0, -30.0, 40.0, 30.0))
