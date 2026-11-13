"""The what-if, filters and ordering of the Implementations page, as pure functions over ranking rows.

The page draws what these return and does no arithmetic of its own, so a figure on the page is the figure in the
JSON, the CSV and the agent tool. ``result.reprice`` is the formula; this applies it row by row.
"""

from __future__ import annotations

from connect_labs.effectiveness.implementation.catalog import STATUS_MODELLED, models
from connect_labs.effectiveness.result import reprice

KNOBS = ("extra", "effect", "reach")  # per model: extra $ per person, effect % of the model, people counted %


KNOB_DEFAULT = {"extra": 0.0, "effect": 100.0, "reach": 100.0}


RANK_KEYS = ("x", "deaths_averted", "cost_per_death", "total_usd")


BASES = ("actual", "model")


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
            got = reprice(
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
