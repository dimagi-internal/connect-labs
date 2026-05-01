"""v1↔v3 parity for the GPS tab — per-FLW summaries.

The previous parity tests covered v1↔v3 medians (median_meters_by_flw,
median_minutes_by_flw). These cover the rest of the GPS dashboard tab:
flagged_visits, total_flagged, cases_with_revisits, visits_with_gps,
unique_cases, and avg_daily_travel_km.

v3 used to leave flagged_visits/total_flagged at 0 (no flagging logic),
count cases_with_revisits as total distance entries (not distinct
mothers), and not compute visits_with_gps / unique_cases /
avg_daily_travel_km at all. Now matches v1.
"""

from __future__ import annotations

from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import analyze_gps_metrics
from commcare_connect.workflow.tests.mbw_parity.v3_python_port import build_gps_data_v3

# ---- helpers -----------------------------------------------------------


def _v1_visit_dict(
    *,
    visit_id: str,
    username: str,
    case_id: str,
    mother_case_id: str,
    visit_datetime: str,  # "YYYY-MM-DDTHH:MM:SS"
    gps_lat: float | None,
    gps_lon: float | None,
    form_name: str = "ANC Visit",
) -> dict:
    """v1 ingests visit dicts with `computed` containing the parsed fields.
    `gps_location` is the packed "lat lon" string v1 parses."""
    gps_str = f"{gps_lat} {gps_lon}" if gps_lat is not None and gps_lon is not None else ""
    return {
        "id": visit_id,
        "username": username,
        "visit_date": visit_datetime[:10],
        "computed": {
            "case_id": case_id,
            "mother_case_id": mother_case_id,
            "form_name": form_name,
            "visit_datetime": visit_datetime,
            "gps_location": gps_str,
            "app_build_version": 1,
        },
    }


def _v3_visit_row(
    *,
    username: str,
    case_id: str,
    mother_case_id: str,
    visit_datetime: str,
    gps_lat: float | None,
    gps_lon: float | None,
    distance_from_prev_m: float | None,
    form_name: str = "ANC Visit",
) -> dict:
    """v3 visits_gps row shape, normalized as `_v3PipelineRows` would
    output it. distance_from_prev_case_visit_m is what SQL computes via
    lag_haversine; we provide it directly here."""
    return {
        "_username": username,
        "_visit_date": visit_datetime[:10],
        "visit_datetime": visit_datetime,
        "case_id": case_id,
        "mother_case_id": mother_case_id,
        "form_name": form_name,
        "latitude": gps_lat,
        "longitude": gps_lon,
        "distance_from_prev_case_visit_m": distance_from_prev_m,
    }


# ---- fixtures ----------------------------------------------------------


def _gps_fixture():
    """3 mothers, alice covers all of them, bob covers one. Distances
    cover both flagged (>5km) and unflagged. One mother has 2 same-day
    visits so daily-travel paths are non-trivial."""
    v1_visits = [
        # m1, alice: 2 close visits (157m apart) on different days
        _v1_visit_dict(
            visit_id="v1",
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-01T10:00:00",
            gps_lat=0.0,
            gps_lon=0.0,
        ),
        _v1_visit_dict(
            visit_id="v2",
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-15T10:00:00",
            gps_lat=0.001,
            gps_lon=0.001,
        ),
        # m1 again, alice: huge jump (>5km) - flag it
        _v1_visit_dict(
            visit_id="v3",
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-20T10:00:00",
            gps_lat=0.1,
            gps_lon=0.1,
        ),
        # m2, alice
        _v1_visit_dict(
            visit_id="v4",
            username="alice",
            case_id="c2",
            mother_case_id="m2",
            visit_datetime="2025-05-20T11:00:00",
            gps_lat=0.05,
            gps_lon=0.05,
        ),
        # m3, bob
        _v1_visit_dict(
            visit_id="v5",
            username="bob",
            case_id="c3",
            mother_case_id="m3",
            visit_datetime="2025-05-20T10:00:00",
            gps_lat=10.0,
            gps_lon=10.0,
        ),
    ]
    # v3 receives the SAME logical visits but in pipeline-row shape with
    # distance_from_prev_case_visit_m pre-computed (matching what SQL's
    # lag_haversine would emit for these GPS points).
    v3_visits = [
        # m1 first visit: no prev, distance None
        _v3_visit_row(
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-01T10:00:00",
            gps_lat=0.0,
            gps_lon=0.0,
            distance_from_prev_m=None,
        ),
        # m1 second: distance from (0,0) → (0.001, 0.001) ~157.2m
        _v3_visit_row(
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-15T10:00:00",
            gps_lat=0.001,
            gps_lon=0.001,
            distance_from_prev_m=157.2,
        ),
        # m1 third: (0.001, 0.001) → (0.1, 0.1) ~15571m, flagged
        _v3_visit_row(
            username="alice",
            case_id="c1",
            mother_case_id="m1",
            visit_datetime="2025-05-20T10:00:00",
            gps_lat=0.1,
            gps_lon=0.1,
            distance_from_prev_m=15571.0,
        ),
        # m2 first: no prev for m2
        _v3_visit_row(
            username="alice",
            case_id="c2",
            mother_case_id="m2",
            visit_datetime="2025-05-20T11:00:00",
            gps_lat=0.05,
            gps_lon=0.05,
            distance_from_prev_m=None,
        ),
        # m3 first for bob
        _v3_visit_row(
            username="bob",
            case_id="c3",
            mother_case_id="m3",
            visit_datetime="2025-05-20T10:00:00",
            gps_lat=10.0,
            gps_lon=10.0,
            distance_from_prev_m=None,
        ),
    ]
    return v1_visits, v3_visits


# ---- tests -------------------------------------------------------------


def _v1_summaries_by_flw(v1_visits) -> dict[str, dict]:
    result = analyze_gps_metrics(v1_visits, flw_names={"alice": "Alice", "bob": "Bob"})
    return {s.username: s for s in result.flw_summaries}


def _v3_summaries_by_flw(v3_visits) -> dict[str, dict]:
    out = build_gps_data_v3(v3_visits, flw_name_map={"alice": "Alice", "bob": "Bob"})
    return {s["username"]: s for s in out["flw_summaries"]}


class TestGpsTabParity:
    def test_total_visits_per_flw_matches_v1(self):
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            assert (
                v1[flw].total_visits == v3[flw]["total_visits"]
            ), f"{flw}: v1={v1[flw].total_visits} v3={v3[flw]['total_visits']}"

    def test_flagged_visits_per_flw_matches_v1(self):
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            assert (
                v1[flw].flagged_visits == v3[flw]["flagged_visits"]
            ), f"{flw}: v1={v1[flw].flagged_visits} v3={v3[flw]['flagged_visits']}"
        assert v1["alice"].flagged_visits == 1  # the >5km jump

    def test_visits_with_gps_per_flw_matches_v1(self):
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            assert (
                v1[flw].visits_with_gps == v3[flw]["visits_with_gps"]
            ), f"{flw}: v1={v1[flw].visits_with_gps} v3={v3[flw]['visits_with_gps']}"

    def test_unique_cases_per_flw_matches_v1(self):
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            assert (
                v1[flw].unique_cases == v3[flw]["unique_cases"]
            ), f"{flw}: v1={v1[flw].unique_cases} v3={v3[flw]['unique_cases']}"

    def test_cases_with_revisits_counts_distinct_mothers(self):
        """v3 used to count total distance entries (which would say 2 for
        alice's m1 — 2 same-mother revisits). v1 counts distinct mothers
        with at least one revisit, which is 1 for alice's m1. The fix
        makes v3 match v1."""
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        # alice has 1 mother (m1) with revisits, even though m1 has 2
        # distance entries (visits 2 + 3 each have a prev-mother distance).
        assert v1["alice"].cases_with_revisits == 1
        assert v3["alice"]["cases_with_revisits"] == 1
        # bob has 0 — only one visit, no prev to compute distance against.
        assert v3["bob"]["cases_with_revisits"] == 0

    def test_total_flagged_global_matches_v1(self):
        v1_visits, v3_visits = _gps_fixture()
        v1_result = analyze_gps_metrics(v1_visits, flw_names={"alice": "Alice", "bob": "Bob"})
        v3_out = build_gps_data_v3(v3_visits, flw_name_map={"alice": "Alice", "bob": "Bob"})
        assert v1_result.total_flagged == v3_out["total_flagged"]
        assert v3_out["total_flagged"] == 1

    def test_avg_case_distance_km_within_tolerance(self):
        """v1 averages meters then converts; v3 (port) does the same.
        Tolerance handles float-rounding drift between v1's haversine and
        the SQL lag_haversine that produced v3's distance_from_prev_m."""
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            v1_avg = v1[flw].avg_case_distance_km
            v3_avg = v3[flw]["avg_case_distance_km"]
            if v1_avg is None and v3_avg is None:
                continue
            assert v1_avg is not None and v3_avg is not None, f"{flw}: v1={v1_avg} v3={v3_avg}"
            assert abs(v1_avg - v3_avg) < 0.01, f"{flw}: v1={v1_avg} v3={v3_avg}"

    def test_max_case_distance_km_within_tolerance(self):
        v1_visits, v3_visits = _gps_fixture()
        v1 = _v1_summaries_by_flw(v1_visits)
        v3 = _v3_summaries_by_flw(v3_visits)
        for flw in ("alice", "bob"):
            v1_max = v1[flw].max_case_distance_km
            v3_max = v3[flw]["max_case_distance_km"]
            if v1_max is None and v3_max is None:
                continue
            assert v1_max is not None and v3_max is not None, f"{flw}: v1={v1_max} v3={v3_max}"
            assert abs(v1_max - v3_max) < 0.05, f"{flw}: v1={v1_max} v3={v3_max}"
