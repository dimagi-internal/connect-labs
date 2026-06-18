"""Dynamic week-window resolver for synthetic-data generation.

Computes the week window an ``ensure_synthetic_data`` run targets, anchored
to *today* so seeded and live-created records stay date-coherent forever
(rather than telling a hardcoded calendar story while live records stamp the
current date).

The window is two parts:

- **trailing N complete Mondays** — the PAR window. These are the ``N`` weeks
  that have already completed; the window ends the Sunday before the current
  week (i.e. at the present).
- **the current week's Monday** — the in-progress run, deliberately OUTSIDE
  the PAR window so the live demo week never renders as a "NO RUN" hole in
  the grid.

This is a pure date function: no Django, no backend, no I/O. Ported verbatim
from ``compute_week_window`` in
``scripts/walkthroughs/program-admin-report/regenerate.py``.
"""

from __future__ import annotations

import datetime as dt


def resolve_window(
    completed_weeks: int,
    include_current_week: bool,
    today: dt.date | None = None,
    start_monday: dt.date | None = None,
) -> tuple[list[str], str | None]:
    """Return ``(weeks, current)`` — ISO Mondays for the env's window.

    Two modes:

    - **Pinned** (``start_monday`` given): the window is the ``completed_weeks``
      consecutive Mondays starting at ``start_monday``, and ``current`` (when
      ``include_current_week``) is the Monday immediately after them. The result
      is independent of *today*, so a re-seed days later produces the SAME weeks —
      the env stays idempotent instead of sliding out from under already-seeded
      runs/flags/audits/tasks. Use this for demos pinned to a fixed calendar
      story (e.g. May 2026).
    - **Trailing** (default): ``weeks`` is the trailing ``completed_weeks``
      COMPLETE weeks ending the Sunday before the current week, and ``current``
      is this week's Monday. Anchoring to *now* keeps seeded and live timestamps
      coherent for an always-current story — but the window slides as time passes.

    ``start_monday`` must be a Monday (``weekday() == 0``); otherwise the run/week
    bookkeeping (which keys runs by their Monday) silently misaligns.
    """
    if start_monday is not None:
        if start_monday.weekday() != 0:
            raise ValueError(
                f"start_monday must be a Monday, got {start_monday.isoformat()} ({start_monday.strftime('%A')})"
            )
        weeks = [(start_monday + dt.timedelta(weeks=i)).isoformat() for i in range(completed_weeks)]
        current = (start_monday + dt.timedelta(weeks=completed_weeks)).isoformat() if include_current_week else None
        return weeks, current

    if today is None:
        today = dt.date.today()
    current_monday = today - dt.timedelta(days=today.weekday())
    weeks = [(current_monday - dt.timedelta(weeks=completed_weeks - i)).isoformat() for i in range(completed_weeks)]
    current = current_monday.isoformat() if include_current_week else None
    return weeks, current
