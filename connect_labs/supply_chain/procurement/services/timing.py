"""Counting back from the day the goods are needed to the day the tender must start.

Sophie's RUTF feedback ("A needed-by date on the tender"): with a needed-by date the
system can say when a tender has to start, and warn when it is late. The count is

    start by  =  needed by  -  delivery time  -  reply time

Delivery time is the longest lead time any live quote states, else the tender's own
expectation (`lead_time_days_expected`), else unknown -- and then no start date is
invented. Reply time is the window suppliers are given: from opening to the reply-by
date once the tender is open, else a fortnight.

Nothing here is stored: the date moves as quotes arrive.
"""

from __future__ import annotations

from datetime import date, timedelta

DEFAULT_REPLY_DAYS = 14


def lead_days(tender, quote_lead_times=()) -> int | None:
    """Days suppliers need to deliver: the longest lead time a live quote states, else the tender's expectation."""
    stated = [d for d in quote_lead_times if d is not None]
    if stated:
        return max(stated)
    return getattr(tender, "lead_time_days_expected", None)


def reply_days(tender) -> int:
    """Days suppliers are given to answer: opened to reply-by date when both are known, else a fortnight."""
    opened = tender.opened_at.date() if getattr(tender, "opened_at", None) else None
    deadline = tender.response_deadline
    if opened and deadline and deadline >= opened:
        return (deadline - opened).days
    return DEFAULT_REPLY_DAYS


def start_by(needed_by: date | None, lead: int | None, reply: int) -> date | None:
    if needed_by is None or lead is None:
        return None
    return needed_by - timedelta(days=lead + reply)


def timing(tender, quote_lead_times=(), today: date | None = None) -> dict:
    """{needed_by, start_by, lead_days, reply_days, late, note} for a tender; empty values when no needed-by date."""
    today = today or date.today()
    needed = tender.needed_by
    if needed is None:
        return {"needed_by": None, "start_by": None, "lead_days": None, "reply_days": None, "late": False, "note": ""}
    lead = lead_days(tender, quote_lead_times)
    reply = reply_days(tender)
    start = start_by(needed, lead, reply)
    if start is None:
        note = "Say how long suppliers need to deliver and this counts back to the day the tender must start."
        late = False
    else:
        not_started = tender.status == "draft"
        late = start < today and not_started
        day = start.strftime("%-d %b %Y")
        if late:
            note = (
                f"Late to start: to have it by {needed.strftime('%-d %b %Y')} the tender should have started by {day}."
            )
        elif not_started:
            note = f"Start by {day} to have it by {needed.strftime('%-d %b %Y')}."
        else:
            note = f"Needed by {needed.strftime('%-d %b %Y')}."
    return {
        "needed_by": needed,
        "start_by": start,
        "lead_days": lead,
        "reply_days": reply,
        "late": late,
        "note": note,
    }
