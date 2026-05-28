"""Time-to-completion bins — port of R time_to_completion.R.

Bins survey duration (minutes) into <10 / 10-15 / 15-20 / 20-30 / >30 with
counts + percentages. Accepts either a precomputed `duration_min` column or
`started_time` + `completed_time` to difference.
"""

from __future__ import annotations

import pandas as pd

BIN_EDGES = [0, 10, 15, 20, 30, float("inf")]
BIN_LABELS = ["<10 min", "10-15 min", "15-20 min", "20-30 min", ">30 min"]


def time_to_completion(visits: pd.DataFrame) -> dict:
    df = visits
    if "duration_min" in df.columns:
        dur = pd.to_numeric(df["duration_min"], errors="coerce")
    elif {"started_time", "completed_time"}.issubset(df.columns):
        start = pd.to_datetime(df["started_time"], errors="coerce")
        end = pd.to_datetime(df["completed_time"], errors="coerce")
        dur = (end - start).dt.total_seconds() / 60.0
    else:
        return {"count": 0, "bins": []}

    dur = dur.dropna()
    dur = dur[dur >= 0]
    n = len(dur)
    if n == 0:
        return {"count": 0, "bins": []}

    cats = pd.cut(dur, bins=BIN_EDGES, labels=BIN_LABELS, right=True, include_lowest=True)
    counts = cats.value_counts().reindex(BIN_LABELS, fill_value=0)
    return {
        "count": n,
        "avg_min": round(float(dur.mean()), 1),
        "median_min": round(float(dur.median()), 1),
        "min_min": round(float(dur.min()), 1),
        "max_min": round(float(dur.max()), 1),
        "bins": [{"label": lbl, "count": int(c), "pct": round(100.0 * c / n, 1)} for lbl, c in counts.items()],
    }
