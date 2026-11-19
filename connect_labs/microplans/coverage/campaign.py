"""Reported coverage of a CommCare-run campaign, from a project's stored forms: children reached against the plan.

CHC reports coverage per work area (visits against the expected visits microplanned for it). A campaign CommCare ran
(Niger SMC) has no work areas: its plan is a target population per health centre (CSI), set on a Chef CSI form, and
its reach is a distribution form per child per cycle. So coverage is children given the dose in a cycle over the
target of the health centres that took part in it, per health district, health centre and cycle.

Everything that varies between campaigns is in ``CampaignSpec`` (form paths, the dose that counts, the target's form
and fields); the defaults are the eCHIS Niger SMC apps' own. The counts are aggregate queries on the project's stored
forms (``GROUP BY`` health centre and cycle, ``COUNT(DISTINCT child)``), so they scale to millions of forms and never
load a form into memory; what comes back is a table of counts, never a child or a household.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.contrib.postgres.aggregates import StringAgg
from django.db.models import Count, Min
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce

from connect_labs.microplans.coverage import check

_PATH = re.compile(r"^form(\.[A-Za-z0-9_@\-]+)+$")


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
    served_path: str = "form.administration_dose.spaq_recu"
    served_value: str = "oui"
    distribution_xmlns: str = ""
    # The plan: one record per health centre with the target population (the latest one a centre filed counts).
    target_csi_path: str = "form.var.location.csi_id"
    target_paths: tuple = ("form.population.population_3_11_mois", "form.population.population_12_59_mois")
    target_xmlns: str = ""
    #: Coverage over this share of the target is the goal the campaign states.
    goal: float = 0.95
    # Where a household is and when it was visited, for the satellite comparisons (``reach``): the household's id, the
    # form paths its point is recorded at (the first that holds one counts), and how the buildings are searched.
    household_path: str = "form.var_menage.case_id_menage"
    gps_paths: tuple = ("form.administration_dose.geolocalisation", "form.geolocalisation_nonelligble")
    iso: str = "NER"
    max_dist_m: float = 30.0
    catchment_k: int = 260
    #: The CommCare location type that is the health centre (left out: the one that looks like it).
    facility_type: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> CampaignSpec:
        data = dict(data or {})
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise CampaignSpecError(f"unknown setting(s): {', '.join(unknown)}")
        for key in ("target_paths", "gps_paths"):
            if key in data:
                data[key] = tuple(data[key])
        spec = cls(**data)
        spec.validate()
        return spec

    def validate(self) -> None:
        for name in ("child_path", "cycle_path", "csi_path", "district_path", "served_path", "target_csi_path"):
            if not _PATH.match(getattr(self, name)):
                raise CampaignSpecError(f"{name} must be a form path such as form.group.question")
        if not self.target_paths or not all(_PATH.match(p) for p in self.target_paths):
            raise CampaignSpecError("target_paths is one or more form paths")
        if not re.fullmatch(r"[A-Za-z0-9_ \-]{1,40}", str(self.served_value)):
            raise CampaignSpecError("served_value must be a short plain word such as oui or yes")
        if not 0 < self.goal <= 1:
            raise CampaignSpecError("goal is a share between 0 and 1")
        if (
            not _PATH.match(self.household_path)
            or not self.gps_paths
            or not all(_PATH.match(p) for p in self.gps_paths)
        ):
            raise CampaignSpecError("household_path and gps_paths are form paths such as form.group.question")
        if not re.fullmatch(r"[A-Za-z]{3}", self.iso):
            raise CampaignSpecError("iso is a three-letter country code such as NER")
        if not 1 <= self.max_dist_m <= 500 or not 10 <= int(self.catchment_k) <= 5000:
            raise CampaignSpecError("max_dist_m is 1 to 500 metres and catchment_k 10 to 5000 buildings")

    def as_dict(self) -> dict:
        return {name: (list(v) if isinstance(v, tuple) else v) for name, v in self.__dict__.items()}


def text_at(path: str):
    """The text at a ``form.a.b`` path of a stored form's JSON, as a database expression."""
    expr = "form_json"
    for key in path.split("."):
        expr = KeyTextTransform(key, expr)
    return expr


def _number(value) -> float:
    try:
        return float(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


def _forms(store, project_id: int, xmlns: str):
    qs = store.forms(project_id).filter(deleted_at__isnull=True, archived=False)
    return qs.filter(xmlns=xmlns) if xmlns else qs


def counts(store, project_id: int, spec: CampaignSpec) -> dict:
    """``{(district id, health centre id, cycle): {"seen": children, "served": children}}``.

    ``seen`` counts the distinct children with a distribution record in the cycle, ``served`` those whose record
    says the dose was given."""
    base = (
        _forms(store, project_id, spec.distribution_xmlns)
        .annotate(
            child=text_at(spec.child_path),
            cyc=text_at(spec.cycle_path),
            csi=text_at(spec.csi_path),
            district=text_at(spec.district_path),
            dose=text_at(spec.served_path),
        )
        .filter(child__isnull=False, cyc__isnull=False, csi__isnull=False)
    )
    out: dict = {}

    def add(rows, key):
        for r in rows:
            cycle = check.cycle_number(r["cyc"])
            if cycle is None:
                continue
            slot = out.setdefault((r["district"] or "", r["csi"], cycle), {"seen": 0, "served": 0})
            slot[key] += r["n"]

    add(base.values("district", "csi", "cyc").annotate(n=Count("child", distinct=True)), "seen")
    add(
        base.filter(dose=spec.served_value).values("district", "csi", "cyc").annotate(n=Count("child", distinct=True)),
        "served",
    )
    return out


def targets(store, project_id: int, spec: CampaignSpec) -> dict:
    """``{health centre id: target children}``: the sum of the target fields of the centre's latest plan record."""
    annotations = {f"t{i}": text_at(p) for i, p in enumerate(spec.target_paths)}
    qs = (
        _forms(store, project_id, spec.target_xmlns)
        .annotate(csi=text_at(spec.target_csi_path), **annotations)
        .filter(csi__isnull=False)
        .exclude(**{f"t{i}__isnull": True for i in range(len(spec.target_paths))})
        .order_by("csi", "-received_on")
        .distinct("csi")
        .values("csi", *annotations)
    )
    return {r["csi"]: sum(_number(r[f"t{i}"]) for i in range(len(spec.target_paths))) for r in qs}


def last_received(store, project_id: int, spec: CampaignSpec):
    """When the newest distribution record arrived: how current the figures are."""
    qs = _forms(store, project_id, spec.distribution_xmlns).annotate(child=text_at(spec.child_path))
    newest = qs.filter(child__isnull=False).order_by("-received_on").values_list("received_on", flat=True).first()
    return newest.isoformat() if newest else None


def rows(store, project_id: int, spec: CampaignSpec, names: dict | None = None) -> tuple[list[dict], dict]:
    """The coverage table: one row per health centre and cycle, with the centre's plan, and what the data lacks."""
    names = names or {}
    found = counts(store, project_id, spec)
    plan = targets(store, project_id, spec)
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
        "data_through": last_received(store, project_id, spec),
        "cycles": sorted({r["cycle"] for r in table}),
        "health_centres": len({r["csi_id"] for r in table}),
        "health_centres_without_plan": len(without_plan),
        "goal": spec.goal,
    }
    return table, meta


def household_rows(store, project_id: int, spec: CampaignSpec) -> list[tuple]:
    """One row per household: ``(health centre, household, GPS text, "cycle,cycle")``, as ``check.points_sql`` gives.

    A household's point is the lowest GPS text its forms recorded (the first visit records it and every later form
    carries none), so it is the same however the forms are ordered. Read as an aggregate query, streamed."""
    qs = (
        _forms(store, project_id, spec.distribution_xmlns)
        .annotate(
            hh=text_at(spec.household_path),
            csi=text_at(spec.csi_path),
            cyc=text_at(spec.cycle_path),
            gps=Coalesce(*[text_at(p) for p in spec.gps_paths]),
        )
        .filter(hh__isnull=False)
        .values("hh")
        .annotate(grp=Min("csi"), point=Min("gps"), cycles=StringAgg("cyc", ",", distinct=True))
        .order_by("hh")
    )
    return [(r["grp"], r["hh"], r["point"], r["cycles"]) for r in qs.iterator(chunk_size=5000)]


def served_by_household(store, project_id: int, spec: CampaignSpec) -> list[tuple]:
    """``(household, cycle, children served)``: distinct children given the dose, as ``check.served_sql`` gives."""
    qs = (
        _forms(store, project_id, spec.distribution_xmlns)
        .annotate(
            hh=text_at(spec.household_path),
            cyc=text_at(spec.cycle_path),
            child=text_at(spec.child_path),
            dose=text_at(spec.served_path),
        )
        .filter(hh__isnull=False, child__isnull=False, cyc__isnull=False, dose=spec.served_value)
        .values("hh", "cyc")
        .annotate(n=Count("child", distinct=True))
        .order_by("hh", "cyc")
    )
    return [(r["hh"], r["cyc"], r["n"]) for r in qs.iterator(chunk_size=5000)]


def reach_rows(store, project_id: int, spec: CampaignSpec, facilities: list[dict], labels: dict, plan: dict):
    """Coverage against the satellite denominators and buildings reached, per health district and cycle.

    ``facilities`` are the health centres as ``{"id", "lon", "lat"}`` (the catchments are drawn around them, never from
    the visits), ``labels`` maps a centre id to its district, ``plan`` maps a centre to its target. Returns
    ``(rows, metadata)``: a row per district and cycle with the children served, the three denominators (the squares
    the households touched, the whole admin unit, the plan) and the buildings reached; what could not be computed is
    said in the metadata rather than left out."""
    from connect_labs.microplans.coverage import compute

    households = household_rows(store, project_id, spec)
    served = check.parse_served(served_by_household(store, project_id, spec))
    check_spec = check.CheckSpec.from_dict(
        {
            "facilities": facilities,
            "catchment_k": int(spec.catchment_k),
            "max_dist_m": float(spec.max_dist_m),
            "household_path": spec.household_path,
            "gps_paths": list(spec.gps_paths),
            "cycle_path": spec.cycle_path,
            "group_path": spec.csi_path,
        }
    )
    buildings = compute.compute_run(households, check_spec, labels)
    population = compute.compute_population(households, served, check_spec, labels, spec.iso.upper(), plan)

    by_district: dict = {}
    for entry in buildings["rollup"]:
        for cycle, v in entry["cycles"].items():
            by_district.setdefault((entry["label"], int(cycle)), {})["buildings"] = v
    for entry in population["denominators"]["districts"]:
        for cycle in population["denominators"]["cycles"]:
            slot = by_district.setdefault((entry["label"], int(cycle)), {})
            slot["denominators"] = {
                "served": entry["served"].get(cycle, entry["served"].get(str(cycle), 0)),
                "squares": (entry["squares"]["eligible"], entry["squares"]["share"].get(cycle)),
                "unit": (entry["unit"]["eligible"], entry["unit"]["share"].get(cycle)),
                "unit_is_fair": "unit" not in entry["range_excludes"],
                "touched_share": entry["unit"]["touched_share"],
                "target": (
                    (entry["target"]["value"], entry["target"]["share"].get(cycle))
                    if entry["target"]
                    else (None, None)
                ),
            }
    table = []
    for (district, cycle), parts in sorted(by_district.items()):
        b = parts.get("buildings") or {}
        d = parts.get("denominators") or {}
        table.append(
            {
                "district": district,
                "cycle": cycle,
                "children_served": d.get("served"),
                "plan_target": (d.get("target") or (None, None))[0],
                "coverage_plan": (d.get("target") or (None, None))[1],
                "eligible_squares": (d.get("squares") or (None, None))[0],
                "coverage_squares": (d.get("squares") or (None, None))[1],
                "eligible_unit": (d.get("unit") or (None, None))[0],
                "coverage_unit": (d.get("unit") or (None, None))[1] if d.get("unit_is_fair") else None,
                "unit_touched_share": d.get("touched_share"),
                "buildings": b.get("buildings"),
                "buildings_reached": b.get("buildings_reached"),
                "coverage_buildings": b.get("share"),
                "households_visited": b.get("households_visited"),
                "households_unplaced": b.get("households_unplaced"),
            }
        )
    meta = {
        "cycles": population["cycles"],
        "quality": population.get("quality"),
        "grid": population.get("grid"),
        "unit_level": population["denominators"].get("unit_level"),
        "unit_note": population["denominators"].get("unit_note"),
        "health_centres": len(facilities),
        "households": buildings["households_total"],
        "centres_without_a_facility": buildings.get("groups_without_a_facility", [])[:50],
        "max_dist_m": spec.max_dist_m,
        "catchment_k": spec.catchment_k,
    }
    return table, meta
