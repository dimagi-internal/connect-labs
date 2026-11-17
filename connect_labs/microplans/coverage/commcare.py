"""Read a campaign's household rows from a CommCare project's stored forms, for the coverage page and the tool.

A CommCare-run campaign (Niger SMC) has no Connect visits to query: its forms are in a CommCare project that labs
syncs (``hqdata``). The check's settings already name every form path it needs (household, GPS, cycle, facility,
and what counts as a child served), so the reader is one pipeline over the project's forms whose fields are those
paths, run through the same function every project pipeline runs through (``hqdata.pipelines.rows_for_schema``:
as the signed-in member, the project gate in front, the project's own store). Its rows are then reduced to the
shapes the check takes, exactly as ``check.points_sql`` / ``check.served_sql`` produce them, so nothing downstream
knows where the households came from.

Nothing here is stored and nothing leaves as a household or a point: the page shows counts and aggregated squares.
"""

from __future__ import annotations

from connect_labs.hqdata.pipelines import data_source_for, rows_for_schema
from connect_labs.microplans.coverage import check

#: The pipeline's own column names (a path is never a column name).
HH, GRP, CYC, SERVED, CHILD = "hh", "grp", "cyc", "served", "child"


def pipeline_schema(spec: check.CheckSpec, project_id: int, *, app_id: str = "", xmlns: str = "") -> dict:
    """The one-row-per-form pipeline over the project's forms that carries every column the check needs."""

    def field(name, path):
        return {"name": name, "path": path, "aggregation": "first"}

    fields = [field(HH, spec.household_path), field(CYC, spec.cycle_path)]
    if spec.group_path:
        fields.append(field(GRP, spec.group_path))
    fields += [field(f"gps{i}", path) for i, path in enumerate(spec.gps_paths)]
    fields += [field(SERVED, spec.served_path), field(CHILD, spec.child_path)]
    return {
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": fields,
        "data_source": data_source_for(project_id, "forms", app_id, xmlns),
    }


def _text(value) -> str | None:
    return None if value in (None, "") else str(value)


def reduce_rows(forms: list[dict], spec: check.CheckSpec) -> tuple[list, list]:
    """Form rows -> ``(points_sql-shaped rows, served_sql-shaped rows)``.

    Points: one row per household: ``(group, household, GPS text, "cycle,cycle")``; the GPS is the first of the
    ``gps_paths`` a form recorded, and the lowest such text across the household's forms (what the SQL's ``min``
    gives), so a household's point is stable however the forms are ordered. Served: ``(household, cycle, n)``,
    the distinct children whose record has the served value, per household and cycle."""
    gps_cols = [f"gps{i}" for i in range(len(spec.gps_paths))]
    households: dict = {}
    children: dict = {}
    for row in forms:
        hh = _text(row.get(HH))
        if hh is None:
            continue
        slot = households.setdefault(hh, {"grp": None, "gps": None, "cycles": set()})
        group = _text(row.get(GRP))
        if group is not None and (slot["grp"] is None or group < slot["grp"]):
            slot["grp"] = group
        gps = next((g for g in (_text(row.get(c)) for c in gps_cols) if g is not None), None)
        if gps is not None and (slot["gps"] is None or gps < slot["gps"]):
            slot["gps"] = gps
        cycle = _text(row.get(CYC))
        if cycle is not None:
            slot["cycles"].add(cycle)
        child = _text(row.get(CHILD))
        if child is not None and cycle is not None and _text(row.get(SERVED)) == str(spec.served_value):
            children.setdefault((hh, cycle), set()).add(child)
    points = [(s["grp"], hh, s["gps"], ",".join(sorted(s["cycles"]))) for hh, s in sorted(households.items())]
    served = [(hh, cyc, len(kids)) for (hh, cyc), kids in sorted(children.items())]
    return points, served


def read(user, project, spec: check.CheckSpec, *, app_id: str = "", xmlns: str = "") -> tuple[list, dict]:
    """``(household rows, {household: {cycle: children served}})`` from the project's stored forms, as ``user``."""
    forms = rows_for_schema(user, project, pipeline_schema(spec, project.pk, app_id=app_id, xmlns=xmlns))
    points, served = reduce_rows(forms, spec)
    return points, check.parse_served(served)
