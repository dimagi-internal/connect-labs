#!/usr/bin/env python3
"""Did the classifier gateway fail -- and was it ever actually put under the load that makes it fail?

    python3 tools/classifier_outcomes.py --hours 168
    python3 tools/classifier_outcomes.py --hours 168 --json

#1231 is about ONE mechanism: overlapping AI review runs saturating a shared
gateway. `audit/tasks.py` states its dose-response directly, next to the constant
it forced:

    five scale runs started within 46 seconds (up to 200 concurrent gateway calls
    against a plateau of ~20), which produced 73-92% error rates. Even two runs two
    minutes apart gave 79% on 2026-08-17, while runs spaced hours apart gave 9%.

The controlled variable is therefore CONCURRENT RUNS. It is not calls/day, and the
difference is the whole reason this file exists rather than a query in a doc. The
profile has been hand-assembled three times (the original #1231 table, the
2026-09-01 refresh, the 2026-09-07 re-run) and BOTH of its failure modes return a
confident WRONG ANSWER instead of an error:

  1. Reading the outcome table without the concurrency inventory beside it. A week
     in which every run happened to be serialized produces a beautiful table -- 12
     timeouts in 45,536 calls on 2026-08-31..09-07 -- that says nothing whatsoever
     about a concurrency bug, because the load was never applied. Reported as a
     pass it would close #1231 on a control that never ran.
  2. Aggregating across agents. `muac_*` runs one-at-a-time and was ~99.9% clean
     even in the worst window; `scale_*` is the pair that failed at 48-70%. When
     scale volume collapses (1,842 calls -> 624 between those two windows) the
     BLENDED rate improves on its own, and the improvement is entirely mix, not
     reliability. Every number here is per-agent for that reason; there is
     deliberately no total row.

So the verdict is conditioned on the load, not on the outcome. Unless the period
contains enough windows in which two or more runs were in flight at once WITH a
`scale_*` run among them, the answer is `inconclusive` -- stated as such -- no
matter how clean the outcomes look. This is the same refusal `residual_bands.py`
makes, for the same reason: a tool that cannot distinguish "it did not break" from
"we never pushed it" must not print the first.

Note the two halves of that condition, which are NOT the same set. Load is counted
over every run, because there is one shared gateway and `muac_*` calls sit on it
like any other. What `scale_*` gates is the READOUT: MUAC stayed ~99.9% clean
through the worst windows, so a MUAC-only overlap is real saturation with no
measurable signal. See `concurrency()`.

Read-only. Needs AWS profile `labs` (account 858923557655). Queries BOTH log groups:
runs execute on the worker and the API path logs on web, and reading only one is a
silent undercount rather than an error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

PROFILE = "labs"
REGION = "us-east-1"

# BOTH groups, always. A run's `[AIReview:...]` lines land on the worker, but the
# `[classifier]` lines follow whichever tier issued the call, so scoping to one
# group returns a plausible, smaller table with no indication anything is missing.
LOG_GROUPS = ("/ecs/labs-jj-web", "/ecs/labs-jj-worker")

# The agents whose error rate is a usable READOUT of gateway saturation -- the two
# that have ever failed at rate (48.8% and 70.3% in #1231's window). The `muac_*`
# pair uses the same 40-wide pool against the same gateway and still ran ~99.9%
# clean, because it runs one at a time; so a window containing only MUAC runs is
# real load that measures nothing.
#
# This gates which overlaps COUNT, never which runs contribute load -- see
# `concurrency()`. Conflating the two under-reports saturation.
SATURATING_AGENTS = frozenset({"scale_validation", "scale_dial_read"})

# Outcomes emitted by `post_with_retry` (see ai_review_agents/base.py). Listed so an
# UNRECOGNISED outcome is surfaced rather than silently folded into a bucket -- a new
# error kind appearing in production must not read as a shift between existing ones.
KNOWN_OUTCOMES = ("ok", "timeout", "gateway_error", "unreachable", "rate_limited")

# Concurrent `scale_*` windows below this count leave the verdict `inconclusive`.
# One overlap is an anecdote: #1231's own 79% figure came from a single pair, and a
# single clean pair is exactly as weak in the other direction.
MIN_CONCURRENT_WINDOWS = 3

# A run with fewer attempts than this has too small a denominator for its error rate
# to mean anything -- `attempted=1, errors=1` is not a 100% failure rate.
MIN_RUN_ATTEMPTS = 20

# The serial baseline from #1231: runs spaced hours apart gave ~9% errors. A
# concurrent window at or below this is evidence the budget is not needed; well
# above it reproduces the bug.
SERIAL_BASELINE_ERROR_RATE = 0.09

QUERY_POLL_SECONDS = 2
QUERY_TIMEOUT_SECONDS = 240


def _aws(args: list[str], timeout: int = 120) -> dict | list:
    cmd = ["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args[:3])}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def _window(hours: int) -> tuple[int, int, str, str]:
    """UTC epoch window, floored to the minute. Never local time.

    Same computation as residual_bands.py, and for the same reason: the window a
    result is LABELLED with is how a re-run gets compared against the right
    baseline, so it comes from one place rather than being formatted twice.
    """
    now = datetime.now(timezone.utc)
    end = now - timedelta(seconds=now.timestamp() % 60)
    start = end - timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return int(start.timestamp()), int(end.timestamp()), start.strftime(fmt), end.strftime(fmt)


def _run_query(query: str, start_epoch: int, end_epoch: int) -> list[list[dict]]:
    started = _aws(
        [
            "logs",
            "start-query",
            "--log-group-names",
            *LOG_GROUPS,
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
            # An error, never an empty result: partial rows from a timed-out query
            # would be read as a quiet week and reported as `inconclusive`, which is
            # a different and wrong answer arrived at for an invented reason.
            raise RuntimeError(f"Logs Insights query did not complete in {QUERY_TIMEOUT_SECONDS}s")
        time.sleep(QUERY_POLL_SECONDS)


# --------------------------------------------------------------------------- #
# Outcomes
# --------------------------------------------------------------------------- #

OUTCOME_QUERY = """fields @timestamp, @message
| filter @message like /\\[classifier\\]/
| parse @message "agent=* endpoint=* outcome=* status=*" as agent, endpoint, outcome, status
| stats count(*) as n by agent, outcome
| sort agent, outcome"""


def fetch_outcomes(start_epoch: int, end_epoch: int) -> dict[str, dict[str, int]]:
    rows = _run_query(OUTCOME_QUERY, start_epoch, end_epoch)
    table: dict[str, dict[str, int]] = {}
    for row in rows:
        rec = {f["field"]: f["value"] for f in row}
        agent, outcome = rec.get("agent"), rec.get("outcome")
        if not agent or not outcome:
            continue
        table.setdefault(agent, {})[outcome] = int(float(rec.get("n", 0)))
    return table


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #

RUN_QUERY = """fields @timestamp, @message
| filter @message like /AIReview:/
| sort @timestamp asc
| limit 10000"""

_RUN_ID = re.compile(r"\[AIReview:([0-9a-f]+)\]")
_RUNNING_AGENT = re.compile(r"Running agent '([^']+)' on (\d+) sessions")
_PER_TYPE = re.compile(r"Per-image-type review on (\d+) sessions: (\{.*)$", re.S)
_AGENT_ID = re.compile(r"'agent_id':\s*'([^']+)'")
_COMPLETE = re.compile(r"Complete in ([\d.]+)s: attempted=(\d+) \(passed=(\d+), failed=(\d+), errors=(\d+)\)")
_TS = "%Y-%m-%d %H:%M:%S.%f"


def _parse_agents(message: str) -> set[str]:
    """Which classifier agents a run start line says it will drive.

    Two shapes: `Running agent '<x>'` (single-agent run) and `Per-image-type review
    ... {selector: [{'agent_id': ...}]}` (the MUAC path, which names its agents
    inside a repr'd dict). The dict is parsed by REGEX over `agent_id`, not by
    ast.literal_eval of the whole structure: CloudWatch truncates very long
    messages, and a truncated repr raises rather than yielding the agent names that
    are plainly present in the intact prefix.
    """
    m = _RUNNING_AGENT.search(message)
    if m:
        return {m.group(1)}
    if "Per-image-type review" in message:
        return set(_AGENT_ID.findall(message))
    return set()


def fetch_runs(start_epoch: int, end_epoch: int) -> list[dict]:
    rows = _run_query(RUN_QUERY, start_epoch, end_epoch)

    runs: dict[str, dict] = {}
    for row in rows:
        rec = {f["field"]: f["value"] for f in row}
        message, ts_raw = rec.get("@message", ""), rec.get("@timestamp")
        match = _RUN_ID.search(message)
        if not match or not ts_raw:
            continue
        try:
            ts = datetime.strptime(ts_raw, _TS).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        run = runs.setdefault(
            match.group(1),
            {"run_id": match.group(1), "first": ts, "last": ts, "agents": set(), "summary": None},
        )
        run["first"] = min(run["first"], ts)
        run["last"] = max(run["last"], ts)
        run["agents"] |= _parse_agents(message)

        done = _COMPLETE.search(message)
        if done:
            run["summary"] = {
                "elapsed_s": float(done.group(1)),
                "attempted": int(done.group(2)),
                "passed": int(done.group(3)),
                "failed": int(done.group(4)),
                "errors": int(done.group(5)),
            }
            # The authoritative end. A run's LAST log line is usually the Complete
            # line, so first/last is normally right -- but `elapsed_s` is measured by
            # the run itself and survives a trailing line arriving late.
            run["ended"] = ts
            run["started"] = ts - timedelta(seconds=float(done.group(1)))

    out = []
    for run in runs.values():
        # A run with no Complete line either crashed or is STILL IN FLIGHT, and its
        # true end is unknown. Falling back to its last log line UNDER-states its
        # span, which under-states concurrency -- biasing toward `inconclusive`, the
        # safe direction, but the flag is carried so the reader is not told a
        # truncated span is a measured one.
        run["truncated"] = run.get("started") is None
        run.setdefault("started", run["first"])
        run.setdefault("ended", run["last"])
        run["agents"] = sorted(run["agents"])
        out.append(run)
    out.sort(key=lambda r: r["started"])
    return out


def concurrency(runs: list[dict], require: frozenset[str] | None = None) -> tuple[int, list[dict]]:
    """Peak simultaneous runs, and every window where two or more overlapped.

    A sweep over start/end events rather than pairwise comparison, so the reported
    peak is the true simultaneous maximum and not the largest overlapping PAIR --
    #1231's incident was five at once, which a pairwise read reports as 2.

    EVERY run is counted toward the load, always. There is one shared classifier
    gateway (#1231's title says so) and a `muac_*` run puts calls on it exactly like
    a `scale_*` one; the reason MUAC looks innocent is that it runs one-at-a-time,
    not that it is off the gateway. So filtering the sweep down to `scale_*` runs
    would DISCARD real saturation -- a scale run overlapping a MUAC run is up to 80
    concurrent calls against a plateau of ~20, which is the condition under test.

    `require` instead constrains which overlaps are REPORTED: a window counts only
    if at least one participant drives one of those agents. That is a statement
    about the READOUT, not the load -- `muac_*` error rates stayed ~99.9% clean
    through the worst windows, so a MUAC-only overlap loads the gateway but tells
    you nothing you can measure.

    (Both halves of this were got wrong first: the initial version scoped the sweep
    itself to `scale_*`, and on the 2026-08-31..09-07 window it reported "peak
    saturating concurrency 1, NONE" for a period that in fact contained a
    scale_dial_read run overlapping a MUAC run. Right verdict, wrong reason.)
    """
    events: list[tuple[datetime, int, str]] = []
    for run in runs:
        events.append((run["started"], 1, run["run_id"]))
        events.append((run["ended"], -1, run["run_id"]))
    # Ends before starts at an identical instant: a run ending exactly as another
    # begins is a handoff, not an overlap, and counting it as one would manufacture
    # the very windows this tool refuses to invent.
    events.sort(key=lambda e: (e[0], e[1]))

    by_id = {r["run_id"]: r for r in runs}
    live: set[str] = set()
    peak, windows, seen = 0, [], set()
    for ts, delta, run_id in events:
        if delta == 1:
            live.add(run_id)
        else:
            live.discard(run_id)
        peak = max(peak, len(live))
        if len(live) < 2:
            continue
        if require is not None and not any(set(by_id[r]["agents"]) & require for r in live):
            continue
        key = tuple(sorted(live))
        if key not in seen:
            seen.add(key)
            windows.append({"at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "n": len(live), "runs": list(key)})
    return peak, windows


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


def judge(runs: list[dict], windows: list[dict]) -> dict:
    """Name what the period showed, or refuse. Never a clean bill of health.

    The refusals come first on purpose. Each is a state in which a plausible verdict
    could be computed from the numbers present, and printing it is the failure this
    tool exists to prevent.
    """
    saturating = [r for r in runs if set(r["agents"]) & SATURATING_AGENTS]

    if not saturating:
        return {
            "verdict": "inconclusive",
            "reason": (
                f"no {'/'.join(sorted(SATURATING_AGENTS))} run executed in this period, so the "
                "gateway's saturating path was never exercised at all."
            ),
        }

    if len(windows) < MIN_CONCURRENT_WINDOWS:
        return {
            "verdict": "inconclusive",
            "reason": (
                f"{len(saturating)} saturating run(s), but only {len(windows)} window(s) with two "
                f"or more concurrently in flight (need {MIN_CONCURRENT_WINDOWS}). #1231's mechanism "
                "is concurrent runs; a serialized period cannot test it, however clean the outcome "
                "table looks. This is NOT evidence the concurrency budget is unnecessary."
            ),
        }

    concurrent_ids = {rid for w in windows for rid in w["runs"]}
    rated = [
        r
        for r in saturating
        if r["run_id"] in concurrent_ids and r.get("summary") and r["summary"]["attempted"] >= MIN_RUN_ATTEMPTS
    ]
    if not rated:
        return {
            "verdict": "inconclusive",
            "reason": (
                f"{len(windows)} concurrent window(s), but no run in them reported a completion "
                f"summary with at least {MIN_RUN_ATTEMPTS} attempts -- no error rate can be "
                "computed from a denominator that small."
            ),
        }

    attempted = sum(r["summary"]["attempted"] for r in rated)
    errors = sum(r["summary"]["errors"] for r in rated)
    rate = errors / attempted

    if rate > SERIAL_BASELINE_ERROR_RATE * 2:
        verdict, reason = "reproduced", (
            f"{errors}/{attempted} = {rate:.1%} errors across {len(rated)} run(s) that overlapped "
            f"another saturating run -- well above the ~{SERIAL_BASELINE_ERROR_RATE:.0%} serial "
            "baseline. #1231's mechanism is live; the global concurrency budget is still needed."
        )
    else:
        verdict, reason = "not_reproduced", (
            f"{errors}/{attempted} = {rate:.1%} errors across {len(rated)} run(s) that overlapped "
            f"another saturating run, at or near the ~{SERIAL_BASELINE_ERROR_RATE:.0%} serial "
            "baseline, over {n} concurrent window(s). The load WAS applied and did not reproduce "
            "the failure.".format(n=len(windows))
        )
    return {
        "verdict": verdict,
        "reason": reason,
        "concurrent_attempted": attempted,
        "concurrent_errors": errors,
        "concurrent_error_rate": round(rate, 4),
    }


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #


def render(result: dict) -> str:
    lines = [f"window: {result['window']['start']} -> {result['window']['end']}", ""]

    lines.append("classifier outcomes, per agent (both log groups):")
    header = f"  {'agent':<20}" + "".join(f"{o:>15}" for o in KNOWN_OUTCOMES) + f"{'total':>10}{'fail%':>9}"
    lines.append(header)
    if not result["outcomes"]:
        lines.append("  (no [classifier] lines in this window)")
    for agent, counts in sorted(result["outcomes"].items()):
        total = sum(counts.values())
        row = f"  {agent:<20}" + "".join(f"{counts.get(o, 0):>15}" for o in KNOWN_OUTCOMES)
        fail = (total - counts.get("ok", 0)) / total if total else 0.0
        lines.append(row + f"{total:>10}{fail:>8.2%}")
        # Surfaced, never folded into a bucket: a new error kind must not read as a
        # shift between the ones already known.
        for unknown in sorted(set(counts) - set(KNOWN_OUTCOMES)):
            lines.append(f"  {'':<20}  !! unrecognised outcome {unknown!r}: {counts[unknown]}")
    lines.append("")
    lines.append("  NOTE: no total row, deliberately. A collapse in scale_* volume improves a")
    lines.append("        blended rate on its own; that is mix, not reliability. See the docstring.")
    lines.append("")

    runs = result["runs"]
    sat = [r for r in runs if set(r["agents"]) & SATURATING_AGENTS]
    lines.append(f"runs: {len(runs)} total, {len(sat)} driving {'/'.join(sorted(SATURATING_AGENTS))}")
    # ONE peak, over all runs: they share one gateway, so load is load. What is
    # scoped is which overlaps are measurable, reported as the window list below.
    lines.append(f"  peak concurrent runs (shared gateway):   {result['peak_any']}")
    truncated = [r for r in runs if r["truncated"]]
    if truncated:
        lines.append(f"  {len(truncated)} run(s) logged no completion summary; their spans are lower bounds")
    lines.append("")

    if result["windows"]:
        lines.append("measurable concurrent windows (>=1 saturating participant):")
        for w in result["windows"]:
            lines.append(f"  {w['at']}  n={w['n']}  {', '.join(w['runs'])}")
    else:
        lines.append("measurable concurrent windows (>=1 saturating participant): NONE")
    lines.append("")

    lines.append(f"VERDICT: {result['verdict']}")
    lines.append(f"  {result['reason']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=int, default=168, help="lookback window (default 168 = 7d)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    start_epoch, end_epoch, start_str, end_str = _window(args.hours)

    outcomes = fetch_outcomes(start_epoch, end_epoch)
    runs = fetch_runs(start_epoch, end_epoch)
    peak_any, _ = concurrency(runs)
    _, windows = concurrency(runs, SATURATING_AGENTS)

    result = {
        "window": {"start": start_str, "end": end_str, "hours": args.hours},
        "outcomes": outcomes,
        "runs": [
            {
                "run_id": r["run_id"],
                "started": r["started"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ended": r["ended"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                "agents": r["agents"],
                "truncated": r["truncated"],
                "summary": r["summary"],
            }
            for r in runs
        ],
        "peak_any": peak_any,
        "windows": windows,
    }
    result.update(judge(runs, windows))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render(result))
    # Exit code carries the verdict so a caller can branch without parsing:
    # 0 not_reproduced, 1 reproduced, 2 inconclusive.
    return {"not_reproduced": 0, "reproduced": 1}.get(result["verdict"], 2)


if __name__ == "__main__":
    sys.exit(main())
