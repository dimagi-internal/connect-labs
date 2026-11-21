"""Reported coverage of a CommCare-run campaign, from a project's stored forms: children reached against the plan.

CHC reports coverage per work area (visits against the expected visits microplanned for it). A campaign CommCare ran
(Niger SMC) has no work areas: its plan is a target population per health centre (CSI), set on a Chef CSI form, and
its reach is a distribution form per child per cycle. So coverage is children given the dose in a cycle over the
target of the health centres that took part in it, per health district, health centre and cycle.

Everything that varies between campaigns is in ``CampaignSpec`` (form paths, the dose that counts, the target's form
and fields); the defaults are the eCHIS Niger SMC apps' own.

The records are read through two pipelines saved in the project (``prepare``): one row per distribution record with
the child, cycle, centre, district, dose and point, and one per plan record. The pipeline engine keeps their per-record
stage up to date with what each sync changed (``analysis.backends.sql.incremental``), so after the first build a
refresh reads only the new forms, and every count here is one aggregate query over those narrow rows (``GROUP BY``
health centre and cycle, ``COUNT(DISTINCT child)``): what comes back is a table of counts, never a child or a
household.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import connection

from connect_labs.microplans.coverage import check

_PATH = re.compile(r"^form(\.[A-Za-z0-9_@#\-]+)+$")


class CampaignSpecError(ValueError):
    """The campaign's settings cannot be run; the message says which."""


@dataclass
class CampaignSpec:
    """The paths and the rule that define one campaign's coverage. Defaults are the eCHIS Niger SMC apps."""

    # The distribution form: one record per child per cycle.
    child_path: str = "form.case.@case_id"
    cycle_path: str = "form.var.passage"
    csi_path: str = "form.localisation.csi_id"
    district_path: str = "form.localisation.district_id"
    #: Names the distribution form writes beside the ids (read from the forms, so no CommCare permission is needed).
    csi_name_path: str = "form.localisation.csi_name"
    district_name_path: str = "form.localisation.district_name"
    served_path: str = "form.administration_dose.spaq_recu"
    served_value: str = "oui"
    distribution_xmlns: str = "http://openrosa.org/formdesigner/98A8ACEF-AF53-4BCF-B904-2A0C0FE2B318"
    # The plan: one record per health centre with the target population (the latest one a centre filed counts).
    target_csi_path: str = "form.location_var.csi_id"
    target_paths: tuple = ("form.population.population_3_11_mois", "form.population.population_12_59_mois")
    target_xmlns: str = "http://openrosa.org/formdesigner/D3759434-0274-4E16-81FB-2DA55315527D"
    #: Coverage over this share of the target is the goal the campaign states.
    goal: float = 0.95
    # Where a dose was recorded, for the satellite comparisons (``reach_rows``): the form path(s) the device's point is
    # recorded at (the first that holds one counts; ``meta.location`` is on every form), and how the buildings are
    # searched. A point is where the form was filled in, so it is read per child and cycle, never per household.
    point_paths: tuple = ("form.meta.location.#text",)
    max_accuracy_m: float = 50.0
    iso: str = "NER"
    max_dist_m: float = 30.0
    catchment_k: int = 260
    #: Decimal places points are gathered to: 4 is about 11 m.
    point_decimals: int = 4
    #: Usernames starting like this are test accounts: their records are left out of every count.
    exclude_username_prefixes: tuple = ("test_",)
    #: The CommCare location type that is the health centre (left out: the one that looks like it).
    facility_type: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> CampaignSpec:
        data = dict(data or {})
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise CampaignSpecError(f"unknown setting(s): {', '.join(unknown)}")
        for key in ("target_paths", "point_paths", "exclude_username_prefixes"):
            if key in data:
                data[key] = tuple(data[key])
        spec = cls(**data)
        spec.validate()
        return spec

    def validate(self) -> None:
        for name in (
            "child_path",
            "cycle_path",
            "csi_path",
            "district_path",
            "served_path",
            "target_csi_path",
            "csi_name_path",
            "district_name_path",
        ):
            if not _PATH.match(getattr(self, name)):
                raise CampaignSpecError(f"{name} must be a form path such as form.group.question")
        if not self.target_paths or not all(_PATH.match(p) for p in self.target_paths):
            raise CampaignSpecError("target_paths is one or more form paths")
        if not re.fullmatch(r"[A-Za-z0-9_ \-]{1,40}", str(self.served_value)):
            raise CampaignSpecError("served_value must be a short plain word such as oui or yes")
        if not 0 < self.goal <= 1:
            raise CampaignSpecError("goal is a share between 0 and 1")
        if not self.point_paths or not all(_PATH.match(p) for p in self.point_paths):
            raise CampaignSpecError("point_paths is one or more form paths such as form.meta.location.#text")
        if not 0 < float(self.max_accuracy_m) <= 1000:
            raise CampaignSpecError("max_accuracy_m is a distance in metres")
        if int(self.point_decimals) not in (3, 4, 5):
            raise CampaignSpecError("point_decimals is 3, 4 or 5")
        if not re.fullmatch(r"[A-Za-z]{3}", self.iso):
            raise CampaignSpecError("iso is a three-letter country code such as NER")
        if not 1 <= self.max_dist_m <= 500 or not 10 <= int(self.catchment_k) <= 5000:
            raise CampaignSpecError("max_dist_m is 1 to 500 metres and catchment_k 10 to 5000 buildings")

    def as_dict(self) -> dict:
        return {name: (list(v) if isinstance(v, tuple) else v) for name, v in self.__dict__.items()}


def _number(value) -> float:
    try:
        return float(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


# -- the campaign's pipelines ----------------------------------------------------------------------------------

DISTRIBUTION_PIPELINE = "Campaign distribution records"
PLAN_PIPELINE = "Campaign plan"


def distribution_schema(spec: CampaignSpec) -> dict:
    """One row per distribution record: what every count and the satellite check read."""
    fields = [
        ("child", spec.child_path),
        ("cycle", spec.cycle_path),
        ("csi", spec.csi_path),
        ("district", spec.district_path),
        ("csi_name", spec.csi_name_path),
        ("district_name", spec.district_name_path),
        ("dose", spec.served_path),
    ]
    return {
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": [{"name": n, "path": p} for n, p in fields] + [{"name": "point", "paths": list(spec.point_paths)}],
    }


def plan_schema(spec: CampaignSpec) -> dict:
    """One row per plan record: the centre, its target fields and where it was filled in."""
    return {
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": [{"name": "csi", "path": spec.target_csi_path}]
        + [{"name": f"t{i}", "path": p} for i, p in enumerate(spec.target_paths)]
        + [{"name": "point", "path": spec.point_paths[0]}],
    }


@dataclass
class CampaignData:
    """Where a project's campaign records are, once ``prepare`` has brought them up to date."""

    scope: int
    distribution: tuple  # (pipeline id, config hash) of the per-record stage
    plan: tuple


def _pipeline(user, project, name: str, schema: dict, xmlns: str):
    """The project's pipeline of this name with this schema: kept if it already says this, else saved."""
    from connect_labs.hqdata.pipelines import find_pipeline, save_pipeline, stamped_schema

    wanted = stamped_schema(project, "forms", schema, "", xmlns)
    record = find_pipeline(user, project, name=name)
    if record is not None and record.schema == wanted:
        return record
    return save_pipeline(
        user,
        project,
        name=name,
        store_kind="forms",
        schema=schema,
        xmlns=xmlns,
        pipeline_id=record.id if record is not None else None,
    )


def _named(name: str, schema: dict, default: dict) -> str:
    """The pipeline's name: plain for the campaign's default forms, with a short mark for others, so two campaigns in
    one project (or a change of form paths) never take turns overwriting one pipeline."""
    if schema == default:
        return name
    import hashlib
    import json

    return f"{name} ({hashlib.sha1(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:6]})"


def prepare(user, project, spec: CampaignSpec, report=None, deadline=None) -> CampaignData:
    """Save (or keep) the campaign's two pipelines and bring their per-record stage up to date.

    The first time this reads every record once (in parts, with ``deadline``: ``MaintenanceContinues`` carries on in
    the next call); afterwards only what the project's syncs changed."""
    from connect_labs.hqdata.pipelines import config_for
    from connect_labs.labs.analysis.backends.sql.incremental import maintain
    from connect_labs.labs.analysis.utils import get_config_hash

    out = []
    default = CampaignSpec()
    for name, schema, xmlns, usual in (
        (
            DISTRIBUTION_PIPELINE,
            distribution_schema(spec),
            spec.distribution_xmlns,
            (distribution_schema(default), default.distribution_xmlns),
        ),
        (PLAN_PIPELINE, plan_schema(spec), spec.target_xmlns, (plan_schema(default), default.target_xmlns)),
    ):
        named = name if (schema, xmlns) == usual else _named(name, {"schema": schema, "xmlns": xmlns}, {})
        record = _pipeline(user, project, named, schema, xmlns)
        config = config_for(record.schema, record.id)
        maintain(config, project.scope_id, report=report, deadline=deadline)
        out.append((record.id, get_config_hash(config)))
    return CampaignData(scope=project.scope_id, distribution=out[0], plan=out[1])


def _records(data: CampaignData, which: str) -> tuple[str, dict]:
    """The FROM/WHERE over one pipeline's per-record rows, test accounts left out, and its parameters."""
    pipeline_id, config_hash = getattr(data, which)
    return (
        "FROM labs_computed_visit_cache WHERE opportunity_id = %(scope)s AND pipeline_id = %(pipeline)s "
        "AND config_hash = %(hash)s AND NOT (username LIKE ANY(%(prefixes)s))",
        {"scope": data.scope, "pipeline": pipeline_id, "hash": config_hash},
    )


def _prefixes(spec: CampaignSpec) -> list[str]:
    return [p.replace("%", "") + "%" for p in spec.exclude_username_prefixes] or ["\x00never%"]


def targets(data: CampaignData, spec: CampaignSpec) -> dict:
    """``{health centre id: target children}``: the sum of the target fields of the centre's latest plan record."""
    where, params = _records(data, "plan")
    t = [f"computed_fields->>'t{i}'" for i in range(len(spec.target_paths))]
    sql = f"""
        SELECT DISTINCT ON (csi) csi, {", ".join(f"t{i}" for i in range(len(t)))}
        FROM (SELECT computed_fields->>'csi' AS csi, {", ".join(f"{e} AS t{i}" for i, e in enumerate(t))},
                     computed_fields->>'date_created' AS at, visit_id {where}) p
        WHERE csi IS NOT NULL AND NOT ({" AND ".join(f"t{i} IS NULL" for i in range(len(t)))})
        ORDER BY csi, at DESC NULLS LAST, visit_id DESC
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, {**params, "prefixes": _prefixes(spec)})
        return {row[0]: sum(_number(v) for v in row[1:]) for row in cursor.fetchall()}


def last_received(data: CampaignData, spec: CampaignSpec):
    """When the newest distribution record arrived: how current the figures are."""
    where, params = _records(data, "distribution")
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT max(computed_fields->>'date_created') {where}", {**params, "prefixes": _prefixes(spec)}
        )
        newest = cursor.fetchone()[0]
    return newest or None


def plan_points(data: CampaignData, spec: CampaignSpec) -> dict:
    """``{health centre id: (lon, lat)}``: the middle of where the centre's plan forms were filled in.

    The plan is filled in by the health centre's own staff, so where they were is the best stand-in for the centre
    when CommCare does not give its coordinates; it never comes from the distribution records, so the catchment drawn
    around it does not depend on where the doses were given."""
    import statistics

    where, params = _records(data, "plan")
    seen: dict = {}
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT computed_fields->>'csi', computed_fields->>'point' {where} "
            "AND computed_fields->>'csi' IS NOT NULL AND computed_fields->>'point' IS NOT NULL",
            {**params, "prefixes": _prefixes(spec)},
        )
        for csi, pt in cursor.fetchall():
            parsed = check.parse_gps(pt)
            if not parsed:
                continue
            lon, lat, acc = parsed
            if (abs(lon) < 1e-6 and abs(lat) < 1e-6) or (acc is not None and acc > spec.max_accuracy_m):
                continue
            seen.setdefault(csi, []).append((lon, lat))
    return {
        csi: (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts)) for csi, pts in seen.items()
    }


def rows(data: CampaignData, spec: CampaignSpec, names: dict | None = None, scanned: dict | None = None):
    """The coverage table: one row per health centre and cycle, with the centre's plan, and what the data lacks.

    ``scanned`` is ``scan``'s answer when the caller already has it (one pass serves everything)."""
    scanned = scanned or scan(data, spec)
    names = {**scanned["names"], **(names or {})}
    found = scanned["counts"]
    plan = targets(data, spec)
    table = []
    for (district, csi, cycle), c in sorted(found.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        table.append(
            {
                "district_id": district,
                "district": names.get(district) or district or "(no district)",
                "csi_id": csi,
                "csi": names.get(csi) or csi,
                "cycle": cycle,
                "children_seen": c["seen"],
                "children_served": c["served"],
                "target": plan.get(csi),
            }
        )
    without_plan = sorted({r["csi_id"] for r in table if r["target"] is None})
    meta = {
        "data_through": last_received(data, spec),
        "cycles": sorted({r["cycle"] for r in table}),
        "health_centres": len({r["csi_id"] for r in table}),
        "health_centres_without_plan": len(without_plan),
        "goal": spec.goal,
    }
    return table, meta


def reach_rows(
    data: CampaignData,
    spec: CampaignSpec,
    facilities: list[dict],
    labels: dict,
    plan: dict,
    report=None,
    scanned: dict | None = None,
):
    """Coverage against the satellite denominators and buildings reached, per health district and cycle.

    ``facilities`` are the health centres as ``{"id", "lon", "lat"}`` (the catchments are drawn around them, never from
    the visits), ``labels`` maps a centre id to its district, ``plan`` maps a centre to its target. Returns
    ``(rows, metadata)``: a row per district and cycle with the children served, the three denominators (the squares
    the points touched, the whole admin unit, the plan) and the buildings reached; what could not be computed is said
    in the metadata rather than left out.

    The "households" the lenses count are the places doses were recorded: each distinct point (to ``point_decimals``
    places) of a health centre is one, weighted by the children served there in each cycle."""
    from connect_labs.microplans.coverage import compute

    def say(message, stage, **progress):
        if report:
            report(message, stage_name=message, current_stage=stage, total_stages=4, **progress)

    say("Reading the distribution records once (children served and where)", 2)
    scanned = scanned or scan(data, spec, with_points=True)
    cells, totals = scanned["cells"], scanned["counts"]
    say(f"{len(cells):,} places where doses were recorded", 2)
    # One "household" per health centre and distinct point (two centres recording at one point are two places).
    by_entity: dict = {}
    served: dict = {}
    for csi, cycle, lat, lon, seen, given in cells:
        entity = f"{csi}|{lat:.7f}|{lon:.7f}"
        e = by_entity.setdefault(entity, {"csi": csi, "lat": lat, "lon": lon, "cycles": set()})
        if seen:
            e["cycles"].add(cycle)
        served.setdefault(entity, {})[cycle] = given
    rows = [
        (e["csi"], entity, f"{e['lat']} {e['lon']} 0 5", ",".join(str(c) for c in sorted(e["cycles"])))
        for entity, e in by_entity.items()
    ]
    check_spec = check.CheckSpec.from_dict(
        {
            "facilities": facilities,
            "catchment_k": int(spec.catchment_k),
            "max_dist_m": float(spec.max_dist_m),
            "max_accuracy_m": None,
            "cycle_path": spec.cycle_path,
            "group_path": spec.csi_path,
        }
    )
    base_fetch = check.default_fetch(check_spec.footprint_filters)
    seen_centres = [0]
    total_centres = len(facilities)

    def fetch(lon, lat, radius_m):
        seen_centres[0] += 1
        if seen_centres[0] == 1 or seen_centres[0] % 5 == 0:
            say(
                f"Buildings around health centre {seen_centres[0]} of {total_centres}",
                3,
                processed=seen_centres[0],
                total=total_centres,
            )
        return base_fetch(lon, lat, radius_m)

    buildings = compute.compute_run(rows, check_spec, labels, fetch)
    say("Reading the population map and district boundaries", 4)
    population = compute.compute_population(rows, served, check_spec, labels, spec.iso.upper(), plan)

    # What share of the children served the lenses could place: the rest had no usable point.
    served_total: dict = {}
    for (district, csi, cycle), c in totals.items():
        if csi in labels:
            key = (labels[csi], cycle)
            served_total[key] = served_total.get(key, 0) + c["served"]
    served_placed: dict = {}
    for csi, cycle, _lat, _lon, _seen, given in cells:
        if csi in labels:
            key = (labels[csi], cycle)
            served_placed[key] = served_placed.get(key, 0) + given

    by_district: dict = {}
    for entry in buildings["rollup"]:
        for cycle, v in entry["cycles"].items():
            by_district.setdefault((entry["label"], int(cycle)), {})["buildings"] = v
    for entry in population["denominators"]["districts"]:
        for cycle in population["denominators"]["cycles"]:
            slot = by_district.setdefault((entry["label"], int(cycle)), {})
            slot["denominators"] = {
                "squares": (entry["squares"]["eligible"], entry["squares"]["share"].get(cycle)),
                "unit": (entry["unit"]["eligible"], entry["unit"]["share"].get(cycle)),
                "unit_is_fair": "unit" not in entry["range_excludes"],
                "touched_share": entry["unit"]["touched_share"],
            }
    table = []
    for (district, cycle), parts in sorted(by_district.items()):
        b = parts.get("buildings") or {}
        d = parts.get("denominators") or {}
        total = served_total.get((district, cycle), 0)
        table.append(
            {
                "district": district,
                "cycle": cycle,
                "eligible_squares": (d.get("squares") or (None, None))[0],
                "coverage_squares": (d.get("squares") or (None, None))[1],
                "eligible_unit": (d.get("unit") or (None, None))[0],
                "coverage_unit": (d.get("unit") or (None, None))[1] if d.get("unit_is_fair") else None,
                "unit_touched_share": d.get("touched_share"),
                "buildings": b.get("buildings"),
                "buildings_reached": b.get("buildings_reached"),
                "coverage_buildings": b.get("share"),
                "served_placed": served_placed.get((district, cycle), 0),
                "served_total": total,
            }
        )
    meta = {
        "cycles": population["cycles"],
        "quality": population.get("quality"),
        "grid": population.get("grid"),
        "unit_level": population["denominators"].get("unit_level"),
        "unit_note": population["denominators"].get("unit_note"),
        "health_centres": len(facilities),
        "points": len(rows),
        "centres_without_a_facility": buildings.get("groups_without_a_facility", [])[:50],
        "max_dist_m": spec.max_dist_m,
        "catchment_k": spec.catchment_k,
        "point_decimals": spec.point_decimals,
    }
    return table, meta


def scan(data: CampaignData, spec: CampaignSpec, with_points: bool = False) -> dict:
    """Everything the coverage figures and the satellite check need from the distribution records, in ONE pass over
    their per-record rows. ``GROUPING SETS`` gives, from that one pass:

    * ``counts``: ``{(district id, centre id, cycle): {"seen", "served"}}`` (distinct children);
    * ``names``: ``{location id: name}`` for centres and districts, as the forms name them;
    * with ``with_points``, ``cells``: ``(centre, cycle, lat, lon, seen, served)`` per distinct place doses were
      recorded, to ``spec.point_decimals`` places, usable points only (a fix, not 0,0, accuracy known and good or
      unknown).
    """
    where, params = _records(data, "distribution")
    dec = int(spec.point_decimals)
    point_sets = ", (csi, cyc, lat_r, lon_r)" if with_points else ""
    place_cols = "lat_r, lon_r" if with_points else "NULL::numeric, NULL::numeric"
    pt_ok = "pt ~ '^-?[0-9]+([.][0-9]+)? -?[0-9]+([.][0-9]+)?( |$)'"
    sql = f"""
        WITH f AS (
            SELECT computed_fields->>'child' AS child, computed_fields->>'cycle' AS cyc,
                   computed_fields->>'csi' AS csi, computed_fields->>'district' AS district,
                   computed_fields->>'csi_name' AS csi_name, computed_fields->>'district_name' AS district_name,
                   computed_fields->>'dose' AS dose, computed_fields->>'point' AS pt
            {where}
        ), g AS (
            SELECT child, cyc, csi, district, csi_name, district_name, dose,
                   CASE WHEN pt IS NULL OR NOT ({pt_ok}) THEN NULL
                        WHEN split_part(pt, ' ', 4) <> ''
                             AND split_part(pt, ' ', 4) !~ '^[0-9]+([.][0-9]+)?$' THEN NULL
                        WHEN NULLIF(split_part(pt, ' ', 4), '')::float > %(acc)s THEN NULL
                        WHEN split_part(pt, ' ', 1)::numeric = 0 AND split_part(pt, ' ', 2)::numeric = 0 THEN NULL
                        ELSE round(split_part(pt, ' ', 1)::numeric, {dec}) END AS lat_r,
                   CASE WHEN pt IS NULL OR NOT ({pt_ok}) THEN NULL
                        ELSE round(split_part(pt, ' ', 2)::numeric, {dec}) END AS lon_r
            FROM f WHERE child IS NOT NULL AND cyc IS NOT NULL AND csi IS NOT NULL
        )
        SELECT GROUPING(district) AS by_place, district, csi, cyc, {place_cols},
               min(csi_name), min(district_name),
               count(DISTINCT child), count(DISTINCT child) FILTER (WHERE dose = %(served)s)
        FROM g
        GROUP BY GROUPING SETS ((district, csi, cyc){point_sets})
    """
    counted: dict = {}
    names: dict = {}
    cells: list = []
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            {**params, "prefixes": _prefixes(spec), "acc": float(spec.max_accuracy_m), "served": spec.served_value},
        )
        while True:
            batch = cursor.fetchmany(5000)
            if not batch:
                break
            for by_place, district, csi, cyc, lat, lon, csi_name, district_name, seen, served in batch:
                cycle = check.cycle_number(cyc)
                if cycle is None:
                    continue
                if not by_place:  # the (district, centre, cycle) set
                    slot = counted.setdefault((district or "", csi, cycle), {"seen": 0, "served": 0})
                    slot["seen"] += seen
                    slot["served"] += served
                    if csi_name:
                        names.setdefault(csi, csi_name)
                    if district and district_name:
                        names.setdefault(district, district_name)
                elif lat is not None and lon is not None:
                    cells.append((csi, cycle, float(lat), float(lon), seen, served))
    return {"counts": counted, "names": names, "cells": cells}
