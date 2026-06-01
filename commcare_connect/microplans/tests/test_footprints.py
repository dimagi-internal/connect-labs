"""Tests for the durable Postgres footprint cache (fetch_buildings).

The Overture/DuckDB query is mocked — we assert the cache behavior: a miss fetches
once and persists rows; a hit reads from Postgres without re-querying; and the
confidence threshold is applied at read time (so one cached fetch serves both
sampling and coverage)."""

from __future__ import annotations

import pandas as pd
import pytest
from shapely.geometry import box

from commcare_connect.microplans.core import footprints
from commcare_connect.microplans.models import FootprintArea, FootprintBuilding

pytestmark = pytest.mark.django_db


def _fake_buildings():
    # 4 buildings: two high-confidence, one low, one null (Microsoft/OSM).
    return pd.DataFrame(
        {
            "lon": [3.00, 3.01, 3.02, 3.03],
            "lat": [6.00, 6.01, 6.02, 6.03],
            "area_m2": [120.0, 95.0, 210.0, None],
            "confidence": [0.9, 0.8, 0.5, None],
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


def test_confidence_filter_applied_at_read(monkeypatch):
    area = box(3.0, 6.0, 3.05, 6.05)
    monkeypatch.setattr(footprints, "_query_overture", lambda a, min_confidence=None: _fake_buildings())

    # Stored unfiltered (4); a 0.7 threshold drops the 0.5 and the null → 2 remain.
    footprints.fetch_buildings(area, min_confidence=None)
    df = footprints.fetch_buildings(area, min_confidence=0.7)
    assert len(df) == 2 and set(df["confidence"]) == {0.9, 0.8}


def test_oversized_area_rejected_before_any_fetch(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(footprints, "_query_overture", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    huge = box(0, 0, 40, 40)  # ~ thousands of km² — exceeds MAX_AREA_KM2
    with pytest.raises(ValueError, match="too large"):
        footprints.fetch_buildings(huge)
    assert calls["n"] == 0 and FootprintArea.objects.count() == 0


def test_query_overture_routes_to_same_region_extract_inside_nigeria(monkeypatch):
    """An area inside an extracted region reads the same-region extract, not live."""
    calls = {"extract": 0, "live": 0}
    monkeypatch.setattr(
        footprints,
        "_query_extract",
        lambda a, r, mc: (calls.__setitem__("extract", calls["extract"] + 1), _fake_buildings())[1],
    )
    monkeypatch.setattr(
        footprints,
        "_query_overture_live",
        lambda a, mc: (calls.__setitem__("live", calls["live"] + 1), _fake_buildings())[1],
    )
    footprints._query_overture(box(8.282, 11.770, 8.288, 11.775), None)  # Madobi, Nigeria
    assert calls == {"extract": 1, "live": 0}


def test_query_overture_falls_back_to_live_outside_extracted_region(monkeypatch):
    """An area with no same-region extract still works via the live Overture read."""
    calls = {"extract": 0, "live": 0}
    monkeypatch.setattr(
        footprints,
        "_query_extract",
        lambda a, r, mc: (calls.__setitem__("extract", calls["extract"] + 1), _fake_buildings())[1],
    )
    monkeypatch.setattr(
        footprints,
        "_query_overture_live",
        lambda a, mc: (calls.__setitem__("live", calls["live"] + 1), _fake_buildings())[1],
    )
    footprints._query_overture(box(36.80, -1.30, 36.81, -1.29), None)  # Nairobi, not extracted
    assert calls == {"extract": 0, "live": 1}


def test_extract_release_guard_falls_back_when_release_bumped(monkeypatch):
    """If the active Overture release no longer matches the extract, fall back to
    live rather than serving stale buildings."""
    from commcare_connect.microplans.core import overture

    assert overture.covering_region((8.282, 11.770, 8.288, 11.775)) == "nigeria"
    monkeypatch.setattr(overture, "OVERTURE_RELEASE", "9999-99-99.0")
    assert overture.covering_region((8.282, 11.770, 8.288, 11.775)) is None
