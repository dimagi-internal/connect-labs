"""Tests for the part that stops a stored conclusion becoming folklore."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import research
from connect_labs.labs.indicators.models import IndicatorValue, License, ResearchNote, Source

pytestmark = pytest.mark.django_db


@pytest.fixture
def nigeria(db):
    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=0,
        name="Nigeria",
        source=AdminBoundary.Source.GEOBOUNDARIES,
        geometry="MULTIPOLYGON(((3 4, 14 4, 14 13, 3 13, 3 4)))",
    )


@pytest.fixture
def cases(nigeria):
    return IndicatorValue.objects.create(
        indicator="malaria_cases",
        boundary=nigeria,
        year=2024,
        value=70_000_000.0,
        source=Source.MAP,
        license_code=License.CC_BY_3,
        retrieved_at=timezone.now(),
    )


def _note(**kw):
    return ResearchNote.objects.create(
        indicator=kw.pop("indicator", "malaria_cases"),
        topic=kw.pop("topic", "which-source"),
        summary=kw.pop("summary", "MAP is the only source with counts."),
        body=kw.pop("body", "..."),
        **kw,
    )


def test_a_value_check_holds_while_the_number_does(cases):
    note = _note(checks=[{"kind": "value", "indicator": "malaria_cases", "iso": "NGA", "expected": 70_000_000}])
    (result,) = research.revalidate(note)
    assert result.holds
    assert research.describe(note)["trust"] == "holds"


def test_a_value_check_catches_a_number_that_moved(cases):
    note = _note(checks=[{"kind": "value", "indicator": "malaria_cases", "iso": "NGA", "expected": 40_000_000}])
    (result,) = research.revalidate(note)
    assert not result.holds
    assert "moved" in result.detail
    described = research.describe(note)
    assert described["trust"] == "drifted"
    assert "re-deriving" in described["advice"]


def test_a_value_check_fails_loudly_when_the_value_is_gone(nigeria):
    note = _note(checks=[{"kind": "value", "indicator": "malaria_cases", "iso": "NGA", "expected": 70_000_000}])
    (result,) = research.revalidate(note)
    assert not result.holds
    assert result.actual is None


def test_coverage_that_grew_is_not_drift(cases):
    """A backfill improving coverage must not read as the note breaking."""
    note = _note(checks=[{"kind": "coverage", "indicator": "malaria_cases", "level": 0, "expected": 1}])
    assert research.revalidate(note)[0].holds

    note.checks = [{"kind": "coverage", "indicator": "malaria_cases", "level": 0, "expected": 0}]
    assert research.revalidate(note)[0].holds, "more data than the note recorded is good news"


def test_coverage_that_fell_is_drift(cases):
    note = _note(checks=[{"kind": "coverage", "indicator": "malaria_cases", "level": 0, "expected": 5}])
    result = research.revalidate(note)[0]
    assert not result.holds
    assert "fell by 4" in result.detail


def test_source_check_notices_a_source_disappearing(cases):
    note = _note(checks=[{"kind": "source", "indicator": "malaria_cases", "source": "dhs", "expected": True}])
    result = research.revalidate(note)[0]
    assert not result.holds
    assert result.detail == "source has gone"


def test_measure_check_catches_a_redefinition():
    """The nastiest drift: reasoning about a count as though it were a rate."""
    note = _note(checks=[{"kind": "measure", "code": "malaria_cases", "expected": {"kind": "rate"}}])
    result = research.revalidate(note)[0]
    assert not result.holds
    assert "note said 'rate'" in result.detail

    note.checks = [{"kind": "measure", "code": "malaria_cases", "expected": {"kind": "count", "family": "burden"}}]
    assert research.revalidate(note)[0].holds


def test_an_unknown_check_kind_reports_itself_rather_than_passing():
    note = _note(checks=[{"kind": "vibes", "indicator": "malaria_cases"}])
    result = research.revalidate(note)[0]
    assert not result.holds
    assert "unknown check kind" in result.detail


def test_a_malformed_check_cannot_break_the_read():
    note = _note(checks=[{"kind": "value", "indicator": "malaria_cases"}])
    result = research.revalidate(note)[0]
    assert not result.holds
    assert "malformed" in result.detail


def test_a_note_with_no_checks_is_unverified_not_trusted():
    note = _note(checks=[])
    described = research.describe(note)
    assert described["trust"] == "unverified"
    assert "lead, not a finding" in described["advice"]


def test_a_never_scanned_note_is_due_a_scan():
    note = _note()
    assert research.rescan_due(note)
    assert "the life of this note" in research.describe(note)["rescan_advice"]


def test_a_recently_scanned_note_is_not_due():
    note = _note(scanned_at=timezone.now() - timedelta(days=10))
    assert not research.rescan_due(note)
    assert "rescan_advice" not in research.describe(note)


def test_a_scan_goes_stale_on_its_own_schedule():
    """Passing checks must not be mistaken for a swept field."""
    stale = timezone.now() - timedelta(days=ResearchNote.SCAN_INTERVAL_DAYS + 1)
    note = _note(scanned_at=stale, checks=[])
    described = research.describe(note)
    assert described["rescan_due"]
    assert "cannot tell you whether something better" in described["rescan_advice"]


def test_cross_cutting_notes_come_back_for_every_indicator():
    _note(indicator="", topic="licensing", summary="IHME is out.")
    _note(indicator="malaria_cases", topic="which-source")
    topics = {n.topic for n in research.for_indicator("malaria_cases")}
    assert topics == {"licensing", "which-source"}
    assert {n.topic for n in research.for_indicator("stunting")} == {"licensing"}
