"""Per-attempt flag derivation — port of R derive_status.R `derive_attempt_flags`.

Operates on a canonical visit DataFrame (one row per visit *attempt*). These are
the rooftop-specific signals Connect's generic visit status does NOT compute:
the 15m GPS gate, the operator "I believe I'm at the pin" override, the
cannot-reach/barrier case, and the GPS-issue case (believed but not within 15m).

Canonical columns expected (missing ones are tolerated → flags default False):
    sample_id, cluster, enumerator, arm, submission_time,
    distance_m                  (target↔arrival distance, meters)
    believed_reached_reason     ("believe_i_am_at_pin" | "cannot_reach_target_pin" | ...)
    survey_completed_flag       ("complete" | "revisit_required" | ...)
    revisit_required_flag       ("yes" | ...)
    inhabited_flag, fallback_distance_m, contact_made, eligible_flag
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DISTANCE_THRESHOLD_M = 15.0
BELIEVE_AT_PIN = "believe_i_am_at_pin"
CANNOT_REACH = "cannot_reach_target_pin"


def derive_attempt_flags(visits: pd.DataFrame, distance_threshold_m: float = DISTANCE_THRESHOLD_M) -> pd.DataFrame:
    df = visits.copy()

    def col(name, default=np.nan):
        return df[name] if name in df.columns else pd.Series([default] * len(df), index=df.index)

    distance_m = pd.to_numeric(col("distance_m"), errors="coerce")
    reason = col("believed_reached_reason", "").astype("object").fillna("")
    completed_flag = col("survey_completed_flag", "").astype("object").fillna("")
    revisit_flag = col("revisit_required_flag", "").astype("object").fillna("")

    df["reached_le15"] = distance_m.le(distance_threshold_m).fillna(False)
    df["believed_reached"] = reason.eq(BELIEVE_AT_PIN)
    df["cannot_reach"] = reason.eq(CANNOT_REACH)
    df["proceed_when_believed"] = df["believed_reached"] & ~df["reached_le15"]
    df["completed"] = completed_flag.eq("complete")
    df["revisit_required"] = revisit_flag.eq("yes") | completed_flag.eq("revisit_required")
    return df


def add_attempt_index(visits: pd.DataFrame) -> pd.DataFrame:
    """Add `attempt_n`: per sample_id, 1-based rank ordered by submission_time."""
    df = visits.copy()
    if "sample_id" not in df.columns:
        df["attempt_n"] = 1
        return df
    order = pd.to_datetime(df.get("submission_time"), errors="coerce")
    df = df.assign(_order=order)
    df["attempt_n"] = df.groupby("sample_id")["_order"].rank(method="first").astype(int)
    return df.drop(columns="_order")
