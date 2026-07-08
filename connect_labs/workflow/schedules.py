"""Pure scheduling helpers for the workflow scheduler.

No Django or model imports here — this module is fully unit-testable in
isolation. Times are UTC. Weekday convention is datetime.weekday()
(Monday=0 … Sunday=6); weekend = {5, 6}.
"""

from __future__ import annotations

from datetime import datetime, timedelta

DAILY = "daily"
WEEKDAYS = "weekdays"
WEEKLY = "weekly"
MONTHLY = "monthly"

CADENCE_CHOICES = [
    (DAILY, "Daily"),
    (WEEKDAYS, "Weekdays (Mon–Fri)"),
    (WEEKLY, "Weekly"),
    (MONTHLY, "Monthly"),
]


def compute_next_run(
    cadence: str,
    hour: int,
    day_of_week: int | None,
    day_of_month: int | None,
    from_dt: datetime,
) -> datetime:
    """Return the next fire time strictly after ``from_dt`` (timezone-aware UTC)."""
    base = from_dt.replace(hour=hour, minute=0, second=0, microsecond=0)

    if cadence == DAILY:
        candidate = base
        if candidate <= from_dt:
            candidate += timedelta(days=1)
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

    if cadence == MONTHLY:
        candidate = base.replace(day=day_of_month)
        if candidate <= from_dt:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1)
            else:
                candidate = candidate.replace(month=candidate.month + 1)
        return candidate

    raise ValueError(f"Unknown cadence: {cadence!r}")
