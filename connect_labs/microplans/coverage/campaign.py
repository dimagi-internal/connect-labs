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

from django.db.models import Count
from django.db.models.fields.json import KeyTextTransform

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

    @classmethod
    def from_dict(cls, data: dict | None) -> CampaignSpec:
        data = dict(data or {})
        unknown = sorted(set(data) - set(cls.__dataclass_fields__))
        if unknown:
            raise CampaignSpecError(f"unknown setting(s): {', '.join(unknown)}")
        if "target_paths" in data:
            data["target_paths"] = tuple(data["target_paths"])
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
