"""Valuing one delivery: the models' own formulas at the delivery's places and its actual cost."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.catalog import (
    SERVICE_MODEL,
    STATUS_MODELLED,
    STATUS_NO_COUNTRY,
    STATUS_NO_MODEL,
    STATUS_NOTHING_DELIVERED,
    iso3_for,
    models,
)

LOCAL_LEVELS = ("district", "state or province")


def headline_level(areas: list) -> str:
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


def value(service: str, country: str, total_usd: float, reached: int, areas: list | None = None) -> dict:
    """The CEA for one delivery, run through the models' own formulas (``connect_labs.effectiveness``).

    ``areas`` is the place layer for where its visits happened (``result.evaluate``);
    without it the delivery is valued at its country's figures, as the published
    per-country results are. No database access unless ``areas`` needs it.
    """
    from connect_labs.effectiveness import baseline, result

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
                "inputs": baseline.Baselines([]).for_area(key, iso3, None),
            }
        ]
    r = result.evaluate(key, iso3, areas, reached, total_usd, m["bar_x"])
    for k in ("model_x", "model_cost_per_person", "model_deaths_per_100k", "country_model_x", "x"):
        out[k] = r[k]
    out["baseline_level"] = headline_level(areas)
    if not reached or total_usd <= 0:
        out["x"] = None
        out["status"] = STATUS_NOTHING_DELIVERED
        return out
    out["status"] = STATUS_MODELLED
    out["deaths_averted"] = r["deaths_averted"]
    out["cost_per_death"] = r["cost_per_death"]
    out["clears_bar"] = r["clears_bar"]
    return out
