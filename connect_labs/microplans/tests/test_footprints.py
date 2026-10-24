"""Tests for the durable Postgres footprint cache (fetch_buildings).

The Overture/DuckDB query is mocked — we assert the cache behavior: a miss fetches
once and persists rows; a hit reads from Postgres without re-querying; and the
confidence threshold is applied at read time (so one cached fetch serves both
sampling and coverage)."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest
from django.utils import timezone
from shapely.geometry import box

from connect_labs.microplans.core import footprints, overture
from connect_labs.microplans.models import FootprintArea, FootprintBuilding

pytestmark = pytest.mark.django_db


GOOGLE = footprints.SOURCE_GOOGLE
MICROSOFT = footprints.SOURCE_MICROSOFT
OSM = footprints.SOURCE_OSM


def _fake_buildings():
    # 4 buildings: three Google (conf 0.9 / 0.8 / 0.5) + one Microsoft (null conf).
    return pd.DataFrame(
        {
            "lon": [3.00, 3.01, 3.02, 3.03],
            "lat": [6.00, 6.01, 6.02, 6.03],
            "area_m2": [120.0, 95.0, 210.0, None],
            "confidence": [0.9, 0.8, 0.5, None],
            "dataset": [GOOGLE, GOOGLE, GOOGLE, MICROSOFT],
        }
    )


def test_miss_fetches_once_then_hits_from_postgres(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)  # tiny area, well under the size cap
    calls = {"n": 0}

    def fake_query(a, min_confidence=None):
        calls["n"] += 1
        return _fake_buildings()

    monkeypatch.setattr(footprints, "_query_overture", fake_query)

    # First call: miss → fetch + persist all 4 buildings as rows.
    df1 = footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 1 and len(df1) == 4
    assert FootprintArea.objects.count() == 1
    fa = FootprintArea.objects.get()
    assert fa.n_buildings == 4 and FootprintBuilding.objects.filter(area=fa).count() == 4

    # Second call (same geometry): hit → no re-query, same rows.
    df2 = footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 1  # _query_overture NOT called again
    assert len(df2) == 4
    assert FootprintArea.objects.count() == 1  # no duplicate area


def test_stale_cache_refetches_and_replaces(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    calls = {"n": 0}

    def fake_query(a, min_confidence=None):
        calls["n"] += 1
        return _fake_buildings()

    monkeypatch.setattr(footprints, "_query_overture", fake_query)

    footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 1
    fa = FootprintArea.objects.get()
    # Backdate past the cache's max age (bypasses auto_now_add via .update(),
    # which issues a plain SQL UPDATE rather than going through model save()).
    stale_at = timezone.now() - footprints.FOOTPRINT_CACHE_MAX_AGE - timedelta(days=1)
    FootprintArea.objects.filter(pk=fa.pk).update(created_at=stale_at)

    df2 = footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 2  # stale — re-fetched instead of reusing the old rows
    assert len(df2) == 4
    # Replaced, not duplicated: still exactly one area row, but a new one.
    assert FootprintArea.objects.count() == 1
    assert FootprintArea.objects.get().pk != fa.pk


def test_fresh_cache_within_max_age_is_not_refetched(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    calls = {"n": 0}

    def fake_query(a, min_confidence=None):
        calls["n"] += 1
        return _fake_buildings()

    monkeypatch.setattr(footprints, "_query_overture", fake_query)

    footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 1
    fa = FootprintArea.objects.get()
    fresh_at = timezone.now() - footprints.FOOTPRINT_CACHE_MAX_AGE + timedelta(hours=1)
    FootprintArea.objects.filter(pk=fa.pk).update(created_at=fresh_at)

    footprints.fetch_buildings(area, min_confidence=None)
    assert calls["n"] == 1  # still within max age — cache honored, no re-fetch


def test_confidence_filter_applies_to_google_keeps_sourceless(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    monkeypatch.setattr(footprints, "_query_overture", lambda a, min_confidence=None: _fake_buildings())

    # Stored unfiltered (4). A 0.7 threshold drops the 0.5 Google building but KEEPS
    # the null-confidence Microsoft one (the threshold only gates the source that
    # carries a confidence; source inclusion is the `sources` filter's job).
    footprints.fetch_buildings(area, min_confidence=None)
    df = footprints.fetch_buildings(area, min_confidence=0.7)
    assert len(df) == 3
    assert sorted(df["confidence"].dropna()) == [0.8, 0.9]
    assert MICROSOFT in set(df["dataset"])


def test_source_filter_selects_providers(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    monkeypatch.setattr(footprints, "_query_overture", lambda a, min_confidence=None: _fake_buildings())
    footprints.fetch_buildings(area, min_confidence=None)  # cache all 4

    google_only = footprints.fetch_buildings(area, sources=[GOOGLE])
    assert len(google_only) == 3 and set(google_only["dataset"]) == {GOOGLE}

    ms_only = footprints.fetch_buildings(area, sources=[MICROSOFT])
    assert len(ms_only) == 1 and set(ms_only["dataset"]) == {MICROSOFT}

    both = footprints.fetch_buildings(area, sources=[GOOGLE, MICROSOFT])
    assert len(both) == 4

    osm_only = footprints.fetch_buildings(area, sources=[OSM])  # none present
    assert len(osm_only) == 0


def test_source_and_confidence_compose(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    monkeypatch.setattr(footprints, "_query_overture", lambda a, min_confidence=None: _fake_buildings())
    footprints.fetch_buildings(area, min_confidence=None)

    # Google + 0.7: the two high-confidence Google buildings (0.5 dropped, MS excluded).
    df = footprints.fetch_buildings(area, sources=[GOOGLE], min_confidence=0.7)
    assert len(df) == 2 and set(df["confidence"]) == {0.9, 0.8}


def test_source_counts_breakdown():
    counts = footprints.source_counts(_fake_buildings())
    assert counts == {GOOGLE: 3, MICROSOFT: 1}


def test_oversized_area_rejected_before_any_fetch(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(footprints, "_query_overture", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    huge = box(0, 0, 40, 40)  # ~ thousands of km² — exceeds MAX_AREA_KM2
    with pytest.raises(ValueError, match="too large"):
        footprints.fetch_buildings(huge)
    assert calls["n"] == 0 and FootprintArea.objects.count() == 0


def _count_reads(monkeypatch):
    calls = {"tiles": 0, "raw": 0}
    monkeypatch.setattr(
        footprints,
        "_query_tiles",
        lambda a, r, mc: (calls.__setitem__("tiles", calls["tiles"] + 1), _fake_buildings())[1],
    )
    monkeypatch.setattr(
        footprints,
        "_query_raw",
        lambda a, mc: (calls.__setitem__("raw", calls["raw"] + 1), _fake_buildings())[1],
    )
    return calls


def test_an_area_inside_a_tiled_region_reads_its_tiles(monkeypatch):
    calls = _count_reads(monkeypatch)
    monkeypatch.setattr(
        overture, "ready", lambda release=None: {"tiles": {"buildings": {"nigeria": (2.6, 4.2, 14.7, 13.9)}}}
    )
    footprints._query_overture(box(8.282, 11.770, 8.288, 11.775), None)  # Madobi, Nigeria
    assert calls == {"tiles": 1, "raw": 0}


def test_an_area_outside_every_tiled_region_reads_the_raw_snapshot(monkeypatch):
    calls = _count_reads(monkeypatch)
    monkeypatch.setattr(
        overture, "ready", lambda release=None: {"tiles": {"buildings": {"nigeria": (2.6, 4.2, 14.7, 13.9)}}}
    )
    footprints._query_overture(box(77.20, 28.60, 77.21, 28.61), None)  # Delhi
    assert calls == {"tiles": 0, "raw": 1}


def test_a_snapshot_with_no_ready_file_never_reads_tiles(monkeypatch):
    """Tiles a refresh is still cutting may be partial; until READY.json lists
    them, even an area deep inside Nigeria reads the raw snapshot."""
    calls = _count_reads(monkeypatch)
    monkeypatch.setattr(overture, "ready", lambda release=None: None)
    footprints._query_overture(box(8.282, 11.770, 8.288, 11.775), None)
    assert calls == {"tiles": 0, "raw": 1}


def test_the_raw_read_is_labs_snapshot_never_overtures_bucket(monkeypatch):
    """The October failure mode: a read pointed at Overture's bucket dies when
    Overture prunes the release. Every read path must name labs' bucket."""
    seen = []

    class Con:
        def execute(self, sql, params=None):
            seen.append(sql)
            return self

        def df(self):
            return pd.DataFrame(columns=["lon", "lat", "area_m2", "confidence", "dataset", "geom_json"])

    monkeypatch.setattr(overture, "connect", lambda: Con())
    footprints._query_raw(box(77.20, 28.60, 77.21, 28.61), None)
    footprints._query_tiles(box(8.282, 11.770, 8.288, 11.775), "nigeria", None)
    assert len(seen) == 2
    for sql in seen:
        assert overture.EXTRACT_BUCKET in sql
        assert overture.SOURCE_BUCKET not in sql


def test_a_release_switch_misses_the_old_cache(monkeypatch):
    """The footprint cache key carries the release, so a switch re-reads an area
    from the new snapshot instead of serving last release's buildings."""
    monkeypatch.setattr(overture, "active_release", lambda: "2026-10-21.0")
    before = footprints._area_cache_key("POLYGON((0 0,1 0,1 1,0 0))")
    monkeypatch.setattr(overture, "active_release", lambda: "2026-11-18.0")
    assert footprints._area_cache_key("POLYGON((0 0,1 0,1 1,0 0))") != before
