"""The verdict must not say healthy while the pager is going off.

On 2026-08-20 `perf_triage --hours 4` printed, in one output:

    VERDICT: healthy
      - ELB-generated 5xx: 418 in window. ...
    NEXT: No sustained performance problem in this window.

while `labs-jj-alb-5xx-high` -- same metric, same period -- fired five separate
times and emailed connect-labs-alerts@dimagi.com. The 5xx count was collected,
printed, and then excluded from the verdict, so the one number proving users
were being failed could not change the answer. These tests pin that it can.

The regression case matters as much as the detection case: 2026-08-19 was a
genuinely healthy day that also carried 39 ELB 5xx, and a rule that flags it
would just teach the next reviewer to ignore the verdict.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "perf_triage.py"
_spec = importlib.util.spec_from_file_location("perf_triage", _SRC)
perf_triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_triage)


def _snapshot(**overrides):
    """A quiet window, as collect() would report it. Override to taste."""
    data = dict(
        window={"period_s": 300},
        empty_metrics=[],
        web_cpu_peak_pct=55.6,
        web_cpu_saturated_periods=0,
        worker_cpu_peak_pct=21.8,
        db_cpu_peak_pct=49.0,
        db_connections_peak=52,
        alb_p95_peak_s=4.14,
        alb_p95_slow_periods=0,
        elb_5xx_total=0,
        elb_5xx_peak_period=0,
        elb_5xx_breaching_periods=0,
        web_running_count=2,
        web_desired_count=2,
    )
    data.update(overrides)
    return data


def test_breaching_5xx_is_not_healthy():
    """The 2026-08-20 shape: 429 5xx, peak 42 in a period, five over the line."""
    verdict = perf_triage.judge(
        _snapshot(
            elb_5xx_total=429,
            elb_5xx_peak_period=42,
            elb_5xx_breaching_periods=5,
            web_cpu_peak_pct=97.3,
        )
    )
    assert verdict["verdict"] == "elb_5xx_elevated"
    assert "No sustained performance problem" not in (verdict["next_step"] or "")


def test_5xx_under_the_alarm_line_stays_healthy():
    """2026-08-19: 39 5xx across a real working day, peak 7 in any period.

    A genuinely good day. Flagging it would make the verdict noise.
    """
    verdict = perf_triage.judge(_snapshot(elb_5xx_total=39, elb_5xx_peak_period=7, elb_5xx_breaching_periods=0))
    assert verdict["verdict"] == "healthy"


def test_sustained_5xx_does_not_mask_web_saturation():
    """web_saturated names the tier, which is strictly more actionable."""
    verdict = perf_triage.judge(
        _snapshot(
            elb_5xx_total=429,
            elb_5xx_peak_period=42,
            elb_5xx_breaching_periods=5,
            web_cpu_saturated_periods=6,
            web_cpu_peak_pct=99.0,
        )
    )
    assert verdict["verdict"] == "web_saturated"


def test_breaching_5xx_overrides_the_slow_dependency_inference():
    """slow_no_saturation is an inference; a failing tier is a measurement.

    This is the combination that actually occurred, and the inference pointed
    at outbound_by_host when the request split was self_ms-dominated.
    """
    verdict = perf_triage.judge(
        _snapshot(
            alb_p95_peak_s=15.5,
            alb_p95_slow_periods=1,
            elb_5xx_total=429,
            elb_5xx_peak_period=42,
            elb_5xx_breaching_periods=5,
        )
    )
    assert verdict["verdict"] == "elb_5xx_elevated"


def test_the_finding_names_the_alarm_it_agrees_with():
    """A reviewer should be able to see the tool and the pager are on the same line."""
    verdict = perf_triage.judge(_snapshot(elb_5xx_total=429, elb_5xx_peak_period=42, elb_5xx_breaching_periods=5))
    joined = " ".join(verdict["findings"])
    assert "labs-jj-alb-5xx-high" in joined
    assert str(perf_triage.ALB_5XX_PER_300S) in joined


@pytest.mark.parametrize(
    "period_s,total,breaching",
    [
        (300, 25, False),  # exactly the alarm line is not over it
        (300, 26, True),
        (900, 75, False),  # same RATE at a coarser period -- must not flag
        (900, 76, True),
        (900, 26, False),  # would have flagged against an unscaled 25
    ],
)
def test_threshold_scales_with_the_window_period(period_s, total, breaching):
    """25/5min is the alarm's unit, so a coarser period must compare like for like.

    Without scaling, every long window would flag: the raw Sum grows with the
    period while a flat 25 does not.
    """
    points = [{"Sum": total}]
    assert bool(perf_triage._breaching_5xx_periods(points, period_s)) is breaching


# --- Web CPU is read as Maximum, not Average -------------------------------
#
# 2026-09-01: `labs-jj-web-cpu-high` fired at 11:42 UTC and `perf_triage --hours 2`
# printed "VERDICT: healthy -- No sustained saturation. Web CPU peak 40.8%" over a
# window containing it. ECS reports CPUUtilization across the service's tasks, so
# one task pinned at 100% beside an idle one averages ~50%. The alarm reads
# Maximum; the tool read Average, so the pager and the verdict disagreed on the
# same metric over the same window -- the module docstring's own reason #2.


def _pinned_task_series():
    """One task pinned, the other idle: high Maximum, unremarkable Average.

    The real 11:15-11:50 UTC datapoints from the incident.
    """
    return [
        {"Timestamp": "2026-09-01T11:20:00Z", "Average": 3.2, "Maximum": 29.4},
        {"Timestamp": "2026-09-01T11:25:00Z", "Average": 10.3, "Maximum": 99.9},
        {"Timestamp": "2026-09-01T11:30:00Z", "Average": 41.3, "Maximum": 100.0},
        {"Timestamp": "2026-09-01T11:35:00Z", "Average": 21.9, "Maximum": 100.0},
        {"Timestamp": "2026-09-01T11:40:00Z", "Average": 15.2, "Maximum": 60.5},
    ]


def _fake_aws(args, record=None):
    """Only the ECS CPU series is hot -- every other tier is quiet.

    Returning the pinned series for *every* metric would also trip the DB
    connection rule, so the test would pass on judge()'s ordering rather than on
    web saturation. Keep the rest boring so only one thing can explain the verdict.
    """
    if args[0] == "cloudwatch" and args[1] == "get-metric-statistics":
        if "AWS/ECS" in args:
            return {"Datapoints": _pinned_task_series()}
        if "HTTPCode_ELB_5XX_Count" in args:
            return {"Datapoints": [{"Timestamp": "2026-09-01T11:30:00Z", "Sum": 0.0}]}
        return {
            "Datapoints": [
                {
                    "Timestamp": "2026-09-01T11:30:00Z",
                    "Average": 5.0,
                    "Maximum": 6.0,
                    "ExtendedStatistics": {"p95": 0.3},
                }
            ]
        }
    if args[0] == "cloudwatch":  # describe-alarms
        return {"MetricAlarms": []}
    return {"services": [{"desiredCount": 2, "runningCount": 2, "events": []}]}


def test_sustained_sees_a_pinned_task_only_on_maximum():
    """The mechanism, isolated: the same series is saturated on Max and quiet on Avg."""
    points = _pinned_task_series()
    on_max = perf_triage._sustained(points, perf_triage.WEB_CPU_SATURATED, 300, key="Maximum")
    on_avg = perf_triage._sustained(points, perf_triage.WEB_CPU_SATURATED, 300, key="Average")
    assert on_max == 3, "three consecutive periods >=90 on Maximum -- what the alarm fires on"
    assert on_avg == 0, "and invisible on Average, which is why this test exists"


def test_collect_asks_cloudwatch_for_maximum_on_web_cpu(monkeypatch):
    """Pin the request itself: an Average-only query cannot see a pinned task."""
    seen = []

    def fake_aws(args, timeout=120):
        seen.append(args)
        return _fake_aws(args, record=None)

    monkeypatch.setattr(perf_triage, "_aws", fake_aws)
    data = perf_triage.collect(2)

    web_cpu_calls = [a for a in seen if a[0] == "cloudwatch" and "get-metric-statistics" in a and "AWS/ECS" in a]
    assert web_cpu_calls, "expected an ECS CPUUtilization query"
    for call in web_cpu_calls:
        assert "Maximum" in call, "ECS CPU must be requested with Maximum -- the alarm's statistic"

    assert data["web_cpu_saturated_periods"] == 3
    assert data["web_cpu_peak_pct"] == 100.0
    assert data["web_cpu_avg_peak_pct"] == 41.3


def test_verdict_is_not_healthy_while_a_task_is_pinned(monkeypatch):
    """The whole point: the pager is going off, so the verdict may not say healthy."""

    def fake_aws(args, timeout=120):
        return _fake_aws(args)

    monkeypatch.setattr(perf_triage, "_aws", fake_aws)
    verdict = perf_triage.judge(perf_triage.collect(2))

    assert verdict["verdict"] == "web_saturated"
    joined = " ".join(verdict["findings"])
    assert "ONE TASK PINNED" in joined, "a single pinned task must be named as such, not as a hot tier"
    assert "100.0%" in joined and "41.3%" in joined, "show both numbers so the shape is checkable"


def test_window_is_floored_to_the_period():
    """An unaligned window shifts every bucket and can split a sustained run.

    CloudWatch buckets from the start time, so the boundaries -- and therefore
    whether 15 pinned minutes read as one run or two blips -- would otherwise
    depend on the minute the tool was run.
    """
    for period in (300, 900, 3600):
        start, end = perf_triage._window(2, period)
        for stamp in (start, end):
            dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            assert dt.timestamp() % period == 0, f"{stamp} is not aligned to a {period}s boundary"


def test_window_alignment_keeps_a_pinned_run_intact():
    """The concrete 2026-09-01 case: aligned buckets see one run, offset ones see two."""
    aligned = [
        {"Timestamp": "2026-09-01T11:25:00Z", "Maximum": 99.9},
        {"Timestamp": "2026-09-01T11:30:00Z", "Maximum": 100.0},
        {"Timestamp": "2026-09-01T11:35:00Z", "Maximum": 100.0},
    ]
    offset = [
        {"Timestamp": "2026-09-01T11:29:05Z", "Maximum": 100.0},
        {"Timestamp": "2026-09-01T11:34:05Z", "Maximum": 88.2},
        {"Timestamp": "2026-09-01T11:39:05Z", "Maximum": 100.0},
    ]
    thresh = perf_triage.WEB_CPU_SATURATED
    assert perf_triage._sustained(aligned, thresh, 300, key="Maximum") == 3
    assert perf_triage._sustained(offset, thresh, 300, key="Maximum") == 0
