"""v1↔v3 parity for the Followups tab.

v3 used to diverge from v1 in two places — completion_rate (no eligibility
filter, no grace period) and mother→FLW attribution (no fallback to the
registration form's submitter when no visits existed). Both are now fixed
in the v3 JS (and mirrored in v3_python_port.py); these tests are the
regression guard.
"""

from __future__ import annotations

from datetime import date

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

    def test_eligibility_split_v1_and_v3_agree(self):
        """m1 eligible (2/2 completed), m2 ineligible (1/2 completed).
        Both should produce 100% — v1 filters m2 out; v3 (now patched)
        also filters m2 out via eligible_full_intervention_bonus."""
        reg_forms, pipeline_rows, v3_regs, v3_visits = _eligibility_split_fixture()
        v1_rates = _build_v1_completion_rate(reg_forms, pipeline_rows)
        v3_rates = _build_v3_completion_rate(v3_regs, v3_visits)
        assert v1_rates == v3_rates, f"v1={v1_rates} v3={v3_rates}"
        assert v1_rates["alice"] == 100

    def test_registered_but_unvisited_mothers_attributed_to_form_submitter(self):
        """v3 used to drop registered-but-not-yet-visited mothers entirely
        because mother→FLW was last-visit-wins from visits_gps with no
        fallback. The fix attributes them to the registration form's
        submitter (registrations row `_username`/`username`), matching
        v1's behaviour."""
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
        v3_drilldown = v3_out["flw_drilldown"]["alice"]
        # Both mothers visible in both views.
        assert v1_alice["missed"] == 1, f"v1 missed = {v1_alice['missed']}"
        assert len(v3_drilldown) == 2, f"v3 drilldown mothers = {len(v3_drilldown)}"
        v3_mids = {m["mother_case_id"] for m in v3_drilldown}
        assert v3_mids == {"m1", "m2"}

    def test_unfiltered_totals_still_visible_post_fix(self):
        """The fix changed completion_rate to be eligibility-filtered, but
        the unfiltered totals (total_expected, total_completed) stay
        present in the per-FLW summary so the drilldown table can still
        show them. This test pins those non-rate fields so a future
        refactor doesn't accidentally drop them."""
        reg_forms, pipeline_rows, v3_regs, v3_visits = _eligibility_split_fixture()
        out = build_followup_data_v3(
            v3_regs, v3_visits, flw_name_map={"alice": "Alice"}, current_date_str=CURRENT_DATE_STR
        )
        alice = next(s for s in out["flw_summaries"] if s["username"] == "alice")
        # 4 expected (m1 ANC+Week, m2 ANC+Week non-Upcoming), 3 completed
        # (m1 both, m2 ANC).
        assert alice["total_expected"] == 4
        assert alice["total_completed"] == 3
        # completion_rate is the eligibility-filtered rate, not 75%.
        assert alice["completion_rate"] == 100
        # Sanity: v1 agrees on the same 100%.
        v1_rates = _build_v1_completion_rate(reg_forms, pipeline_rows)
        assert v1_rates["alice"] == 100
