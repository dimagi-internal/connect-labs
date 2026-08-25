"""A cursor the poller cannot read must not be retried forever at tier cadence.

Measured on prod 2026-08-25: 80 ``user_visits`` cursors had been failing
continuously since 2026-07-29 -- the worst at 71,905 consecutive failures --
because ``last_polled_at`` is only written on SUCCESS. A cursor that always
fails therefore keeps a frozen (or null) ``last_polled_at``, is permanently
past due, and is re-polled on every sweep. Together they spent ~130k failed
calls a day against production Connect, 1,697,618 over fourteen days.

Worse, the tail tier reported *healthy* throughout, because ``poll_visit_tail``
called ``record_success`` after every sweep regardless of how many cursors in
it had failed -- which zeroes ``consecutive_failures`` and clears
``last_error`` on the tier. That is the reason a month of continuous failure
produced no signal at all.

These tests pin both halves. (#1277)
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.pulse import ingest, tasks
from connect_labs.pulse.models import _FAILURE_BACKOFF_CAP_SECONDS, TIER_HOT, PulseCursor, PulseIngestHealth


def _cursor(**kwargs) -> PulseCursor:
    return PulseCursor(opportunity_id=1, endpoint=ingest.VISITS_ENDPOINT, tier=TIER_HOT, **kwargs)


class TestBackoff:
    def test_healthy_cursor_keeps_tier_cadence(self):
        """No failures: unchanged behaviour, due one tier interval after the poll."""
        polled = timezone.now() - timedelta(seconds=30)
        cursor = _cursor(last_polled_at=polled)
        assert cursor.failure_backoff_seconds == 0
        assert cursor.due_at == polled + timedelta(seconds=15)
        assert cursor.is_due()

    def test_never_polled_cursor_is_due_at_the_epoch(self):
        """The pre-existing guard against a freshly-evaluated now() -- still true."""
        assert _cursor().due_at.year == 1970

    def test_first_failure_backs_off_a_minute(self):
        failed = timezone.now()
        cursor = _cursor(last_polled_at=failed - timedelta(days=1), consecutive_failures=1, last_failed_at=failed)
        assert cursor.failure_backoff_seconds == 60
        assert cursor.due_at == failed + timedelta(seconds=60)
        assert not cursor.is_due()

    def test_backoff_doubles(self):
        failed = timezone.now()
        delays = []
        for n in (1, 2, 3, 4):
            cursor = _cursor(consecutive_failures=n, last_failed_at=failed)
            delays.append(cursor.failure_backoff_seconds)
        assert delays == [60, 120, 240, 480]

    def test_backoff_is_capped_not_terminal(self):
        """The real worst case must produce a finite, small delay -- and recover.

        2 ** 71905 is a ~21,600-digit integer; computing one per cursor per
        sweep would itself be an outage. The exponent is clamped before the
        shift, and the result capped at six hours so restored access is picked
        up without an operator step.
        """
        failed = timezone.now()
        cursor = _cursor(consecutive_failures=71_905, last_failed_at=failed)
        assert cursor.failure_backoff_seconds == _FAILURE_BACKOFF_CAP_SECONDS
        assert cursor.due_at == failed + timedelta(seconds=6 * 60 * 60)
        assert not cursor.is_due()

    def test_a_stuck_cursor_becomes_due_again_after_the_cap(self):
        failed = timezone.now() - timedelta(hours=7)
        cursor = _cursor(consecutive_failures=71_905, last_failed_at=failed)
        assert cursor.is_due()

    def test_clearing_the_failure_count_restores_tier_cadence(self):
        """Success resets consecutive_failures, so backoff must stop applying."""
        polled = timezone.now() - timedelta(seconds=30)
        cursor = _cursor(last_polled_at=polled, consecutive_failures=0, last_failed_at=timezone.now())
        assert cursor.due_at == polled + timedelta(seconds=15)
        assert cursor.is_due()


@pytest.mark.django_db
class TestTierHealthReflectsTheSweep:
    def test_a_sweep_where_every_cursor_fails_is_not_a_success(self, monkeypatch):
        cursors = [
            PulseCursor.objects.create(opportunity_id=i, endpoint=ingest.VISITS_ENDPOINT, tier=TIER_HOT)
            for i in (1, 2)
        ]
        monkeypatch.setattr(tasks, "get_client", _null_client)
        monkeypatch.setattr(ingest, "due_cursors", lambda limit=40: list(cursors))

        def boom(client, cursor):
            raise RuntimeError("Export API returned 404")

        monkeypatch.setattr(ingest, "tail_visits", boom)

        tasks.poll_visit_tail(sweep_interval=0, deadline=0)

        health = PulseIngestHealth.objects.get(tier=tasks.TIER_TAIL)
        assert health.consecutive_failures == 1
        assert "404" in health.last_error
        assert health.last_success_at is None

    def test_an_empty_sweep_is_healthy(self, monkeypatch):
        """Nothing due is the normal idle state, not a failure."""
        monkeypatch.setattr(tasks, "get_client", _null_client)
        monkeypatch.setattr(ingest, "due_cursors", lambda limit=40: [])

        tasks.poll_visit_tail(sweep_interval=0, deadline=0)

        health = PulseIngestHealth.objects.get(tier=tasks.TIER_TAIL)
        assert health.consecutive_failures == 0
        assert health.last_success_at is not None

    def test_one_success_among_failures_still_counts_as_a_live_tier(self, monkeypatch):
        """The tier is up; the individual cursor's own row carries its failure."""
        good, bad = (
            PulseCursor.objects.create(opportunity_id=i, endpoint=ingest.VISITS_ENDPOINT, tier=TIER_HOT)
            for i in (1, 2)
        )
        monkeypatch.setattr(tasks, "get_client", _null_client)
        monkeypatch.setattr(ingest, "due_cursors", lambda limit=40: [good, bad])

        def half(client, cursor):
            if cursor.opportunity_id == 2:
                raise RuntimeError("nope")
            return {"stored": 1}

        monkeypatch.setattr(ingest, "tail_visits", half)

        tasks.poll_visit_tail(sweep_interval=0, deadline=0)

        assert PulseIngestHealth.objects.get(tier=tasks.TIER_TAIL).consecutive_failures == 0

    def test_a_failing_cursor_records_when_it_failed(self, monkeypatch):
        """Without last_failed_at there is no clock to back off from."""
        cursor = PulseCursor.objects.create(opportunity_id=1, endpoint=ingest.VISITS_ENDPOINT, tier=TIER_HOT)
        monkeypatch.setattr(tasks, "get_client", _null_client)
        monkeypatch.setattr(ingest, "due_cursors", lambda limit=40: [cursor])
        monkeypatch.setattr(ingest, "tail_visits", _raise)

        tasks.poll_visit_tail(sweep_interval=0, deadline=0)

        cursor.refresh_from_db()
        assert cursor.consecutive_failures == 1
        assert cursor.last_failed_at is not None
        assert not cursor.is_due()


def _raise(client, cursor):
    raise RuntimeError("Export API returned 404")


class _NullClient:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _null_client(*args, **kwargs):
    return _NullClient()
