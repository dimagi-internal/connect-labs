from datetime import datetime, timezone

from connect_labs.workflow.schedules import (
    DAILY,
    MONTHLY,
    WEEKDAYS,
    WEEKLY,
    compute_next_run,
)

# Wednesday 2026-07-08 09:30 UTC as the reference "now".
NOW = datetime(2026, 7, 8, 9, 30, tzinfo=timezone.utc)


def test_daily_rolls_to_tomorrow_when_hour_passed():
    # 06:00 today already passed at 09:30 -> tomorrow 06:00
    assert compute_next_run(DAILY, 6, None, None, NOW) == datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)


def test_daily_today_when_hour_still_ahead():
    assert compute_next_run(DAILY, 18, None, None, NOW) == datetime(2026, 7, 8, 18, 0, tzinfo=timezone.utc)


def test_weekdays_friday_evening_rolls_to_monday():
    friday_pm = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)  # Fri
    # next 06:00 weekday after Fri 20:00 -> Mon 2026-07-13 06:00
    assert compute_next_run(WEEKDAYS, 6, None, None, friday_pm) == datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)


def test_weekdays_midweek_next_day():
    # Wed 09:30, 06:00 passed -> Thu 06:00 (a weekday)
    assert compute_next_run(WEEKDAYS, 6, None, None, NOW) == datetime(2026, 7, 9, 6, 0, tzinfo=timezone.utc)


def test_weekly_same_week_future_day():
    # Wed(2) now; target Friday(4) 08:00 -> this Fri
    assert compute_next_run(WEEKLY, 8, 4, None, NOW) == datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)


def test_weekly_target_day_already_passed_rolls_a_week():
    # target Monday(0) 06:00; from Wed -> next Monday 2026-07-13
    assert compute_next_run(WEEKLY, 6, 0, None, NOW) == datetime(2026, 7, 13, 6, 0, tzinfo=timezone.utc)


def test_monthly_this_month_when_day_ahead():
    # day 20 at 06:00, from Jul 8 -> Jul 20
    assert compute_next_run(MONTHLY, 6, None, 20, NOW) == datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)


def test_monthly_rolls_to_next_month_when_day_passed():
    # day 1 at 06:00, from Jul 8 -> Aug 1
    assert compute_next_run(MONTHLY, 6, None, 1, NOW) == datetime(2026, 8, 1, 6, 0, tzinfo=timezone.utc)


def test_monthly_december_wraps_year():
    dec = datetime(2026, 12, 15, 9, 0, tzinfo=timezone.utc)
    assert compute_next_run(MONTHLY, 6, None, 5, dec) == datetime(2027, 1, 5, 6, 0, tzinfo=timezone.utc)
