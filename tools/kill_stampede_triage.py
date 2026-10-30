#!/usr/bin/env python3
"""Answer "were these worker kills the RawVisitCache stampede?" in one command.

    python3 tools/kill_stampede_triage.py --start 2026-09-16T07:30:00Z --end 2026-09-16T10:00:00Z
    python3 tools/kill_stampede_triage.py --hours 6
    python3 tools/kill_stampede_triage.py --hours 6 --json
    python3 tools/kill_stampede_triage.py --print-queries      # no AWS call; inspect first

`labs-jj-web-worker-kill-rate-actionable` names #1361 as the prime suspect and
tells the responder to go read `/ecs/labs-jj-web`.  Everything after that point
was prose: three Logs Insights queries written out by hand in #1361's comments,
re-typed on each incident.  This runs all three, together, over one window, and
-- the part that actually matters -- refuses to report health from an empty
result.

WHY THIS IS CODE.  Every one of these returns a WRONG answer rather than an
error, and three of them have already been committed on this exact issue:

  1. ABSENCE OF `[SingleFlight]` LINES IS NOT EVIDENCE THE GUARD WORKS.  The
     guard logs only on the LOSER path -- the winner is silent by design (see
     single_flight.py) -- so "no SingleFlight lines" is produced both by a
     working guard under no contention and by a guard that never engaged.  Read
     as a clean bill of health it is the most expensive output here, because it
     closes the investigation.  #1361's own comment says so in prose; this tool
     encodes it as a verdict that cannot say `guard_confirmed` without a
     positive line, and says `no_evidence` instead of anything reassuring.

  2. A TOO-NARROW WINDOW UNDER-REPORTS AND LOOKS COMPLETE.  On 2026-09-08 a
     Logs Insights window ending at 13:10Z truncated the second burst of an
     incident entirely and under-counted it by 39% (11 kills reported, 18 real)
     -- the runbook's "do not conclude from one page of filter-log-events",
     wearing a time window instead of a page token.  So the window is echoed in
     the output, and a kill in the first or last bucket raises an explicit
     `window may be clipped` flag rather than being silently accepted.

  3. KILLS TRACK TRAFFIC, NOT HEALTH.  "Kills dropped after the deploy" was true
     on 2026-09-07 and meaningless: they hit zero three hours BEFORE the fix
     shipped, because people stopped working.  The comparable quantity is kills
     per unit of rebuild pressure, so kills are always reported ALONGSIDE `Raw
     cache MISS` in the same buckets, and the ratio is computed rather than left
     for the reader to eyeball.  Pre-fix reference points, both real and both
     from #1361: 48 misses -> 7 kills, and 12 misses -> 12 kills.

  4. `start_query` IS ASYNCHRONOUS AND `Complete` IS NOT THE ONLY TERMINAL
     STATE.  A caller that reads results once gets `Running` with an empty
     `results` list, which is shaped exactly like a successful empty query.
     This polls to a terminal status and treats `Failed`/`Cancelled`/`Timeout`
     as errors rather than as zero rows.

  5. LOGS INSIGHTS TIMES ARE UNIX SECONDS, NOT ISO.  `--start-time` silently
     accepts a number that is not what you meant; there is no format error to
     catch.  Parsed once, here, from an explicit UTC string.

WHAT A STAMPEDE LOOKS LIKE.  The tell is NOT the `[SQL] Loaded N visits` line --
that logs the cache read AFTER the rebuild finishes, so it is small (8-35) and
lands after the kills.  The memory goes upstream, in the repagination, which
logs only as httpx GETs.  The tell is `user_visits/?page_size=2500` fetches
whose `last_id` cursors REPEAT across log streams: on 2026-09-07, 53 fetches
over 16 distinct cursors, every cursor 2-6x, across both web tasks -- N workers
each walking the same ~40k-visit rebuild at once, peak memory N x per-request on
a 4096 MB task.

THE TELL CHANGED ON 2026-10-29.  #2776 strips query strings from outbound-request
lines (the cursor is a visit id and must not be logged), so lines after that carry
no `last_id`.  Since 2026-10-30 the logger (connect_labs/utils/log_scrub.py) appends
non-identifying facts instead:

    ... user_visits/?… "HTTP/1.1 200 OK" [query: page_size=2500 paginated=true has_cursor=true]

so a fetch is still recognisable as a 2500-row repagination page, and
`has_cursor=false` marks the FIRST page of a walk.  Without the cursor the tool
can no longer prove two streams walked the SAME rebuild; the new-line tell is
CONCURRENT repagination: two or more log streams fetching 2500-row user_visits
pages in the same 2-minute bin.  That is weaker than a repeated
cursor (it cannot tell one opportunity from two) and the verdict says so.  Old
lines (before #2776) still carry `last_id` and are judged on the exact cursor tell.
Lines from the gap between #2776 and the facts suffix carry neither; the tool
counts them and refuses to read their silence as "not #1361".  Cursor values are
used only to count repeats and are never printed.

WHAT THIS TOOL DOES NOT ANSWER.  Single-flight partitions on
(opportunity_id, pipeline_id) -- see raw_rebuild_lock_key -- so it bounds
concurrent rebuilds of the SAME opportunity and does nothing about several
opportunities rebuilding at once.  A cursor set that repeats WITHIN one
opportunity is a guard failure; distinct opportunities rebuilding concurrently
is a different question this tool reports but does not judge.

Needs `aws sso login --profile labs`.  Logs Insights is not reachable through
`run-labs-command` -- that Fargate task role holds the DB credentials but no
CloudWatch read permission at all -- so there is no credential-free fallback for
this measurement.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

PROFILE = "labs"
REGION = "us-east-1"
WEB_LOG_GROUP = "/ecs/labs-jj-web"

# Bucket width for the kills/MISS series. 30 minutes matches
# labs-jj-web-worker-kill-rate's own Period, so a row here is directly
# comparable with what the alarm scored.
BUCKET_MINUTES = 30

# Both from #1361, both pre-single-flight, both real windows. Quoted so the
# ratio below is read against something rather than in a vacuum.
PRE_FIX_REFERENCE = ((48, 7), (12, 12))

Q_KILLS_AND_MISSES = f"""fields @timestamp, @message
| filter @message like /Raw cache MISS|was sent SIGKILL/
| parse @message /(?<kind>Raw cache MISS|was sent SIGKILL)/
| stats count() as n by kind, bin(@timestamp, {BUCKET_MINUTES}m) as t
| sort t asc"""

Q_SINGLEFLIGHT = """fields @timestamp, @message
| filter @message like /SingleFlight/
| sort @timestamp asc
| limit 500"""

# Two web tasks repaginating at once is the #1361 concurrency half.
CONCURRENCY_MINUTES = 2
STAMPEDE_PAGE_SIZE = 2500
# Logs Insights' own ceiling; a result this long is truncated, and says so.
CURSOR_ROW_LIMIT = 10000

# @logStream is what makes a repeat evidence of CONCURRENCY rather than of one
# worker retrying: the same cursor (old lines) or the same minutes (new lines) on
# two streams is two tasks walking at once. Every httpx user_visits line is
# matched -- not just page_size=2500 ones -- so lines that carry NEITHER the old
# query nor the new facts (2026-10-29 until the facts suffix shipped) are counted
# as blind instead of silently missing. @message itself is never returned: only
# the parsed, non-identifying fields (plus old lines' cursor, counted not shown).
Q_CURSORS = f"""fields @timestamp, @logStream
| filter @message like /HTTP Request/ and @message like /user_visits/
| parse @message /page_size=(?<page_size>\\d+)/
| parse @message /has_cursor=(?<has_cursor>true|false)/
| parse @message /last_id=(?<cursor>\\d+)/
| sort @timestamp asc
| limit {CURSOR_ROW_LIMIT}"""

QUERIES = {
    "kills_and_misses": Q_KILLS_AND_MISSES,
    "singleflight": Q_SINGLEFLIGHT,
    "cursors": Q_CURSORS,
}


def _aws(args: list[str], timeout: int = 120) -> dict | list:
    cmd = ["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args[:2])}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def _parse_utc(s: str) -> datetime:
    """Parse an explicit UTC instant. Refuses a naive string rather than guessing.

    A naive timestamp here would be interpreted as local time by fromisoformat
    and silently shift the whole window by the UTC offset -- the failure
    alarm_rule_replay.py's trap (1) describes, arriving through the argument
    parser instead of through the AWS response.
    """
    text = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{s!r} has no timezone -- pass an explicit UTC instant, e.g. 2026-09-16T07:30:00Z"
        )
    return dt.astimezone(timezone.utc)


def _run_query(log_group: str, query: str, start: datetime, end: datetime, poll_seconds: float = 2.0) -> list[dict]:
    """Start a Logs Insights query and poll it to a TERMINAL status.

    Reading results once returns `Running` with an empty `results` list, which
    is indistinguishable from a successful empty query -- see the module
    docstring, trap 4. Non-Complete terminal states raise rather than returning
    zero rows, because a failed query that reports "no kills" is the exact
    shape of wrong answer this file exists to prevent.
    """
    started = _aws(
        [
            "logs",
            "start-query",
            "--log-group-name",
            log_group,
            "--start-time",
            str(int(start.timestamp())),
            "--end-time",
            str(int(end.timestamp())),
            "--query-string",
            query,
        ]
    )
    qid = started["queryId"]
    deadline = time.monotonic() + 300
    while True:
        out = _aws(["logs", "get-query-results", "--query-id", qid])
        status = out.get("status")
        if status == "Complete":
            break
        if status in ("Failed", "Cancelled", "Timeout"):
            raise RuntimeError(f"Logs Insights query {status} (queryId={qid})")
        if time.monotonic() > deadline:
            raise RuntimeError(f"Logs Insights query still {status} after 300s (queryId={qid})")
        time.sleep(poll_seconds)
    rows = []
    for row in out.get("results", []):
        rows.append({f["field"]: f["value"] for f in row if f.get("field") != "@ptr"})
    return rows


def _series(rows: list[dict]) -> dict[str, dict[str, int]]:
    """{bucket -> {"kills": n, "misses": n}} from the stats-by-kind result."""
    series: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = (r.get("t") or "").strip()
        kind = (r.get("kind") or "").strip()
        try:
            n = int(float(r.get("n", 0)))
        except ValueError:
            continue
        slot = series.setdefault(bucket, {"kills": 0, "misses": 0})
        if "SIGKILL" in kind:
            slot["kills"] += n
        elif "MISS" in kind:
            slot["misses"] += n
    return dict(sorted(series.items()))


def _classify_singleflight(rows: list[dict]) -> dict[str, int]:
    """Bucket the guard's loser-path lines by which branch they record."""
    counts = {"lent_existing": 0, "no_prior_rows": 0, "lock_unavailable": 0, "other": 0}
    for r in rows:
        msg = r.get("@message", "")
        if "no prior rows to lend" in msg:
            counts["no_prior_rows"] += 1
        elif "could not take the rebuild lock" in msg:
            counts["lock_unavailable"] += 1
        elif "is being rebuilt by another connection" in msg:
            counts["lent_existing"] += 1
        else:
            counts["other"] += 1
    return counts


def _minute_bin(ts: str) -> str | None:
    """Logs Insights @timestamp ('2026-09-07 10:01:02.123') -> its CONCURRENCY_MINUTES bin."""
    try:
        dt = datetime.fromisoformat((ts or "").strip().replace(" ", "T")[:19])
    except ValueError:
        return None
    return dt.replace(minute=dt.minute - dt.minute % CONCURRENCY_MINUTES, second=0).strftime("%Y-%m-%d %H:%M")


def _cursor_repetition(rows: list[dict]) -> dict:
    """Repagination evidence from user_visits fetch lines, old and new shapes.

    Old lines (query string intact): fetches per cursor and how many distinct log
    streams each appeared on. A cursor fetched N times from ONE stream is a retry;
    the same cursor on two streams is two tasks walking the same rebuild
    concurrently -- the documented stampede tell. Cursor values are counted, never
    returned.

    New lines (`[query: page_size=... has_cursor=...]`): which streams fetched
    2500-row pages in each CONCURRENCY_MINUTES bin, and how many walks started
    (has_cursor=false is a first page). Two streams in one bin is concurrent
    repagination; it cannot say whether they walked the same opportunity.

    Lines with neither shape are counted as `blind_fetches`.
    """
    per_cursor: dict[str, dict] = {}
    bins: dict[str, dict] = {}
    unparsed = blind = new_style = walks_started = 0
    for r in rows:
        page_size = r.get("page_size")
        has_cursor = r.get("has_cursor")
        cursor = r.get("cursor")
        stream = r.get("@logStream")
        if not page_size:
            blind += 1
            continue
        if str(page_size) != str(STAMPEDE_PAGE_SIZE):
            continue
        if cursor:
            slot = per_cursor.setdefault(cursor, {"fetches": 0, "streams": set()})
            slot["fetches"] += 1
            if stream:
                slot["streams"].add(stream)
        elif has_cursor in ("true", "false"):
            new_style += 1
            if has_cursor == "false":
                walks_started += 1
            b = _minute_bin(r.get("@timestamp", ""))
            if b and stream:
                slot = bins.setdefault(b, {"streams": set(), "walk_streams": set()})
                slot["streams"].add(stream)
                if has_cursor == "false":
                    slot["walk_streams"].add(stream)
        else:
            unparsed += 1  # old line on its first page (no last_id yet)
    table = sorted(per_cursor.values(), key=lambda v: -v["fetches"])
    multi = [v for v in table if v["fetches"] > 1 and len(v["streams"]) > 1]
    concurrent = {b: v for b, v in bins.items() if len(v["streams"]) > 1}
    return {
        "total_fetches": sum(v["fetches"] for v in table) + unparsed + new_style,
        "fetches_without_cursor": unparsed,
        "distinct_cursors": len(table),
        "cursors_repeated_across_streams": len(multi),
        # Ranked by fetch count; the cursor values themselves are visit ids and stay out.
        "top": [
            {"rank": i + 1, "fetches": v["fetches"], "streams": len(v["streams"])} for i, v in enumerate(table[:15])
        ],
        "new_style_fetches": new_style,
        "walks_started": walks_started,
        "concurrent_bins": len(concurrent),
        "concurrent_walk_start_bins": sum(1 for v in concurrent.values() if len(v["walk_streams"]) > 1),
        "max_streams_in_bin": max((len(v["streams"]) for v in bins.values()), default=0),
        "blind_fetches": blind,
        "truncated": len(rows) >= CURSOR_ROW_LIMIT,
    }


def _verdict(series: dict, sf: dict[str, int], cursors: dict, clipped: bool) -> dict:
    """Name what the evidence supports -- and refuse to imply health from silence."""
    kills = sum(v["kills"] for v in series.values())
    misses = sum(v["misses"] for v in series.values())
    notes: list[str] = []

    if kills == 0 and misses == 0:
        return {
            "verdict": "no_evidence",
            "kills": 0,
            "misses": 0,
            "reason": (
                "No kills and no cache misses in this window. That is NOT a clean bill of "
                "health -- it is equally consistent with a window that missed the incident. "
                "Widen it or confirm the window against the alarm's own transition times."
            ),
            "notes": notes,
        }

    if cursors.get("cursors_repeated_across_streams", 0) > 0:
        verdict = "stampede_present"
        reason = (
            f"{cursors['cursors_repeated_across_streams']} cursor(s) fetched more than once "
            f"across MORE THAN ONE log stream -- several workers walking the same rebuild "
            f"concurrently, the #1361 tell."
        )
    elif cursors.get("concurrent_bins", 0) > 0:
        verdict = "concurrent_repagination"
        reason = (
            f"{cursors['concurrent_bins']} {CONCURRENCY_MINUTES}-minute bin(s) with up to "
            f"{cursors['max_streams_in_bin']} log streams fetching {STAMPEDE_PAGE_SIZE}-row user_visits "
            f"pages at once ({cursors['concurrent_walk_start_bins']} with walks STARTING on several "
            "streams) -- the #1361 concurrency shape. The logs no longer carry the cursor, so this "
            "cannot prove the streams walked the SAME opportunity; single-flight only guards that case."
        )
    elif cursors["distinct_cursors"] > 0 or cursors.get("new_style_fetches", 0) > 0:
        verdict = "repagination_without_stampede"
        reason = (
            "Full repaginations are happening but no cursor repeats across streams, so this "
            "window shows the invalidation half of #1361 (cost per request) without the "
            "concurrency half (the OOM driver)."
        )
    elif cursors.get("blind_fetches", 0) > 0:
        verdict = "blind_window"
        reason = (
            f"{cursors['blind_fetches']} user_visits fetch line(s) carry neither the old query string "
            "nor the [query: ...] facts (logged between #2776 on 2026-10-29 and the facts suffix). "
            "This window cannot say whether these kills are #1361 -- no evidence either way."
        )
    else:
        verdict = "no_repagination_seen"
        reason = "No page_size=2500 fetches in this window; these kills are probably not #1361."
    if cursors.get("truncated"):
        notes.append(
            f"FETCH ROWS TRUNCATED at {CURSOR_ROW_LIMIT}: narrow the window before quoting the cursor counts."
        )

    if sf["lent_existing"] > 0:
        notes.append(
            f"single-flight ENGAGED {sf['lent_existing']}x (losers served existing rows) -- "
            "the guard is demonstrably working in this window."
        )
    else:
        notes.append(
            "no single-flight 'lent existing' line: the guard logs ONLY on the loser path, so "
            "this is indistinguishable from no contention. It is NOT evidence the guard works."
        )
    if sf["lock_unavailable"]:
        notes.append(
            f"{sf['lock_unavailable']}x could-not-take-lock (fail-open path) -- " "the lock itself may be erroring."
        )

    if misses:
        notes.append(
            f"kills per miss = {kills / misses:.2f} ({kills} kills / {misses} misses); "
            f"pre-fix reference points {PRE_FIX_REFERENCE[0][0]}->{PRE_FIX_REFERENCE[0][1]} "
            f"and {PRE_FIX_REFERENCE[1][0]}->{PRE_FIX_REFERENCE[1][1]}."
        )
    else:
        notes.append(f"{kills} kill(s) with ZERO cache misses -- rebuild pressure is not the driver here.")

    if clipped:
        notes.append(
            "WINDOW MAY BE CLIPPED: activity in the first or last bucket. A narrow window "
            "under-reported an incident by 39% on 2026-09-08 -- widen and re-run before quoting totals."
        )
    return {"verdict": verdict, "kills": kills, "misses": misses, "reason": reason, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=_parse_utc, help="UTC start, e.g. 2026-09-16T07:30:00Z")
    ap.add_argument("--end", type=_parse_utc, help="UTC end (default: now)")
    ap.add_argument("--hours", type=float, help="window ending now, in hours (alternative to --start)")
    ap.add_argument("--log-group", default=WEB_LOG_GROUP)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--print-queries", action="store_true", help="print the three queries and exit; no AWS call")
    args = ap.parse_args()

    if args.print_queries:
        for name, q in QUERIES.items():
            print(f"### {name}\n{q}\n")
        return 0

    if args.start is None and args.hours is None:
        ap.error("pass --start (with optional --end) or --hours")
    end = args.end or datetime.now(timezone.utc)
    start = args.start or (end - timedelta(hours=args.hours))
    if start >= end:
        ap.error(f"empty window: start {start.isoformat()} is not before end {end.isoformat()}")

    try:
        km = _run_query(args.log_group, Q_KILLS_AND_MISSES, start, end)
        sf_rows = _run_query(args.log_group, Q_SINGLEFLIGHT, start, end)
        cur_rows = _run_query(args.log_group, Q_CURSORS, start, end)
    except RuntimeError as exc:
        print(f"kill_stampede_triage: {exc}", file=sys.stderr)
        if "sso" in str(exc).lower() or "token" in str(exc).lower() or "credential" in str(exc).lower():
            print("  fix: aws sso login --profile labs", file=sys.stderr)
        return 2

    series = _series(km)
    sf = _classify_singleflight(sf_rows)
    cursors = _cursor_repetition(cur_rows)
    buckets = list(series)
    clipped = bool(buckets) and (any(series[b]["kills"] or series[b]["misses"] for b in (buckets[0], buckets[-1])))
    verdict = _verdict(series, sf, cursors, clipped)

    payload = {
        "window": {"start": start.isoformat(), "end": end.isoformat(), "log_group": args.log_group},
        "buckets": series,
        "single_flight": sf,
        "cursors": cursors,
        **verdict,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"window   {start.isoformat()} -> {end.isoformat()}  ({args.log_group})")
    print(f"\nkills / cache MISS per {BUCKET_MINUTES}min:")
    if series:
        print(f"  {'bucket (UTC)':<24} {'misses':>7} {'kills':>7}")
        for b, v in series.items():
            print(f"  {b:<24} {v['misses']:>7} {v['kills']:>7}")
    else:
        print("  (none)")

    print("\nsingle-flight guard (loser path only -- the winner is silent):")
    for k, v in sf.items():
        print(f"  {k:<18} {v}")

    print(f"\nrepagination ({STAMPEDE_PAGE_SIZE}-row user_visits pages):")
    print(f"  total fetches                  {cursors['total_fetches']}")
    print(f"  old lines: distinct cursors    {cursors['distinct_cursors']}")
    print(f"  old lines: repeated ACROSS log streams {cursors['cursors_repeated_across_streams']}")
    for t in cursors["top"][:8]:
        print(f"    cursor #{t['rank']:<3} fetches={t['fetches']:<3} streams={t['streams']}")
    print(f"  new lines: fetches             {cursors['new_style_fetches']}")
    print(f"  new lines: walks started       {cursors['walks_started']}")
    print(
        f"  new lines: concurrent {CONCURRENCY_MINUTES}m bins  {cursors['concurrent_bins']}"
        f" (walk-start on >1 stream: {cursors['concurrent_walk_start_bins']},"
        f" max streams {cursors['max_streams_in_bin']})"
    )
    print(f"  blind lines (no query, no facts) {cursors['blind_fetches']}")

    print(f"\nVERDICT: {verdict['verdict']}")
    print(f"  {verdict['reason']}")
    for n in verdict["notes"]:
        print(f"  - {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
