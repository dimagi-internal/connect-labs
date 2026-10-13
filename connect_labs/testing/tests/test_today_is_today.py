"""connect_labs/conftest.py moves a module's import-time TODAY when a run crosses midnight.

Only a TODAY that was read off the clock may move. A fixed calendar date is
already independent of the day the suite runs, and moving it broke the tests
that relied on it being fixed.
"""

from datetime import date

from connect_labs.conftest import shifted_today

COLLECTED = date(2026, 10, 31)
NEXT_DAY = date(2026, 11, 1)


def test_a_today_read_at_collection_follows_the_clock_past_midnight():
    assert shifted_today(COLLECTED, NEXT_DAY, COLLECTED, {COLLECTED}) == NEXT_DAY


def test_a_today_read_off_djangos_clock_follows_it_too():
    # Off UTC, timezone.localdate() at collection can be the day after date.today().
    assert shifted_today(NEXT_DAY, date(2026, 11, 2), COLLECTED, {COLLECTED, NEXT_DAY}) == date(2026, 11, 3)


def test_a_fixed_calendar_date_stays_where_it_is():
    assert shifted_today(date(2026, 9, 28), NEXT_DAY, COLLECTED, {COLLECTED}) is None


def test_nothing_moves_on_the_day_it_was_collected():
    assert shifted_today(COLLECTED, COLLECTED, COLLECTED, {COLLECTED}) is None
