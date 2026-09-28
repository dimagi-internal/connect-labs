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


def worker_weeks(events, exclude_days=None) -> list[WorkerWeek]:
    """Usable pairs and prompt pairs per worker per ISO week, for an events queryset.

    One query. The queryset supplies the scope (program, partner, window, test
    exclusions) exactly as every other card gets it; its SQL is wrapped rather
    than re-expressed, so this can never judge a different set of visits than
    the card beside it counts. The window function has to run over the scoped
    rows, not the table: the "next visit" is the next one IN SCOPE.

    Bucketed on the earlier visit's ``field_ts``, like every other weekly series.
    Days when forwarding to Connect was backed up are left out (``backlog_days``).
    """
    rows = _pair_counts(
        events,
        "date_trunc('week', field_ts AT TIME ZONE 'UTC') AS week, worker_hash",
        "1, 2",
        exclude_days,
    )
    return [WorkerWeek(week.replace(tzinfo=dt.timezone.utc), h, pairs, prompt) for week, h, pairs, prompt in rows]


# UTC offset in minutes by the country Pulse files a visit under, for the
# hour-of-day view. Every one of these is a single-zone country without DST,
# except the DRC, which is split at roughly 24 degrees east (Kinshasa +1,
# Lubumbashi and Goma +2). A visit with no country falls back to its longitude.
_UTC_OFFSET_MINUTES = {"NG": 60, "KE": 180, "UG": 180, "IN": 330, "LR": 0, "SL": 0, "TZ": 180, "ML": 0}


def _local_hour_sql() -> str:
    cases = " ".join(f"WHEN '{c}' THEN {m}" for c, m in _UTC_OFFSET_MINUTES.items())
    offset = (
        f"CASE country {cases} "
        "WHEN 'CD' THEN CASE WHEN lon >= 24 THEN 120 ELSE 60 END "
        "ELSE CASE WHEN lon IS NOT NULL THEN round(lon / 15.0) * 60 END END"
    )
    return f"EXTRACT(HOUR FROM (field_ts AT TIME ZONE 'UTC') + make_interval(mins => ({offset})::int))"


def by_hour(events, exclude_days=None) -> list[dict]:
    """Share of pairs sent promptly by the local hour the earlier visit started.

    Across every worker in scope, so it answers "when is there signal" rather
    than "who has it": a network that is online only in the evening, at home,
    shows as a climb late in the day.
    """
    counts = {
        int(h): (pairs, prompt)
        for h, pairs, prompt in _pair_counts(events, f"{_local_hour_sql()} AS hour", "1", exclude_days)
        if h is not None
    }
    return [
        {
            "hour": h,
            "pairs": counts.get(h, (0, 0))[0],
            "prompt": counts.get(h, (0, 0))[1],
            "share": (counts[h][1] / counts[h][0]) if counts.get(h, (0, 0))[0] else None,
        }
        for h in range(24)
    ]


def _pair_counts(events, select_sql: str, group_by: str, exclude_days) -> list[tuple]:
    """Usable pairs and prompt pairs, grouped however the caller asks.

    The one place the pair rules live (see the module docstring). ``select_sql``
    and ``group_by`` are fixed strings from this module, never request input.
    """
    inner = (
        events.filter(field_ts__gte=RELIABLE_FROM)
        .exclude(worker_hash="")
        .values("worker_hash", "field_ts", "sync_ts", "lon", "country")
    )
    try:
        sql, params = inner.query.sql_with_params()
    except EmptyResultSet:
        # A scope that can match nothing (``.none()``, an empty ``__in``) has
        # no SQL to wrap, and nothing to judge.
        return []
    if exclude_days is None:
        exclude_days = [d["date"] for d in backlog_days()]
    skip_days = ""
    extra = []
    if exclude_days:
        skip_days = "AND NOT ((field_ts AT TIME ZONE 'UTC')::date = ANY(%s::date[]))"
        extra = [list(exclude_days)]
    query = f"""
        WITH ev AS ({sql}),
        seq AS (
            SELECT worker_hash, field_ts, sync_ts, lon, country,
                   LEAD(field_ts) OVER (PARTITION BY worker_hash ORDER BY field_ts) AS next_ts
            FROM ev
        )
        SELECT {select_sql},
               COUNT(*) AS pairs,
               COUNT(*) FILTER (WHERE sync_ts <= next_ts) AS prompt
        FROM seq
        WHERE next_ts IS NOT NULL
          AND next_ts - field_ts >= %s
          AND next_ts - field_ts <= %s
          AND sync_ts >= field_ts
          {skip_days}
        GROUP BY {group_by}
    """
    with connection.cursor() as cur:
        cur.execute(query, [*params, PAIR_MIN_GAP, PAIR_MAX_GAP, *extra])
        return cur.fetchall()


# A day is a forwarding backlog when even its fastest visits were slow. The
# 10th-percentile lag is the platform's online phones: normally a minute or two
# (sampled 2026-09-28: 0.8 to 9 minutes across the history), because it is
# form time plus HQ-to-Connect forwarding. When forwarding backs up, every
# phone looks offline at once and that floor jumps; a genuinely offline worker
# cannot move it, since the online ones still set it.
BACKLOG_P10_MINUTES = 30
# Below this many visits a day's percentile is too thin to call a backlog.
BACKLOG_MIN_VISITS = 200
_BACKLOG_CACHE_KEY = "pulse:connectivity:backlog:v1"
_BACKLOG_CACHE_SECONDS = 6 * 60 * 60


def backlog_days() -> list[dict]:
    """Days when forwarding to Connect was backed up, platform-wide.

    Judged over every visit, not the caller's scope: a backlog is a platform
    event, and one partner's thin day would read as one. Cached, because it is
    a percentile over the whole table and changes only as days complete.
    """
    from django.core.cache import cache

    from connect_labs.pulse.models import PulseEvent

    hit = cache.get(_BACKLOG_CACHE_KEY)
    if hit is not None:
        return hit
    table = PulseEvent._meta.db_table
    query = f"""
        SELECT (field_ts AT TIME ZONE 'UTC')::date AS day,
               COUNT(*) AS n,
               percentile_cont(0.1) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM sync_ts - field_ts)) / 60.0 AS p10
        FROM {table}
        WHERE field_ts >= %s AND field_ts <= now() AND sync_ts >= field_ts
        GROUP BY 1
        HAVING COUNT(*) >= %s
           AND percentile_cont(0.1) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM sync_ts - field_ts)) > %s
        ORDER BY 1
    """
    with connection.cursor() as cur:
        cur.execute(query, [RELIABLE_FROM, BACKLOG_MIN_VISITS, BACKLOG_P10_MINUTES * 60])
        days = [{"date": day, "visits": n, "p10_minutes": round(float(p10), 1)} for day, n, p10 in cur.fetchall()]
    cache.set(_BACKLOG_CACHE_KEY, days, _BACKLOG_CACHE_SECONDS)
    return days


# The map places each worker in the ~11 km cell (0.1 degree) where most of
# their visits happen, and draws a cell only when it holds at least this many
# judged workers. Coarser than the delivery map's 1 km grid on purpose: this
# layer describes PEOPLE, and a 1 km cell with one worker in it is that
# worker's home village.
CELL_DEGREES = 0.1
MIN_WORKERS_PER_CELL = 3


def home_cells(events) -> dict[str, tuple[int, int]]:
    """Each worker's modal 0.1-degree cell, as integer (lat, lon) indices."""
    inner = events.filter(field_ts__gte=RELIABLE_FROM, lat__isnull=False, lon__isnull=False).exclude(worker_hash="")
    inner = inner.values("worker_hash", "lat", "lon")
    try:
        sql, params = inner.query.sql_with_params()
    except EmptyResultSet:
        return {}
    scale = int(round(1 / CELL_DEGREES))
    query = f"""
        WITH ev AS ({sql}),
        c AS (
            SELECT worker_hash, floor(lat * {scale})::int AS la, floor(lon * {scale})::int AS lo, COUNT(*) AS n
            FROM ev GROUP BY 1, 2, 3
        ),
        r AS (
            SELECT worker_hash, la, lo,
                   ROW_NUMBER() OVER (PARTITION BY worker_hash ORDER BY n DESC, la, lo) AS rn
            FROM c
        )
        SELECT worker_hash, la, lo FROM r WHERE rn = 1
    """
    with connection.cursor() as cur:
        cur.execute(query, params)
        return {h: (la, lo) for h, la, lo in cur.fetchall()}


def cells(workers: dict[str, dict], homes: dict[str, tuple[int, int]]) -> dict:
    """Judged workers per home cell: how many, which classes, and the mean share.

    Cells under MIN_WORKERS_PER_CELL are withheld and only counted, so the
    display can say how many workers are not on the map and why.
    """
    by_cell: dict[tuple[int, int], list[dict]] = {}
    for h, w in workers.items():
        if w["class"] is None or h not in homes:
            continue
        by_cell.setdefault(homes[h], []).append(w)
    shown, withheld = [], 0
    for (la, lo), ws in by_cell.items():
        if len(ws) < MIN_WORKERS_PER_CELL:
            withheld += len(ws)
            continue
        classes = {c: sum(1 for w in ws if w["class"] == c) for c in CLASSES}
        shown.append(
            {
                "lat": round((la + 0.5) * CELL_DEGREES, 2),
                "lon": round((lo + 0.5) * CELL_DEGREES, 2),
                "workers": len(ws),
                **classes,
                "share": sum(w["share"] for w in ws) / len(ws),
                "connected_rate": (classes[ONLINE] + classes[SOMETIMES]) / len(ws),
            }
        )
    shown.sort(key=lambda c: -c["workers"])
    return {"cells": shown, "withheld_workers": withheld, "min_workers_per_cell": MIN_WORKERS_PER_CELL}


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
        "backlog_p10_minutes": BACKLOG_P10_MINUTES,
        "backlog_min_visits": BACKLOG_MIN_VISITS,
    }
