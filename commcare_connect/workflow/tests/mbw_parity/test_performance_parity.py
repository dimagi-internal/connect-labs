"""v1↔v3 parity for the Performance tab.

The Performance tab JSX expects v1's bucket-aggregated shape (4 rows: one
per FLW assessment status) — but v3's `_v3BuildPerformanceData` was
returning a flat per-FLW list, so the tab was rendering wrong shape
entirely (showing usernames instead of status buckets, never showing
milestone percentages).

The fix moves v3 to the same bucket-aggregated shape v1 produces. These
tests verify the structural alignment plus key aggregate fields. The full
field-level v1↔v3 diff (including milestones) is harder because v1 uses
visit_type *display names* in its drilldown ("Month 1") while v3 uses
canonical ("1 Month Visit"); the milestones produce equivalent semantics
but pulled from different drilldown shapes.
"""

from __future__ import annotations

from commcare_connect.workflow.tests.mbw_parity.v3_python_port import build_followup_data_v3, build_performance_data_v3


def _v3_reg_row(*, mother_case_id, username, schedules, eligible_bonus="1"):
    return {
        "mother_case_id": mother_case_id,
        "_username": username,
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
    }


def _v3_visit(*, mother_case_id, username, visit_date, form_name):
    return {
        "_username": username,
        "_visit_date": visit_date,
        "visit_datetime": visit_date + "T10:00:00",
        "mother_case_id": mother_case_id,
        "form_name": form_name,
    }


CURRENT = "2025-06-01"


class TestPerformanceTabShape:
    def test_returns_four_status_buckets_in_canonical_order(self):
        """Always 4 rows, even with no FLWs — empty buckets included so
        the JSX table renders all categories."""
        out = build_performance_data_v3({}, {}, CURRENT)
        assert len(out) == 4
        assert [r["status_key"] for r in out] == [
            "eligible_for_renewal",
            "probation",
            "suspended",
            "none",
        ]
        for r in out:
            assert r["num_flws"] == 0
            assert r["total_cases"] == 0
            assert r["pct_still_eligible"] == 0

    def test_status_display_names_match_v1(self):
        """v1's FLW_STATUS_DISPLAY constant: each row's `status` field
        matches the v1 display label so the JSX doesn't have to translate."""
        out = build_performance_data_v3({}, {}, CURRENT)
        labels = {r["status_key"]: r["status"] for r in out}
        assert labels["eligible_for_renewal"] == "Eligible for Renewal"
        assert labels["probation"] == "Probation"
        assert labels["suspended"] == "Suspended"
        assert labels["none"] == "No Category"

    def test_flws_bucketed_by_status(self):
        """alice eligible, bob suspended, carol unknown → "none" bucket."""
        statuses = {
            "alice": "eligible_for_renewal",
            "bob": "suspended",
            "carol": "weird_status",  # falls into "none"
        }
        out = build_performance_data_v3(statuses, {}, CURRENT)
        by_key = {r["status_key"]: r for r in out}
        assert by_key["eligible_for_renewal"]["num_flws"] == 1
        assert by_key["suspended"]["num_flws"] == 1
        assert by_key["probation"]["num_flws"] == 0
        assert by_key["none"]["num_flws"] == 1

    def test_status_can_be_dict_or_string(self):
        """Connect's worker management API may return the status as a
        nested dict (e.g. `{"status": "probation", ...}`) or a bare string;
        v3 must handle both since instance.state.flw_statuses comes
        through unchanged from upstream."""
        statuses = {
            "alice": {"status": "probation", "extra": "ignored"},
            "bob": "suspended",
        }
        out = build_performance_data_v3(statuses, {}, CURRENT)
        by_key = {r["status_key"]: r for r in out}
        assert by_key["probation"]["num_flws"] == 1
        assert by_key["suspended"]["num_flws"] == 1


class TestPerformanceAggregation:
    def test_total_cases_is_drilldown_count_per_bucket(self):
        """alice (eligible_for_renewal) has 2 mothers → total_cases=2 in
        that bucket; suspended bucket has 0."""
        statuses = {"alice": "eligible_for_renewal", "bob": "suspended"}
        # Build a real drilldown via the followup builder so we test the
        # actual data flow.
        regs = [
            _v3_reg_row(
                mother_case_id="m1",
                username="alice",
                schedules=[
                    {"visit_type": "ANC Visit", "scheduled": "2025-05-01"},
                ],
            ),
            _v3_reg_row(
                mother_case_id="m2",
                username="alice",
                schedules=[
                    {"visit_type": "ANC Visit", "scheduled": "2025-05-01"},
                ],
            ),
        ]
        visits = [
            _v3_visit(
                mother_case_id="m1",
                username="alice",
                visit_date="2025-05-02",
                form_name="ANC Visit",
            ),
        ]
        followup = build_followup_data_v3(regs, visits, flw_name_map={}, current_date_str=CURRENT)
        out = build_performance_data_v3(statuses, followup["flw_drilldown"], CURRENT)
        by_key = {r["status_key"]: r for r in out}
        assert by_key["eligible_for_renewal"]["total_cases"] == 2
        assert by_key["suspended"]["total_cases"] == 0

    def test_still_eligible_uses_v1_business_rule(self):
        """still_eligible = mother is `eligible` AND (>=5 completed OR
        <=1 missed). Mirrors v1's compute_flw_performance_by_status logic.
        """
        # Construct a mother with all visits Missed → not eligible at all.
        # Then a mother with all visits Completed → still eligible.
        drilldown = {
            "alice": [
                {
                    "mother_case_id": "m1",
                    "eligible": True,
                    "visits": [{"status": "Completed", "visit_type": "ANC Visit"}] * 6,
                },
                {
                    "mother_case_id": "m2",
                    "eligible": True,
                    "visits": [{"status": "Missed", "visit_type": "ANC Visit"}] * 3,
                },
                {
                    "mother_case_id": "m3",
                    "eligible": False,
                    "visits": [],
                },
            ],
        }
        statuses = {"alice": "eligible_for_renewal"}
        out = build_performance_data_v3(statuses, drilldown, CURRENT)
        by_key = {r["status_key"]: r for r in out}
        bucket = by_key["eligible_for_renewal"]
        assert bucket["total_cases"] == 3
        assert bucket["total_cases_eligible_at_registration"] == 2
        # m1: 6 completed → still eligible; m2: 3 missed → not still
        # eligible (missed > 1 AND completed < 5).
        assert bucket["total_cases_still_eligible"] == 1
        assert bucket["pct_still_eligible"] == 50  # 1 / 2

    def test_pct_missed_1_or_less_uses_eligible_denominator(self):
        drilldown = {
            "alice": [
                {
                    "mother_case_id": "m1",
                    "eligible": True,
                    "visits": [{"status": "Missed", "visit_type": "ANC Visit"}],
                },
                {
                    "mother_case_id": "m2",
                    "eligible": True,
                    "visits": [{"status": "Missed", "visit_type": "ANC Visit"}] * 3,
                },
            ],
        }
        out = build_performance_data_v3({"alice": "eligible_for_renewal"}, drilldown, CURRENT)
        bucket = next(r for r in out if r["status_key"] == "eligible_for_renewal")
        # 1 mother has ≤1 missed; 2 eligible → 50%.
        assert bucket["pct_missed_1_or_less_visits"] == 50

    def test_milestone_pct_uses_grace_period(self):
        """A 1 Month Visit scheduled today (not yet past grace) shouldn't
        count in the denominator. One scheduled 10 days ago should."""
        drilldown = {
            "alice": [
                {
                    "mother_case_id": "m1",
                    "eligible": True,
                    "visits": [
                        # 5 completed visits + 1 Month Visit scheduled long ago
                        {"status": "Completed", "visit_type": "ANC Visit"},
                        {"status": "Completed", "visit_type": "Postnatal Delivery Visit"},
                        {"status": "Completed", "visit_type": "1 Week Visit"},
                        {
                            "status": "Completed",
                            "visit_type": "1 Month Visit",
                            "scheduled": "2025-05-15",  # 17 days before CURRENT
                        },
                    ],
                },
                {
                    "mother_case_id": "m2",
                    "eligible": True,
                    "visits": [
                        {
                            "status": "Due",
                            "visit_type": "1 Month Visit",
                            "scheduled": "2025-05-31",  # 1 day before CURRENT — within grace
                        },
                    ],
                },
            ],
        }
        out = build_performance_data_v3({"alice": "eligible_for_renewal"}, drilldown, CURRENT)
        bucket = next(r for r in out if r["status_key"] == "eligible_for_renewal")
        # m1's 1 Month Visit qualifies (past grace) AND has 4 completed →
        # numerator. m2's is within grace → excluded from denominator.
        # Denom=1 (only m1), numer=1 → 100%.
        assert bucket["pct_4_visits_on_track"] == 100
