"""Putting scenarios side by side: what each one sets, what it gave, and how they differ.

Pure. A scenario's *settings* are what a person chose (``settings``); its *headline* is the few numbers that decide
between scenarios (``headline``, read from the answer ``sizing.market_of`` gives); ``compare`` lines a set of them up
against a reference, marks the best of each result, and says in words what differs. The attributes that matter are
declared once (``RESULTS``), so the group page, the agent tool and the snapshot all show the same columns.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.effectiveness import market
from connect_labs.effectiveness.scenario import CAPS, COST_BASES, DEFAULT_GRAIN, Scenario

#: A change in a headline smaller than this (relative) is not a change worth saying.
DRIFT = 0.005


@dataclass(frozen=True)
class Result:
    """A number that decides between scenarios, how to show it, and which way is better (None: just information)."""

    key: str
    label: str
    unit: str  # "people", "usd", "count", "multiple", "share"
    better: str | None = None  # "high" | "low" | None
    #: Whether this is one of the few that belong in a one-line summary.
    headline: bool = False


RESULTS: tuple[Result, ...] = (
    Result("deaths", "Deaths averted a year", "count", "high", True),
    Result("cost", "Cost a year", "usd", None, True),
    Result("cost_per_death", "Cost per death averted", "usd", "low", True),
    Result("multiple", "Multiple of GiveWell's benchmark", "multiple", "high", True),
    Result("people", "People reached", "people"),
    Result("places", "Places", "count"),
    Result("countries", "Countries", "count"),
    Result("deaths_p05", "Deaths averted, low (5th percentile)", "count", "high"),
    Result("deaths_p95", "Deaths averted, high (95th percentile)", "count", "high"),
    Result("overlap", "Steadiness: share of the plan in the best plan on a redraw", "share", "high"),
    Result("regional_proxy_people", "People counted from a regional low-birthweight rate", "people", "low"),
    Result("births_off_people", "People in countries whose births are far from the UN's", "people", "low"),
)
RESULT_KEYS = tuple(r.key for r in RESULTS)


def headline(payload: dict) -> dict:
    """The decisive numbers from ``sizing.market_of``'s answer. Several programmes add up (``overall``); the plan's own
    reliability, proxy and births notes are taken from the first programme that has a plan."""
    overall = payload["overall"]
    plan = next((m["plan"] for m in payload["models"] if m.get("plan")), None)
    rel = (plan or {}).get("reliability") or {}
    out = {
        "deaths": overall["deaths_averted"],
        "cost": overall["cost"],
        "cost_per_death": overall["cost_per_death"],
        "multiple": overall["multiple"],
        "people": overall["people"],
        "places": plan["totals"]["places"] if plan else overall["places"],
        "countries": (
            plan["totals"]["countries"] if plan else len({r["country"] for m in payload["models"] for r in m["rows"]})
        ),
        "deaths_p05": (rel.get("deaths") or {}).get("p05"),
        "deaths_p95": (rel.get("deaths") or {}).get("p95"),
        "overlap": rel.get("overlap"),
        "regional_proxy_people": ((plan or {}).get("regional_proxy") or {}).get("people"),
        "births_off_people": ((plan or {}).get("births_off") or {}).get("people"),
        "feasible": overall["feasible"],
        "left_out": {
            **{k: v["left_out"] for k, v in ((plan or {}).get("limits") or {}).get("caps", {}).items()},
            **(
                {"min_place": plan["limits"]["left_out_small"]}
                if plan and plan["limits"].get("left_out_small")
                else {}
            ),
        },
    }
    return out


# ----------------------------------------------------------------------------------------------------- settings


def _num(v: float) -> str:
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}".rstrip("0").rstrip(".")


def settings(sc: Scenario) -> dict[str, str]:
    """What a scenario chooses, as short phrases keyed by what is chosen (only keys that apply), in reading order."""
    out: dict[str, str] = {}
    out["Places"] = "territories" if sc.grain != DEFAULT_GRAIN else "countries"
    out["Target"] = f"{_num(sc.target)} people" if sc.target else "everyone reachable"
    reach = [f"{m} {_num(sc.dials_for(m)['reach'])}%" for m in sc.models]
    out["Reach cap"] = ", ".join(reach)
    if any(m in market.PLACE_RISK for m in sc.models):
        out["Risk"] = "scaled to each place's mortality" if sc.place_risk else "the model's one figure everywhere"
    out["Price"] = (
        "from delivered programmes where there are some" if sc.cost_basis != COST_BASES[0] else "the model's"
    )
    out["Trust in place figures"] = f"{_num(sc.trust)}%"
    ceilings = [f"{c.column.lower()} at most {_num(sc.caps[c.key])}" for c in CAPS if c.key in sc.caps]
    limits = (
        ceilings
        + ([f"places of at least {_num(sc.min_place)} people"] if sc.min_place else [])
        + ([f"at most {sc.max_countries} countries"] if sc.max_countries else [])
    )
    out["Limits"] = "; ".join(limits) if limits else "none"
    extras = [f"{m} {k} {_num(v)}" for m, ch in sc.dials.items() for k, v in ch.items() if k != "reach"]
    extras += [f"{m} input {k} = {_num(v)}" for m, ch in sc.overrides.items() for k, v in ch.items()]
    if extras:
        out["Other changes"] = "; ".join(extras)
    return out


def sets(sc: Scenario) -> list[str]:
    """Only what differs from the model as published, as a sentence each: what to read before the numbers."""
    base = Scenario(models=sc.models)
    mine, theirs = settings(sc), settings(base)
    keep = [k for k, v in mine.items() if theirs.get(k) != v]
    return [f"{k}: {mine[k]}" for k in keep]


def differences(a: Scenario, b: Scenario) -> list[str]:
    """What ``b`` changes from ``a``, as phrases ("Limits: none → conflict at most 10")."""
    x, y = settings(a), settings(b)
    return [f"{k}: {x.get(k, '–')} → {y.get(k, '–')}" for k in dict.fromkeys([*x, *y]) if x.get(k) != y.get(k)]


# ----------------------------------------------------------------------------------------------------- comparing


def moved(was: dict | None, now: dict) -> dict[str, tuple[float, float]]:
    """The headline numbers that changed by more than ``DRIFT`` since ``was``: ``{key: (was, now)}``."""
    out = {}
    for r in RESULTS:
        a, b = (was or {}).get(r.key), now.get(r.key)
        if a is None or b is None:
            continue
        if abs(b - a) > DRIFT * max(abs(a), 1e-9):
            out[r.key] = (a, b)
    return out


def compare(items: list[dict], reference: int | None = None) -> dict:
    """``items``: ``[{"id", "name", "scenario": Scenario, "headline": dict, "snapshot": dict, ...}]`` in the group's
    order. Returns the same items with ``settings``, ``sets``, ``delta`` (change in each result against the
    reference, in %), ``differs`` (in words, from the scenario it was made from when that is
    listed, else the reference), ``drift`` (what moved since its snapshot) and
    ``best`` (the results on which it is best), plus the attributes. The reference is the one asked for, else the
    first."""
    if not items:
        return {"attributes": [vars(r) for r in RESULTS], "reference": None, "scenarios": []}
    ref = next((i for i in items if i["id"] == reference), items[0])
    best: dict[str, float] = {}
    for r in RESULTS:
        vals = [i["headline"][r.key] for i in items if i.get("headline") and i["headline"].get(r.key) is not None]
        if r.better and vals:
            # a "best" means something only when ONE scenario is ahead on it: a tie for the top is nobody's win
            top = max(vals) if r.better == "high" else min(vals)
            if sum(1 for v in vals if abs(v - top) < 1e-9 * max(abs(top), 1)) == 1:
                best[r.key] = top
    by_id = {i["id"]: i for i in items}
    out = []
    for i in items:
        h = i.get("headline") or {}
        # what a scenario changes is read against the one it was made from (when that is here), else the reference
        parent = by_id.get(i.get("derived_from")) or ref
        if parent is i:
            parent = None
        delta = {}
        for r in RESULTS:
            a, b = (ref.get("headline") or {}).get(r.key), h.get(r.key)
            if a and b is not None and i is not ref:
                delta[r.key] = (b - a) / abs(a) * 100
        out.append(
            {
                **{k: v for k, v in i.items() if k != "scenario"},
                "settings": settings(i["scenario"]),
                "sets": sets(i["scenario"]),
                "differs": differences(parent["scenario"], i["scenario"]) if parent else [],
                "differs_from": {"id": parent["id"], "name": parent["name"]} if parent else None,
                "delta": delta,
                "drift": moved(i.get("snapshot"), h) if i.get("snapshot") else None,
                "best": [k for k, v in best.items() if h.get(k) is not None and abs(h[k] - v) < 1e-9 * max(abs(v), 1)],
            }
        )
    return {"attributes": [vars(r) for r in RESULTS], "reference": ref["id"], "scenarios": out}
