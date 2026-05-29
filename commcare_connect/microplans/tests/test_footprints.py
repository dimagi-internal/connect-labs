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
