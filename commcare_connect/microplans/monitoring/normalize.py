"""Map raw Connect/CommCare visit rows → the canonical monitoring schema.

Port of R cleaning.R `apply_mapping`: the rooftop survey's form-field names vary
per deployment, so callers pass a `field_map` of {canonical_column: source_field}.
Missing source fields are tolerated (the canonical column comes through as NaN),
exactly like the R `.getcol` helper.

Canonical columns consumed downstream by derive/rollups/duration/gps_issue:
    sample_id, cluster, enumerator, arm, submission_time, date_local,
    distance_m, believed_reached_reason, survey_completed_flag,
    revisit_required_flag, inhabited_flag, fallback_distance_m,
    contact_made, eligible_flag, target_lat, target_lon, arrival_lat,
    arrival_lon, cannot_reach_reason, screenshot_link, duration_min
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = [
    "sample_id",
    "cluster",
    "enumerator",
    "arm",
    "submission_time",
    "distance_m",
    "believed_reached_reason",
    "survey_completed_flag",
    "revisit_required_flag",
    "inhabited_flag",
    "fallback_distance_m",
    "contact_made",
    "eligible_flag",
    "target_lat",
    "target_lon",
    "arrival_lat",
    "arrival_lon",
    "cannot_reach_reason",
    "screenshot_link",
    "duration_min",
]

# A reasonable default mapping for a work-area-case rooftop survey. Override per opp.
DEFAULT_FIELD_MAP = {
    "sample_id": "work_area_id",
    "cluster": "cluster",
    "enumerator": "username",
    "arm": "arm",
    "submission_time": "visit_date",
    "distance_m": "distance_target_pin_from_arrival_point",
    "believed_reached_reason": "believed_reached_reason",
    "survey_completed_flag": "interview_outcome",
    "revisit_required_flag": "revisit_required",
    "inhabited_flag": "pin_inhabited_residential",
    "fallback_distance_m": "fallback_distance_m",
    "contact_made": "contact_made",
    "eligible_flag": "eligible",
    "target_lat": "target_lat",
    "target_lon": "target_lon",
    "arrival_lat": "arrival_lat",
    "arrival_lon": "arrival_lon",
    "cannot_reach_reason": "cannot_reach_reason",
    "screenshot_link": "screenshot",
    "duration_min": "duration_min",
}


def normalize_visits(raw: pd.DataFrame, field_map: dict | None = None) -> pd.DataFrame:
    field_map = field_map or DEFAULT_FIELD_MAP
    n = len(raw)
    out = pd.DataFrame(index=range(n))
    for canonical in CANONICAL_COLUMNS:
        source = field_map.get(canonical)
        out[canonical] = raw[source].to_numpy() if source and source in raw.columns else np.nan

    out["submission_time"] = pd.to_datetime(out["submission_time"], errors="coerce", utc=True)
    out["date_local"] = out["submission_time"].dt.date
    for numeric in (
        "distance_m",
        "fallback_distance_m",
        "duration_min",
        "target_lat",
        "target_lon",
        "arrival_lat",
        "arrival_lon",
    ):
        out[numeric] = pd.to_numeric(out[numeric], errors="coerce")
    return out
