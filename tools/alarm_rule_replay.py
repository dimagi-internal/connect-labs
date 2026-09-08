#!/usr/bin/env python3
"""Replay candidate CloudWatch alarm rules against real per-minute history.

    python3 tools/alarm_rule_replay.py --metric web-cpu --days 9
    python3 tools/alarm_rule_replay.py --metric web-cpu --days 9 --json
    python3 tools/alarm_rule_replay.py --metric web-cpu --days 9 \
        --rules 3of5@300 12of15@60 --threshold 90

Answers one question: "if the alarm had been configured as M-of-N at period P,
how often would it have fired, and were those firings real?"  Tuning an alarm by
argument produces a plausible number; replaying it over the history the alarm
actually saw produces a checkable one.  Both #1418/#1419 (the 3-of-5 change) and
#1578 (the kill-rate `>=2 in 10min` proposal) were argued this way by hand, and
the hand version was rebuilt from scratch each time.

Why this is code and not a list of commands in the runbook: four steps of that
replay silently return a WRONG answer rather than an error --

  1. `aws --output json` renders Timestamp in the CALLER'S LOCAL ZONE while
     --start-date/--end-date are UTC.  Slice the string (a natural `[:19]`) and
     the offset is dropped silently, shifting every row by the UTC offset -- six
     hours on a US/Mountain laptop.  Nothing errors; the timeline is simply
     wrong, and it is wrong in the direction that makes an incident look like it
     preceded its own cause.  Timestamps here are parsed, never sliced.

  2. `describe-alarm-history` on a COMPOSITE alarm returns an empty list unless
     you pass `--alarm-types CompositeAlarm`.  Zero rows for an alarm that
     demonstrably paged reads as "it never fired" -- the most misleading
     possible output -- so composites are queried with the flag, always.

  3. Cross-referencing firings against `labs-jj-deploy-in-progress` is VACUOUS
     before that alarm existed (created 2026-09-06T14:02:42Z; see #1463).  A
     naive join labels every pre-creation firing "no-deploy", which is not a
     measurement -- it is the absence of one, wearing the same label as a real
     negative.  Windows before the suppressor's creation are reported as
     `unknown`, and the summary refuses to total them.

  4. GetMetricStatistics caps a response at 1440 datapoints, so a 60-second
     period hard-fails past 24h.  The window is chunked.

CHOOSING A RULE.  Read the run-length distribution before the firing counts.
A duration rule can only separate two populations if their consecutive-run
lengths differ; if the tier emits mostly 1-minute spikes, `Maximum` over a long
Period cannot tell one spike from a sustained pin no matter what M-of-N you pick,
because a single breaching minute marks the whole bucket.  That is a statement
about the instrument, and no threshold search fixes it.

CAVEAT the summary cannot enforce: a rule that fires only on the incidents you
already know about is fitted to them.  Firing counts are evidence about NOISE
(how often it would page when nothing was wrong); they are only evidence about
DETECTION for incidents independently established as real.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# Created by #1463. Any deploy cross-reference before this instant is unknowable,
# not negative -- see trap 3 in the module docstring.
SUPPRESSOR_ALARM = "labs-jj-deploy-in-progress"
SUPPRESSOR_CREATED = datetime(2026, 9, 6, 14, 2, 42, tzinfo=timezone.utc)

METRICS = {
    "web-cpu": dict(
        namespace="AWS/ECS",
        metric="CPUUtilization",
        dimensions=["Name=ClusterName,Value=labs-jj-cluster", "Name=ServiceName,Value=labs-jj-web"],
        statistic="Maximum",
        threshold=90.0,
        alarm="labs-jj-web-cpu-high",
        composite="labs-jj-web-cpu-high-actionable",
    ),
    "worker-cpu": dict(
        namespace="AWS/ECS",
        metric="CPUUtilization",
        dimensions=["Name=ClusterName,Value=labs-jj-cluster", "Name=ServiceName,Value=labs-jj-worker"],
        statistic="Maximum",
        threshold=90.0,
        alarm=None,
        composite=None,
    ),
}

DEFAULT_RULES = ["3of5@300", "5of10@60", "8of15@60", "10of15@60", "12of15@60", "15of20@60"]


def _aws(args: list[str], profile: str) -> dict:
    proc = subprocess.run(
        ["aws", *args, "--profile", profile, "--output", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(f"aws {args[0]} {args[1]} failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout or "{}")


def _utc(raw: str) -> datetime:
    """Parse an AWS timestamp to UTC. Never slice these strings -- see trap 1."""
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_series(spec: dict, start: datetime, end: datetime, profile: str) -> dict[datetime, float]:
    """Per-minute maxima, chunked under the 1440-datapoint cap (trap 4)."""
    series: dict[datetime, float] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(hours=20), end)
        data = _aws(
            [
                "cloudwatch",
                "get-metric-statistics",
                "--namespace",
                spec["namespace"],
                "--metric-name",
                spec["metric"],
                "--dimensions",
                *spec["dimensions"],
                "--start-time",
                _iso(cursor),
                "--end-time",
                _iso(chunk_end),
                "--period",
                "60",
                "--statistics",
                spec["statistic"],
            ],
            profile,
        )
        for point in data.get("Datapoints", []):
            stamp = _utc(point["Timestamp"]).replace(second=0, microsecond=0)
            series[stamp] = point[spec["statistic"]]
        cursor = chunk_end
    return series


def fetch_windows(
    alarm: str, start: datetime, end: datetime, profile: str, composite: bool = False
) -> list[tuple[datetime, datetime]]:
    """ALARM intervals for `alarm`. Composites need --alarm-types (trap 2)."""
    args = [
        "cloudwatch",
        "describe-alarm-history",
        "--alarm-name",
        alarm,
        "--history-item-type",
        "StateUpdate",
        "--start-date",
        _iso(start),
        "--end-date",
        _iso(end),
        "--max-items",
        "1000",
    ]
    if composite:
        args += ["--alarm-types", "CompositeAlarm"]
    events = []
    for item in _aws(args, profile).get("AlarmHistoryItems", []):
        match = re.search(r"from (\S+) to (\S+)", item["HistorySummary"])
        if match:
            events.append((_utc(item["Timestamp"]), match.group(2)))
    events.sort()
    windows, opened = [], None
    for stamp, state in events:
        if state == "ALARM" and opened is None:
            opened = stamp
        elif state == "OK" and opened is not None:
            windows.append((opened, stamp))
            opened = None
    if opened is not None:
        windows.append((opened, end))
    return windows


@dataclass
class Run:
    start: datetime
    end: datetime
    minutes: int


@dataclass
class Rule:
    m: int
    n: int
    period: int
    fires: list[datetime] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.m}of{self.n}@{self.period}s"


def parse_rule(text: str) -> Rule:
    match = re.fullmatch(r"(\d+)of(\d+)@(\d+)", text.strip())
    if not match:
        sys.exit(f"bad rule {text!r}; expected e.g. 12of15@60")
    m, n, period = (int(g) for g in match.groups())
    if m > n:
        sys.exit(f"bad rule {text!r}: M ({m}) cannot exceed N ({n})")
    if period % 60:
        sys.exit(f"bad rule {text!r}: period must be a multiple of 60")
    return Rule(m, n, period)


def bucketise(series: dict[datetime, float], period: int) -> list[tuple[datetime, float]]:
    """Collapse per-minute maxima into `period`-second buckets, taking the max --
    which is exactly what Statistic: Maximum does, and exactly why one breaching
    minute marks a whole bucket."""
    if period == 60:
        return sorted(series.items())
    span = period // 60
    buckets: dict[datetime, float] = {}
    for stamp, value in series.items():
        anchor = stamp - timedelta(minutes=stamp.minute % span, seconds=0)
        anchor = anchor.replace(second=0, microsecond=0)
        buckets[anchor] = max(buckets.get(anchor, float("-inf")), value)
    return sorted(buckets.items())


def replay(rule: Rule, series: dict[datetime, float], threshold: float) -> Rule:
    """Fire when M of the last N buckets breach. One continuous condition counts
    once -- re-arming only after the condition lapses -- so the count is
    incidents-paged, not transitions."""
    points = bucketise(series, rule.period)
    armed = False
    for index in range(len(points)):
        window = points[max(0, index - rule.n + 1) : index + 1]
        if len(window) < rule.n:
            continue
        breaching = sum(1 for _, value in window if value >= threshold)
        if breaching >= rule.m:
            if not armed:
                rule.fires.append(points[index][0])
                armed = True
        else:
            armed = False
    return rule


def consecutive_runs(series: dict[datetime, float], threshold: float) -> list[Run]:
    runs: list[Run] = []
    start = previous = None
    for stamp in sorted(series):
        breaching = series[stamp] >= threshold
        contiguous = previous is not None and (stamp - previous) == timedelta(minutes=1)
        if breaching and (start is None or not contiguous):
            if start is not None:
                runs.append(Run(start, previous, int((previous - start).total_seconds() // 60) + 1))
            start = stamp
        elif not breaching and start is not None:
            runs.append(Run(start, previous, int((previous - start).total_seconds() // 60) + 1))
            start = None
        previous = stamp
    if start is not None:
        runs.append(Run(start, previous, int((previous - start).total_seconds() // 60) + 1))
    return runs


def classify(stamp: datetime, windows: list[tuple[datetime, datetime]]) -> str:
    """deploy | no-deploy | unknown. `unknown` before the suppressor existed --
    the distinction trap 3 exists to preserve."""
    if stamp < SUPPRESSOR_CREATED:
        return "unknown"
    return "deploy" if any(a <= stamp <= b for a, b in windows) else "no-deploy"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--metric", choices=sorted(METRICS), default="web-cpu")
    parser.add_argument("--days", type=float, default=9.0, help="lookback; 60s data is retained ~15 days")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--rules", nargs="+", default=DEFAULT_RULES)
    parser.add_argument("--profile", default="labs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    spec = METRICS[args.metric]
    threshold = args.threshold if args.threshold is not None else spec["threshold"]
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=args.days)

    series = fetch_series(spec, start, end, args.profile)
    if not series:
        sys.exit(
            f"no datapoints for {args.metric} over {args.days}d -- past retention, "
            f"or the dimensions in METRICS[{args.metric!r}] no longer match the service"
        )
    windows = fetch_windows(SUPPRESSOR_ALARM, start, end, args.profile)
    runs = sorted(consecutive_runs(series, threshold), key=lambda r: -r.minutes)
    rules = [replay(parse_rule(text), series, threshold) for text in args.rules]

    covered = max(start, SUPPRESSOR_CREATED)
    payload = {
        "metric": args.metric,
        "threshold": threshold,
        "window": {"start": _iso(start), "end": _iso(end), "datapoints": len(series)},
        "deploy_labels_valid_from": _iso(covered) if covered < end else None,
        "runs": {
            "total": len(runs),
            "one_minute": sum(1 for r in runs if r.minutes == 1),
            "over_three_minutes": sum(1 for r in runs if r.minutes > 3),
            "longest": [
                {"start": _iso(r.start), "minutes": r.minutes, "deploy": classify(r.start, windows)} for r in runs[:10]
            ],
        },
        "rules": [
            {
                "rule": r.label,
                "fires": len(r.fires),
                "by_deploy_state": {
                    state: sum(1 for f in r.fires if classify(f, windows) == state)
                    for state in ("deploy", "no-deploy", "unknown")
                },
                "firings": [{"at": _iso(f), "deploy": classify(f, windows)} for f in r.fires][:25],
            }
            for r in rules
        ],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print(f"{args.metric}  threshold >={threshold}  {_iso(start)} -> {_iso(end)}")
    print(f"{len(series)} one-minute datapoints; {len(windows)} deploy windows")
    if start < SUPPRESSOR_CREATED:
        print(
            f"NOTE: deploy labels are 'unknown' before {_iso(SUPPRESSOR_CREATED)} "
            f"({SUPPRESSOR_ALARM} did not exist) -- not 'no-deploy'."
        )

    print(
        f"\nconsecutive runs >={threshold}%: {len(runs)} total, "
        f"{payload['runs']['one_minute']} of them 1 minute, "
        f"{payload['runs']['over_three_minutes']} longer than 3 minutes"
    )
    for run in runs[:10]:
        print(
            f"  {run.start:%m-%d %H:%M}Z -> {run.end:%H:%M}Z  {run.minutes:3d} min  " f"{classify(run.start, windows)}"
        )

    print(f"\n{'rule':<16}{'fires':>7}{'deploy':>9}{'no-deploy':>11}{'unknown':>9}")
    for rule in rules:
        counts = {
            s: sum(1 for f in rule.fires if classify(f, windows) == s) for s in ("deploy", "no-deploy", "unknown")
        }
        print(
            f"{rule.label:<16}{len(rule.fires):>7}{counts['deploy']:>9}"
            f"{counts['no-deploy']:>11}{counts['unknown']:>9}"
        )
        if len(rule.fires) <= 12:
            for fire in rule.fires:
                print(f"    {fire:%m-%d %H:%M}Z  {classify(fire, windows)}")

    print(
        "\nA rule that fires only on incidents you already knew about is fitted to them.\n"
        "Firing counts are evidence about NOISE; detection needs independently-real incidents."
    )


if __name__ == "__main__":
    main()
