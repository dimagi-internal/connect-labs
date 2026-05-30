"""Coverage-mode monitoring lens — per-work-area completion.

Sampling monitoring asks "did the FLW reach the pinned household within 15m and
complete the survey". Coverage asks a different question: "of the households we
expect in this work area, how many have been visited" — progress toward visiting
*everyone*, per area and overall.

Inputs:
  * canonical visits (one row per visit), via normalize_visits — we count
    distinct households (sample_id) per cluster;
  * `expected_by_cluster`: {cluster_id: expected_visit_count}, taken from the
    saved coverage frame (each cluster polygon carries expected_visit_count).

Pure and DataFrame-in/dict-out, like pipeline.compute_monitoring.
"""

from __future__ import annotations

import pandas as pd

from commcare_connect.microplans.monitoring.normalize import normalize_visits

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETE = "complete"


def expected_from_areas(areas_geojson: dict) -> dict:
    """{cluster: expected_visit_count} from a coverage frame's cluster polygons."""
    out: dict[str, int] = {}
    for feat in (areas_geojson or {}).get("features", []):
        props = feat.get("properties", {})
        cluster = props.get("cluster")
        if cluster is None:
            continue
        expected = props.get("expected_visit_count", props.get("building_count", 0))
        out[str(cluster)] = int(expected or 0)
    return out


def _status(visited: int, expected: int) -> str:
    if visited <= 0:
        return STATUS_NOT_STARTED
    if expected and visited >= expected:
        return STATUS_COMPLETE
    return STATUS_IN_PROGRESS


def compute_coverage_monitoring(
    raw_visits: pd.DataFrame,
    expected_by_cluster: dict | None = None,
    field_map: dict | None = None,
) -> dict:
    """Return a coverage dashboard payload: per-work-area + summary + daily progress."""
    expected_by_cluster = {str(k): int(v) for k, v in (expected_by_cluster or {}).items()}
    canonical = normalize_visits(raw_visits, field_map)

    # Households visited per cluster = distinct sample_id (fall back to row count).
    visited_by_cluster: dict[str, int] = {}
    daily: list[dict] = []
    if not canonical.empty and "cluster" in canonical.columns:
        df = canonical.copy()
        df["cluster"] = df["cluster"].astype("object")
        use_distinct = "sample_id" in df.columns and df["sample_id"].notna().any()
        for cluster, sub in df.groupby("cluster"):
            if cluster is None or (isinstance(cluster, float) and pd.isna(cluster)):
                continue
            visited_by_cluster[str(cluster)] = (
                int(sub["sample_id"].dropna().nunique()) if use_distinct else int(len(sub))
            )
        if "date_local" in df.columns and df["date_local"].notna().any():
            grp = df.dropna(subset=["date_local"]).groupby("date_local")
            for day, sub in grp:
                visited = int(sub["sample_id"].dropna().nunique()) if use_distinct else int(len(sub))
                daily.append({"date": str(day), "households_visited": visited})
            daily.sort(key=lambda r: r["date"])

    # One row per known cluster (union of expected + observed).
    clusters = sorted(set(expected_by_cluster) | set(visited_by_cluster))
    per_cluster: list[dict] = []
    for cluster in clusters:
        expected = expected_by_cluster.get(cluster, 0)
        visited = visited_by_cluster.get(cluster, 0)
        per_cluster.append(
            {
                "cluster": cluster,
                "expected": expected,
                "visited": visited,
                "remaining": max(0, expected - visited),
                "coverage_pct": round(100.0 * visited / expected, 1) if expected else None,
                "status": _status(visited, expected),
            }
        )

    total_expected = sum(expected_by_cluster.values())
    total_visited = sum(visited_by_cluster.values())
    counts = {STATUS_NOT_STARTED: 0, STATUS_IN_PROGRESS: 0, STATUS_COMPLETE: 0}
    for row in per_cluster:
        counts[row["status"]] += 1

    return {
        "summary": {
            "work_areas": len(per_cluster),
            "complete": counts[STATUS_COMPLETE],
            "in_progress": counts[STATUS_IN_PROGRESS],
            "not_started": counts[STATUS_NOT_STARTED],
            "total_expected": total_expected,
            "total_visited": total_visited,
            "coverage_pct": round(100.0 * total_visited / total_expected, 1) if total_expected else None,
        },
        "per_cluster": per_cluster,
        "daily": daily,
    }
