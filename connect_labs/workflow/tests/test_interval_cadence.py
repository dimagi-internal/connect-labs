"""INTERVAL cadence: fires on a fixed daily grid anchored at ``hour``.

The grid is derived from the clock alone -- compute_next_run keeps no schedule
history -- so these tests pin the two properties that depend on that: the
spacing never varies (including across midnight), and a non-divisor interval is
rejected rather than silently leaving a short gap at the day boundary.
"""

from datetime import datetime, timedelta, timezone

import pytest

from connect_labs.workflow.schedules import INTERVAL, INTERVAL_HOURS_CHOICES, compute_next_run

# Wednesday 2026-07-08 09:30 UTC, matching test_compute_next_run.py's reference.
NOW = datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)


def _next(now, hour=0, interval=6):
    return compute_next_run(INTERVAL, hour, None, None, now, interval_hours=interval)


def test_six_hourly_picks_the_next_grid_slot():
    # 00/06/12/18 grid; 09:30 -> 12:00 the same day
    assert _next(NOW) == datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)


def test_exactly_on_a_slot_advances_rather_than_returning_now():
    # Strictly after: landing exactly on 12:00 must return 18:00, not 12:00.
    # The ticker claims a row by writing this value, so returning `now` would
    # re-fire the same window on the next beat.
    on_slot = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    assert _next(on_slot) == datetime(2026, 7, 8, 18, 0, tzinfo=timezone.utc)


def test_last_slot_of_the_day_rolls_into_tomorrow():
    late = datetime(2026, 7, 8, 18, 1, tzinfo=timezone.utc)
    assert _next(late) == datetime(2026, 7, 9, 0, 0, tzinfo=timezone.utc)


def test_anchor_hour_offsets_the_grid():
    # hour=2, 3-hourly -> 02/05/08/11/14/17/20/23
    assert _next(NOW, hour=2, interval=3) == datetime(2026, 7, 8, 11, 0, tzinfo=timezone.utc)


def test_anchor_hour_is_taken_modulo_the_interval():
    # hour=14 with a 6-hour interval is the same grid as hour=2 (14 % 6 == 2).
    assert _next(NOW, hour=14, interval=6) == _next(NOW, hour=2, interval=6)


@pytest.mark.parametrize("interval", INTERVAL_HOURS_CHOICES)
def test_spacing_is_exact_across_a_full_day_including_midnight(interval):
    now = datetime(2026, 7, 8, 0, 0, tzinfo=timezone.utc)
    gaps = set()
    previous = None
    # Enough steps to cross at least one midnight for every supported interval.
    for _ in range((24 // interval) + 2):
        nxt = _next(now, hour=0, interval=interval)
        if previous is not None:
            gaps.add(nxt - previous)
        previous, now = nxt, nxt
    assert gaps == {timedelta(hours=interval)}


@pytest.mark.parametrize("bad", [5, 7, 9, 13, 24, 0, -6, None])
def test_non_divisor_intervals_are_rejected(bad):
    with pytest.raises(ValueError, match="interval_hours must be one of"):
        _next(NOW, interval=bad)


def test_every_supported_interval_divides_a_day():
    # Guards the choice list itself: a value that does not divide 24 would make
    # the grid restart mid-cycle at midnight.
    assert all(24 % hours == 0 for hours in INTERVAL_HOURS_CHOICES)
