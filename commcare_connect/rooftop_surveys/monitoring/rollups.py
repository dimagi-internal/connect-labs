"""Per-cluster and per-FLW rollups — port of R derive_status.R rollups.

These are the rooftop monitoring analytics Connect does not compute:
- per-cluster GPS adherence (within-15m rate, GPS-issue rate, barrier rate),
  believed-at-pin distance bands, what-was-found at the pin, fallback/
  substitution usage, completion rate;
- per-FLW-per-day productivity.

Input is a canonical visit DataFrame that has been through
derive.derive_attempt_flags (+ add_attempt_index for enum_daily).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rate(numer, denom):
    return round(100.0 * numer / denom, 1) if denom else 0.0


def _s(df, name, default=""):
    return df[name].astype("object").fillna(default) if name in df.columns else pd.Series([default] * len(df))


def build_cluster_rollup(visits: pd.DataFrame) -> pd.DataFrame:
    """One row per cluster with coverage/GPS/fallback/completion metrics + rates."""
    if visits.empty:
        return pd.DataFrame()
    df = visits.copy()
    distance_m = pd.to_numeric(df.get("distance_m"), errors="coerce")
    inhabited = _s(df, "inhabited_flag")
    fallback_d = pd.to_numeric(df.get("fallback_distance_m"), errors="coerce").fillna(0)
    contact = _s(df, "contact_made")
    eligible = _s(df, "eligible_flag")

    df["_reached"] = df["reached_le15"].astype(int)
    df["_gps_issue"] = df["proceed_when_believed"].astype(int)
    df["_barrier"] = df["cannot_reach"].astype(int)
    df["_b_16_25"] = (df["proceed_when_believed"] & distance_m.gt(15) & distance_m.le(25)).astype(int)
    df["_b_26_50"] = (df["proceed_when_believed"] & distance_m.gt(25) & distance_m.le(50)).astype(int)
    df["_b_over50"] = (df["proceed_when_believed"] & distance_m.gt(50)).astype(int)
    df["_inhabited"] = inhabited.eq("yes").astype(int)
    df["_empty_no_structure"] = inhabited.eq("no_no_structure").astype(int)
    df["_nonresidential"] = inhabited.eq("no_nonresidential").astype(int)
    df["_uninhabited"] = inhabited.eq("no_uninhabited").astype(int)
    df["_fallback_used"] = fallback_d.gt(0).astype(int)
    df["_completed"] = df["completed"].astype(int)
    df["_contact"] = contact.eq("yes").astype(int)
    df["_eligible"] = eligible.eq("eligible").astype(int)
    df["_ineligible"] = eligible.eq("ineligible").astype(int)

    g = df.groupby("cluster")
    out = g.agg(
        arm=("arm", "first") if "arm" in df.columns else ("cluster", "size"),
        points_attempted=("cluster", "size"),
        reached_within_15m=("_reached", "sum"),
        believed_at_pin_gps_issue=("_gps_issue", "sum"),
        cannot_reach_barrier=("_barrier", "sum"),
        believed_16_25m=("_b_16_25", "sum"),
        believed_26_50m=("_b_26_50", "sum"),
        believed_over_50m=("_b_over50", "sum"),
        target_inhabited=("_inhabited", "sum"),
        empty_no_structure=("_empty_no_structure", "sum"),
        nonresidential=("_nonresidential", "sum"),
        uninhabited=("_uninhabited", "sum"),
        fallback_used=("_fallback_used", "sum"),
        surveys_completed=("_completed", "sum"),
        contact_made_total=("_contact", "sum"),
        eligible_households=("_eligible", "sum"),
        ineligible_households=("_ineligible", "sum"),
    ).reset_index()

    out["gps_accuracy_rate"] = [_rate(r, p) for r, p in zip(out.reached_within_15m, out.points_attempted)]
    out["gps_issue_rate"] = [_rate(r, p) for r, p in zip(out.believed_at_pin_gps_issue, out.points_attempted)]
    out["barrier_rate"] = [_rate(r, p) for r, p in zip(out.cannot_reach_barrier, out.points_attempted)]
    out["target_occupied_rate"] = [
        _rate(occ, reached + gps)
        for occ, reached, gps in zip(out.target_inhabited, out.reached_within_15m, out.believed_at_pin_gps_issue)
    ]
    out["completion_rate"] = [_rate(c, p) for c, p in zip(out.surveys_completed, out.points_attempted)]
    return out


def build_enum_daily(visits: pd.DataFrame) -> pd.DataFrame:
    """Per-FLW-per-day productivity. Requires attempt_n (add_attempt_index)."""
    if visits.empty:
        return pd.DataFrame()
    df = visits.copy()
    if "date_local" not in df.columns:
        df["date_local"] = pd.to_datetime(df.get("submission_time"), errors="coerce").dt.date
    inhabited = _s(df, "inhabited_flag")
    fallback_d = pd.to_numeric(df.get("fallback_distance_m"), errors="coerce").fillna(0)
    attempt_n = df["attempt_n"] if "attempt_n" in df.columns else pd.Series([1] * len(df), index=df.index)

    df["_reached"] = df["reached_le15"].astype(int)
    df["_inhabited_found"] = ((inhabited.eq("yes")) & (fallback_d.ge(0))).astype(int)
    df["_completed"] = df["completed"].astype(int)
    df["_revisit_req"] = (df["revisit_required"] & attempt_n.eq(1)).astype(int)
    df["_revisit_done"] = (df["completed"] & attempt_n.gt(1)).astype(int)

    g = df.groupby([c for c in ["enumerator", "date_local"] if c in df.columns] or ["cluster"])
    out = g.agg(
        points_attempted=("_reached", "size"),
        unique_targets_touched=("sample_id", "nunique") if "sample_id" in df.columns else ("_reached", "size"),
        targets_reached_le15=("_reached", "sum"),
        inhabited_households_found=("_inhabited_found", "sum"),
        surveys_completed=("_completed", "sum"),
        revisits_required=("_revisit_req", "sum"),
        revisits_completed=("_revisit_done", "sum"),
    ).reset_index()
    return out
