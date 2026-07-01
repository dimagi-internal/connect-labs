"""GPS-issue review report — port of R derive_status.R `build_gps_issue_report`.

Surfaces the attempts where the FLW proceeded on the "I believe I'm at the pin"
override AND the GPS distance was > 25m — the cases a reviewer should eyeball
(with the map screenshot the FLW attached). Banded 26-50m vs >50m.
"""

from __future__ import annotations

import pandas as pd

REVIEW_DISTANCE_M = 25.0


def build_gps_issue_report(visits: pd.DataFrame) -> pd.DataFrame:
    if visits.empty:
        return pd.DataFrame()
    df = visits.copy()
    distance_m = pd.to_numeric(df.get("distance_m"), errors="coerce")
    flag = df["proceed_when_believed"] & distance_m.gt(REVIEW_DISTANCE_M)
    rep = df[flag].copy()
    if rep.empty:
        return pd.DataFrame()

    d = pd.to_numeric(rep.get("distance_m"), errors="coerce")
    rep["distance_from_pin_m"] = d
    rep["distance_category"] = d.apply(lambda x: ">50m" if x > 50 else "26-50m")
    keep = [
        c
        for c in [
            "cluster",
            "sample_id",
            "enumerator",
            "arm",
            "target_lat",
            "target_lon",
            "arrival_lat",
            "arrival_lon",
            "distance_from_pin_m",
            "distance_category",
            "cannot_reach_reason",
            "screenshot_link",
        ]
        if c in rep.columns
    ]
    return rep[keep].reset_index(drop=True)
