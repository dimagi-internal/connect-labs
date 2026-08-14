"""The history walk must survive being interrupted.

The property under test is not "it pulls rows" but "an interrupted pull does not
lose what it already pulled". That was the real defect: the cursor was committed
only after an opportunity finished, so a task killed mid-opportunity restarted
from the same place -- which on the largest programme means never finishing.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from connect_labs.pulse import ingest, tasks
from connect_labs.pulse.models import PulseCursor, PulseEvent, PulseOpportunity


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(opportunity_id=765, name="Programme", org_slug="pride", country="NG")


@pytest.fixture
def cursor(db, opp):
    return PulseCursor.objects.create(
        opportunity_id=opp.opportunity_id, endpoint=ingest.VISITS_ENDPOINT, backfill_complete=False
    )


def _visit(vid, day):
    ts = datetime(2026, 7, day, 12, tzinfo=dt_timezone.utc).isoformat()
    return {
        "id": vid,
        "opportunity_id": 765,
        "visit_date": ts,
        "date_created": ts,
        "status": "approved",
        "username": "hash1",
        "location": "11.03 7.63 0 5",
    }


class _Client:
    """Pages backwards, and can be told to blow up partway through."""

    def __init__(self, pages, fail_after=None):
        self.pages = pages
        self.fail_after = fail_after
        self.served = 0

    def paginate(self, endpoint, params=None, partial_ok=False):
        for page in self.pages:
            if self.fail_after is not None and self.served >= self.fail_after:
                raise RuntimeError("connection reset")
            self.served += 1
            yield page


class TestResume:
    def test_cursor_advances_after_every_page(self, cursor, opp):
        client = _Client([[_visit(30, 10), _visit(29, 9)], [_visit(28, 8), _visit(27, 7)]])
        cutoff = datetime(2020, 1, 1, tzinfo=dt_timezone.utc)

        tasks._backfill_one(client, cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_oldest_id == 27

    def test_progress_survives_a_mid_opportunity_failure(self, cursor, opp):
        """The regression: a crash on page two must not discard page one."""
        client = _Client([[_visit(30, 10), _visit(29, 9)], [_visit(28, 8)]], fail_after=1)
        cutoff = datetime(2020, 1, 1, tzinfo=dt_timezone.utc)

        with pytest.raises(RuntimeError):
            tasks._backfill_one(client, cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_oldest_id == 29, "page one's progress was thrown away"
        assert not cursor.backfill_complete
        assert PulseEvent.objects.count() == 2

    def test_resumes_from_the_stored_position(self, cursor, opp):
        cursor.backfill_oldest_id = 29
        cursor.save()
        seen = {}

        class _Recording(_Client):
            def paginate(self, endpoint, params=None, partial_ok=False):
                seen.update(params or {})
                return iter([[_visit(28, 8)]])

        tasks._backfill_one(_Recording([]), cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        assert seen["last_id"] == 29

    def test_exhausted_stream_counts_as_complete(self, cursor, opp):
        """Reaching the true start of history must not be re-walked forever."""
        client = _Client([[_visit(30, 10)]])
        tasks._backfill_one(client, cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))

        cursor.refresh_from_db()
        assert cursor.backfill_complete

    def test_cutoff_still_stops_the_walk(self, cursor, opp):
        # Page one is entirely older than the cutoff, so the walk has gone as
        # deep as it was asked to and should not request page two.
        client = _Client([[_visit(30, 5)], [_visit(29, 4)]])
        cutoff = datetime(2026, 7, 10, tzinfo=dt_timezone.utc)

        tasks._backfill_one(client, cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_complete
        assert client.served == 1, "should have stopped at the first page past the cutoff"

    def test_pages_newer_than_the_cutoff_keep_going(self, cursor, opp):
        client = _Client([[_visit(30, 20)], [_visit(29, 5)]])

        tasks._backfill_one(client, cursor, datetime(2026, 7, 10, tzinfo=dt_timezone.utc))

        assert client.served == 2

    def test_missing_timestamps_do_not_disable_the_cutoff(self, cursor, opp):
        """`min()` over raw values returned '' and silently ran past the cutoff."""
        page = [_visit(30, 5), {"id": 31, "opportunity_id": 765, "status": "approved"}]
        client = _Client([page, [_visit(29, 4)]])

        tasks._backfill_one(client, cursor, datetime(2026, 7, 10, tzinfo=dt_timezone.utc))

        assert client.served == 1

    def test_max_pages_bounds_a_pass_without_completing_it(self, cursor, opp):
        client = _Client([[_visit(30, 10)], [_visit(29, 9)], [_visit(28, 8)]])

        tasks._backfill_one(client, cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc), max_pages=2)

        cursor.refresh_from_db()
        assert client.served == 2
        assert not cursor.backfill_complete, "a bounded slice must stay resumable"


class TestPacing:
    def test_pauses_between_pages(self, cursor, opp, monkeypatch):
        slept = []
        monkeypatch.setattr(tasks.time, "sleep", lambda s: slept.append(s))
        client = _Client([[_visit(30, 10)], [_visit(29, 9)]])

        tasks._backfill_one(client, cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc), page_pause=0.25)

        assert slept and all(s == 0.25 for s in slept)

    def test_no_pause_means_no_sleeping(self, cursor, opp, monkeypatch):
        slept = []
        monkeypatch.setattr(tasks.time, "sleep", lambda s: slept.append(s))
        client = _Client([[_visit(30, 10)]])

        tasks._backfill_one(client, cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc), page_pause=0)

        assert slept == []
