"""Shared fixture for v4↔v5 parity tests.

The fixture mimics the pipeline_data shape v4's job handler consumes
(`visits`, `visits_agg`, `registrations`, `gs_forms`). Same dict is exposed
as JSON for the node-side v5 test so both runners see byte-identical input.

Includes coverage for every code path that matters:
  - Two FLWs with overlapping mother visits (tests last-FLW attribution)
  - Mothers with eligible_full_intervention_bonus = 1 and 0
  - Mothers with antenatal_visit_completion = "ok" and not
  - Various bf_status values (incl. "ebf" tokenization)
  - GPS coords that produce nonzero lag_haversine distances
  - timeStart/timeEnd for duration + inter-visit gap stats
  - Mother schedules covering ANC/PNC/1-week/1-month with mixed
    scheduled/expiry dates (some past grace, some not, some missed)
  - GS scores keyed by user_connect_id
  - task_filters for Tab 2 baseline rate test
"""

from datetime import date, timedelta


def build_fixture(current_date_str: str = "2025-06-01") -> dict:
    """Return a fixture dict with all four pipeline row sets.

    Dates are derived from current_date_str so the grace-period and
    expiry boundaries are deterministic relative to that "today."
    """
    today = date.fromisoformat(current_date_str)
    d = lambda days: (today + timedelta(days=days)).isoformat()
    dt = lambda days, h=12, m=0: (today + timedelta(days=days)).isoformat() + f"T{h:02d}:{m:02d}:00Z"

    visits = [
        # FLW alice visits mother M1 (registered, eligible, ANC OK), three visits
        # PNC then 1-week then 1-month, all completed past-grace
        {
            "username": "alice",
            "mother_case_id": "M1",
            "visit_datetime": dt(-60),
            "time_start": dt(-60, h=11, m=45),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "ok",
            "latitude": 0.10,
            "longitude": 0.10,
            "distance_from_prev_case_visit_m": None,
        },
        {
            "username": "alice",
            "mother_case_id": "M1",
            "visit_datetime": dt(-50),
            "time_start": dt(-50, h=11, m=30),
            "form_name": "1 Week Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "",
            "latitude": 0.11,
            "longitude": 0.11,
            "distance_from_prev_case_visit_m": 15.0,
        },
        {
            "username": "alice",
            "mother_case_id": "M1",
            "visit_datetime": dt(-40),
            "time_start": dt(-40, h=11, m=15),
            "form_name": "1 Month Visit",
            "bf_status": "mixed feeding",
            "antenatal_visit_completion": "",
            "latitude": 0.12,
            "longitude": 0.12,
            "distance_from_prev_case_visit_m": 18.0,
        },
        # FLW alice visits mother M2 (registered, eligible, ANC OK) - PNC only
        {
            "username": "alice",
            "mother_case_id": "M2",
            "visit_datetime": dt(-55),
            "time_start": dt(-55, h=12, m=30),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "ok",
            "latitude": 0.20,
            "longitude": 0.20,
            "distance_from_prev_case_visit_m": None,
        },
        # FLW bob visits mother M3 (registered, eligible, ANC OK) - all visits
        {
            "username": "bob",
            "mother_case_id": "M3",
            "visit_datetime": dt(-58),
            "time_start": dt(-58, h=13, m=45),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "ok",
            "latitude": 0.30,
            "longitude": 0.30,
            "distance_from_prev_case_visit_m": None,
        },
        {
            "username": "bob",
            "mother_case_id": "M3",
            "visit_datetime": dt(-45),
            "time_start": dt(-45, h=13, m=20),
            "form_name": "1 Week Visit",
            "bf_status": "mixed",
            "antenatal_visit_completion": "",
            "latitude": 0.31,
            "longitude": 0.31,
            "distance_from_prev_case_visit_m": 28.0,
        },
        # FLW bob visits mother M2 LATER than alice — tests last-FLW-wins
        # attribution: M2 ends up attributed to bob, NOT alice.
        {
            "username": "bob",
            "mother_case_id": "M2",
            "visit_datetime": dt(-30),
            "time_start": dt(-30, h=14, m=10),
            "form_name": "1 Week Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "",
            "latitude": 0.21,
            "longitude": 0.21,
            "distance_from_prev_case_visit_m": 35.0,
        },
        # FLW carol visits mother M4 (registered, NOT eligible — bonus=0)
        {
            "username": "carol",
            "mother_case_id": "M4",
            "visit_datetime": dt(-40),
            "time_start": dt(-40, h=10, m=0),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "no",
            "antenatal_visit_completion": "ok",
            "latitude": 0.40,
            "longitude": 0.40,
            "distance_from_prev_case_visit_m": None,
        },
    ]

    visits_agg = [
        # Pre-aggregated counts — should match what v4 would have computed
        # from visits_rows scan. Tests the use_agg_counts branch.
        {"username": "alice", "num_mothers": 2, "bf_count": 4, "ebf_count": 3},
        {"username": "bob", "num_mothers": 2, "bf_count": 3, "ebf_count": 2},
        {"username": "carol", "num_mothers": 1, "bf_count": 1, "ebf_count": 0},
    ]

    # mbw_visit_schedules extractor output: list of {visit_type,
    # visit_date_scheduled, visit_expiry_date, mother_case_id}
    def _schedules(mid, anchor_days_ago):
        """Build a 4-visit schedule centered on the registration date.
        ANC is in the past (skipped from FU rate). PNC/1-Week/1-Month
        spread across grace and expiry boundaries.
        """
        anc = today + timedelta(days=-anchor_days_ago - 30)
        pnc = today + timedelta(days=-anchor_days_ago - 5)
        wk1 = today + timedelta(days=-anchor_days_ago + 5)
        mo1 = today + timedelta(days=-anchor_days_ago + 25)
        return [
            {
                "visit_type": "ANC Visit",
                "visit_date_scheduled": anc.isoformat(),
                "visit_expiry_date": (anc + timedelta(days=14)).isoformat(),
                "mother_case_id": mid,
            },
            {
                "visit_type": "Postnatal Delivery Visit",
                "visit_date_scheduled": pnc.isoformat(),
                "visit_expiry_date": (pnc + timedelta(days=14)).isoformat(),
                "mother_case_id": mid,
            },
            {
                "visit_type": "1 Week Visit",
                "visit_date_scheduled": wk1.isoformat(),
                "visit_expiry_date": (wk1 + timedelta(days=14)).isoformat(),
                "mother_case_id": mid,
            },
            {
                "visit_type": "1 Month Visit",
                "visit_date_scheduled": mo1.isoformat(),
                "visit_expiry_date": (mo1 + timedelta(days=14)).isoformat(),
                "mother_case_id": mid,
            },
        ]

    registrations = [
        {
            "mother_case_id": "M1",
            "eligible_full_intervention_bonus": "1",
            "schedules": _schedules("M1", 60),
        },
        {
            "mother_case_id": "M2",
            "eligible_full_intervention_bonus": "1",
            "schedules": _schedules("M2", 55),
        },
        {
            "mother_case_id": "M3",
            "eligible_full_intervention_bonus": "1",
            "schedules": _schedules("M3", 58),
        },
        {
            "mother_case_id": "M4",
            "eligible_full_intervention_bonus": "0",
            "schedules": _schedules("M4", 40),
        },
    ]

    gs_forms = [
        {"user_connect_id": "alice", "gs_score": 78.5},
        {"user_connect_id": "alice", "gs_score": 81.0},  # max wins
        {"user_connect_id": "bob", "gs_score": 45.0},  # red flag
        # carol has no GS form → gs_score should be None
    ]

    return {
        "current_date": current_date_str,
        "active_usernames": ["alice", "bob", "carol"],
        "flw_names": {"alice": "Alice A", "bob": "Bob B", "carol": "Carol C"},
        "visits": visits,
        "visits_agg": visits_agg,
        "registrations": registrations,
        "gs_forms": gs_forms,
    }


def fixture_for_tab2(current_date_str: str = "2025-06-01") -> dict:
    """Same fixture, but with task_filters set for Tab 2 baseline test."""
    f = build_fixture(current_date_str)
    # Trigger task for bob 21 days ago — baseline rate is computed as-of that date
    trigger_dt = (date.fromisoformat(current_date_str) + timedelta(days=-21)).isoformat()
    f["task_filters"] = {"bob": trigger_dt + "T12:00:00Z"}
    return f


def fixture_edge_cases(current_date_str: str = "2025-06-01") -> dict:
    """Pathological inputs that exercise NULL handling, empty datasets, and
    boundary conditions in both v4 and v5.

    Coverage:
      - FLW with zero visits → should appear with all-null metrics
      - FLW with one visit (no GPS distance, no inter-visit gap)
      - Mother with empty schedules list
      - Mother with schedules entries having empty visit_type
      - bf_status edge cases: "ebf and pumping" (multi-token w/ ebf),
        "non-ebf" (contains "ebf" but as substring, not whole token),
        "" (empty), missing
      - Two visits at exact same timestamp (sort stability)
      - GPS distance NaN
      - GS form with missing user_connect_id (falls back to username)
    """
    today = date.fromisoformat(current_date_str)
    dt = lambda days, h=12, m=0: (today + timedelta(days=days)).isoformat() + f"T{h:02d}:{m:02d}:00Z"

    visits = [
        # dave: ONE visit only, no GPS, no prior visit to gap-compute
        {
            "username": "dave",
            "mother_case_id": "M10",
            "visit_datetime": dt(-30),
            "time_start": dt(-30, h=11, m=50),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "ebf and pumping",  # multi-token, "ebf" present
            "antenatal_visit_completion": "ok",
            "latitude": None,
            "longitude": None,
            "distance_from_prev_case_visit_m": None,
        },
        # eve: tokenization edge — "non-ebf" should NOT match contains_word "ebf"
        # because contains_word treats it as a whitespace-separated token list.
        {
            "username": "eve",
            "mother_case_id": "M11",
            "visit_datetime": dt(-25),
            "time_start": dt(-25, h=10, m=20),
            "form_name": "Postnatal Delivery Visit",
            "bf_status": "non-ebf",  # one token, doesn't equal "ebf"
            "antenatal_visit_completion": "ok",
            "latitude": 0.50,
            "longitude": 0.50,
            "distance_from_prev_case_visit_m": None,
        },
        # eve: SAME timestamp as previous (sort stability — both should be
        # processed; last-write-wins is deterministic regardless).
        {
            "username": "eve",
            "mother_case_id": "M11",
            "visit_datetime": dt(-25),
            "time_start": dt(-25, h=10, m=20),
            "form_name": "1 Week Visit",
            "bf_status": "",  # empty bf_status — should NOT increment bf_count
            "antenatal_visit_completion": "",
            "latitude": 0.51,
            "longitude": 0.51,
            "distance_from_prev_case_visit_m": float("nan"),  # NaN → skipped
        },
        # eve: a third visit so meaningful GPS stats exist
        {
            "username": "eve",
            "mother_case_id": "M11",
            "visit_datetime": dt(-20),
            "time_start": dt(-20, h=10, m=15),
            "form_name": "1 Month Visit",
            "bf_status": "ebf",
            "antenatal_visit_completion": "",
            "latitude": 0.52,
            "longitude": 0.52,
            "distance_from_prev_case_visit_m": 22.0,
        },
    ]

    visits_agg = [
        # Reflects post-aggregation counts. fran has none → not present.
        # Note bf_count for eve = 2 (the "non-ebf" one + the "ebf" one;
        # the empty string row was not counted by v4's SQL filter).
        {"username": "dave", "num_mothers": 1, "bf_count": 1, "ebf_count": 1},
        {"username": "eve", "num_mothers": 1, "bf_count": 2, "ebf_count": 1},
    ]

    registrations = [
        {
            "mother_case_id": "M10",
            "eligible_full_intervention_bonus": "1",
            "schedules": [
                {
                    "visit_type": "Postnatal Delivery Visit",
                    "visit_date_scheduled": (today + timedelta(days=-35)).isoformat(),
                    "visit_expiry_date": (today + timedelta(days=-21)).isoformat(),
                    "mother_case_id": "M10",
                },
                # An entry with empty visit_type — should be tolerated (treated
                # as non-matching, not a crash).
                {
                    "visit_type": "",
                    "visit_date_scheduled": (today + timedelta(days=-20)).isoformat(),
                    "visit_expiry_date": (today + timedelta(days=-6)).isoformat(),
                    "mother_case_id": "M10",
                },
            ],
        },
        {
            "mother_case_id": "M11",
            "eligible_full_intervention_bonus": "0",  # NOT eligible
            "schedules": [],  # empty schedules list
        },
        # Mother M12 registered but no visits attributed
        {
            "mother_case_id": "M12",
            "eligible_full_intervention_bonus": "1",
            "schedules": [
                {
                    "visit_type": "Postnatal Delivery Visit",
                    "visit_date_scheduled": (today + timedelta(days=-35)).isoformat(),
                    "visit_expiry_date": (today + timedelta(days=-21)).isoformat(),
                    "mother_case_id": "M12",
                },
            ],
        },
    ]

    gs_forms = [
        # dave has user_connect_id; eve has username only (fallback path)
        {"user_connect_id": "dave", "gs_score": 92.0},
        {"username": "eve", "gs_score": 38.0},
    ]

    # fran is in active_usernames but has NO visits — should appear with all
    # null/zero metrics (visits_completed=0, etc.).
    return {
        "current_date": current_date_str,
        "active_usernames": ["dave", "eve", "fran"],
        "flw_names": {"dave": "Dave D", "eve": "Eve E", "fran": "Fran F"},
        "visits": visits,
        "visits_agg": visits_agg,
        "registrations": registrations,
        "gs_forms": gs_forms,
    }


def fixture_tab2_edge(current_date_str: str = "2025-06-01") -> dict:
    """Tab 2 edge cases:
      - FLW in task_filters who has zero visits (bug: divide by zero)
      - FLW in task_filters whose trigger date is BEFORE any of their visits
        (baseline numerator/denominator should be 0 → rate=None)
      - FLW in task_filters whose trigger date is AFTER all their visits
        (baseline should reflect all-time history, then post-trigger fields
        should be empty for Tab 2's active visits scan)
    """
    f = build_fixture(current_date_str)
    today = date.fromisoformat(current_date_str)
    # Bob's task triggered way in the future (after all visits) — Tab 2's
    # task_filters-aware scan should skip all his visits.
    future_trigger = (today + timedelta(days=10)).isoformat() + "T12:00:00Z"
    # Alice's task triggered before any visits she has — baseline should be 0/0.
    past_trigger = (today + timedelta(days=-200)).isoformat() + "T12:00:00Z"
    f["task_filters"] = {"bob": future_trigger, "alice": past_trigger}
    return f
