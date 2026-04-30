"""v1↔v3 parity for the Followups tab.

The followup tab is the first tab where v1 and v3 use materially different
algorithms — v1 has a "filtered follow-up rate" that requires (a) the
mother to be eligible_full_intervention_bonus=1 and (b) the visit to be 5+
days past its scheduled date. v3's JS uses a naive
total_completed/total_expected ratio with no eligibility or grace-period
filter.

These tests pin both behaviours and document the gap. When v3's JS is
fixed to match v1, the divergence test will start failing and signal that
v3 is now in lockstep — at which point the test should be flipped to
assert agreement (or removed in favor of the agreement test).
"""

from __future__ import annotations

from datetime import date

import pytest

from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
    aggregate_flw_followup,
    build_followup_from_pipeline,
)
from commcare_connect.workflow.tests.mbw_parity.v3_python_port import build_followup_data_v3

# ---- helpers -----------------------------------------------------------


class _FakePipelineRow:
    """Stand-in for a pipeline row: has `.username`, `.visit_date`, `.computed`.

    v1's `build_followup_from_pipeline` reads `row.visit_date` when
    stamping a completion's "actual" date, so the stand-in needs it even
    if the test doesn't care about the value."""

    def __init__(self, username: str, computed: dict, visit_date: date | None = None):
        self.username = username
        self.visit_date = visit_date if visit_date is not None else date(2025, 5, 15)
        self.computed = computed


def _registration_form(
    *,
    mother_case_id: str,
    username: str,
    eligible_bonus: str = "1",
    schedules: list[dict],
) -> dict:
    """Build a CCHQ registration form dict the v1 path consumes.

    Mirrors the production shape: top-level "form" with "var_visit_*"
    blocks plus "mother_details", and a "metadata.username" key.
    """
    form: dict = {"mother_details": {"eligible_full_intervention_bonus": eligible_bonus}}
    for i, s in enumerate(schedules, start=1):
        form[f"var_visit_{i}"] = {
            "visit_type": s["visit_type"],
            "create_antenatal_visit": "1" if s["visit_type"] == "ANC Visit" else "0",
            "create_postnatal_visit": "1" if s["visit_type"] == "Postnatal Delivery Visit" else "0",
            "create_one_two_visit": "1" if s["visit_type"] == "1 Week Visit" else "0",
            "create_one_month_visit": "1" if s["visit_type"] == "1 Month Visit" else "0",
            "create_three_month_visit": "1" if s["visit_type"] == "3 Month Visit" else "0",
            "create_six_month_visit": "1" if s["visit_type"] == "6 Month Visit" else "0",
            "visit_date_scheduled": s.get("scheduled", ""),
            "visit_expiry_date": s.get("expiry", ""),
            "mother_case_id": mother_case_id,
        }
    return {"form": form, "metadata": {"username": username}}


def _v3_registrations_row(
    *,
    mother_case_id: str,
    username: str,
    schedules: list[dict],
    eligible_bonus: str = "1",
) -> dict:
    """v3 pipeline row shape: schedules as a structured list, eligibility
    as a top-level `eligible_full_intervention_bonus` field. Matches the
    REGISTRATIONS_SCHEMA fields produced by v3."""
    return {
        "mother_case_id": mother_case_id,
        "username": username,
        "eligible_full_intervention_bonus": eligible_bonus,
        "schedules": [
            {
                "visit_type": s["visit_type"],
                "visit_date_scheduled": s.get("scheduled", ""),
                "visit_expiry_date": s.get("expiry", ""),
                "mother_case_id": mother_case_id,
            }
            for s in schedules
        ],
        "registration_date": "2025-01-01T00:00:00.000",
    }


def _v3_visits_gps_row(
    *,
    mother_case_id: str,
    username: str,
    visit_date: str,
    form_name: str,
) -> dict:
    """v3 visits_gps pipeline row, normalized as `_v3PipelineRows` would
    produce — `_username`, `_visit_date`, plus form_name and other fields."""
    return {
        "_username": username,
        "_visit_date": visit_date,
        "visit_datetime": visit_date + "T10:00:00.000",
        "mother_case_id": mother_case_id,
        "form_name": form_name,
    }


def _v1_completion_row(*, username: str, mother_case_id: str, visit_type: str) -> _FakePipelineRow:
    """v1 reads pipeline rows for completion matching. The completion-flag
    field is set to "1" so `is_visit_completed` returns True for the
    matching visit type."""
    flag_for_type = {
        "ANC Visit": "antenatal_visit_completion",
        "Postnatal Delivery Visit": "postnatal_visit_completion",
        "1 Week Visit": "one_two_week_visit_completion",
        "1 Month Visit": "one_month_visit_completion",
        "3 Month Visit": "three_month_visit_completion",
        "6 Month Visit": "six_month_visit_completion",
    }[visit_type]
    return _FakePipelineRow(
        username=username,
        computed={
            "mother_case_id": mother_case_id,
            "visit_type": visit_type,
            "form_name": visit_type,
            flag_for_type: "1",
        },
    )


# ---- the parity tests --------------------------------------------------


CURRENT_DATE = date(2025, 6, 1)
CURRENT_DATE_STR = CURRENT_DATE.isoformat()


def _all_eligible_past_grace_fixture():
    """Fixture where every mother is eligible AND every visit is 5+ days past
    its schedule. Under both algorithms the denominators should be the same,
    so completion_rates *could* match. They don't, because v1 also filters
    by eligibility while v3 doesn't — see the second test for the case
    where eligibility differs."""
    schedules_m1 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]
    schedules_m2 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]
    reg_forms = [
        _registration_form(mother_case_id="m1", username="alice", eligible_bonus="1", schedules=schedules_m1),
        _registration_form(mother_case_id="m2", username="alice", eligible_bonus="1", schedules=schedules_m2),
    ]
    pipeline_rows = [
        # m1 completed both ANC + 1 Week. m2 completed only ANC.
        _v1_completion_row(username="alice", mother_case_id="m1", visit_type="ANC Visit"),
        _v1_completion_row(username="alice", mother_case_id="m1", visit_type="1 Week Visit"),
        _v1_completion_row(username="alice", mother_case_id="m2", visit_type="ANC Visit"),
    ]
    v3_regs = [
        _v3_registrations_row(mother_case_id="m1", username="alice", schedules=schedules_m1),
        _v3_registrations_row(mother_case_id="m2", username="alice", schedules=schedules_m2),
    ]
    v3_visits = [
        _v3_visits_gps_row(mother_case_id="m1", username="alice", visit_date="2025-05-02", form_name="ANC Visit"),
        _v3_visits_gps_row(mother_case_id="m1", username="alice", visit_date="2025-05-16", form_name="1 Week Visit"),
        _v3_visits_gps_row(mother_case_id="m2", username="alice", visit_date="2025-05-02", form_name="ANC Visit"),
    ]
    return reg_forms, pipeline_rows, v3_regs, v3_visits


def _eligibility_split_fixture():
    """Fixture where m1 is eligible but m2 is NOT. m2 has one completed
    visit so v3 actually attributes her (v3 needs at least one visit to
    map a mother → FLW, otherwise the mother is silently dropped — that's
    a separate bug; this fixture sidesteps it). The outcome v1 should
    filter m2 out entirely from the rate; v3 should still count m2's
    visits."""
    schedules_m1 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]
    schedules_m2 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]
    reg_forms = [
        _registration_form(mother_case_id="m1", username="alice", eligible_bonus="1", schedules=schedules_m1),
        _registration_form(mother_case_id="m2", username="alice", eligible_bonus="0", schedules=schedules_m2),
    ]
    pipeline_rows = [
        _v1_completion_row(username="alice", mother_case_id="m1", visit_type="ANC Visit"),
        _v1_completion_row(username="alice", mother_case_id="m1", visit_type="1 Week Visit"),
        # m2: 1/2 completed.
        _v1_completion_row(username="alice", mother_case_id="m2", visit_type="ANC Visit"),
    ]
    v3_regs = [
        _v3_registrations_row(mother_case_id="m1", username="alice", schedules=schedules_m1, eligible_bonus="1"),
        _v3_registrations_row(mother_case_id="m2", username="alice", schedules=schedules_m2, eligible_bonus="0"),
    ]
    v3_visits = [
        _v3_visits_gps_row(mother_case_id="m1", username="alice", visit_date="2025-05-02", form_name="ANC Visit"),
        _v3_visits_gps_row(mother_case_id="m1", username="alice", visit_date="2025-05-16", form_name="1 Week Visit"),
        _v3_visits_gps_row(mother_case_id="m2", username="alice", visit_date="2025-05-02", form_name="ANC Visit"),
    ]
    return reg_forms, pipeline_rows, v3_regs, v3_visits


def _build_v1_completion_rate(reg_forms, pipeline_rows) -> dict[str, int]:
    """Convenience: run v1 path → return {username: completion_rate} only."""
    visit_cases_by_flw = build_followup_from_pipeline(
        pipeline_rows, active_usernames={"alice"}, registration_forms=reg_forms
    )
    # v1's rate uses mother metadata (eligible_full_intervention_bonus). We
    # pass it explicitly so the rate's eligibility filter actually fires.
    mother_cases_map: dict[str, dict] = {}
    for f in reg_forms:
        for j in range(1, 7):
            v = f.get("form", {}).get(f"var_visit_{j}", {})
            if isinstance(v, dict) and v.get("mother_case_id"):
                mother_cases_map[v["mother_case_id"]] = {
                    "properties": {
                        "eligible_full_intervention_bonus": (
                            f.get("form", {}).get("mother_details", {}).get("eligible_full_intervention_bonus", "0")
                        )
                    }
                }
                break
    summaries = aggregate_flw_followup(
        visit_cases_by_flw, current_date=CURRENT_DATE, flw_names={"alice": "Alice"}, mother_cases_map=mother_cases_map
    )
    return {s["username"]: s["completion_rate"] for s in summaries}


def _build_v3_completion_rate(v3_regs, v3_visits) -> dict[str, int]:
    """Convenience: run v3 path → return {username: completion_rate} only."""
    out = build_followup_data_v3(
        v3_regs, v3_visits, flw_name_map={"alice": "Alice"}, current_date_str=CURRENT_DATE_STR
    )
    return {s["username"]: s["completion_rate"] for s in out["flw_summaries"]}


class TestFollowupRateParity:
    def test_all_eligible_past_grace_v1_and_v3_agree(self):
        """When every mother is eligible AND every visit is past grace, the
        eligibility filter is a no-op and v1 should equal v3.

        m1: 2/2 completed → 100%. m2: 1/2 completed → 50%. Both eligible → v1 averages
        per-visit: 3 completed / 4 due-and-past-grace = 75%. v3: same (3/4).
        """
        reg_forms, pipeline_rows, v3_regs, v3_visits = _all_eligible_past_grace_fixture()
        v1_rates = _build_v1_completion_rate(reg_forms, pipeline_rows)
        v3_rates = _build_v3_completion_rate(v3_regs, v3_visits)
        assert v1_rates == v3_rates, f"v1={v1_rates} v3={v3_rates}"
        # Sanity: 3 of 4 visits completed.
        assert v1_rates["alice"] == 75

    @pytest.mark.xfail(
        reason=(
            "Known divergence: v1 filters by eligible_full_intervention_bonus before "
            "computing completion_rate; v3 does not. v3's rate will be lower because "
            "ineligible mothers' uncompleted visits drag the denominator down. "
            "When v3's JS is fixed to match v1, this test should pass."
        ),
        strict=True,
    )
    def test_eligibility_split_v1_and_v3_agree(self):
        """m1 eligible (2/2 completed), m2 ineligible (0/2 completed).
        v1: filters m2 out → 2/2 = 100%.
        v3: counts both → 2/4 = 50%.
        These should match; today they don't."""
        reg_forms, pipeline_rows, v3_regs, v3_visits = _eligibility_split_fixture()
        v1_rates = _build_v1_completion_rate(reg_forms, pipeline_rows)
        v3_rates = _build_v3_completion_rate(v3_regs, v3_visits)
        assert v1_rates == v3_rates, f"v1={v1_rates} v3={v3_rates}"

    def test_v3_drops_mothers_with_zero_visits(self):
        """v3's mother→FLW attribution is "last visit wins from visits_gps"
        with no fallback. v1 attributes via the registration form's
        metadata.username when no pipeline rows exist for a mother.

        Result: a mother registered but never visited shows up in v1's
        followup table (with all visits Missed) but is silently absent in
        v3.
        """
        schedules = [
            {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        ]
        # Both mothers eligible. m1 has visits, m2 has none.
        reg_forms = [
            _registration_form(mother_case_id="m1", username="alice", schedules=schedules),
            _registration_form(mother_case_id="m2", username="alice", schedules=schedules),
        ]
        pipeline_rows = [
            _v1_completion_row(username="alice", mother_case_id="m1", visit_type="ANC Visit"),
        ]
        v3_regs = [
            _v3_registrations_row(mother_case_id="m1", username="alice", schedules=schedules),
            _v3_registrations_row(mother_case_id="m2", username="alice", schedules=schedules),
        ]
        v3_visits = [
            _v3_visits_gps_row(mother_case_id="m1", username="alice", visit_date="2025-05-02", form_name="ANC Visit"),
        ]
        v1_summaries = aggregate_flw_followup(
            build_followup_from_pipeline(pipeline_rows, {"alice"}, registration_forms=reg_forms),
            current_date=CURRENT_DATE,
            flw_names={"alice": "Alice"},
            mother_cases_map={
                "m1": {"properties": {"eligible_full_intervention_bonus": "1"}},
                "m2": {"properties": {"eligible_full_intervention_bonus": "1"}},
            },
        )
        v3_out = build_followup_data_v3(
            v3_regs, v3_visits, flw_name_map={"alice": "Alice"}, current_date_str=CURRENT_DATE_STR
        )
        v1_alice = next(s for s in v1_summaries if s["username"] == "alice")
        # v1 sees both mothers — m2's missed ANC counted in `missed`.
        assert v1_alice["missed"] == 1, f"v1 missed = {v1_alice['missed']}"
        # v3's drilldown only has m1 — m2 dropped entirely.
        v3_drilldown = v3_out["flw_drilldown"]["alice"]
        assert len(v3_drilldown) == 1, f"v3 drilldown mothers = {len(v3_drilldown)}"
        assert v3_drilldown[0]["mother_case_id"] == "m1"

    def test_eligibility_split_documents_current_v1_and_v3_outputs(self):
        """The non-strict counterpart of the xfail above: documents what v1
        and v3 actually produce on the eligibility-split fixture, so a
        future change to v3 doesn't silently flip this without us noticing.

        Setup: alice has m1 (eligible, 2/2 completed) and m2 (ineligible,
        1/2 completed). All visits are scheduled in May; current date June 1
        so all are well past the 5-day grace period.

        - v1: filters out m2 because she's not eligible_full_intervention_bonus.
          2 completed / 2 eligible+past-grace = 100%.
        - v3: counts every non-Upcoming visit across all mothers.
          3 completed / 4 (m1 ANC+Week, m2 ANC completed; m2 Week missed) = 75%.
        """
        reg_forms, pipeline_rows, v3_regs, v3_visits = _eligibility_split_fixture()
        v1_rates = _build_v1_completion_rate(reg_forms, pipeline_rows)
        v3_rates = _build_v3_completion_rate(v3_regs, v3_visits)
        assert v1_rates["alice"] == 100, f"v1 rate (eligibility filter active) = {v1_rates['alice']}"
        assert v3_rates["alice"] == 75, f"v3 rate (no eligibility filter) = {v3_rates['alice']}"
