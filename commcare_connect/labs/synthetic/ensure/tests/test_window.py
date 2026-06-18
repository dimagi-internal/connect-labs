import datetime as dt

import pytest

from commcare_connect.labs.synthetic.ensure.window import resolve_window


def test_trailing_complete_mondays_plus_current():
    today = dt.date(2026, 6, 13)  # a Saturday
    weeks, current = resolve_window(completed_weeks=4, include_current_week=True, today=today)
    assert current == "2026-06-08"  # this week's Monday
    assert weeks == ["2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01"]


def test_no_current_week():
    weeks, current = resolve_window(completed_weeks=2, include_current_week=False, today=dt.date(2026, 6, 13))
    assert current is None and len(weeks) == 2


def test_fixed_start_monday_ignores_today():
    """A pinned start_monday yields the SAME weeks regardless of today (no slide)."""
    kwargs = dict(completed_weeks=4, include_current_week=True, start_monday=dt.date(2026, 5, 4))
    weeks_a, current_a = resolve_window(today=dt.date(2026, 6, 13), **kwargs)
    weeks_b, current_b = resolve_window(today=dt.date(2026, 9, 1), **kwargs)
    assert weeks_a == weeks_b == ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25"]
    # current week is the Monday right after the completed window.
    assert current_a == current_b == "2026-06-01"


def test_fixed_start_monday_without_current():
    weeks, current = resolve_window(completed_weeks=4, include_current_week=False, start_monday=dt.date(2026, 5, 4))
    assert weeks == ["2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25"]
    assert current is None


def test_fixed_start_monday_must_be_a_monday():
    with pytest.raises(ValueError, match="must be a Monday"):
        resolve_window(completed_weeks=4, include_current_week=True, start_monday=dt.date(2026, 5, 5))  # Tuesday
