import datetime as dt

from commcare_connect.labs.synthetic.ensure.window import resolve_window


def test_trailing_complete_mondays_plus_current():
    today = dt.date(2026, 6, 13)  # a Saturday
    weeks, current = resolve_window(completed_weeks=4, include_current_week=True, today=today)
    assert current == "2026-06-08"  # this week's Monday
    assert weeks == ["2026-05-11", "2026-05-18", "2026-05-25", "2026-06-01"]


def test_no_current_week():
    weeks, current = resolve_window(completed_weeks=2, include_current_week=False, today=dt.date(2026, 6, 13))
    assert current is None and len(weeks) == 2
