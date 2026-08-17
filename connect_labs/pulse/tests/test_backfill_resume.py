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

    def test_an_empty_response_is_never_treated_as_complete(self, cursor, opp):
        """The regression that cost a run.

        A request returning nothing is ambiguous, and `backfill_complete` is
        sticky -- marking it stopped 409 opportunities being walked ever again,
        including ones holding 100k+ unfetched visits. Re-checking an exhausted
        opportunity costs one empty request; guessing wrong is unrecoverable.
        """
        tasks._backfill_one(_Client([]), cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))

        cursor.refresh_from_db()
        assert not cursor.backfill_complete

    def test_only_empty_pages_is_not_completion_either(self, cursor, opp):
        tasks._backfill_one(_Client([[], []]), cursor, datetime(2020, 1, 1, tzinfo=dt_timezone.utc))

        cursor.refresh_from_db()
        assert not cursor.backfill_complete

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


class TestCompletionDepth:
    """A shallow completion must not cap a deeper request.

    This is the defect that cost the first full-history run: `--days 37` marked
    the largest opportunities complete after one page, and the later all-history
    pass skipped them entirely. They held almost all the volume -- opp 411 sat
    at 1,000 stored against 101,458 lifetime.
    """

    def _cutoff(self, days):
        from django.utils import timezone

        return timezone.now() - timezone.timedelta(days=days)

    def test_stopping_at_a_cutoff_records_that_depth(self, cursor, opp):
        client = _Client([[_visit(30, 5)]])
        cutoff = datetime(2026, 7, 10, tzinfo=dt_timezone.utc)

        tasks._backfill_one(client, cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_complete
        assert cursor.backfill_complete_to == cutoff

    def test_exhaustion_records_completion_to_the_epoch(self, cursor, opp):
        """Nothing older exists, so no future depth should re-walk it.

        The cutoff is a fixed date safely before the fixture's July 2026
        visits: expressing it as "N days ago" made the test rot -- the day
        "now - 37d" crossed the visit date, the walk hit the cutoff instead
        of exhausting, and the assertion failed on pure calendar drift."""
        cutoff = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
        tasks._backfill_one(_Client([[_visit(30, 10)]]), cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_complete_to == tasks._EPOCH

    def test_a_deeper_request_reopens_a_shallow_completion(self, cursor, opp, monkeypatch):
        cursor.backfill_complete = True
        cursor.backfill_complete_to = self._cutoff(37)
        cursor.save()

        picked = self._selected(monkeypatch, days=3650)
        assert cursor.opportunity_id in picked, "a deeper pass must re-walk a shallow completion"

    def test_a_shallower_request_skips_a_deeper_completion(self, cursor, opp, monkeypatch):
        cursor.backfill_complete = True
        cursor.backfill_complete_to = self._cutoff(3650)
        cursor.save()

        assert cursor.opportunity_id not in self._selected(monkeypatch, days=37)

    def test_unknown_depth_is_always_rechecked(self, cursor, opp, monkeypatch):
        """Rows predating the column must not be trusted as fully complete."""
        cursor.backfill_complete = True
        cursor.backfill_complete_to = None
        cursor.save()

        assert cursor.opportunity_id in self._selected(monkeypatch, days=3650)

    def _selected(self, monkeypatch, *, days):
        """Which opportunities a backfill pass would actually walk."""
        seen = []

        class _NullClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def paginate(self, endpoint, params=None, partial_ok=False):
                seen.append(int(endpoint.split("/")[3]))
                return iter([])

        monkeypatch.setattr(tasks, "get_client", lambda **kw: _NullClient())
        monkeypatch.setattr(tasks.ingest, "rebuild_rollups", lambda **kw: 0)
        tasks.backfill_visits(days=days, page_pause=0)
        return seen


class TestConvergence:
    """Depth-unknown re-checks must resolve, or every run repeats them forever.

    Observed in production the day depth tracking shipped: ~420 opportunities
    marked complete by the pre-depth code returned an empty re-check each pass,
    could never record a depth (completion requires seeing a page), and so were
    re-requested on every pass -- while the loop guard counted each re-check as
    work done and kept looping. ~530 requests per pass, indefinitely.
    """

    def _cutoff(self, days):
        from django.utils import timezone

        return timezone.now() - timezone.timedelta(days=days)

    def test_an_empty_recheck_records_the_depth_it_verified(self, cursor, opp):
        cursor.backfill_complete = True
        cursor.backfill_complete_to = None
        cursor.backfill_oldest_id = 29
        cursor.save()
        cutoff = self._cutoff(3650)

        tasks._backfill_one(_Client([]), cursor, cutoff)

        cursor.refresh_from_db()
        assert cursor.backfill_complete
        assert cursor.backfill_complete_to == cutoff, "the re-check verified this depth and must record it"

    def test_an_empty_response_still_never_completes_a_fresh_cursor(self, cursor, opp):
        """The empty-response hazard is unchanged for cursors not yet complete."""
        tasks._backfill_one(_Client([]), cursor, self._cutoff(3650))

        cursor.refresh_from_db()
        assert not cursor.backfill_complete
        assert cursor.backfill_complete_to is None

    def test_a_recheck_that_finds_data_walks_to_exhaustion(self, cursor, opp):
        """A wrongly-capped opportunity (the opp-411 case) still gets re-walked."""
        cursor.backfill_complete = True
        cursor.backfill_complete_to = None
        cursor.save()

        tasks._backfill_one(_Client([[_visit(30, 10)]]), cursor, self._cutoff(3650))

        cursor.refresh_from_db()
        assert cursor.backfill_complete_to == tasks._EPOCH

    def test_rechecks_converge_instead_of_spinning(self, cursor, opp, monkeypatch):
        """Pass one resolves the depth; pass two must not re-request the opp."""
        cursor.backfill_complete = True
        cursor.backfill_complete_to = None
        cursor.save()

        first = self._pass(monkeypatch)
        second = self._pass(monkeypatch)

        assert first["seen"] == [cursor.opportunity_id]
        assert first["result"]["opportunities_satisfied"] == 1
        assert second["seen"] == [], "a resolved re-check must not be requested again"

    def test_a_pass_that_moves_nothing_reports_no_progress(self, cursor, opp, monkeypatch):
        """What the loop guard watches: re-checking is not progress."""
        cursor.backfill_complete = True
        cursor.backfill_complete_to = None
        cursor.save()

        self._pass(monkeypatch)
        cursor.refresh_from_db()
        cursor.backfill_complete_to = None
        cursor.save()  # force a second depth-unknown re-check of the same opp

        # The stamp is progress; a pass where nothing changes state would be 0.
        assert self._pass(monkeypatch)["result"]["opportunities_satisfied"] == 1

    def _pass(self, monkeypatch):
        seen = []

        class _NullClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def paginate(self, endpoint, params=None, partial_ok=False):
                seen.append(int(endpoint.split("/")[3]))
                return iter([])

        monkeypatch.setattr(tasks, "get_client", lambda **kw: _NullClient())
        monkeypatch.setattr(tasks.ingest, "rebuild_rollups", lambda **kw: 0)
        result = tasks.backfill_visits(days=3650, page_pause=0)
        return {"seen": seen, "result": result}


class TestNightlyCatchup:
    """The bounded nightly catch-up: new access arrives on its own.

    Selection is already self-healing (anything not provably complete is
    re-tried), so putting a bounded slice on beat is what turns "run a
    command after every access grant" into "wait a night".
    """

    def test_the_beat_entry_is_bounded(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["pulse-backfill-catchup"]
        assert entry["task"] == "connect_labs.pulse.tasks.backfill_visits"
        assert entry["kwargs"]["max_seconds"] <= 3600, "the catch-up must never become the full walk"
        assert entry["kwargs"]["days"] >= 3650, "a shallow catch-up would cap real history"

    def test_a_fruitless_pass_skips_the_rollup_rebuild(self, db, monkeypatch):
        """The common night: nothing new, so the 1.6M-row rollup rebuild must
        not run. A pass that stored rows still rebuilds."""
        calls = []
        monkeypatch.setattr(tasks.ingest, "rebuild_rollups", lambda **kw: calls.append(kw))

        class _NullClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def paginate(self, endpoint, params=None, partial_ok=False):
                return iter([])

        monkeypatch.setattr(tasks, "get_client", lambda **kw: _NullClient())
        result = tasks.backfill_visits(days=3650, page_pause=0)
        assert result["stored"] == 0
        assert calls == [], "nothing stored, nothing to roll up"
