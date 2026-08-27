"""A fortnightly cadence has to be exactly 14 days apart, forever, with no stored anchor.

``compute_next_run`` is a pure function of (cadence, hour, day_of_week, day_of_month,
from_dt). It is handed no schedule history, so "every 14 days" must be derivable from the
date alone. The implementation counts whole weeks from one fixed Monday and fires on even
counts.

The obvious alternative -- ISO week numbers -- is subtly wrong, and one test below pins
exactly why: a 53-week ISO year puts weeks 53 and 1 back to back, both odd, so an
ISO-parity rule would silently stretch one gap to 7 days. These tests would fail on such
an implementation, which is the point of writing them.

The other thing worth pinning is that this cadence must audit a window that ABUTS the
previous run rather than overlapping it -- a 14-day window on a weekly cadence would
re-audit half of what the run before it already did, giving reviewers duplicate sessions
and paying the classifier twice for the same photos.
"""

from datetime import datetime, timedelta, timezone

import pytest

from connect_labs.workflow import schedules
from connect_labs.workflow.audit_generation import resolve_window, window_preset_for_cadence
from connect_labs.workflow.schedules import BIWEEKLY, CADENCE_CHOICES, compute_next_run

TUESDAY = 1
SUNDAY = 6


def _utc(y, m, d, h=0, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=timezone.utc)


def _next(from_dt, day_of_week=TUESDAY, hour=21):
    return compute_next_run(BIWEEKLY, hour, day_of_week, None, from_dt)


class TestItIsOfferedAtAll:
    def test_the_cadence_is_selectable(self):
        assert BIWEEKLY == "biweekly"
        assert (BIWEEKLY, "Every 2 weeks") in CADENCE_CHOICES

    def test_it_did_not_displace_the_existing_cadences(self):
        keys = [c[0] for c in CADENCE_CHOICES]
        assert keys == ["daily", "weekdays", "weekly", "biweekly", "monthly"]

    def test_the_stored_choices_match_the_scheduler_choices(self):
        """The model and the scheduler keep separate lists; if they drift, a cadence can
        be saved that compute_next_run then raises ValueError on."""
        from connect_labs.labs.models import WorkflowSchedule

        assert [c[0] for c in WorkflowSchedule.CADENCE_CHOICES] == [c[0] for c in CADENCE_CHOICES]
        assert dict(WorkflowSchedule.CADENCE_CHOICES) == dict(CADENCE_CHOICES)


class TestTheGapIsAlwaysFourteenDays:
    def test_consecutive_firings_are_exactly_a_fortnight_apart(self):
        """Walked forward over four years, so a year boundary is crossed four times."""
        when = _utc(2026, 1, 1)
        previous = None
        seen = 0
        while when < _utc(2030, 1, 1):
            fire = _next(when)
            if previous is not None:
                assert fire - previous == timedelta(days=14), f"{previous} -> {fire}"
            previous = fire
            seen += 1
            when = fire + timedelta(minutes=1)
        assert seen > 100, "the walk did not actually cover the period"

    def test_the_53_week_iso_year_does_not_stretch_a_gap(self):
        """2026 is a 53-week ISO year. An ISO-week-parity implementation puts weeks 53 and
        1 next to each other -- both odd -- and skips a firing, making one gap 21 days or
        collapsing another to 7. A plain day count has no such seam."""
        end_of_2026 = _utc(2026, 12, 1)
        fire = _next(end_of_2026)
        for _ in range(6):
            nxt = _next(fire + timedelta(minutes=1))
            assert nxt - fire == timedelta(days=14), f"gap broke across the year boundary: {fire} -> {nxt}"
            fire = nxt

    def test_every_firing_lands_on_the_requested_weekday_and_hour(self):
        when = _utc(2026, 3, 1)
        for _ in range(30):
            fire = _next(when, day_of_week=SUNDAY, hour=6)
            assert fire.weekday() == SUNDAY
            assert (fire.hour, fire.minute, fire.second) == (6, 0, 0)
            when = fire + timedelta(minutes=1)


class TestItIsStrictlyInTheFuture:
    def test_the_result_is_always_after_the_moment_asked(self):
        for day in range(1, 29):
            when = _utc(2026, 4, day, 21, 0)
            assert _next(when) > when

    def test_asking_exactly_at_a_firing_time_returns_the_next_one_not_the_same_one(self):
        """Otherwise a schedule that has just fired re-fires immediately in a loop."""
        fire = _next(_utc(2026, 4, 1))
        assert _next(fire) == fire + timedelta(days=14)

    def test_asking_a_minute_before_a_firing_returns_that_firing(self):
        fire = _next(_utc(2026, 4, 1))
        assert _next(fire - timedelta(minutes=1)) == fire


class TestItIsDeterministicAndNeedsNoStoredState:
    def test_two_schedules_created_weeks_apart_agree_on_the_fortnight(self):
        """The whole point of the stateless anchor: when a schedule was CREATED must not
        change which fortnight it lands on, or two schedules would interleave and the
        cadence would mean something different for each."""
        early = _next(_utc(2026, 5, 1))
        later = _next(_utc(2026, 5, 8))
        assert (later - early).days % 14 == 0

    def test_a_date_before_the_anchor_epoch_still_alternates(self):
        """Floor division keeps the parity alternating for negative week counts; integer
        truncation toward zero would double up a fortnight just before the epoch."""
        when = _utc(2025, 6, 1)
        previous = None
        for _ in range(10):
            fire = _next(when)
            if previous is not None:
                assert fire - previous == timedelta(days=14)
            previous = fire
            when = fire + timedelta(minutes=1)


class TestTheWindowItAudits:
    def test_a_biweekly_run_audits_fourteen_days(self):
        assert window_preset_for_cadence(BIWEEKLY) == "last_14_days"

    def test_consecutive_runs_abut_rather_than_overlap(self):
        """The reason this cadence exists instead of a 14-day window on a weekly schedule:
        run N+1 must start the day after run N ended, never re-auditing the same photos."""
        first_fire = _next(_utc(2026, 4, 1))
        second_fire = first_fire + timedelta(days=14)

        start_a, end_a = resolve_window("last_14_days", first_fire.date())
        start_b, end_b = resolve_window("last_14_days", second_fire.date())

        assert start_b > end_a, f"windows overlap: {start_a}..{end_a} then {start_b}..{end_b}"
        gap = (datetime.strptime(start_b, "%Y-%m-%d") - datetime.strptime(end_a, "%Y-%m-%d")).days
        assert gap == 1, f"windows should abut, gap was {gap} days"

    def test_the_window_is_actually_fourteen_days_long(self):
        start, end = resolve_window("last_14_days", _utc(2026, 4, 14).date())
        span = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        assert span == 13, "14 days inclusive"


class TestTheOtherCadencesAreUntouched:
    @pytest.mark.parametrize(
        "cadence,preset",
        [("daily", "yesterday"), ("weekdays", "yesterday"), ("weekly", "last_week"), ("monthly", "last_month")],
    )
    def test_existing_window_mappings_are_unchanged(self, cadence, preset):
        assert window_preset_for_cadence(cadence) == preset

    def test_weekly_still_fires_every_seven_days(self):
        when = _utc(2026, 4, 1)
        previous = None
        for _ in range(8):
            fire = compute_next_run(schedules.WEEKLY, 21, TUESDAY, None, when)
            if previous is not None:
                assert fire - previous == timedelta(days=7)
            previous = fire
            when = fire + timedelta(minutes=1)

    def test_an_unknown_cadence_still_raises(self):
        with pytest.raises(ValueError):
            compute_next_run("fortnightly", 21, TUESDAY, None, _utc(2026, 4, 1))
