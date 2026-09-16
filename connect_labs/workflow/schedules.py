"""Pure scheduling helpers for the workflow scheduler.

No Django or model imports here — this module is fully unit-testable in
isolation. Times are UTC. Weekday convention is datetime.weekday()
(Monday=0 … Sunday=6); weekend = {5, 6}.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

DAILY = "daily"
INTERVAL = "interval"
WEEKDAYS = "weekdays"
WEEKLY = "weekly"
BIWEEKLY = "biweekly"
MONTHLY = "monthly"

# Hours-per-day divisors only. An interval that does not divide 24 cannot be
# anchored to a daily grid without leaving a short gap at the day boundary, and
# compute_next_run is a pure function of its arguments -- it is handed no
# schedule history, so there is nowhere to carry the running offset that would
# smooth that seam over. Restricting to divisors keeps every fire time
# derivable from the clock alone, which is the same property the biweekly
# epoch below exists to preserve.
INTERVAL_HOURS_CHOICES = (1, 2, 3, 4, 6, 8, 12)

CADENCE_CHOICES = [
    (DAILY, "Daily"),
    (INTERVAL, "Every N hours"),
    (WEEKDAYS, "Weekdays (Mon–Fri)"),
    (WEEKLY, "Weekly"),
    (BIWEEKLY, "Every 2 weeks"),
    (MONTHLY, "Monthly"),
]

# Which fortnight a biweekly schedule lands on, fixed for all schedules.
#
# compute_next_run is a PURE function of (cadence, hour, day_of_week, day_of_month,
# from_dt) -- it is handed no schedule history and no anchor date -- so "every 14 days"
# has to be derivable from the date alone. Counting whole weeks from one fixed Monday
# does that: the parity of that count alternates every week forever, so firing only on
# even counts is exactly a 14-day period with no state to store and nothing to drift.
#
# ISO week numbers were the obvious alternative and are subtly wrong for this: a 53-week
# ISO year puts two odd-numbered weeks back to back, which silently turns one gap into 7
# days every few years. A plain day count has no such seam.
#
# The epoch is arbitrary -- it only decides WHICH fortnight, never the spacing -- but it
# must be a Monday so the count increments on the same boundary as weekday() rolls over.
_BIWEEKLY_EPOCH = date(2026, 1, 5)  # a Monday


def _is_active_fortnight(when: datetime) -> bool:
    """True when ``when`` falls in a firing week for a biweekly schedule.

    Floor division is deliberate: it keeps the parity alternating for dates BEFORE the
    epoch too (Python floors toward negative infinity), so a schedule is not thrown off
    by a from_dt earlier than the epoch.
    """
    return ((when.date() - _BIWEEKLY_EPOCH).days // 7) % 2 == 0


def compute_next_run(
    cadence: str,
    hour: int,
    day_of_week: int | None,
    day_of_month: int | None,
    from_dt: datetime,
    interval_hours: int | None = None,
) -> datetime:
    """Return the next fire time strictly after ``from_dt`` (timezone-aware UTC).

    ``interval_hours`` is read only for the INTERVAL cadence, where it is the
    gap between fires and must divide 24 (see INTERVAL_HOURS_CHOICES). It is
    keyword-optional so the existing call sites for the calendar cadences are
    unaffected.
    """
    base = from_dt.replace(hour=hour, minute=0, second=0, microsecond=0)

    if cadence == DAILY:
        candidate = base
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        return candidate

    if cadence == INTERVAL:
        # Fire on a fixed daily grid anchored at ``hour``: with hour=0 and
        # interval_hours=6 that is 00:00, 06:00, 12:00, 18:00 UTC, every day.
        # Derived from from_dt alone, so a missed tick never shifts the grid.
        if interval_hours not in INTERVAL_HOURS_CHOICES:
            raise ValueError(f"interval_hours must be one of {INTERVAL_HOURS_CHOICES}, got {interval_hours!r}")
        day_start = from_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        candidate = day_start + timedelta(hours=hour % interval_hours)
        while candidate <= from_dt:
            candidate += timedelta(hours=interval_hours)
        return candidate

    if cadence == WEEKDAYS:
        candidate = base
        if candidate <= from_dt:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # skip Sat(5)/Sun(6)
            candidate += timedelta(days=1)
        return candidate

    if cadence == WEEKLY:
        candidate = base + timedelta(days=(day_of_week - base.weekday()) % 7)
        if candidate <= from_dt:
            candidate += timedelta(days=7)
        return candidate

    if cadence == BIWEEKLY:
        # The next matching weekday, exactly as WEEKLY finds it...
        candidate = base + timedelta(days=(day_of_week - base.weekday()) % 7)
        if candidate <= from_dt:
            candidate += timedelta(days=7)
        # ...then skipped forward to the next firing fortnight. At most one skip is ever
        # needed, because the week parity alternates on every step.
        if not _is_active_fortnight(candidate):
            candidate += timedelta(days=7)
        return candidate

    if cadence == MONTHLY:
        candidate = base.replace(day=day_of_month)
        if candidate <= from_dt:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1)
            else:
                candidate = candidate.replace(month=candidate.month + 1)
        return candidate

    raise ValueError(f"Unknown cadence: {cadence!r}")
