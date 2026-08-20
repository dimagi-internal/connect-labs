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
