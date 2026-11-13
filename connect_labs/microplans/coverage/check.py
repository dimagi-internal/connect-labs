"""The buildings-reached coverage check, end to end for one campaign.

``reached.py`` answers the geometry; this turns a campaign's recorded visits into that geometry's
inputs and back into a table a report can show:

1. ``points_sql(spec)``: one row per household (its id, its GPS as the form recorded it, the cycles it was
   visited in, and its group, e.g. the health centre), read from the visits relation with the caller's own
   access (the MCP tool runs it through the Data query path, so a person's, a space's and the sensitive-data
   rules all apply). The household's point is the one the form recorded on its FIRST visit and stored on the
   household; every later cycle reuses it.
2. ``run_check(rows, spec, fetch)``: for each group, the catchment (the buildings the microplanner keeps near
   its facility), the points that can place a household (every dropped point counted by kind), and the
   buildings reached in each cycle at the chosen distance.

Pure apart from ``fetch`` (footprints for an area), which is injected: the default asks the microplanner's own
cached Overture reader and applies its own cleaning, so a building here is the building plans use.

Nothing about the campaign is baked in: the form paths, the cycle rule, the facilities and every distance are in
the spec, so another campaign is another spec, not another module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from connect_labs.microplans.core.filters import FilterConfig
from connect_labs.microplans.core.geo import project_to_meters
from connect_labs.microplans.coverage import reached

_PATH = re.compile(r"^[A-Za-z0-9_@.\-]+$")
#: A catchment is searched for within this radius of its facility before the nearest K are kept.
MAX_SEARCH_RADIUS_M = 3000.0


class SpecError(ValueError):
    """The check's settings cannot be run; the message says which."""


def _sql_path(path: str, what: str) -> str:
    if not isinstance(path, str) or not _PATH.match(path):
        raise SpecError(f"{what} must be a dotted form path such as form.group.question, got {path!r}")
    parts = path.split(".")
    return "form_json #>> '{" + ",".join(parts) + "}'"


@dataclass
class CheckSpec:
    """Everything that varies between campaigns. Defaults are the SMC (eCHIS Niger) form's own paths."""

    household_path: str = "form.var_menage.case_id_menage"
    gps_paths: tuple = ("form.administration_dose.geolocalisation", "form.geolocalisation_nonelligble")
    cycle_path: str = "form.var.passage"
    group_path: str = "form.localisation.csi_id"
    #: Facilities that define the catchments: [{"id": <group value>, "lon": .., "lat": ..}, ...].
    facilities: list = field(default_factory=list)
    #: How a catchment is drawn around a facility: the nearest ``catchment_k`` cleaned buildings, or a fixed radius.
    catchment_k: int | None = 260
    catchment_radius_m: float | None = None
    max_dist_m: float = reached.DEFAULT_MAX_DIST_M
    max_accuracy_m: float | None = reached.DEFAULT_MAX_ACCURACY_M
    #: A point farther than this from its facility is off-area (a bad fix, not a household).
    off_area_m: float = 33000.0
    #: The microplanner's building cleaning (``FilterConfig``): any subset of its fields.
    footprint_filters: dict = field(default_factory=dict)
    #: Also return the catchments gridded into square cells of this many metres (a map's cells); None leaves it out.
    #: A cell holding fewer than ``min_cell_buildings`` buildings is not returned, so a cell never stands for a
    #: household or two (the counts in the tables still include its buildings).
    cell_m: float | None = None
    min_cell_buildings: int = 5
    #: The population lens counts people served: distinct ``child_path`` values per household and cycle on records
    #: where ``served_path`` equals ``served_value`` (defaults: the SMC dose given).
    served_path: str = "form.administration_dose.spaq_recu"
    served_value: str = "oui"
    child_path: str = "form.case.@case_id"

    @classmethod
    def from_dict(cls, data: dict | None) -> CheckSpec:
        data = dict(data or {})
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise SpecError(f"unknown setting(s): {', '.join(unknown)}")
        if "gps_paths" in data:
            data["gps_paths"] = tuple(data["gps_paths"])
        spec = cls(**data)
        spec.validate()
        return spec

    def validate(self) -> None:
        _sql_path(self.household_path, "household_path")
        _sql_path(self.cycle_path, "cycle_path")
        if self.group_path:
            _sql_path(self.group_path, "group_path")
        if not self.gps_paths:
            raise SpecError("gps_paths needs at least one form path")
        for p in self.gps_paths:
            _sql_path(p, "gps_paths")
        if not self.facilities:
            raise SpecError("facilities is required: the catchments are drawn around them")
        for f in self.facilities:
            if not isinstance(f, dict) or not {"id", "lon", "lat"} <= set(f):
                raise SpecError("each facility needs id, lon and lat")
        if (self.catchment_k is None) == (self.catchment_radius_m is None):
            raise SpecError("set exactly one of catchment_k and catchment_radius_m")
        if self.max_dist_m <= 0:
            raise SpecError("max_dist_m must be positive")
        if self.cell_m is not None and not 20 <= self.cell_m <= 2000:
            raise SpecError("cell_m must be between 20 and 2000 metres")
        if self.min_cell_buildings < 1:
            raise SpecError("min_cell_buildings must be at least 1")
        _sql_path(self.served_path, "served_path")
        _sql_path(self.child_path, "child_path")
        if not re.fullmatch(r"[A-Za-z0-9_ \-]{1,40}", str(self.served_value)):
            raise SpecError("served_value must be a short plain word such as oui or yes")
        bad = sorted(set(self.footprint_filters) - set(FilterConfig.__dataclass_fields__))
        if bad:
            raise SpecError(f"unknown footprint filter(s): {', '.join(bad)}")


#: Rows one read may return: well under the Data query's hard cap, so a read is never silently cut.
ROWS_PER_SHARD = 4000


def shard_bounds(households: int, rows_per_shard: int = ROWS_PER_SHARD) -> list[tuple[str, str]]:
    """Hex-prefix ranges ``('00', '3f') ...`` that split the households into reads of about ``rows_per_shard``.

    A household falls in the range its id's md5 prefix does, so the shards partition the households exactly,
    whatever the ids look like."""
    n = max(1, min(256, -(-int(households) // int(rows_per_shard))))
    return [(f"{i * 256 // n:02x}", f"{(i + 1) * 256 // n - 1:02x}") for i in range(n)]


def count_sql(spec: CheckSpec) -> str:
    hh = _sql_path(spec.household_path, "household_path")
    return f"SELECT count(DISTINCT {hh}) AS n FROM visits WHERE {hh} IS NOT NULL"


def points_sql(spec: CheckSpec, shard: tuple[str, str] | None = None) -> str:
    """One row per household: group, household, its GPS text, and the cycle values it was visited in.

    ``shard`` (a ``shard_bounds`` pair) restricts it to the households whose id hashes into that range."""
    gps = "COALESCE(" + ", ".join(_sql_path(p, "gps_paths") for p in spec.gps_paths) + ")"
    hh = _sql_path(spec.household_path, "household_path")
    group = f"min({_sql_path(spec.group_path, 'group_path')})" if spec.group_path else "NULL"
    cyc = _sql_path(spec.cycle_path, "cycle_path")
    where = f"{hh} IS NOT NULL"
    if shard:
        lo, hi = shard
        if not (re.fullmatch(r"[0-9a-f]{2}", lo) and re.fullmatch(r"[0-9a-f]{2}", hi)):
            raise SpecError("a shard is a pair of two-digit hex prefixes")
        where += f" AND left(md5({hh}), 2) BETWEEN '{lo}' AND '{hi}'"
    return (
        f"SELECT {group} AS grp, {hh} AS hh, min({gps}) AS gps, string_agg(DISTINCT {cyc}, ',') AS cycles "
        f"FROM visits WHERE {where} GROUP BY 2 ORDER BY 2"
    )


def served_sql(spec: CheckSpec, shard: tuple[str, str] | None = None) -> str:
    """People served: per household and cycle, how many distinct children the records say were served.

    Same shards as ``points_sql`` (a household falls in the range its id's md5 prefix does)."""
    hh = _sql_path(spec.household_path, "household_path")
    cyc = _sql_path(spec.cycle_path, "cycle_path")
    child = _sql_path(spec.child_path, "child_path")
    served = _sql_path(spec.served_path, "served_path")
    where = f"{hh} IS NOT NULL AND {child} IS NOT NULL AND {served} = '{spec.served_value}'"
    if shard:
        lo, hi = shard
        if not (re.fullmatch(r"[0-9a-f]{2}", lo) and re.fullmatch(r"[0-9a-f]{2}", hi)):
            raise SpecError("a shard is a pair of two-digit hex prefixes")
        where += f" AND left(md5({hh}), 2) BETWEEN '{lo}' AND '{hi}'"
    return (
        f"SELECT {hh} AS hh, {cyc} AS cyc, count(DISTINCT {child}) AS n "
        f"FROM visits WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2"
    )


def parse_served(rows) -> dict:
    """``served_sql`` rows -> {household: {cycle number: children served}}."""
    out: dict = {}
    for hh, cyc, n in rows:
        c = cycle_number(cyc)
        if c is None or n is None:
            continue
        slot = out.setdefault(hh, {})
        slot[c] = slot.get(c, 0) + int(n)
    return out


def parse_gps(text) -> tuple[float, float, float | None] | None:
    """CommCare's geopoint text ``"lat lon altitude accuracy"`` -> ``(lon, lat, accuracy_m)``, or None."""
    if not isinstance(text, str):
        return None
    parts = text.split()
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return None
    try:
        acc = float(parts[3]) if len(parts) > 3 else None
    except ValueError:
        acc = None
    return lon, lat, acc


def cycle_number(text) -> int | None:
    """The cycle a form value names: its last whole number (``contact_3`` -> 3)."""
    found = re.findall(r"\d+", str(text or ""))
    return int(found[-1]) if found else None


def _households(rows) -> pd.DataFrame:
    out = []
    for grp, hh, gps, cycles in rows:
        cyc = {n for n in (cycle_number(c) for c in str(cycles or "").split(",")) if n is not None}
        parsed = parse_gps(gps)
        out.append(
            {
                "grp": grp if grp not in (None, "") else "(no group)",
                "hh": hh,
                "lon": parsed[0] if parsed else None,
                "lat": parsed[1] if parsed else None,
                "accuracy_m": parsed[2] if parsed else None,
                "cycles": cyc,
            }
        )
    return pd.DataFrame(out, columns=["grp", "hh", "lon", "lat", "accuracy_m", "cycles"])


def _meters(lon0, lat0, lon, lat):
    x, y, _ = project_to_meters(
        np.concatenate([[lon0], np.asarray(lon, float)]), np.concatenate([[lat0], np.asarray(lat, float)])
    )
    return np.hypot(x[1:] - x[0], y[1:] - y[0])


def placed_households(rows, spec: CheckSpec) -> tuple[pd.DataFrame, dict]:
    """The households whose recorded point can place them, with the same rules ``run_check`` applies per facility:
    no point and 0,0 dropped, far from the facility dropped (a bad fix), poor accuracy dropped.

    Returns ``(frame with grp, hh, lon, lat, cycles, one row per placed household, quality counts)``. Households of
    a group with no facility cannot be judged for distance and are left out (``run_check`` lists those groups)."""
    hh = _households(rows)
    facilities = {str(f["id"]): f for f in spec.facilities}
    kept, quality = [], {
        "households": int(len(hh)),
        "no_point": 0,
        "null_island": 0,
        "off_area": 0,
        "poor_accuracy": 0,
    }
    for gid, facility in facilities.items():
        mine = hh[hh["grp"].astype(str) == gid]
        located = mine.dropna(subset=["lon", "lat"])
        quality["no_point"] += int(len(mine) - len(located))
        null_island = (located["lon"].abs() < 1e-6) & (located["lat"].abs() < 1e-6)
        quality["null_island"] += int(null_island.sum())
        placed = located[~null_island.to_numpy()]
        if not len(placed):
            continue
        far = (
            _meters(
                float(facility["lon"]),
                float(facility["lat"]),
                placed["lon"].to_numpy(float),
                placed["lat"].to_numpy(float),
            )
            > spec.off_area_m
        )
        quality["off_area"] += int(far.sum())
        near = placed[~far]
        good, q = reached.valid_points(
            near[["lon", "lat", "accuracy_m"]].assign(hh=near["hh"]),
            max_accuracy_m=spec.max_accuracy_m,
            drop_duplicates=False,
        )
        quality["poor_accuracy"] += q.poor_accuracy
        keep = near[near["hh"].isin(set(good["hh"]))]
        kept.append(keep[["grp", "hh", "lon", "lat", "cycles"]])
    frame = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=["grp", "hh", "lon", "lat", "cycles"])
    quality["usable"] = int(len(frame))
    return frame, quality


def default_fetch(filters: dict):
    """Footprints for a circle (lon, lat, radius_m): the microplanner's reader and cleaning."""

    def fetch(lon: float, lat: float, radius_m: float) -> pd.DataFrame:
        from connect_labs.microplans.core.footprints import fetch_buildings
        from connect_labs.microplans.core.geo import point_buffer

        raw = fetch_buildings(point_buffer(lon, lat, radius_m))
        return reached.clean_buildings(raw, FilterConfig(**filters) if filters else None)

    return fetch


def catchment(spec: CheckSpec, facility: dict, fetch) -> tuple[pd.DataFrame, float]:
    """The buildings of one catchment and its radius, drawn from the facility alone (never from the visits)."""
    lon, lat = float(facility["lon"]), float(facility["lat"])
    if spec.catchment_radius_m is not None:
        blds = fetch(lon, lat, float(spec.catchment_radius_m))
        return blds.reset_index(drop=True), float(spec.catchment_radius_m)
    blds = fetch(lon, lat, MAX_SEARCH_RADIUS_M)
    if blds.empty:
        return blds, 0.0
    dist = _meters(lon, lat, blds["lon"].to_numpy(float), blds["lat"].to_numpy(float))
    order = np.argsort(dist, kind="stable")[: int(spec.catchment_k)]
    return blds.iloc[order].reset_index(drop=True), float(dist[order].max())


def cells(blds: pd.DataFrame, masks: dict, lon0: float, lat0: float, size_m: float, min_buildings: int) -> dict:
    """A catchment's buildings gridded into square cells, with how many of each were reached in each cycle.

    ``masks`` maps a cycle to the per-building reached flags. The grid is anchored on the facility, so it does not
    depend on where visits happened. Cells under ``min_buildings`` are left out. Returns the cell size in degrees
    and rows ``[lon, lat, buildings, [reached per cycle, in the order of "cycles"]]`` (the cell's centre)."""
    d_lat = size_m / 111_320.0
    d_lon = size_m / (111_320.0 * max(np.cos(np.radians(lat0)), 1e-6))
    order = sorted(masks)
    gi = np.floor((blds["lon"].to_numpy(float) - lon0) / d_lon).astype(int)
    gj = np.floor((blds["lat"].to_numpy(float) - lat0) / d_lat).astype(int)
    frame = pd.DataFrame({"gi": gi, "gj": gj, "n": 1})
    for c in order:
        frame[f"r{c}"] = masks[c].astype(int)
    grouped = frame.groupby(["gi", "gj"], sort=True).sum().reset_index()
    rows = []
    for rec in grouped.itertuples(index=False):
        if rec.n < min_buildings:
            continue
        reached_counts = [int(getattr(rec, f"r{c}")) for c in order]
        rows.append(
            [
                round(float(lon0 + (rec.gi + 0.5) * d_lon), 6),
                round(float(lat0 + (rec.gj + 0.5) * d_lat), 6),
                int(rec.n),
                reached_counts,
            ]
        )
    return {"size_m": size_m, "d_lon": d_lon, "d_lat": d_lat, "cycles": order, "rows": rows}


def run_check(rows, spec: CheckSpec, fetch=None) -> dict:
    """Run the check over ``rows`` (``points_sql`` rows). ``fetch(lon, lat, radius_m)`` returns cleaned buildings."""
    spec.validate()
    fetch = fetch or default_fetch(spec.footprint_filters)
    hh = _households(rows)
    facilities = {str(f["id"]): f for f in spec.facilities}
    areas = []
    for gid, facility in facilities.items():
        mine = hh[hh["grp"].astype(str) == gid]
        blds, radius = catchment(spec, facility, fetch)
        located = mine.dropna(subset=["lon", "lat"])
        flon, flat = float(facility["lon"]), float(facility["lat"])
        # A device that had no fix reports 0,0: name that before asking whether the point is far from the facility.
        null_island = (located["lon"].abs() < 1e-6) & (located["lat"].abs() < 1e-6)
        placed = located[~null_island.to_numpy()]
        if len(placed):
            far = _meters(flon, flat, placed["lon"].to_numpy(float), placed["lat"].to_numpy(float)) > spec.off_area_m
        else:
            far = np.zeros(0, dtype=bool)
        near = placed[~far]
        kept, quality = reached.valid_points(
            near[["lon", "lat", "accuracy_m"]].assign(hh=near["hh"]),
            max_accuracy_m=spec.max_accuracy_m,
            drop_duplicates=False,
        )
        usable = dict(zip(kept["hh"], zip(kept["lon"], kept["lat"])))
        cycles = sorted({c for s in mine["cycles"] for c in s})
        by_cycle = {}
        masks = {}
        for c in cycles:
            seen = [h for h, s in zip(mine["hh"], mine["cycles"]) if c in s]
            pts = pd.DataFrame([{"lon": usable[h][0], "lat": usable[h][1], "cycle": c} for h in seen if h in usable])
            if not len(pts):
                pts = pd.DataFrame({"lon": [], "lat": [], "cycle": []})
            res = reached.reached_by_cycle(blds, pts, max_dist_m=spec.max_dist_m, cycles=[c])
            if spec.cell_m:
                masks[c] = reached.reached_mask(blds, pts, max_dist_m=spec.max_dist_m)
            by_cycle[c] = {
                "households_visited": len(seen),
                "households_unplaced": sum(1 for h in seen if h not in usable),
                "buildings_reached": res.by_cycle[c],
                "share": (res.by_cycle[c] / len(blds)) if len(blds) else None,
            }
        areas.append(
            {
                "group": gid,
                "buildings": len(blds),
                "catchment_radius_m": round(radius, 1),
                "facility": [flon, flat],
                "households": len(mine),
                "data_quality": {
                    "no_point": int(len(mine) - len(located)),
                    "null_island": int(null_island.sum()),
                    "off_area": int(far.sum()),
                    "poor_accuracy": quality.poor_accuracy,
                    "usable": quality.valid,
                },
                "cycles": by_cycle,
                **(
                    {"cells": cells(blds, masks, flon, flat, spec.cell_m, spec.min_cell_buildings)}
                    if spec.cell_m and len(blds)
                    else {}
                ),
            }
        )
    unknown = sorted(set(hh["grp"].astype(str)) - set(facilities))
    return {
        "settings": {
            "max_dist_m": spec.max_dist_m,
            "catchment": {"k": spec.catchment_k} if spec.catchment_k else {"radius_m": spec.catchment_radius_m},
            "max_accuracy_m": spec.max_accuracy_m,
            "footprint_filters": spec.footprint_filters or "microplanner defaults",
        },
        "areas": areas,
        "groups_without_a_facility": unknown,
        "households_total": int(len(hh)),
    }


def total_row(result: dict) -> dict:
    """All areas together, per cycle: the headline row.

    A cycle's share is out of the buildings of the areas that RAN that cycle (a campaign may run a cycle in
    only some districts), never out of every area's buildings."""
    cycles: dict = {}
    for a in result["areas"]:
        for c, v in a["cycles"].items():
            t = cycles.setdefault(
                c, {"buildings": 0, "buildings_reached": 0, "households_visited": 0, "households_unplaced": 0}
            )
            t["buildings"] += a["buildings"]
            for k in ("buildings_reached", "households_visited", "households_unplaced"):
                t[k] += v[k]
    for t in cycles.values():
        t["share"] = (t["buildings_reached"] / t["buildings"]) if t["buildings"] else None
    return {"buildings": sum(a["buildings"] for a in result["areas"]), "cycles": cycles}


def rollup(result: dict, labels: dict) -> list[dict]:
    """The areas gathered under a label each (``labels``: group id -> e.g. its district), headline row per label.

    A group with no label is gathered under ``"(none)"``. Same rule as ``total_row``: a cycle's share is out of
    the buildings of the areas that ran it. The data-quality counts add up; nothing else is carried."""
    gathered: dict = {}
    for a in result["areas"]:
        gathered.setdefault(labels.get(a["group"]) or "(none)", []).append(a)
    out = []
    for label, areas in sorted(gathered.items()):
        head = total_row({"areas": areas})
        quality = {k: sum(a["data_quality"][k] for a in areas) for k in areas[0]["data_quality"]}
        points = [a["facility"] for a in areas if a.get("facility")]
        out.append(
            {
                "label": label,
                "areas": len(areas),
                "center": (
                    [sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)]
                    if points
                    else None
                ),
                "households": sum(a["households"] for a in areas),
                "data_quality": quality,
                **head,
            }
        )
    return out
