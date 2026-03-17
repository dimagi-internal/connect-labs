"""
Shared v1 data transformation functions for MBW monitoring.

These pure functions convert VisitRow objects (the native pipeline output)
into the shapes that the shared computation functions (GPS analysis,
follow-up rates, quality metrics) expect.

Extracted from the inline code in MBWMonitoringStreamView (views.py) so that
both the SSE view (v1) and the parity test can share the same transformations
without duplication.
"""

from __future__ import annotations


def build_gps_visit_dicts(
    pipeline_rows,
    active_usernames: set[str],
) -> list[dict]:
    """Build GPS visit dicts from VisitRow objects, filtered by active FLWs.

    Converts pipeline VisitRow objects into the dict format expected by
    ``analyze_gps_metrics``.  Only includes rows whose lowercased username
    is in *active_usernames*.

    Extracted from views.py (MBWMonitoringStreamView).
    """
    visits_for_gps = []
    for row in pipeline_rows:
        row_username = (row.username or "").lower()
        if row_username not in active_usernames:
            continue
        gps_location = None
        if row.latitude is not None and row.longitude is not None:
            gps_location = f"{row.latitude} {row.longitude}"

        visits_for_gps.append(
            {
                "id": row.id,
                "username": row_username,
                "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                "entity_name": row.entity_name,
                "computed": row.computed,
                "metadata": {"location": gps_location},
            }
        )
    return visits_for_gps


def extract_per_mother_fields(pipeline_rows) -> dict:
    """Extract parity, ANC date, PNC date, baby DOB from pipeline VisitRows.

    Returns a dict with keys: ``parity_by_mother``, ``anc_date_by_mother``,
    ``pnc_date_by_mother``, ``baby_dob_by_mother``.

    Extracted from views.py (MBWMonitoringStreamView).
    """
    parity_by_mother: dict[str, str] = {}
    anc_date_by_mother: dict[str, str] = {}
    pnc_date_by_mother: dict[str, str] = {}
    baby_dob_by_mother: dict[str, str] = {}
    for row in pipeline_rows:
        form_name = row.computed.get("form_name", "").strip()
        mother_id = row.computed.get("mother_case_id")
        if not mother_id:
            continue
        if form_name == "ANC Visit":
            parity = row.computed.get("parity")
            if parity:
                parity_by_mother[mother_id] = parity
            anc_date = row.computed.get("anc_completion_date")
            if anc_date:
                anc_date_by_mother[mother_id] = anc_date
        elif form_name == "Post delivery visit":
            pnc_date = row.computed.get("pnc_completion_date")
            if pnc_date:
                pnc_date_by_mother[mother_id] = pnc_date
            baby_dob = row.computed.get("baby_dob")
            if baby_dob:
                baby_dob_by_mother[mother_id] = baby_dob
    return {
        "parity_by_mother": parity_by_mother,
        "anc_date_by_mother": anc_date_by_mother,
        "pnc_date_by_mother": pnc_date_by_mother,
        "baby_dob_by_mother": baby_dob_by_mother,
    }


def compute_ebf_by_flw(pipeline_rows) -> dict[str, int]:
    """Compute % exclusive breastfeeding per FLW from pipeline VisitRows.

    Returns a dict mapping lowercased username to integer percentage (0-100).

    Extracted from views.py (MBWMonitoringStreamView).
    """
    ebf_counts_by_flw: dict[str, dict] = {}
    for row in pipeline_rows:
        bf_status = (row.computed.get("bf_status") or "").strip()
        if not bf_status:
            continue
        username = (row.username or "").strip().lower()
        if not username:
            continue
        if username not in ebf_counts_by_flw:
            ebf_counts_by_flw[username] = {"ebf": 0, "total": 0}
        ebf_counts_by_flw[username]["total"] += 1
        if "ebf" in bf_status.split():
            ebf_counts_by_flw[username]["ebf"] += 1

    ebf_pct_by_flw: dict[str, int] = {}
    for username, counts in ebf_counts_by_flw.items():
        if counts["total"] > 0:
            ebf_pct_by_flw[username] = round(counts["ebf"] / counts["total"] * 100)
    return ebf_pct_by_flw
