"""Whether a worker's phone is online: judged from how promptly their work arrives.

The question is about people, not forms. A worker with signal submits each
visit as they finish it; a worker without it keeps visits on the phone and
sends them together when signal returns. Out of airtime or a bad week can make
an online worker look offline for a while, so the answer is a share of the
time, not a yes or no.

**The test: was a visit sent before the worker started the next one?** For
consecutive visits by the same worker, visit *i* is *prompt* when it reached
Connect (``sync_ts``) before visit *i+1* was opened (``field_ts``). This never
needs the form's duration, which matters because Pulse does not have it:
``field_ts`` is Connect's ``visit_date``, taken from the form's ``timeStart``,
and ``timeEnd`` lives only in the form JSON Pulse deliberately never stores. A
latency threshold ("arrived within N minutes") would have to guess how long the
form took; "before the next one" does not.

It replaces a test that asked whether a submission arrived within 24 hours.
Nearly everything does -- about 95% of workers passed it every week -- so it
could not tell a connected worker from one who syncs once each evening.

Not every pair is evidence:

* **Started under 5 minutes apart** -- too little time for even an online phone
  to finish, send, and for HQ to forward to Connect (which alone takes a
  minute or two). Counting these would mark online workers offline.
* **Started more than 3 hours apart** -- usually the end of a working day.
  Arriving before the next morning says nothing about "very quickly".
* **Arrived before it was started** -- the phone's clock is wrong, and so is
  every comparison made against it.

What is left splits into two clear groups when measured (a 42k-visit sample of
production, 2026-09-28): worker-days that sent nearly every visit before the
next, and worker-days that sent nearly none that way, with a smaller mixed
group between. That split is what the three classes below describe.

**Nothing before 2025-01-14 can be judged.** Connect added
``UserVisit.date_created`` in its migration 0066 and filled every existing row
with the visit's own time, so older visits read as arriving the instant they
were started. The same cutover (and the same date) is why ``network_api``
refuses to date works before it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.core.exceptions import EmptyResultSet
from django.db import connection

# Visits before this carry no real arrival time; see the module docstring.
RELIABLE_FROM = dt.datetime(2025, 1, 15, tzinfo=dt.timezone.utc)

PAIR_MIN_GAP = dt.timedelta(minutes=5)
PAIR_MAX_GAP = dt.timedelta(hours=3)

# A worker is ONLINE when at least this share of their pairs were prompt ...
ONLINE_SHARE = 0.8
# ... and online SOMETIMES when at least this share were. Below it, a stray
# prompt pair is as likely a clock quirk as a moment of signal.
SOMETIMES_SHARE = 0.2

# Fewer pairs than this and the share is noise, so the worker is not judged.
MIN_PAIRS_WEEK = 3
MIN_PAIRS_WORKER = 10

ONLINE = "online"
SOMETIMES = "sometimes"
OFFLINE = "offline"
CLASSES = (ONLINE, SOMETIMES, OFFLINE)

HISTOGRAM_BINS = 10


def classify(pairs: int, prompt: int, min_pairs: int) -> str | None:
    """Which class a worker (or worker-week) falls in; None when there is too little to say."""
    if pairs < min_pairs:
        return None
    share = prompt / pairs
    if share >= ONLINE_SHARE:
        return ONLINE
    if share >= SOMETIMES_SHARE:
        return SOMETIMES
    return OFFLINE


@dataclass
class WorkerWeek:
    week: dt.datetime
    worker_hash: str
    pairs: int
    prompt: int


def worker_weeks(events) -> list[WorkerWeek]:
    """Usable pairs and prompt pairs per worker per ISO week, for an events queryset.

    One query. The queryset supplies the scope (program, partner, window, test
    exclusions) exactly as every other card gets it; its SQL is wrapped rather
    than re-expressed, so this can never judge a different set of visits than
    the card beside it counts. The window function has to run over the scoped
    rows, not the table: the "next visit" is the next one IN SCOPE.

    Bucketed on the earlier visit's ``field_ts``, like every other weekly series.
    """
    inner = (
        events.filter(field_ts__gte=RELIABLE_FROM).exclude(worker_hash="").values("worker_hash", "field_ts", "sync_ts")
    )
    try:
        sql, params = inner.query.sql_with_params()
    except EmptyResultSet:
        # A scope that can match nothing (``.none()``, an empty ``__in``) has
        # no SQL to wrap, and nothing to judge.
        return []
    query = f"""
        WITH ev AS ({sql}),
        seq AS (
            SELECT worker_hash, field_ts, sync_ts,
                   LEAD(field_ts) OVER (PARTITION BY worker_hash ORDER BY field_ts) AS next_ts
            FROM ev
        )
        SELECT date_trunc('week', field_ts AT TIME ZONE 'UTC') AS week,
               worker_hash,
               COUNT(*) AS pairs,
               COUNT(*) FILTER (WHERE sync_ts <= next_ts) AS prompt
        FROM seq
        WHERE next_ts IS NOT NULL
          AND next_ts - field_ts >= %s
          AND next_ts - field_ts <= %s
          AND sync_ts >= field_ts
        GROUP BY 1, 2
    """
    with connection.cursor() as cur:
        cur.execute(query, [*params, PAIR_MIN_GAP, PAIR_MAX_GAP])
        rows = cur.fetchall()
    return [WorkerWeek(week.replace(tzinfo=dt.timezone.utc), h, pairs, prompt) for week, h, pairs, prompt in rows]


def weekly(rows: list[WorkerWeek]) -> dict[dt.datetime, dict]:
    """Per week: how many workers were judged, and how many fell in each class."""
    out: dict[dt.datetime, dict] = {}
    for r in rows:
        cls = classify(r.pairs, r.prompt, MIN_PAIRS_WEEK)
        if cls is None:
            continue
        agg = out.setdefault(r.week, {"workers": 0, **{c: 0 for c in CLASSES}})
        agg["workers"] += 1
        agg[cls] += 1
    return out


def per_worker(rows: list[WorkerWeek]) -> dict[str, dict]:
    """Every worker's whole record in scope: pairs, prompt pairs, share, class.

    Summed over pairs rather than averaged over weeks, so a worker's one busy
    week counts for what it was and a quiet week with two visits does not
    weigh the same.
    """
    totals: dict[str, list[int]] = {}
    for r in rows:
        t = totals.setdefault(r.worker_hash, [0, 0])
        t[0] += r.pairs
        t[1] += r.prompt
    return {
        h: {
            "pairs": pairs,
            "prompt": prompt,
            "share": (prompt / pairs) if pairs else None,
            "class": classify(pairs, prompt, MIN_PAIRS_WORKER),
        }
        for h, (pairs, prompt) in totals.items()
    }


def distribution(workers: dict[str, dict]) -> dict:
    """The shape across workers: a histogram of shares, and a count per class.

    Only judged workers (at least MIN_PAIRS_WORKER pairs) are binned; the rest
    are counted so the display can say how many were too thin to call.
    """
    bins = [0] * HISTOGRAM_BINS
    classes = {c: 0 for c in CLASSES}
    judged = 0
    for w in workers.values():
        if w["class"] is None:
            continue
        judged += 1
        classes[w["class"]] += 1
        bins[min(int(w["share"] * HISTOGRAM_BINS), HISTOGRAM_BINS - 1)] += 1
    return {
        "workers": judged,
        "unjudged": len(workers) - judged,
        "classes": classes,
        "histogram": bins,
        "method": method(),
    }


def method() -> dict:
    """The thresholds, published with the figures so a display can state them."""
    return {
        "reliable_from": RELIABLE_FROM.date().isoformat(),
        "pair_min_gap_minutes": int(PAIR_MIN_GAP.total_seconds() // 60),
        "pair_max_gap_minutes": int(PAIR_MAX_GAP.total_seconds() // 60),
        "online_share": ONLINE_SHARE,
        "sometimes_share": SOMETIMES_SHARE,
        "min_pairs_week": MIN_PAIRS_WEEK,
        "min_pairs_worker": MIN_PAIRS_WORKER,
    }
