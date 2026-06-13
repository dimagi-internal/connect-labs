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
) -> tuple[list[str], str | None]:
    """Return ``(weeks, current)`` — ISO Mondays computed from today.

    ``weeks`` is the trailing ``completed_weeks`` COMPLETE weeks (the PAR
    window): the in-order list of ISO-formatted Mondays ending the Sunday
    before the current week. ``current`` is this week's Monday — the
    in-progress run, deliberately OUTSIDE the PAR window — when
    ``include_current_week`` is true, else ``None``.

    Anchoring the window to *now* keeps seeded and live timestamps coherent
    forever, instead of telling a hardcoded calendar story.
    """
    if today is None:
        today = dt.date.today()
    current_monday = today - dt.timedelta(days=today.weekday())
    weeks = [(current_monday - dt.timedelta(weeks=completed_weeks - i)).isoformat() for i in range(completed_weeks)]
    current = current_monday.isoformat() if include_current_week else None
    return weeks, current
