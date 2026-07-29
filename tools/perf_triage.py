#!/usr/bin/env python3
"""One-command performance triage for connect-labs. Built for agents, not humans.

    python3 tools/perf_triage.py --hours 3            # verdict + evidence
    python3 tools/perf_triage.py --hours 3 --json     # machine-readable

Why this exists rather than a list of commands in a doc: the 2026-07-29 incident
was diagnosed by hand, and three of the steps have traps that silently return a
WRONG answer rather than an error --

  1. The AWS CLI returns datapoints in LOCAL time while --start-time/--end-time
     are UTC. Mix them and you confidently read the wrong window.
  2. Averages hide outages. ALB Average latency stayed unremarkable through 54
     minutes of total saturation; only p95 showed it.
  3. filter-log-events paginates, so a truncated read looks like a quiet system.

Each is absorbed here so the answer is not a function of who ran it.

The verdict names the TIER, because that is the fork that matters: web-vs-worker
CPU resolves "request path or background job" in one comparison, and starting at
the database -- which is almost always healthy -- is the single most expensive
wrong turn available.

Read-only. Needs AWS profile `labs` (account 858923557655).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

PROFILE = "labs"
REGION = "us-east-1"
CLUSTER = "labs-jj-cluster"
WEB_SERVICE = "labs-jj-web"
WORKER_SERVICE = "labs-jj-worker"
DB_INSTANCE = "labs-jj-postgres"
ALB_DIMENSION = "app/labs-jj-alb/ffecbe258260c7ee"

# Baselines measured over the 5 days to 2026-07-29. Anything here is a
# "meaningfully above normal" line, not a guess.
BASELINE = {
    "web_cpu_median_pct": 0.8,
    "alb_p95_median_s": 0.23,
    "db_connections_normal": "5-15",
    "db_connection_slots": 155,
    "db_cpu_normal_pct": "7-12",
}
WEB_CPU_SATURATED = 90.0
ALB_P95_SLOW_S = 10.0
DB_CONNECTIONS_HIGH = 90
DB_CPU_HIGH = 50.0


def _aws(args: list[str], timeout: int = 120) -> dict | list:
    cmd = ["aws", "--profile", PROFILE, "--region", REGION, *args, "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args[:3])}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "{}")


def _window(hours: int) -> tuple[str, str]:
    """UTC window with an explicit Z. Never build these from local time."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def _metric(namespace, name, dimensions, start, end, period=300, stats=None, ext=None):
    args = [
        "cloudwatch",
        "get-metric-statistics",
        "--namespace",
        namespace,
        "--metric-name",
        name,
        "--start-time",
        start,
        "--end-time",
        end,
        "--period",
        str(period),
    ]
    # ONE --dimensions flag carrying every pair. Repeating the flag makes the
    # last occurrence win, so an ECS query silently loses ClusterName, matches
    # no metric, and returns [] -- which reads as "the service was idle" rather
    # than as an error. Single-dimension metrics (RDS, ALB) work either way,
    # which is what hides the bug.
    if dimensions:
        args += ["--dimensions", *(f"Name={k},Value={v}" for k, v in dimensions.items())]
    if ext:
        args += ["--extended-statistics", *ext]
    else:
        args += ["--statistics", *(stats or ["Average"])]
    pts = _aws(args).get("Datapoints", [])
    return sorted(pts, key=lambda p: p["Timestamp"])


def _peak(points, key="Average"):
    vals = [p[key] for p in points if key in p]
    return max(vals) if vals else None


def _peak_ext(points, stat):
    vals = [p["ExtendedStatistics"][stat] for p in points if "ExtendedStatistics" in p]
    return max(vals) if vals else None


def _sustained(points, threshold, consecutive=3, key="Average"):
    """Longest run of consecutive periods at/above threshold.

    Sustained runs, not peaks: a single spike is a deploy or a cron tick, while
    a run of periods is the outage as a user experienced it. This is the same
    reason the audit-trail review ranks failure STREAKS over failure rates.
    """
    run = best = 0
    for p in points:
        if p.get(key, 0) >= threshold:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best if best >= consecutive else 0


def collect(hours: int) -> dict:
    start, end = _window(hours)
    ecs_dims = lambda svc: {"ClusterName": CLUSTER, "ServiceName": svc}  # noqa: E731

    web_cpu = _metric("AWS/ECS", "CPUUtilization", ecs_dims(WEB_SERVICE), start, end)
    worker_cpu = _metric("AWS/ECS", "CPUUtilization", ecs_dims(WORKER_SERVICE), start, end)
    db_cpu = _metric("AWS/RDS", "CPUUtilization", {"DBInstanceIdentifier": DB_INSTANCE}, start, end)
    db_conns = _metric(
        "AWS/RDS",
        "DatabaseConnections",
        {"DBInstanceIdentifier": DB_INSTANCE},
        start,
        end,
        stats=["Average", "Maximum"],
    )
    alb_p95 = _metric(
        "AWS/ApplicationELB",
        "TargetResponseTime",
        {"LoadBalancer": ALB_DIMENSION},
        start,
        end,
        ext=["p95"],
    )
    elb_5xx = _metric(
        "AWS/ApplicationELB",
        "HTTPCode_ELB_5XX_Count",
        {"LoadBalancer": ALB_DIMENSION},
        start,
        end,
        stats=["Sum"],
    )

    alarms = [
        {"name": a["AlarmName"], "state": a["StateValue"]}
        for a in _aws(["cloudwatch", "describe-alarms"]).get("MetricAlarms", [])
        if a["AlarmName"].startswith("labs-jj")
    ]
    svc = _aws(["ecs", "describe-services", "--cluster", CLUSTER, "--services", WEB_SERVICE])
    service = svc.get("services", [{}])[0]

    # An empty series is never good news: it means the dimensions did not match
    # a real metric, not that the tier was quiet. Tracked explicitly so a broken
    # query can never masquerade as a clean bill of health.
    empty = [
        label
        for label, pts in (
            ("web_cpu", web_cpu),
            ("worker_cpu", worker_cpu),
            ("db_cpu", db_cpu),
            ("db_connections", db_conns),
            ("alb_p95", alb_p95),
        )
        if not pts
    ]

    return {
        "window": {"start": start, "end": end, "hours": hours},
        "empty_metrics": empty,
        "web_cpu_peak_pct": _peak(web_cpu),
        "web_cpu_saturated_periods": _sustained(web_cpu, WEB_CPU_SATURATED),
        "worker_cpu_peak_pct": _peak(worker_cpu),
        "db_cpu_peak_pct": _peak(db_cpu),
        "db_connections_peak": _peak(db_conns, "Maximum"),
        "alb_p95_peak_s": _peak_ext(alb_p95, "p95"),
        "alb_p95_slow_periods": len(
            [p for p in alb_p95 if p.get("ExtendedStatistics", {}).get("p95", 0) > ALB_P95_SLOW_S]
        ),
        "elb_5xx_total": sum(p.get("Sum", 0) for p in elb_5xx),
        "alarms_firing": [a["name"] for a in alarms if a["state"] == "ALARM"],
        "alarms_all": alarms,
        "web_desired_count": service.get("desiredCount"),
        "web_running_count": service.get("runningCount"),
        "recent_ecs_events": [e["message"] for e in service.get("events", [])[:5]],
    }


def judge(d: dict) -> dict:
    """Name the tier and the next action. Ordered by which fork matters most."""
    findings, verdict, next_step = [], "healthy", None
    web = d["web_cpu_peak_pct"] or 0
    worker = d["worker_cpu_peak_pct"] or 0

    if d["empty_metrics"]:
        return {
            "verdict": "inconclusive",
            "findings": [
                "NO DATA for: " + ", ".join(d["empty_metrics"]) + ". "
                "A CloudWatch series is empty, which means the query did not match a real "
                "metric -- NOT that the tier was idle. Do not report healthy from this run."
            ],
            "next_step": (
                "Verify the dimensions match a live metric: "
                "aws cloudwatch list-metrics --namespace AWS/ECS "
                "--metric-name CPUUtilization --profile labs --region us-east-1"
            ),
        }

    if d["web_cpu_saturated_periods"]:
        mins = d["web_cpu_saturated_periods"] * 5
        findings.append(
            f"WEB TIER SATURATED: >={WEB_CPU_SATURATED}% CPU for >={mins} consecutive minutes "
            f"(peak {web:.1f}%). Worker peaked at {worker:.1f}% -- "
            + (
                "worker is idle, so this is a REQUEST PATH, not a Celery job."
                if worker < 50
                else "worker is ALSO busy; check both."
            )
        )
        verdict = "web_saturated"
        next_step = (
            "Logs Insights on /ecs/labs-jj-web: "
            "fields @timestamp, path, duration_ms, outbound_calls, db_queries, reason "
            "| filter ispresent(outbound_calls) | sort duration_ms desc | limit 50 "
            "-- branch on `reason`; outbound_fanout means a loop calling a remote API per item."
        )
    elif worker >= WEB_CPU_SATURATED and web < 50:
        findings.append(
            f"WORKER SATURATED (peak {worker:.1f}%) while web peaked at {web:.1f}%. "
            "A Celery task, not a request path."
        )
        verdict = "worker_saturated"
        next_step = "Inspect /ecs/labs-jj-worker for the running task; the web tier is collateral."

    p95 = d["alb_p95_peak_s"]
    if p95 and p95 > ALB_P95_SLOW_S:
        findings.append(
            f"USER-VISIBLE LATENCY: ALB p95 peaked at {p95:.1f}s across "
            f"{d['alb_p95_slow_periods']} period(s); median p95 is normally "
            f"{BASELINE['alb_p95_median_s']}s."
        )
        if verdict == "healthy":
            verdict = "slow_no_saturation"
            next_step = (
                "High latency without CPU saturation implies a slow dependency. "
                "Check outbound_by_host in the telemetry stream."
            )

    conns = d["db_connections_peak"]
    if conns and conns >= DB_CONNECTIONS_HIGH:
        db_cpu = d["db_cpu_peak_pct"] or 0
        symptom = db_cpu < DB_CPU_HIGH
        findings.append(
            f"DB CONNECTIONS at {conns:.0f} of ~{BASELINE['db_connection_slots']} slots "
            f"(normal {BASELINE['db_connections_normal']}), DB CPU peak {db_cpu:.1f}%. "
            + (
                "DB CPU is low, so this is a SYMPTOM of requests holding connections -- "
                "fix the request path, not the database."
                if symptom
                else "DB CPU is also high; the database is genuinely loaded."
            )
        )
        if verdict == "healthy":
            verdict = "db_connection_pressure"
            next_step = "Find the slow requests holding connections (§3), not the database."

    if d["elb_5xx_total"]:
        findings.append(
            f"ELB-generated 5xx: {d['elb_5xx_total']:.0f} in window. The ALB could not get a "
            "usable response (distinct from target 5xx, which means the app answered with an error)."
        )

    if d["web_running_count"] != d["web_desired_count"]:
        findings.append(f"CAPACITY: {d['web_running_count']} of {d['web_desired_count']} web tasks running.")

    if verdict == "healthy":
        # Distinguish a transient peak from sustained saturation explicitly. A
        # single 5-minute period at 100% is a task start or a deploy; only a run
        # of periods is an outage. Reporting the bare peak next to "no
        # saturation" reads as a contradiction and invites the wrong conclusion.
        peak_note = (
            f"Web CPU peaked at {web:.1f}% but never for {3 * 5} consecutive minutes "
            f"(transient -- task start, deploy, or a single heavy request)"
            if web >= WEB_CPU_SATURATED
            else f"Web CPU peak {web:.1f}% (median baseline {BASELINE['web_cpu_median_pct']}%)"
        )
        conn_note = (
            f"DB connections peaked at {conns:.0f} of ~{BASELINE['db_connection_slots']} slots "
            f"-- above the {BASELINE['db_connections_normal']} baseline but under the "
            f"{DB_CONNECTIONS_HIGH} alarm line; worth a second look if it recurs"
            if conns and conns > 30
            else f"DB connections peak {conns if conns is not None else 0:.0f}"
        )
        findings.append(
            f"No sustained saturation. {peak_note}, ALB p95 peak "
            f"{p95 if p95 is not None else 0:.2f}s. {conn_note}."
        )
        next_step = (
            "No sustained performance problem in this window. Widen --hours before concluding "
            "it never happened -- a window that starts after an incident ended looks clean."
        )

    return {"verdict": verdict, "findings": findings, "next_step": next_step}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=3, help="lookback window (default 3)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    try:
        data = collect(args.hours)
    except Exception as exc:
        print(f"triage failed: {exc}", file=sys.stderr)
        return 2

    result = judge(data)

    if args.json:
        print(json.dumps({**result, "evidence": data}, indent=2, default=str))
        return 0

    print(f"\nVERDICT: {result['verdict']}   (window: last {args.hours}h, UTC)\n")
    for f in result["findings"]:
        print(f"  - {f}")
    print(f"\nNEXT: {result['next_step']}\n")
    if data["alarms_firing"]:
        print(f"  alarms firing: {', '.join(data['alarms_firing'])}")
    else:
        print(f"  alarms: all {len(data['alarms_all'])} OK")
    print(f"  web tasks: {data['web_running_count']}/{data['web_desired_count']} running\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
