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
        for c in cycles:
            seen = [h for h, s in zip(mine["hh"], mine["cycles"]) if c in s]
            pts = pd.DataFrame([{"lon": usable[h][0], "lat": usable[h][1], "cycle": c} for h in seen if h in usable])
            if not len(pts):
                pts = pd.DataFrame({"lon": [], "lat": [], "cycle": []})
            res = reached.reached_by_cycle(blds, pts, max_dist_m=spec.max_dist_m, cycles=[c])
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
                "households": len(mine),
                "data_quality": {
                    "no_point": int(len(mine) - len(located)),
                    "null_island": int(null_island.sum()),
                    "off_area": int(far.sum()),
                    "poor_accuracy": quality.poor_accuracy,
                    "usable": quality.valid,
                },
                "cycles": by_cycle,
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
        out.append(
            {
                "label": label,
                "areas": len(areas),
                "households": sum(a["households"] for a in areas),
                "data_quality": quality,
                **head,
            }
        )
    return out
