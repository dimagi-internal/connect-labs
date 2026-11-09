"""Every opportunity Connect has delivered, ranked against Connect's cost-effectiveness models.

The models are Dimagi's own CEAs (dimagi-internal/connect-cea-models): CHC, MBW and
kangaroo care, each a published GiveWell chain changed only where Connect differs. Each
gives a multiple of GiveWell's benchmark per country, at the model's default price per
person. ``data/cea_models.json`` vendors those per-country results (the file names the
commit it came from); refresh it from that repo's ``planning/model_results.json``, never
by hand.

What this module adds is the opportunity's OWN price. A model's multiple is benefit per
person divided by cost per person, and every model here runs with GiveWell's leveraging
adjustment at 1 (``model_results.js`` asserts it), so re-pricing is exact:

    multiple at actual cost = model multiple x model cost per person / actual cost per person

Three inputs come from Pulse, and each is stated where it is shown:

* **Total cost**: everything paid for the opportunity, in USD -- worker pay and the
  organisation's per-service fee accrued on every completed work, plus fixed costs
  (Connect's custom invoices and costs entered in labs). `costs.opportunity_costs`.
* **People reached**: distinct beneficiaries with approved work. Each completed work
  carries a keyed hash of its beneficiary (``PulseWork.beneficiary_key``, see
  ``normalize.beneficiary_label``), so this counts each child, mother or baby once,
  across workers, visits and payment units -- whether the app pays per visit stage
  (MBW cohorts), per visit (MBW Nigeria) or per baby (KMC). Only approved work counts:
  Connect never approves work for a form without a deliver block, nor for a rejected,
  duplicate, over-limit or pending visit. ``works_per_person`` is approved work per
  person, a first look at how far through a longitudinal programme people got.
  Opportunities synced before the key existed fall back to the old proxy (approved
  works on the busiest payment unit), and say so in ``reach_basis``.
* **Where**: the opportunity's country, and the centre of its approved visits.

An opportunity whose delivery type has no model says "no model". One delivered outside
the models' 45 sub-Saharan countries says so too. Neither borrows another model.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from django.db.models import Avg, Count, Q

from connect_labs.pulse import costs
from connect_labs.pulse.normalize import COUNTRY_NAMES, service_label

DATA = Path(__file__).with_name("data") / "cea_models.json"

# Connect delivery type -> the model that values it. Only programs a model was built for.
SERVICE_MODEL = {"chc": "chc", "mbw": "mbw", "kmc": "kc"}

STATUS_MODELLED = "modelled"
STATUS_NO_MODEL = "no_model"
STATUS_NO_COUNTRY = "no_country_data"
STATUS_NOTHING_DELIVERED = "nothing_delivered"
STATUS_COST_POOLED = "cost_pooled"

STATUS_TEXT = {
    STATUS_MODELLED: "Modelled",
    STATUS_NO_MODEL: "No model",
    STATUS_NO_COUNTRY: "No model for this country",
    STATUS_NOTHING_DELIVERED: "Nothing delivered yet",
    STATUS_COST_POOLED: "Cost counted in its programme's delivery",
}


@lru_cache(maxsize=1)
def models() -> dict:
    with DATA.open(encoding="utf-8") as f:
        return json.load(f)


REACH_DISTINCT = "distinct_people"
REACH_PROXY = "busiest_payment_unit"


def _people_reached(opp_ids) -> dict[int, dict]:
    """People reached per opportunity: ``{reached, reach_basis, works_per_person}``."""
    from connect_labs.pulse.models import PulseWork

    approved = PulseWork.objects.filter(opportunity_id__in=list(opp_ids), status="approved")
    keyed = {
        r["opportunity_id"]: r
        for r in approved.exclude(beneficiary_key="")
        .values("opportunity_id")
        .annotate(people=Count("beneficiary_key", distinct=True), works=Count("id"))
    }
    busiest: dict[int, int] = {}
    for r in approved.values("opportunity_id", "payment_unit_id").annotate(n=Count("id")):
        busiest[r["opportunity_id"]] = max(busiest.get(r["opportunity_id"], 0), r["n"])

    out: dict[int, dict] = {}
    for oid in set(keyed) | set(busiest):
        k = keyed.get(oid)
        if k and k["people"]:
            out[oid] = {
                "reached": k["people"],
                "reach_basis": REACH_DISTINCT,
                "works_per_person": round(k["works"] / k["people"], 2),
            }
        else:
            out[oid] = {"reached": busiest.get(oid, 0), "reach_basis": REACH_PROXY, "works_per_person": None}
    return out


def _centres(opp_ids) -> dict[int, tuple[float, float]]:
    """Mean position of each opportunity's approved visits that carry GPS."""
    from connect_labs.pulse.models import PulseEvent

    rows = (
        PulseEvent.objects.filter(opportunity_id__in=list(opp_ids), status="approved")
        .exclude(lat__isnull=True)
        .exclude(lon__isnull=True)
        .exclude(Q(lat=0) & Q(lon=0))
        .values("opportunity_id")
        .annotate(lat=Avg("lat"), lon=Avg("lon"))
    )
    return {r["opportunity_id"]: (round(r["lat"], 3), round(r["lon"], 3)) for r in rows}


def value(service: str, country: str, total_usd: float, reached: int) -> dict:
    """The CEA for one delivery: pure arithmetic over the vendored models, no database."""
    m = models()
    key = SERVICE_MODEL.get(service or "")
    out = {
        "model": key,
        "model_label": m["models"][key]["label"] if key else None,
        "cost_per_person": (total_usd / reached) if reached else None,
        "model_x": None,
        "model_cost_per_person": None,
        "model_deaths_per_100k": None,
        "x": None,
        "deaths_averted": None,
        "cost_per_death": None,
        "clears_bar": None,
    }
    if key is None:
        out["status"] = STATUS_NO_MODEL
        return out
    loc = m["countries"].get((country or "").upper())
    if loc is None:
        out["status"] = STATUS_NO_COUNTRY
        return out
    cell = loc[key]
    out["model_x"] = cell["x"]
    out["model_cost_per_person"] = cell["unit_cost"]
    # Per person reached; the page's what-if controls recompute deaths from it.
    out["model_deaths_per_100k"] = cell["deaths_per_100k"]
    if not reached or total_usd <= 0:
        out["status"] = STATUS_NOTHING_DELIVERED
        return out
    out["status"] = STATUS_MODELLED
    out["x"] = cell["x"] * cell["unit_cost"] / out["cost_per_person"]
    out["deaths_averted"] = reached * cell["deaths_per_100k"] / 100_000
    out["cost_per_death"] = (total_usd / out["deaths_averted"]) if out["deaths_averted"] else None
    out["clears_bar"] = out["x"] >= m["bar_x"]
    return out


def _pool_cost_only(opps, own_usd: dict, reach_of: dict):
    """Fold the spend of cost-only opportunities into the delivery they paid for.

    Some partners are paid through an opportunity that records no service at
    all: Ruwoyd's Mother Baby Wellness invoices went through "MBW Invoicing for
    Ruwoyd" ($12k, nobody reached), while its two cohort opportunities carried
    only $3k between them. Valued alone, the cohorts looked 13x better than
    they were, and topped the ranking at 349x and 274x.

    So spend on an opportunity that reached no one moves to the opportunities
    in the same programme, run by the same partner, that did reach people,
    split by people reached. Every invoice counts (owner decision, 2026-11-08).
    A cost-only opportunity with no such delivery to join keeps its own row.

    Returns (extra $ per delivery opp, delivery opp -> [source ids],
    source opp -> [delivery ids]).
    """
    groups: dict[tuple, list] = {}
    for o in opps:
        if o.program_id is not None and o.org_slug:
            groups.setdefault((o.program_id, o.org_slug), []).append(o)
    pooled_usd: dict[int, float] = {}
    pooled_from: dict[int, list] = {}
    pooled_into: dict[int, list] = {}
    for members in groups.values():
        delivery = [o for o in members if reach_of[o.opportunity_id]["reached"] > 0]
        sources = [o for o in members if reach_of[o.opportunity_id]["reached"] == 0 and own_usd[o.opportunity_id] > 0]
        if not delivery or not sources:
            continue
        people = sum(reach_of[o.opportunity_id]["reached"] for o in delivery)
        for src in sources:
            for d in delivery:
                share = own_usd[src.opportunity_id] * reach_of[d.opportunity_id]["reached"] / people
                pooled_usd[d.opportunity_id] = pooled_usd.get(d.opportunity_id, 0.0) + share
                pooled_from.setdefault(d.opportunity_id, []).append(src.opportunity_id)
            pooled_into[src.opportunity_id] = [d.opportunity_id for d in delivery]
    return pooled_usd, pooled_from, pooled_into


def ranking(opps_qs=None) -> dict:
    """Every real opportunity, modelled ones ranked by multiple at their actual cost."""
    from connect_labs.pulse.models import PulseOpportunity

    if opps_qs is None:
        opps_qs = PulseOpportunity.objects.filter(is_test=False)
    opps = list(opps_qs)
    ids = [o.opportunity_id for o in opps]
    cost_of = costs.opportunity_costs(ids)
    reached_of = _people_reached(ids)
    centre_of = _centres(ids)
    m = models()

    own_usd = {
        o.opportunity_id: (float(cost_of[o.opportunity_id].total_usd) if o.opportunity_id in cost_of else 0.0)
        for o in opps
    }
    reach_of = {
        o.opportunity_id: reached_of.get(o.opportunity_id)
        or {"reached": 0, "reach_basis": None, "works_per_person": None}
        for o in opps
    }
    pooled_usd, pooled_from, pooled_into = _pool_cost_only(opps, own_usd, reach_of)

    rows = []
    for o in opps:
        total = own_usd[o.opportunity_id] + pooled_usd.get(o.opportunity_id, 0.0)
        c = cost_of.get(o.opportunity_id)
        reach = reach_of[o.opportunity_id]
        reached = reach["reached"]
        country = (o.country or "").upper()
        loc = m["countries"].get(country)
        centre = centre_of.get(o.opportunity_id)
        row = {
            "id": o.opportunity_id,
            "name": o.name or f"Opportunity {o.opportunity_id}",
            "org_slug": o.org_slug,
            "program_id": o.program_id,
            "service": o.service_slug or "other",
            "service_name": service_label(o.service_slug),
            "country": country,
            "country_name": (loc or {}).get("name") or COUNTRY_NAMES.get(country) or country or "Unknown",
            "lat": centre[0] if centre else None,
            "lon": centre[1] if centre else None,
            "active": bool(o.is_active),
            "end_date": o.end_date.isoformat() if o.end_date else None,
            "total_usd": round(total, 2),
            "own_usd": round(own_usd[o.opportunity_id], 2),
            "pooled_usd": round(pooled_usd.get(o.opportunity_id, 0.0), 2),
            "pooled_from": pooled_from.get(o.opportunity_id, []),
            "pooled_into": pooled_into.get(o.opportunity_id, []),
            "fixed_usd": round(float(c.fixed_usd), 2) if c else 0.0,
            "reached": reached,
            "reach_basis": reach["reach_basis"],
            "works_per_person": reach["works_per_person"],
        }
        if o.opportunity_id in pooled_into:
            # Its spend is already inside the delivery rows above; valuing it again would count it twice.
            row.update(value(row["service"], country, 0.0, 0))
            row["status"] = STATUS_COST_POOLED
        else:
            row.update(value(row["service"], country, total, reached))
        row["status_text"] = STATUS_TEXT[row["status"]]
        rows.append(row)

    order = {
        STATUS_MODELLED: 0,
        STATUS_NOTHING_DELIVERED: 1,
        STATUS_NO_COUNTRY: 2,
        STATUS_NO_MODEL: 3,
        STATUS_COST_POOLED: 4,
    }
    rows.sort(key=lambda r: (order[r["status"]], -(r["x"] or 0), -r["total_usd"]))
    rank = 0
    for r in rows:
        if r["status"] == STATUS_MODELLED:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None

    modelled = [r for r in rows if r["status"] == STATUS_MODELLED]
    spend_modelled = sum(r["total_usd"] for r in modelled)
    deaths = sum(r["deaths_averted"] for r in modelled)
    return {
        "rows": rows,
        "totals": {
            "opportunities": len(rows),
            "modelled": len(modelled),
            "clearing_bar": sum(1 for r in modelled if r["clears_bar"]),
            # Each dollar once: a pooled row's spend is already inside its delivery rows.
            "spend": round(sum(r["own_usd"] for r in rows), 2),
            "spend_modelled": round(spend_modelled, 2),
            "deaths_averted": round(deaths, 2),
            # Spend-weighted: the portfolio's multiple is total benefit over total cost.
            "portfolio_x": (
                sum(r["x"] * r["total_usd"] for r in modelled) / spend_modelled if spend_modelled else None
            ),
        },
        "bar_x": m["bar_x"],
        "bar_note": m["bar_note"],
        "models": {
            k: dict(v, price=next(iter(m["countries"].values()))[k]["unit_cost"]) for k, v in m["models"].items()
        },
        "source": m["source"],
        "generated": m["generated"],
    }
