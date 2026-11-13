"""A household CSV, in place of Connect visits: how real data from a CommCare export reaches the check.

Niger SMC ran on CommCare, which the Data query does not offer, so the real campaign comes in as a file the person
exports and uploads. One row per household and cycle:

    facility_id, household_id, lat, lon, cycle, children_served[, accuracy_m]

The file is read in memory for one request and never stored; what leaves is counts and aggregated squares, never a
household or a point. Pure: pandas only, no Django (the view hands over the bytes).
"""

from __future__ import annotations

import io

import pandas as pd

from connect_labs.microplans.coverage import check

REQUIRED = ["facility_id", "household_id", "lat", "lon", "cycle", "children_served"]
OPTIONAL = ["accuracy_m"]
MAX_BYTES = 60 * 1024 * 1024
MAX_ROWS = 800_000
#: More unreadable rows than this share and the file is the wrong shape, not a few dirty rows.
MAX_BAD_SHARE = 0.2

TEMPLATE = (
    ",".join(REQUIRED + OPTIONAL) + "\n"
    "# EXAMPLE ROWS: replace with your export, one row per household and cycle,\n"
    "CSI-001,hh-0001,13.51186,7.09189,1,2,12\n"
    "CSI-001,hh-0001,13.51186,7.09189,2,1,12\n"
    "CSI-002,hh-0002,13.49021,7.11422,1,3,8\n"
)


class UploadError(ValueError):
    """The file cannot be used; the message says what to fix."""


def parse(data: bytes) -> tuple[list, dict, dict]:
    """CSV bytes -> ``(rows, served, summary)`` in the shapes the check takes.

    ``rows`` are ``(facility, household, "lat lon alt accuracy" or None, "cycle,cycle")`` like ``points_sql``;
    ``served`` is ``{household: {cycle: children served}}`` like ``check.parse_served``."""
    if len(data) > MAX_BYTES:
        raise UploadError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MB; split it by district")
    try:
        frame = pd.read_csv(io.BytesIO(data), dtype=str, comment="#", skip_blank_lines=True, keep_default_na=False)
    except Exception as e:  # noqa: BLE001 -- pandas raises several types for a malformed file
        raise UploadError(f"could not read the file as CSV ({str(e)[:120]})") from e
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise UploadError(f"missing column(s): {', '.join(missing)}. Expected: {', '.join(REQUIRED + OPTIONAL)}")
    if len(frame) > MAX_ROWS:
        raise UploadError(f"more than {MAX_ROWS:,} rows; split it by district")
    if not len(frame):
        raise UploadError("the file has a header but no rows")

    frame["_row"] = range(2, len(frame) + 2)  # spreadsheet row numbers: the header is row 1
    for col in ("facility_id", "household_id", "cycle"):
        frame[col] = frame[col].astype(str).str.strip()
    served_n = pd.to_numeric(frame["children_served"].str.strip(), errors="coerce")
    bad = (
        (frame["household_id"] == "")
        | (frame["facility_id"] == "")
        | (frame["cycle"] == "")
        | served_n.isna()
        | (served_n < 0)
    )
    examples = [int(r) for r in frame.loc[bad, "_row"].head(3)]
    if bad.mean() > MAX_BAD_SHARE:
        raise UploadError(
            f"{int(bad.sum()):,} of {len(frame):,} rows are unreadable (a blank facility, household or cycle, or "
            f"children_served that is not a number of 0 or more). First rows: {', '.join(map(str, examples))}"
        )
    good = frame[~bad].assign(_n=served_n[~bad].astype(int))
    lat = pd.to_numeric(good["lat"].str.strip(), errors="coerce")
    lon = pd.to_numeric(good["lon"].str.strip(), errors="coerce")
    acc = (
        pd.to_numeric(good["accuracy_m"].str.strip(), errors="coerce")
        if "accuracy_m" in good.columns
        else pd.Series(float("nan"), index=good.index)
    )
    good = good.assign(_lat=lat, _lon=lon, _acc=acc)

    served: dict = {}
    for hh, cyc, n in zip(good["household_id"], good["cycle"], good["_n"]):
        c = check.cycle_number(cyc)
        if c is None:
            continue
        slot = served.setdefault(hh, {})
        slot[c] = slot.get(c, 0) + int(n)

    rows = []
    for hh, one in good.groupby("household_id", sort=False):
        located = one.dropna(subset=["_lat", "_lon"])
        if len(located):
            first = located.iloc[0]
            accuracy = "" if pd.isna(first["_acc"]) else f"{first['_acc']:g}"
            gps = f"{first['_lat']:.7f} {first['_lon']:.7f} 0.0 {accuracy}".strip()
        else:
            gps = None
        cycles = ",".join(sorted(set(one["cycle"])))
        rows.append((one["facility_id"].iloc[0], hh, gps, cycles))
    summary = {
        "rows": int(len(frame)),
        "households": len(rows),
        "bad_rows": int(bad.sum()),
        "bad_row_examples": examples,
    }
    return rows, served, summary
