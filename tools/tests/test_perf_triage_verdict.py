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
import json
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
        target_5xx_total=0,
        target_5xx_peak_period=0,
        target_5xx_breaching_periods=0,
        web_running_count=2,
        web_desired_count=2,
    )
    data.update(overrides)
    return data


def test_a_page_below_the_sustained_line_is_not_healthy():
    """2026-11-03: a 4-minute pin paged, and the tool printed healthy, "all 17 OK"."""
    verdict = perf_triage.judge(
        _snapshot(
            web_cpu_peak_pct=100.0,
            paged_in_window=[{"name": "labs-jj-web-cpu-high-actionable", "at": "2026-11-03T12:02:42Z"}],
        )
    )
    assert verdict["verdict"] == "paged_below_sustained"
    assert "labs-jj-web-cpu-high-actionable at 2026-11-03T12:02:42Z" in verdict["findings"][0]
    assert "No sustained performance problem" not in verdict["next_step"]


def test_a_page_does_not_mask_a_named_tier():
    verdict = perf_triage.judge(
        _snapshot(
            web_cpu_peak_pct=99.0,
            web_cpu_saturated_periods=6,
            paged_in_window=[{"name": "labs-jj-web-cpu-high-actionable", "at": "2026-11-03T12:02:42Z"}],
        )
    )
    assert verdict["verdict"] == "web_saturated"


def test_no_page_and_no_saturation_stays_healthy():
    assert perf_triage.judge(_snapshot(paged_in_window=[]))["verdict"] == "healthy"


# The real 2026-11-05 08:51Z page (#2872): the CLI renders times in the caller's offset.
_NOV5_ACTIVITIES = [
    {
        "StartTime": "2026-11-05T01:46:37.350000-07:00",
        "Description": "Setting desired count to 4.",
        "Cause": "monitor alarm TargetTracking-x-AlarmHigh-02 in state ALARM triggered policy labs-jj-web-mem70",
    },
    {
        "StartTime": "2026-11-05T01:43:37.378000-07:00",
        "Description": "Setting desired count to 3.",
        "Cause": "monitor alarm TargetTracking-x-AlarmHigh-02 in state ALARM triggered policy labs-jj-web-mem70",
    },
    {
        "StartTime": "2026-11-03T18:39:41.939000-07:00",
        "Description": "Setting desired count to 2.",
        "Cause": "monitor alarm TargetTracking-x-AlarmLow-e3 in state ALARM triggered policy labs-jj-web-cpu60",
    },
]
_NOV5_PAGE = {"name": "labs-jj-web-cpu-high-actionable", "at": "2026-11-05T08:51:25Z"}


def _nov5_scale_outs():
    return perf_triage._scale_outs_from_activities(_NOV5_ACTIVITIES, datetime(2026, 11, 4, tzinfo=timezone.utc))


def _cpu_minutes(values):
    return [{"Timestamp": f"2026-11-05T01:{minute}:00-07:00", "Maximum": value} for minute, value in values.items()]


def test_scale_outs_are_utc_oldest_first_and_exclude_scale_ins():
    assert _nov5_scale_outs() == [
        {"at": "2026-11-05T08:43:37Z", "to": 3, "policy": "labs-jj-web-mem70"},
        {"at": "2026-11-05T08:46:37Z", "to": 4, "policy": "labs-jj-web-mem70"},
    ]


def test_scale_outs_before_since_are_dropped():
    since = datetime(2026, 11, 5, 8, 45, tzinfo=timezone.utc)
    assert [row["to"] for row in perf_triage._scale_outs_from_activities(_NOV5_ACTIVITIES, since)] == [4]


def test_two_boots_three_minutes_apart_are_recognised():
    points = _cpu_minutes({"44": 100.0, "45": 100.0, "46": 2.0, "47": 100.0, "48": 100.0, "49": 14.0})
    shape = perf_triage._boot_shape(_NOV5_PAGE, points, _nov5_scale_outs())
    assert shape["breaching_minutes"] == ["08:44", "08:45", "08:47", "08:48"]
    assert [row["to"] for row in shape["scale_outs"]] == [3, 4]


def test_one_breaching_minute_outside_a_boot_means_a_real_pin():
    points = _cpu_minutes({"44": 100.0, "45": 100.0, "50": 95.0})
    assert perf_triage._boot_shape(_NOV5_PAGE, points, _nov5_scale_outs()) is None


def test_a_page_with_no_scale_out_is_not_boot_shaped():
    """2026-11-03 12:02Z: a real pin from a request, with no scale-out anywhere near."""
    assert perf_triage._boot_shape(_NOV5_PAGE, _cpu_minutes({"44": 100.0, "45": 100.0}), []) is None


def test_no_breaching_minute_is_not_boot_shaped():
    assert perf_triage._boot_shape(_NOV5_PAGE, _cpu_minutes({"44": 12.0}), _nov5_scale_outs()) is None


def _boot_snapshot(**overrides):
    shape = {
        "name": _NOV5_PAGE["name"],
        "at": _NOV5_PAGE["at"],
        "breaching_minutes": ["08:44", "08:45", "08:47", "08:48"],
        "scale_outs": _nov5_scale_outs(),
    }
    return _snapshot(
        web_cpu_peak_pct=100.0,
        paged_in_window=[_NOV5_PAGE],
        boot_shaped_pages=[shape],
        scale_outs=_nov5_scale_outs(),
        **overrides,
    )


def test_a_page_that_is_all_boots_gets_its_own_verdict():
    verdict = perf_triage.judge(_boot_snapshot())
    assert verdict["verdict"] == "paged_by_task_boots"
    assert any(f.startswith("BOOT-SHAPED PAGE: labs-jj-web-cpu-high-actionable") for f in verdict["findings"])
    assert "do not hunt a request path" in verdict["next_step"]


def test_a_second_unexplained_page_keeps_the_general_verdict():
    snapshot = _boot_snapshot()
    snapshot["paged_in_window"] = [_NOV5_PAGE, {"name": _NOV5_PAGE["name"], "at": "2026-11-05T07:00:00Z"}]
    assert perf_triage.judge(snapshot)["verdict"] == "paged_below_sustained"


def test_boots_do_not_mask_a_named_tier():
    assert perf_triage.judge(_boot_snapshot(web_cpu_saturated_periods=6))["verdict"] == "web_saturated"


def test_scale_outs_are_listed_under_any_verdict():
    verdict = perf_triage.judge(_snapshot(paged_in_window=[], scale_outs=_nov5_scale_outs()))
    assert verdict["verdict"] == "healthy"
    assert "SCALE-OUT: web to 3 tasks at 2026-11-05T08:43:37Z (labs-jj-web-mem70)." in verdict["findings"]


def test_a_failed_scale_out_read_is_not_silence():
    verdict = perf_triage.judge(_snapshot(scale_outs_error="AccessDenied"))
    assert any(f.startswith("SCALE-OUTS UNREAD: AccessDenied") for f in verdict["findings"])


def test_only_alarms_that_page_count_and_times_become_utc():
    """The raw child has no actions; the CLI prints local offsets."""
    items = [
        {
            "AlarmName": "labs-jj-web-cpu-high-actionable",
            "Timestamp": "2026-11-03T05:02:42.842000-07:00",
            "HistorySummary": "Alarm updated from OK to ALARM",
        },
        {
            "AlarmName": "labs-jj-web-cpu-high-actionable",
            "Timestamp": "2026-11-03T05:04:42.842000-07:00",
            "HistorySummary": "Alarm updated from ALARM to OK",
        },
        {
            "AlarmName": "labs-jj-web-cpu-high",
            "Timestamp": "2026-11-03T05:02:42.000000-07:00",
            "HistorySummary": "Alarm updated from OK to ALARM",
        },
    ]
    assert perf_triage._paged_from_history(items, {"labs-jj-web-cpu-high-actionable"}) == [
        {"name": "labs-jj-web-cpu-high-actionable", "at": "2026-11-03T12:02:42Z"}
    ]


def _slow_line(ended_ms, duration_ms, cpu_ms, route="labs/x/"):
    message = json.dumps(
        {
            "event": "slow_request",
            "route": route,
            "status": 500,
            "duration_ms": duration_ms,
            "cpu_ms": cpu_ms,
            "db_ms": 1000,
            "db_queries": 7,
            "outbound_ms": 2000,
            "outbound_calls": 3,
        }
    )
    return {"timestamp": ended_ms, "message": message}


def test_heaviest_requests_rank_by_cpu_and_report_when_they_started():
    """The line is written at the END, so a long request logs after its own pin."""
    ended = int(datetime(2026, 11, 3, 11, 59, 35, tzinfo=timezone.utc).timestamp() * 1000)
    rows = perf_triage._heaviest_from_events(
        [
            _slow_line(ended, 900_000, 1_000, route="labs/waiting/"),
            _slow_line(ended, 746_000, 230_000, route="labs/burning/"),
            {"timestamp": ended, "message": "not json"},
        ]
    )
    assert [row["route"] for row in rows] == ["labs/burning/", "labs/waiting/"]
    assert rows[0]["started"] == "2026-11-03T11:47:09Z"
    assert rows[0]["cpu_s"] == 230


def test_a_heavy_request_is_reported_under_any_verdict():
    row = {
        "route": "labs/burning/",
        "status": 500,
        "started": "2026-11-03T11:47:09Z",
        "ended": "2026-11-03T11:59:35Z",
        "duration_s": 746,
        "cpu_s": 230,
        "db_s": 234,
        "db_queries": 444,
        "outbound_s": 286,
        "outbound_calls": 98,
    }
    verdict = perf_triage.judge(_snapshot(heaviest_requests=[row]))
    assert verdict["verdict"] == "healthy"
    assert any("HEAVY REQUEST labs/burning/" in f and "11:47:09Z" in f for f in verdict["findings"])


def test_a_failed_telemetry_read_says_so():
    verdict = perf_triage.judge(_snapshot(heaviest_requests_error="AccessDenied"))
    assert any("HEAVY REQUESTS UNREAD: AccessDenied" in f for f in verdict["findings"])


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


# ── target (app-generated) 5xx ──────────────────────────────────────────
# Added 2026-09-01. The tool queried only HTTPCode_ELB_5XX_Count, so on
# 2026-08-31 it reported 44 ELB 5xx over a window in which the application had
# in fact failed 410 requests -- every one of them /umami/api/send hitting a
# Prisma P2002 race. Nothing in the output hinted at them, and no alarm existed
# either, so a two-hour outage read as a mildly busy afternoon. These pin that
# the app's own errors can reach the verdict.


def test_breaching_target_5xx_is_not_healthy():
    """The 2026-08-31 shape: 410 app 5xx, peak 42, four periods over the line."""
    verdict = perf_triage.judge(
        _snapshot(
            target_5xx_total=410,
            target_5xx_peak_period=42,
            target_5xx_breaching_periods=4,
        )
    )
    assert verdict["verdict"] == "target_5xx_elevated"
    assert any("TARGET (app-generated) 5xx: 410" in f for f in verdict["findings"])


def test_target_5xx_under_the_alarm_line_stays_healthy():
    """Regression guard: a trickle of app 5xx is normal and must not flag.

    08-30 carried 0 and 09-01 carried 1; a rule that flags those teaches the
    next reviewer to ignore the verdict, which is the failure this whole file
    exists to prevent.
    """
    verdict = perf_triage.judge(_snapshot(target_5xx_total=3, target_5xx_peak_period=2))
    assert verdict["verdict"] == "healthy"


def test_target_5xx_does_not_mask_web_saturation():
    """web_saturated is more specific and already names the tier -- it wins."""
    verdict = perf_triage.judge(
        _snapshot(
            target_5xx_total=410,
            target_5xx_peak_period=42,
            target_5xx_breaching_periods=4,
            web_cpu_peak_pct=100.0,
            web_cpu_saturated_periods=4,
        )
    )
    assert verdict["verdict"] == "web_saturated"


def test_target_and_elb_5xx_are_reported_separately():
    """They fail in OPPOSITE directions and lead to different log groups.

    2026-07-29: target 1 / ELB 84. 2026-08-31: target 410 / ELB peak 19. A
    reviewer who cannot tell which one fired cannot pick the right next step,
    so collapsing them into one number would be worse than either alone.
    """
    verdict = perf_triage.judge(
        _snapshot(
            elb_5xx_total=44,
            elb_5xx_peak_period=19,
            target_5xx_total=410,
            target_5xx_peak_period=42,
            target_5xx_breaching_periods=4,
        )
    )
    joined = " ".join(verdict["findings"])
    assert "ELB-generated 5xx: 44" in joined
    assert "TARGET (app-generated) 5xx: 410" in joined
    # and the next step must send the reader to the ALB access logs, since the
    # web log group knows nothing about the Umami target group.
    assert "labs-jj-alb-access-logs" in verdict["next_step"]


def test_target_5xx_finding_names_the_alarm_it_agrees_with():
    """Same contract as the ELB alarm: the tool must not disagree with the pager."""
    verdict = perf_triage.judge(
        _snapshot(target_5xx_total=410, target_5xx_peak_period=42, target_5xx_breaching_periods=4)
    )
    assert any("labs-jj-alb-target-5xx-high" in f for f in verdict["findings"])


# --- the database as the slow dependency, and which service was slow ---------
#
# 2026-10-23, labs-jj-alb-latency-high: a day-by-day audit-trail scrape held
# labs-jj-postgres at 100% for 30+ minutes; ALB p95 19.9s, web CPU unremarkable.
# The tool said slow_no_saturation and pointed at outbound_by_host, which was {}
# on every slow request. It had collected DB CPU at 100% and not used it.


def test_latency_with_a_pinned_database_names_the_database():
    verdict = perf_triage.judge(
        _snapshot(alb_p95_peak_s=19.9, alb_p95_slow_periods=3, db_cpu_peak_pct=100.0, db_cpu_saturated_periods=4)
    )
    assert verdict["verdict"] == "db_saturated"
    assert "outbound_by_host" not in verdict["next_step"]
    assert "db_ms" in verdict["next_step"]


def test_latency_with_a_busy_but_unsaturated_database_is_unchanged():
    """The shared DB now idles at 63-90%; a high peak alone must not claim the verdict."""
    verdict = perf_triage.judge(
        _snapshot(alb_p95_peak_s=19.9, alb_p95_slow_periods=3, db_cpu_peak_pct=88.0, db_cpu_saturated_periods=0)
    )
    assert verdict["verdict"] == "slow_no_saturation"


def test_a_pinned_database_without_latency_does_not_change_the_verdict():
    verdict = perf_triage.judge(_snapshot(db_cpu_peak_pct=100.0, db_cpu_saturated_periods=4))
    assert verdict["verdict"] == "healthy"


def test_slow_target_group_is_named():
    verdict = perf_triage.judge(
        _snapshot(
            alb_p95_peak_s=29.7,
            alb_p95_slow_periods=3,
            alb_p95_by_target_group={
                "labs-jj-target-group": 29.7,
                "labs-jj-canopy-web-tg": 3.2,
                "labs-jj-umami-tg": None,
            },
        )
    )
    slow = [f for f in verdict["findings"] if f.startswith("SLOW TARGET GROUP")]
    assert slow and "labs-jj-target-group p95 29.7s" in slow[0]
    assert "canopy-web" not in slow[0]


def test_a_slow_neighbour_is_not_blamed_on_labs():
    verdict = perf_triage.judge(
        _snapshot(
            alb_p95_peak_s=25.0,
            alb_p95_slow_periods=3,
            alb_p95_by_target_group={"labs-jj-target-group": 0.4, "labs-jj-canopy-web-tg": 25.0},
        )
    )
    slow = [f for f in verdict["findings"] if f.startswith("SLOW TARGET GROUP")][0]
    assert "labs-jj-canopy-web-tg" in slow and "not labs-jj-web" in slow


def test_reads_default_to_the_read_only_observe_role():
    """connect-labs#2770: every read here is allowed to labs-jj-observe-readonly, so
    the admin `labs` SSO session is an override, never the default."""
    assert perf_triage.DEFAULT_PROFILE == "labs-observe"
