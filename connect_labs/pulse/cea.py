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

from connect_labs.impact.result import reprice as impact_reprice
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


def iso3_for(country: str) -> str | None:
    """Pulse's alpha-2 country -> the models' alpha-3, or None outside the models' 45 countries."""
    return (models()["countries"].get((country or "").upper()) or {}).get("iso3")


def value(service: str, country: str, total_usd: float, reached: int, areas: list | None = None) -> dict:
    """The CEA for one delivery, run through the models' own formulas (``connect_labs.impact``).

    ``areas`` is the place layer for where its visits happened (``impact.result.evaluate``);
    without it the delivery is valued at its country's figures, as the published
    per-country results are. No database access unless ``areas`` needs it.
    """
    from connect_labs.impact import baseline as impact_baseline
    from connect_labs.impact import result as impact_result

    m = models()
    key = SERVICE_MODEL.get(service or "")
    out = {
        "model": key,
        "model_label": m["models"][key]["label"] if key else None,
        "cost_per_person": (total_usd / reached) if reached else None,
        "model_x": None,
        "model_cost_per_person": None,
        "model_deaths_per_100k": None,
        "country_model_x": None,
        "baseline_level": None,
        "x": None,
        "deaths_averted": None,
        "cost_per_death": None,
        "clears_bar": None,
    }
    if key is None:
        out["status"] = STATUS_NO_MODEL
        return out
    iso3 = iso3_for(country)
    if iso3 is None:
        out["status"] = STATUS_NO_COUNTRY
        return out
    if not areas:
        areas = [
            {
                "name": (m["countries"].get((country or "").upper()) or {}).get("name") or country,
                "level": "country",
                "share": 1.0,
                "inputs": impact_baseline.Baselines([]).for_area(key, iso3, None),
            }
        ]
    r = impact_result.evaluate(key, iso3, areas, reached, total_usd, m["bar_x"])
    for k in ("model_x", "model_cost_per_person", "model_deaths_per_100k", "country_model_x", "x"):
        out[k] = r[k]
    out["baseline_level"] = _headline_level(areas)
    if not reached or total_usd <= 0:
        out["x"] = None
        out["status"] = STATUS_NOTHING_DELIVERED
        return out
    out["status"] = STATUS_MODELLED
    out["deaths_averted"] = r["deaths_averted"]
    out["cost_per_death"] = r["cost_per_death"]
    out["clears_bar"] = r["clears_bar"]
    return out


LOCAL_LEVELS = ("district", "state or province")


def _headline_level(areas: list) -> str:
    """Where a delivery's place figures came from, in a few words.

    The most local level any input used (district, then state or province), and
    how many of the model's place inputs used a local figure, by visit share:
    "state or province · 3 of 9". "country" when every input took the country
    figure; "no place inputs" for a model without any (KC).
    """
    counts: dict[str, float] = {}
    n_inputs = 0
    local_inputs = 0.0
    for a in areas:
        n_inputs = max(n_inputs, len(a["inputs"]))
        local = [v for v in a["inputs"].values() if v.level in LOCAL_LEVELS]
        local_inputs += a["share"] * len(local)
        for lvl in {v.level for v in local}:
            counts[lvl] = counts.get(lvl, 0.0) + a["share"]
    if not n_inputs:
        return "no place inputs"
    if not counts:
        return "country"
    level = next(lvl for lvl in LOCAL_LEVELS if lvl in counts)
    return f"{level} · {round(local_inputs)} of {n_inputs}"


def opportunity_areas(opps, reach_of: dict) -> dict:
    """The place layer for every valued opportunity: ``{opp_id: [area dict, ...]}``.

    One spatial query places every approved visit (``impact.places``), and one
    resolver reads every area's baseline (``impact.baseline``). An opportunity
    with no placed visits is left out and valued at its country's figures.
    """
    from connect_labs.impact import baseline as impact_baseline
    from connect_labs.impact import places as impact_places

    wanted = {
        o.opportunity_id: (SERVICE_MODEL.get(o.service_slug or ""), iso3_for(o.country))
        for o in opps
        if SERVICE_MODEL.get(o.service_slug or "") and iso3_for(o.country)
    }
    if not wanted:
        return {}
    placed = impact_places.opportunity_places(wanted)
    boundaries = {a.boundary.pk: a.boundary for p in placed.values() for a in p.areas}
    resolver = impact_baseline.Baselines(list(boundaries.values()))
    out = {}
    for opp_id, (key, iso3) in wanted.items():
        p = placed.get(opp_id)
        # Only areas in the opportunity's own country: a stray point over a border
        # must not value a delivery with a neighbour's figures.
        areas = [a for a in (p.areas if p else []) if a.boundary.iso_code == iso3]
        total = sum(a.visits for a in areas)
        if not total:
            continue
        out[opp_id] = [
            {
                "name": a.boundary.name,
                "level": impact_baseline.LEVEL_NAMES.get(a.boundary.admin_level, ""),
                "boundary_id": a.boundary.pk,
                "visits": a.visits,
                "share": a.visits / total,
                "inputs": resolver.for_area(key, iso3, a.boundary),
            }
            for a in areas
        ]
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


def rollup(rows: list[dict]) -> dict:
    """What a set of opportunities achieved together: the portfolio, a programme, a partner or a country.

    The one place a group's figures are added up, so the page's tiles, the programme table, the JSON and
    the agent tool cannot disagree. Each dollar counts once (a pooled row's spend is already inside its
    delivery rows), and the multiple is total benefit over total cost, so it is spend-weighted.
    """
    modelled = [r for r in rows if r["status"] == STATUS_MODELLED]
    spend_modelled = sum(r["total_usd"] for r in modelled)
    # What the multiple is weighted by: the cost a row was valued at. That is its actual cost unless a what-if
    # re-priced it (extra cost, or the models' own prices), which says so in ``cost_used``.
    cost = sum(r.get("cost_used") or r["total_usd"] for r in modelled)
    deaths = sum(r["deaths_averted"] for r in modelled)
    return {
        "opportunities": len(rows),
        "modelled": len(modelled),
        "clearing_bar": sum(1 for r in modelled if r["clears_bar"]),
        "spend": round(sum(r["own_usd"] for r in rows), 2),
        "spend_modelled": round(spend_modelled, 2),
        "people": sum(r["reached"] for r in modelled),
        "deaths_averted": round(deaths, 2),
        "cost_per_death": (cost / deaths) if deaths else None,
        "portfolio_x": (
            (sum(r["x"] * (r.get("cost_used") or r["total_usd"]) for r in modelled) / cost) if cost else None
        ),
    }


def programmes(rows: list[dict]) -> list[dict]:
    """The same roll-up per Connect programme, biggest spend first.

    A programme is Connect's own grouping (``PulseProgram``); opportunities with none are gathered as
    "No programme". Its partners and the models that value it are listed so a reader sees what is in it.
    """
    from connect_labs.pulse.models import PulseProgram

    names = dict(
        PulseProgram.objects.filter(program_id__in={r["program_id"] for r in rows}).values_list("program_id", "name")
    )
    groups: dict[int | None, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["program_id"], []).append(r)
    out = []
    for pid, members in groups.items():
        out.append(
            {
                "program_id": pid,
                "name": names.get(pid) or (f"Programme {pid}" if pid is not None else "No programme"),
                "partners": sorted({r["org_slug"] for r in members if r["org_slug"]}),
                "models": sorted({r["model"] for r in members if r.get("model")}),
                "opportunity_ids": [r["id"] for r in members],
                **rollup(members),
            }
        )
    out.sort(key=lambda g: -g["spend"])
    return out


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
    areas_of = opportunity_areas(opps, reach_of)

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
            row.update(value(row["service"], country, total, reached, areas_of.get(o.opportunity_id)))
        row["areas_n"] = len(areas_of.get(o.opportunity_id) or [])
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

    return {
        "rows": rows,
        "totals": rollup(rows),
        "programmes": programmes(rows),
        "bar_x": m["bar_x"],
        "bar_note": m["bar_note"],
        "placed_at": _placed_at(),
        "models": {
            k: dict(v, price=next(iter(m["countries"].values()))[k]["unit_cost"]) for k, v in m["models"].items()
        },
        "source": m["source"],
        "generated": m["generated"],
    }


def _placed_at():
    from connect_labs.impact import places as impact_places

    return impact_places.placed_at()


# ---------------------------------------------------------------------------------------------------------
# The Implementation view: what-if, filters, ranking and roll-up, all here. The page draws what this returns
# and does no arithmetic of its own, so a figure on the page is the figure in the JSON, the CSV and the agent
# tool. ``result.reprice`` is the formula; this applies it row by row.

KNOBS = ("extra", "effect", "reach")  # per model: extra $ per person, effect % of the model, people counted %
KNOB_DEFAULT = {"extra": 0.0, "effect": 100.0, "reach": 100.0}
RANK_KEYS = ("x", "deaths_averted", "cost_per_death", "total_usd")
BASES = ("actual", "model")
RANKING_CACHE_KEY = "pulse:cea:ranking:v1"
RANKING_CACHE_SECONDS = 60


def _number(value, default=None, *, positive=False):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")) or v < 0 or (positive and v == 0):
        return default
    return v


def what_if_from(params, bar_x: float, model_keys=None) -> dict:
    """The what-if as the page keeps it in its URL: ``basis``, ``rank``, ``bar`` and ``<model>_<knob>``."""
    model_keys = list(model_keys or models()["models"])
    basis = params.get("basis")
    rank = params.get("rank")
    out = {
        "basis": basis if basis in BASES else "actual",
        "rank": rank if rank in RANK_KEYS else "x",
        "bar": _number(params.get("bar"), bar_x, positive=True),
        "m": {k: dict(KNOB_DEFAULT) for k in model_keys},
    }
    for k in model_keys:
        for knob in KNOBS:
            out["m"][k][knob] = _number(params.get(f"{k}_{knob}"), KNOB_DEFAULT[knob])
    return out


def filters_from(params, model_keys=None) -> dict:
    """The page's filters: ``models`` (those shown), ``country``, ``active``, ``modelled``, ``min_spend``, ``q``."""
    model_keys = list(model_keys or models()["models"])
    shown = params.get("models")
    return {
        "models": [k for k in shown.split(",") if k in model_keys] if shown is not None else model_keys,
        "country": (params.get("country") or "").strip(),
        "active": params.get("active") in ("1", "true"),
        "modelled": params.get("modelled") in ("1", "true"),
        "min_spend": _number(params.get("min_spend"), 0.0),
        "q": (params.get("q") or "").strip().lower(),
    }


def apply_what_if(rows: list[dict], what_if: dict) -> list[dict]:
    """Every modelled row re-priced under the what-if (copies; the ranking's own rows are not touched)."""
    bar = what_if["bar"]
    out = []
    for base in rows:
        r = dict(base)
        r["clears_bar"] = (
            (r["x"] >= bar) if (r["status"] == STATUS_MODELLED and r["x"] is not None) else r["clears_bar"]
        )
        if r["status"] == STATUS_MODELLED and r["model_x"] is not None and r["reached"] and r["total_usd"] > 0:
            knobs = what_if["m"].get(r["model"]) or KNOB_DEFAULT
            got = impact_reprice(
                r["model_x"],
                r["model_cost_per_person"],
                r["model_deaths_per_100k"],
                r["reached"],
                r["total_usd"],
                bar,
                effect=knobs["effect"] / 100,
                reach_share=knobs["reach"] / 100,
                extra_per_person=knobs["extra"],
                at_model_price=what_if["basis"] == "model",
            )
            if got:
                r.update(got)
        out.append(r)
    return out


def passes(row: dict, flt: dict) -> bool:
    if flt["active"] and not row["active"]:
        return False
    if flt["modelled"] and row["status"] != STATUS_MODELLED:
        return False
    if flt["country"] and row["country_name"] != flt["country"]:
        return False
    if flt["min_spend"] and row["total_usd"] < flt["min_spend"]:
        return False
    if row.get("model") and row["model"] not in flt["models"]:
        return False
    if flt["q"]:
        hay = f"{row['name']} {row['org_slug']} {row['country_name']} {row['service_name']}".lower()
        if flt["q"] not in hay:
            return False
    return True


def rank_rows(rows: list[dict], key: str) -> list[dict]:
    """Modelled rows first, ranked by ``key`` (lowest cost per death first, otherwise highest first), then the rest."""
    modelled = [r for r in rows if r["status"] == STATUS_MODELLED]
    modelled.sort(key=lambda r: (r[key] or 0), reverse=(key != "cost_per_death"))
    for i, r in enumerate(modelled, 1):
        r["rank"] = i
    rest = [r for r in rows if r["status"] != STATUS_MODELLED]
    for r in rest:
        r["rank"] = None
    return modelled + rest


def cached_ranking() -> dict:
    """The ranking, kept for a minute: a slider moved ten times a second must not read the database ten times."""
    from django.core.cache import cache

    return cache.get_or_set(RANKING_CACHE_KEY, ranking, RANKING_CACHE_SECONDS)


def view(params, data: dict | None = None) -> dict:
    """The page's view under ``params``: what-if applied, filtered, ranked, with its roll-up and programme split."""
    data = data if data is not None else cached_ranking()
    what_if = what_if_from(params, data["bar_x"])
    flt = filters_from(params)
    rows = [r for r in apply_what_if(data["rows"], what_if) if passes(r, flt)]
    rows = rank_rows(rows, what_if["rank"])
    return {
        "rows": rows,
        "totals": rollup(rows),
        "programmes": programmes(rows),
        "what_if": what_if,
        "filters": flt,
        "bar_x": what_if["bar"],
    }


def opportunity_detail(opp_id: int) -> dict | None:
    """One opportunity end to end: where, baseline, model, cost and result, every step shown.

    Ranked among the opportunities it shares a programme and partner with, so
    invoice pooling is the same as on the ranking page. ``None`` if it is not a
    real (non-test) opportunity.
    """
    from connect_labs.impact import places as impact_places
    from connect_labs.impact import result as impact_result
    from connect_labs.pulse.models import PulseOpportunity

    o = PulseOpportunity.objects.filter(opportunity_id=opp_id, is_test=False).first()
    if o is None:
        return None
    peers = PulseOpportunity.objects.filter(is_test=False, opportunity_id=opp_id)
    if o.program_id is not None and o.org_slug:
        peers = PulseOpportunity.objects.filter(is_test=False, program_id=o.program_id, org_slug=o.org_slug)
    data = ranking(peers)
    row = next(r for r in data["rows"] if r["id"] == opp_id)
    out = {
        "row": row,
        "bar_x": data["bar_x"],
        "bar_note": data["bar_note"],
        "placed_at": data["placed_at"],
        "result": None,
        "placement": None,
    }
    key, iso3 = row["model"], iso3_for(row["country"])
    if key is None or iso3 is None:
        return out
    placement = impact_places.opportunity_places([opp_id]).get(opp_id)
    out["placement"] = (
        {"visits": placement.visits, "unplaced": placement.unplaced, "areas": len(placement.areas)}
        if placement
        else {"visits": 0, "unplaced": 0, "areas": 0}
    )
    areas = opportunity_areas([o], {}).get(opp_id)
    if not areas:
        from connect_labs.impact import baseline as impact_baseline

        areas = [
            {
                "name": row["country_name"],
                "level": "country",
                "share": 1.0,
                "inputs": impact_baseline.Baselines([]).for_area(key, iso3, None),
            }
        ]
    total = 0.0 if row["status"] == STATUS_COST_POOLED else row["total_usd"]
    out["result"] = impact_result.evaluate(key, iso3, areas, row["reached"], total, data["bar_x"], detail=True)
    out["model_meta"] = models()["models"][key]
    out["explore"] = _explore_links(key, row, out["result"])
    return out


def _explore_links(key: str, row: dict, result: dict) -> list[dict]:
    """Links into the Models explorer with this opportunity's place and its actual cost already set.

    One per area its visits fell in (a delivery across several states is valued state by state), so a
    reader can test how sensitive the answer is for a real opportunity, on the same evaluator. Pooled and
    unvalued rows have no cost of their own to carry, so they get none.
    """
    from urllib.parse import urlencode

    from django.urls import reverse

    from connect_labs.impact import explore

    if row["status"] != STATUS_MODELLED or not result.get("cost_per_person"):
        return []
    links = []
    for a in result["areas"]:
        overrides = explore.delivery_overrides(
            key, {k: v["value"] for k, v in a["inputs"].items()}, result["cost_per_person"]
        )
        query = {"model": key, **{f"i_{k}": v for k, v in overrides.items()}}
        links.append(
            {
                "area": a["name"],
                "level": a["level"],
                "share": a["share"],
                "path": f"{reverse('cea:models')}?{urlencode(query)}",
            }
        )
    return links
