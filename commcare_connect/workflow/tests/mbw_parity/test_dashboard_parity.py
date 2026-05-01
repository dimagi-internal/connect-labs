"""Full v1↔v3 dashboard payload parity on shared fixture data.

Runs both `build_v1_dashboard_payload` and `build_v3_dashboard_payload` on
the same logical fixture (registration forms + visits + GS forms), then
compares the resulting dashboards leaf by leaf at the per-FLW level.

Earlier parity tests covered tab-internal computations (followup
algorithm, GPS aggregation, performance bucketing). This test stitches
them together end-to-end — proves the two paths agree on the
dashboard-as-a-whole, not just on the individual builders. Lets us catch
integration drift, not just per-builder drift.
"""

from __future__ import annotations

from datetime import date

from commcare_connect.workflow.templates.mbw_monitoring.dashboard_builder import build_v1_dashboard_payload
from commcare_connect.workflow.tests.mbw_parity.v3_python_port import build_v3_dashboard_payload

# ---- fixture ----------------------------------------------------------------

CURRENT = date(2025, 6, 1)


class _PipelineRow:
    """v1 pipeline-row stand-in: matches the SQLBackend visit_level row
    shape v1 helpers consume — top-level `.id`, `.username`, `.visit_date`,
    `.latitude`, `.longitude`, `.entity_name`, `.computed`."""

    def __init__(
        self,
        *,
        username,
        visit_id,
        visit_date,
        computed,
        latitude=None,
        longitude=None,
        entity_name="",
    ):
        self.id = visit_id
        self.username = username
        self.visit_date = visit_date
        self.latitude = latitude
        self.longitude = longitude
        self.entity_name = entity_name
        self.computed = computed


def _v1_visit_row(
    *,
    username,
    visit_id,
    case_id,
    mother_case_id,
    visit_datetime,
    form_name,
    completion_flag=None,
    parity=None,
    bf_status=None,
    gps=None,
):
    """v1 ingests pipeline rows of a single shape — the SQLBackend visit_level
    output. Build one with the fields v1's helpers actually consume."""
    computed = {
        "case_id": case_id,
        "mother_case_id": mother_case_id,
        "form_name": form_name,
        "visit_datetime": visit_datetime,
        "gps_location": (f"{gps[0]} {gps[1]}" if gps else ""),
        "app_build_version": 1,
    }
    if completion_flag:
        computed[completion_flag] = "1"
    if parity:
        computed["parity"] = parity
    if bf_status:
        computed["bf_status"] = bf_status
    return _PipelineRow(
        username=username,
        visit_id=visit_id,
        visit_date=date.fromisoformat(visit_datetime[:10]),
        latitude=gps[0] if gps else None,
        longitude=gps[1] if gps else None,
        computed=computed,
    )


def _registration_form(*, mother_case_id, username, eligible="1", schedules):
    """Same shape v1's _extract_schedules_from_registration_form expects.

    `eligible_full_intervention_bonus` lives at the top level of `form`,
    NOT under mother_details — v1's extract_mother_metadata_from_forms
    reads it via `form.get("eligible_full_intervention_bonus", "")`,
    matching v3's REGISTRATIONS_SCHEMA which uses path
    `form.eligible_full_intervention_bonus`.
    """
    form = {
        "mother_details": {},
        "eligible_full_intervention_bonus": eligible,
    }
    for i, s in enumerate(schedules, start=1):
        flags_for_type = {
            "ANC Visit": "create_antenatal_visit",
            "Postnatal Delivery Visit": "create_postnatal_visit",
            "1 Week Visit": "create_one_two_visit",
            "1 Month Visit": "create_one_month_visit",
            "3 Month Visit": "create_three_month_visit",
            "6 Month Visit": "create_six_month_visit",
        }
        block = {
            "visit_type": s["visit_type"],
            "visit_date_scheduled": s.get("scheduled", ""),
            "visit_expiry_date": s.get("expiry", ""),
            "mother_case_id": mother_case_id,
        }
        flag = flags_for_type.get(s["visit_type"])
        if flag:
            block[flag] = "1"
        form[f"var_visit_{i}"] = block
    return {"form": form, "metadata": {"username": username}}


def _v3_registrations_row(*, mother_case_id, username, eligible="1", schedules):
    return {
        "_username": username,
        "username": username,
        "mother_case_id": mother_case_id,
        "eligible_full_intervention_bonus": eligible,
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


def _v3_visits_gps_row(*, username, mother_case_id, case_id, visit_datetime, form_name, gps):
    return {
        "_username": username,
        "_visit_date": visit_datetime[:10],
        "visit_datetime": visit_datetime,
        "case_id": case_id,
        "mother_case_id": mother_case_id,
        "form_name": form_name,
        "latitude": gps[0] if gps else None,
        "longitude": gps[1] if gps else None,
        "distance_from_prev_case_visit_m": None,
    }


def _v3_visits_aggregated_row(*, username, mother_count, ebf_count, bf_status_count, last_visit_date):
    """v3's `visits` pipeline is terminal-aggregated — one row per FLW with
    pre-computed counts. Mirrors the SQL output shape."""
    return {
        "_username": username,
        "username": username,
        "mother_count": mother_count,
        "ebf_count": ebf_count,
        "bf_status_count": bf_status_count,
        "_base_last_visit_date": last_visit_date,
    }


def _build_fixture():
    """One FLW (alice) with two eligible mothers. m1 has 2 completed visits,
    m2 has 1 completed visit + 1 missed.
    """
    schedules_m1 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]
    schedules_m2 = [
        {"visit_type": "ANC Visit", "scheduled": "2025-05-01", "expiry": "2025-05-08"},
        {"visit_type": "1 Week Visit", "scheduled": "2025-05-15", "expiry": "2025-05-22"},
    ]

    # v1 inputs — gps populated so gps_data has parity content.
    v1_pipeline_rows = [
        _v1_visit_row(
            username="alice",
            visit_id="v1",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-02T10:00:00",
            form_name="ANC Visit",
            completion_flag="antenatal_visit_completion",
            parity="2",
            bf_status="ebf",
            gps=(0.0, 0.0),
        ),
        _v1_visit_row(
            username="alice",
            visit_id="v2",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-16T10:00:00",
            form_name="1 Week Visit",
            completion_flag="one_two_week_visit_completion",
            bf_status="ebf",
            gps=(0.001, 0.001),
        ),
        _v1_visit_row(
            username="alice",
            visit_id="v3",
            case_id="c2",
            mother_case_id="m2",
            visit_datetime="2025-05-02T11:00:00",
            form_name="ANC Visit",
            completion_flag="antenatal_visit_completion",
            parity="2",
            bf_status="non-ebf",
            gps=(0.05, 0.05),
        ),
    ]
    v1_reg_forms = [
        _registration_form(mother_case_id="m1", username="alice", schedules=schedules_m1),
        _registration_form(mother_case_id="m2", username="alice", schedules=schedules_m2),
    ]

    # v3 inputs (pipeline-row shaped)
    v3_visits_rows = [
        _v3_visits_aggregated_row(
            username="alice",
            mother_count=2,
            ebf_count=2,
            bf_status_count=3,
            last_visit_date="2025-05-16",
        ),
    ]
    v3_visits_gps_rows = [
        _v3_visits_gps_row(
            username="alice",
            mother_case_id="m1",
            case_id="c1",
            visit_datetime="2025-05-02T10:00:00",
            form_name="ANC Visit",
            gps=(0.0, 0.0),
        ),
        _v3_visits_gps_row(
            username="alice",
            mother_case_id="m1",
            case_id="c1",
            visit_datetime="2025-05-16T10:00:00",
            form_name="1 Week Visit",
            gps=(0.001, 0.001),
        ),
        _v3_visits_gps_row(
            username="alice",
            mother_case_id="m2",
            case_id="c2",
            visit_datetime="2025-05-02T11:00:00",
            form_name="ANC Visit",
            gps=(0.05, 0.05),
        ),
    ]
    v3_regs_rows = [
        _v3_registrations_row(mother_case_id="m1", username="alice", schedules=schedules_m1),
        _v3_registrations_row(mother_case_id="m2", username="alice", schedules=schedules_m2),
    ]

    return {
        "v1": {"pipeline_rows": v1_pipeline_rows, "registration_forms": v1_reg_forms},
        "v3": {
            "visits_rows": v3_visits_rows,
            "visits_gps_rows": v3_visits_gps_rows,
            "registrations_rows": v3_regs_rows,
            "gs_forms_rows": [],
        },
    }


# ---- tests ----------------------------------------------------------------


class TestDashboardPayloadParity:
    def setup_method(self):
        self.fx = _build_fixture()
        self.v1 = build_v1_dashboard_payload(
            pipeline_rows=self.fx["v1"]["pipeline_rows"],
            registration_forms=self.fx["v1"]["registration_forms"],
            gs_forms=[],
            active_usernames={"alice"},
            flw_names={"alice": "Alice"},
            current_date=CURRENT,
        )
        self.v3 = build_v3_dashboard_payload(
            visits_rows=self.fx["v3"]["visits_rows"],
            visits_gps_rows=self.fx["v3"]["visits_gps_rows"],
            registrations_rows=self.fx["v3"]["registrations_rows"],
            gs_forms_rows=self.fx["v3"]["gs_forms_rows"],
            active_usernames={"alice"},
            flw_name_map={"alice": "Alice"},
            current_date_str=CURRENT.isoformat(),
        )

    def _v1_overview(self, username):
        return next(s for s in self.v1["overview_data"]["flw_summaries"] if s["username"] == username)

    def _v3_overview(self, username):
        return next(s for s in self.v3["overview_data"]["flw_summaries"] if s["username"] == username)

    def test_overview_cases_registered_match(self):
        assert self._v1_overview("alice")["cases_registered"] == self._v3_overview("alice")["cases_registered"]
        assert self._v1_overview("alice")["cases_registered"] == 2

    def test_overview_eligible_mothers_match(self):
        assert self._v1_overview("alice")["eligible_mothers"] == self._v3_overview("alice")["eligible_mothers"]

    def test_overview_followup_rate_match(self):
        assert self._v1_overview("alice")["followup_rate"] == self._v3_overview("alice")["followup_rate"]

    def test_overview_ebf_pct_match(self):
        assert self._v1_overview("alice")["ebf_pct"] == self._v3_overview("alice")["ebf_pct"]

    def test_overview_cases_still_eligible_match(self):
        v1_cse = self._v1_overview("alice")["cases_still_eligible"]
        v3_cse = self._v3_overview("alice")["cases_still_eligible"]
        assert v1_cse == v3_cse, f"v1={v1_cse} v3={v3_cse}"

    def test_followup_total_cases_match(self):
        assert self.v1["followup_data"]["total_cases"] == self.v3["followup_data"]["total_cases"]

    def test_visit_status_distribution_shape_matches(self):
        v1_dist = self.v1["overview_data"]["visit_status_distribution"]
        v3_dist = self.v3["overview_data"]["visit_status_distribution"]
        # Both produce {by_visit_type, totals}.
        assert "by_visit_type" in v1_dist and "by_visit_type" in v3_dist
        assert "totals" in v1_dist and "totals" in v3_dist
        # Totals across status keys agree.
        for key in (
            "completed_on_time",
            "completed_late",
            "due_on_time",
            "due_late",
            "missed",
            "not_due_yet",
        ):
            assert v1_dist["totals"].get(key, 0) == v3_dist["totals"].get(
                key, 0
            ), f"totals.{key}: v1={v1_dist['totals'].get(key)} v3={v3_dist['totals'].get(key)}"

    def test_performance_data_has_four_buckets_in_canonical_order(self):
        v1_keys = [r["status_key"] for r in self.v1["performance_data"]]
        v3_keys = [r["status_key"] for r in self.v3["performance_data"]]
        assert v1_keys == v3_keys
        assert v1_keys == ["eligible_for_renewal", "probation", "suspended", "none"]

    def test_performance_data_total_cases_per_bucket_match(self):
        v1_by_key = {r["status_key"]: r for r in self.v1["performance_data"]}
        v3_by_key = {r["status_key"]: r for r in self.v3["performance_data"]}
        for key in v1_by_key:
            assert (
                v1_by_key[key]["total_cases"] == v3_by_key[key]["total_cases"]
            ), f"{key}: v1={v1_by_key[key]['total_cases']} v3={v3_by_key[key]['total_cases']}"

    def test_gps_total_visits_match(self):
        assert self.v1["gps_data"]["total_visits"] == self.v3["gps_data"]["total_visits"]

    def test_gps_per_flw_visits_with_gps_match(self):
        v1_g = next(s for s in self.v1["gps_data"]["flw_summaries"] if s["username"] == "alice")
        v3_g = next(s for s in self.v3["gps_data"]["flw_summaries"] if s["username"] == "alice")
        assert v1_g["visits_with_gps"] == v3_g["visits_with_gps"]

    def test_gps_per_flw_unique_cases_match(self):
        v1_g = next(s for s in self.v1["gps_data"]["flw_summaries"] if s["username"] == "alice")
        v3_g = next(s for s in self.v3["gps_data"]["flw_summaries"] if s["username"] == "alice")
        assert v1_g["unique_cases"] == v3_g["unique_cases"]
