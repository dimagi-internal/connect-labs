#!/usr/bin/env python3
"""Is the unexplained request time OUR PYTHON, or are we just not running?

    python3 tools/residual_bands.py --hours 8 --path-prefix /audit/image/
    python3 tools/residual_bands.py --hours 8 --json

`self_ms` is a residual -- `duration_ms - outbound_ms - db_ms` -- so a large one
only means "unexplained", never "our CPU". `cpu_ms` is the measurement that
settles it, and the fork is the one `request_telemetry`'s module docstring
already states:

  * `cpu_ms` ~= `self_ms` -- the time really is our Python. Profile the view.
  * `cpu_ms` << `self_ms` -- the thread was NOT RUNNING. Profiling finds nothing;
    look for an unmeasured wait (an uninstrumented client, a body download) or
    for descheduling.

This exists rather than a query in a doc because the comparison was hand-built
five separate times for #1386, and BOTH of its failure modes return a confident
wrong answer instead of an error:

  1. Banding the DURATION-GATED stream. `_maybe_log` emits only at
     `duration_ms >= 3000` unless the request was sampled, so that population is
     a tail whose floor moves with load. Comparing bands drawn from it is the
     artefact in docs/PERFORMANCE_RUNBOOK.md: on 2026-09-01 it reported `self_ms`
     HIGHEST when the tier was idle -- the opposite of what queueing predicts --
     from data that was real, plentiful and uninterpretable for the question.
     The unbiased population is `sampled = 1`. It is NOT `reason = "sample"`: a
     request that is both slow and sampled reads "slow,sample", so selecting on
     `reason` silently drops exactly the slow ones and re-creates the bias the
     sample exists to remove.
  2. Percentiles of different fields from different requests. `pct(self_ms, 50)`
     and `pct(cpu_ms, 50)` over a band describe two different populations, and
     the whole inference is WITHIN one request. Every band here comes from a
     single `stats ... by` over the same rows.

Prose did not hold these. The `labs-perf` note recording the 2026-09-01 error
says so directly: "I had written the confound down in the same paragraph and
reasoned past it."

An empty or too-small sample returns `inconclusive`. It never returns a verdict
that reads like a clean bill of health, for the same reason perf_triage.py
refuses to print `healthy` off a query that returned nothing.

Read-only. Needs AWS profile `labs` (account 858923557655) and the unbiased
sample switched on (`TELEMETRY_SAMPLE_RATE > 0`, optionally scoped by
`TELEMETRY_SAMPLE_PATH_PREFIX`) -- without it there are no `sampled = 1` rows
and this tool correctly says so instead of falling back to the gated stream.
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
LOG_GROUP = "/ecs/labs-jj-web"

# Duration edges, in ms, for the bands. These are the bands #1386 was argued on,
# kept as a default so a re-run is comparable with the numbers on that issue
# rather than being a function of who ran it.
DEFAULT_EDGES = (1500, 3000)

# `case` accepts at most 10 branches, and N edges produce N conditions plus the
# default. Enforced here so an over-long --edges fails immediately rather than
# after a Logs Insights round trip.
MAX_EDGES = 9

# Below this many rows in a band, the band's means are noise and get reported as
# such. Not a hard failure -- a thin top band is the normal shape of this data
# (90 rows in the >=3s band of the 4,177-request #1386 sample) -- but a band of
# three requests must not carry a verdict.
MIN_BAND_ROWS = 30

# Total sampled rows below which no verdict is issued at all.
MIN_TOTAL_ROWS = 100

# The cpu/self ratio that separates the two branches of the fork. Deliberately
# wide: the claim being tested is order-of-magnitude ("is the residual CPU at
# all"), not a precise split. #1386 measured 8.1% on the >=3s band and the
# opposing hypothesis would have put it near 100%.
CPU_IS_THE_RESIDUAL = 0.70  # cpu_ms >= 70% of self_ms  -> it is our Python
CPU_IS_NOT_THE_RESIDUAL = 0.30  # cpu_ms <= 30% of self_ms -> it is not

QUERY_POLL_SECONDS = 2
QUERY_TIMEOUT_SECONDS = 180


def _aws(args: list[str], timeout: int = 120) -> dict | list:
    cmd = ["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args[:3])}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def _window(hours: int) -> tuple[int, int, str, str]:
    """UTC epoch window, floored to the minute. Never local time.

    Logs Insights takes epoch seconds, so the local/UTC trap perf_triage.py
    documents for GetMetricStatistics cannot bite in the same way -- but the
    HUMAN-readable window printed alongside the result can still be wrong, and a
    result labelled with the wrong window is how a re-run gets compared against
    the wrong baseline. Both come from one computation here.

    Flooring to the minute keeps two runs a few seconds apart over the same
    traffic returning the same window, so a re-measured falsifier is comparable
    with the run it is being checked against.
    """
    now = datetime.now(timezone.utc)
    end = now - timedelta(seconds=now.timestamp() % 60)
    start = end - timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return int(start.timestamp()), int(end.timestamp()), start.strftime(fmt), end.strftime(fmt)


def build_query(edges: tuple[int, ...], path_prefix: str, allow_biased: bool) -> str:
    """The Logs Insights query, with both traps closed by construction.

    `sampled = 1` is the filter, not `reason = "sample"` -- see the module
    docstring. Every statistic comes from ONE `stats ... by band`, so a band's
    `cpu_ms` and `self_ms` are means over the same requests; there is
    deliberately no code path here that issues a second query per field.
    """
    lines = [
        "fields @timestamp, path, duration_ms, cpu_ms, self_ms, db_ms, outbound_ms, sampled",
    ]
    if not allow_biased:
        # The load-bearing line. Everything else in this file is presentation.
        lines.append("| filter sampled = 1")
    if path_prefix:
        # startsWith, not `like`: `like "/audit/image/"` is a SUBSTRING match, so it
        # would also match a path that merely contains the prefix. Both are documented
        # Logs Insights string functions; the difference is the whole point of scoping.
        lines.append(f'| filter startsWith(path, "{path_prefix}") = 1')

    # A single computed band field, so the grouping key and the statistics are
    # evaluated over the same row. `case` is a documented Logs Insights general
    # function -- case(cond1, val1, ..., [default]) -- and it caps at 10 branches,
    # which is why MAX_EDGES exists rather than letting a long --edges list build a
    # query that AWS rejects only once it has been run.
    cases = []
    for i, edge in enumerate(edges):
        cases.append(f"duration_ms < {edge}, {i}")
    band_expr = ", ".join(cases)
    lines.append(f"| fields (case({band_expr}, {len(edges)})) as band")
    lines.append(
        "| stats count() as n, avg(duration_ms) as duration_ms, avg(cpu_ms) as cpu_ms, "
        "avg(self_ms) as self_ms, avg(outbound_ms) as outbound_ms, avg(db_ms) as db_ms by band"
    )
    lines.append("| sort band asc")
    return "\n".join(lines)


def _run_query(query: str, start_epoch: int, end_epoch: int, log_group: str) -> list[dict]:
    started = _aws(
        [
            "logs",
            "start-query",
            "--log-group-name",
            log_group,
            "--start-time",
            str(start_epoch),
            "--end-time",
            str(end_epoch),
            "--query-string",
            query,
            "--limit",
            "10000",
        ]
    )
    query_id = started["queryId"]

    deadline = time.monotonic() + QUERY_TIMEOUT_SECONDS
    while True:
        result = _aws(["logs", "get-query-results", "--query-id", query_id])
        status = result.get("status")
        if status == "Complete":
            return result.get("results", [])
        if status in ("Failed", "Cancelled", "Timeout"):
            raise RuntimeError(f"Logs Insights query {status}")
        if time.monotonic() > deadline:
            # Explicitly an error, not an empty result: a timed-out query that
            # returned partial rows would otherwise be read as a thin sample and
            # reported as `inconclusive`, which is a different and wrong answer.
            raise RuntimeError(f"Logs Insights query did not complete in {QUERY_TIMEOUT_SECONDS}s")
        time.sleep(QUERY_POLL_SECONDS)


def _rows_to_bands(rows: list[list[dict]], edges: tuple[int, ...]) -> list[dict]:
    """Insights returns [[{field,value},...],...]. Reshape and label the bands."""
    labels = []
    prev = 0
    for edge in edges:
        labels.append(f"<{edge}ms" if prev == 0 else f"{prev}-{edge}ms")
        prev = edge
    labels.append(f">={prev}ms")

    bands = []
    for row in rows:
        rec = {f["field"]: f["value"] for f in row}
        try:
            idx = int(float(rec.get("band", -1)))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(labels):
            continue
        bands.append(
            {
                "band": labels[idx],
                "index": idx,
                "n": int(float(rec.get("n", 0))),
                "duration_ms": round(float(rec.get("duration_ms", 0)), 1),
                "cpu_ms": round(float(rec.get("cpu_ms", 0)), 1),
                "self_ms": round(float(rec.get("self_ms", 0)), 1),
                "outbound_ms": round(float(rec.get("outbound_ms", 0)), 1),
                "db_ms": round(float(rec.get("db_ms", 0)), 1),
            }
        )
    bands.sort(key=lambda b: b["index"])
    return bands


def judge(bands: list[dict], allow_biased: bool = False) -> dict:
    """Name the branch of the fork, or refuse. Never a clean bill of health.

    The refusals come first on purpose. Every one of them is a state in which a
    plausible-looking verdict could be computed from the numbers present, and
    printing it is the failure this tool exists to prevent.
    """
    total = sum(b["n"] for b in bands)

    if allow_biased:
        return {
            "verdict": "uninterpretable",
            "findings": [
                "Ran with --allow-biased: the rows are the duration-gated stream, whose floor "
                "moves with load. Band-to-band differences here are an artefact of sampling, "
                "not a measurement. Use this for FINDING slow requests only.",
            ],
            "next_step": "Turn on the unbiased sample (TELEMETRY_SAMPLE_RATE) and re-run without --allow-biased.",
            "bands": bands,
            "total_rows": total,
        }

    if not bands or total == 0:
        return {
            "verdict": "inconclusive",
            "findings": [
                "No rows with sampled = 1 in this window. That is what an unbiased sample that is "
                "switched OFF looks like -- it is not evidence the tier is healthy.",
            ],
            "next_step": (
                "Check TELEMETRY_SAMPLE_RATE (and TELEMETRY_SAMPLE_PATH_PREFIX) on the running "
                "task definition, then re-run. Do NOT substitute the gated stream."
            ),
            "bands": bands,
            "total_rows": total,
        }

    if total < MIN_TOTAL_ROWS:
        return {
            "verdict": "inconclusive",
            "findings": [
                f"Only {total} sampled requests in this window (need >= {MIN_TOTAL_ROWS}).",
            ],
            "next_step": "Widen --hours, or raise TELEMETRY_SAMPLE_RATE, and re-run.",
            "bands": bands,
            "total_rows": total,
        }

    top = bands[-1]
    if top["n"] < MIN_BAND_ROWS:
        return {
            "verdict": "inconclusive",
            "findings": [
                f"Top band {top['band']} has only {top['n']} requests (need >= {MIN_BAND_ROWS}); "
                "its means are noise. The slow tail is where the question lives, so a thin top "
                "band means the window cannot answer it.",
            ],
            "next_step": "Widen --hours so the slow tail fills out, and re-run.",
            "bands": bands,
            "total_rows": total,
        }

    if top["self_ms"] <= 0:
        return {
            "verdict": "no_residual",
            "findings": [
                f"Top band {top['band']} has no unexplained time left ({top['self_ms']} ms): "
                "outbound_ms and db_ms already account for the duration.",
            ],
            "next_step": "Read outbound_ms / db_ms -- whichever dominates is the answer.",
            "bands": bands,
            "total_rows": total,
        }

    ratio = top["cpu_ms"] / top["self_ms"]
    findings = [
        f"Top band {top['band']}: n={top['n']}, duration {top['duration_ms']} ms, "
        f"self {top['self_ms']} ms, cpu {top['cpu_ms']} ms "
        f"({ratio:.0%} of the residual), outbound {top['outbound_ms']} ms, db {top['db_ms']} ms.",
    ]

    base = bands[0]
    if base["self_ms"] > 0 and base["cpu_ms"] > 0:
        findings.append(
            f"Growth from {base['band']}: duration x{top['duration_ms'] / max(base['duration_ms'], 1):.1f}, "
            f"self x{top['self_ms'] / base['self_ms']:.1f}, cpu x{top['cpu_ms'] / base['cpu_ms']:.1f}. "
            "A residual that grows while CPU stays flat is time the thread was not running."
        )

    if ratio >= CPU_IS_THE_RESIDUAL:
        verdict = "residual_is_cpu"
        next_step = "The residual really is our Python -- profile the view. cpu_ms accounts for most of it."
    elif ratio <= CPU_IS_NOT_THE_RESIDUAL:
        verdict = "residual_is_not_cpu"
        next_step = (
            "The thread was NOT RUNNING for most of the residual. Profiling the view will find "
            "nothing. Look for an unmeasured wait -- an uninstrumented client, or a body transfer "
            "outside the measured span (#1386) -- or for descheduling under contention. Note that "
            "self_ms cannot separate those two: it is still a residual."
        )
    else:
        verdict = "mixed"
        next_step = (
            f"cpu_ms is {ratio:.0%} of the residual -- neither branch cleanly. Narrow the window "
            "around a single incident, or scope --path-prefix to one endpoint, and re-run."
        )

    return {
        "verdict": verdict,
        "findings": findings,
        "next_step": next_step,
        "bands": bands,
        "total_rows": total,
    }


def collect(hours: int, edges: tuple[int, ...], path_prefix: str, allow_biased: bool, log_group: str) -> dict:
    start_epoch, end_epoch, start_iso, end_iso = _window(hours)
    query = build_query(edges, path_prefix, allow_biased)
    rows = _run_query(query, start_epoch, end_epoch, log_group)
    return {
        "window": {"start": start_iso, "end": end_iso, "hours": hours},
        "query": query,
        "log_group": log_group,
        "path_prefix": path_prefix or "(all paths)",
        "bands": _rows_to_bands(rows, edges),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=8, help="lookback window (default 8)")
    ap.add_argument(
        "--path-prefix",
        default="",
        help="scope to one endpoint, e.g. /audit/image/ -- matched as a true prefix",
    )
    ap.add_argument(
        "--edges",
        default=",".join(str(e) for e in DEFAULT_EDGES),
        help=f"duration_ms band edges (default {','.join(str(e) for e in DEFAULT_EDGES)}, as used on #1386)",
    )
    ap.add_argument("--log-group", default=LOG_GROUP, help=f"CloudWatch log group (default {LOG_GROUP})")
    ap.add_argument(
        "--allow-biased",
        action="store_true",
        help="drop the sampled=1 filter. Every result is then stamped UNINTERPRETABLE for "
        "band-to-band comparison -- use only to FIND slow requests, never to attribute them.",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--print-query", action="store_true", help="print the query and exit, without calling AWS")
    args = ap.parse_args()

    try:
        edges = tuple(int(e) for e in args.edges.split(",") if e.strip())
    except ValueError:
        print(f"--edges must be comma-separated integers, got {args.edges!r}", file=sys.stderr)
        return 2
    if not edges or list(edges) != sorted(edges):
        print(f"--edges must be ascending and non-empty, got {args.edges!r}", file=sys.stderr)
        return 2
    if len(edges) > MAX_EDGES:
        print(f"--edges takes at most {MAX_EDGES} values (case() caps at 10 branches)", file=sys.stderr)
        return 2

    if args.print_query:
        print(build_query(edges, args.path_prefix, args.allow_biased))
        return 0

    try:
        data = collect(args.hours, edges, args.path_prefix, args.allow_biased, args.log_group)
    except Exception as exc:
        print(f"residual_bands failed: {exc}", file=sys.stderr)
        return 2

    result = judge(data["bands"], allow_biased=args.allow_biased)

    if args.json:
        print(json.dumps({**result, "evidence": data}, indent=2, default=str))
        return 0

    w = data["window"]
    print(f"\nVERDICT: {result['verdict']}   ({w['start']} -> {w['end']} UTC, {data['path_prefix']})\n")
    if data["bands"]:
        print(f"  {'band':>12}  {'n':>6}  {'dur':>8}  {'cpu':>8}  {'self':>8}  {'out':>8}  {'db':>8}")
        for b in data["bands"]:
            print(
                f"  {b['band']:>12}  {b['n']:>6}  {b['duration_ms']:>8}  {b['cpu_ms']:>8}  "
                f"{b['self_ms']:>8}  {b['outbound_ms']:>8}  {b['db_ms']:>8}"
            )
        print()
    for f in result["findings"]:
        print(f"  - {f}")
    print(f"\nNEXT: {result['next_step']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
