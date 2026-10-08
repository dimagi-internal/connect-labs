"""Rank (state, design) pairs for malaria chemoprevention by value for money, at the caller's costs.

The answer to "of these states, which state and which design is most cost-effective at my delivery
costs?". It reads the precomputed per-state design grid (``data/pmc_state_grid.json``, written by
``tools/pmc_emod/states/run_grid.py``): for every state, EMOD run in THAT state's own setting --
transmission fitted to its DHS prevalence, season taken from its CHIRPS monthly rainfall -- for each
design in ``tools/pmc_emod/states/designs.py``. Each design carries its relative reduction in under-5
cases and its doses per targeted child per year.

The costing is the explorer's (``pmc.cost_per_dose``) and the burden is MAP-anchored:

* cases averted = averted_u5_pct / 100 x the state's MAP incidence (all ages, per 1,000) / 1,000 x pop_u5
* spend = doses per targeted child per year x the targeted children (target_pop_fraction x pop_u5)
  x cost per dose
* cost per case averted = spend / cases averted

Given each state's under-5 malaria deaths (``mortality.malaria_u5_deaths``), pairs rank by cost per death
averted instead -- deaths averted = averted_u5_pct / 100 x those deaths -- and carry the multiple of
GiveWell's 1x benchmark and whether it clears GiveWell's bar. Cost per case alone cannot tell a state
worth doing from one that is not: clinical incidence saturates as transmission rises, deaths do not.
Without deaths (the tool always passes them) the ranking falls back to cost per case.

Young children get malaria more often than the all-ages rate, so cases averted are a floor and the
cost per case a ceiling. Nothing here is silently dropped: a state the fit could not reach, a state
missing from the grid, and a design with no measurable effect are each returned with the reason.

No Django import at module level: the tool passes the grid in, so tests can use a fixture.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from connect_labs.labs.indicators import pmc
from connect_labs.labs.indicators.cost_effectiveness import BAR
from connect_labs.labs.indicators.emod import mortality

GRID_PATH = Path(__file__).resolve().parents[1] / "data" / "pmc_state_grid.json"

LABEL = "illustrative · fitted to each state's prevalence and rainfall"

DEFAULT_TOP_N = 10
MAX_TOP_N = 50
KINDS = ("pmc", "smc")

NO_EFFECT = "no measurable effect"

CAVEATS = (
    "One fitted setting per state: EMOD's transmission is fitted to the state's DHS malaria prevalence and its "
    "season to CHIRPS monthly rainfall. That is not a full calibration, so every figure is illustrative.",
    "EMOD gives each design's relative reduction in under-5 cases; the absolute burden is the state's MAP "
    "malaria incidence (all ages). Young children get malaria more often than the all-ages rate, so cases "
    "averted are a floor and cost per case a ceiling.",
    "SMC is simulated in EMOD as SPAQ, PMC as SP. SMC is modelled only where the rain is seasonal "
    "(at least 60% of the year's rain in the wettest quarter).",
    "Targeted children are estimated from the under-5 population: 21/60 for 3-24 months (PMC), 56/60 for "
    "3-59 months (SMC).",
    "The same price per visit is applied to PMC and SMC visits.",
)

DEATHS_CAVEATS = {
    "prevalence_scaled": (
        "Deaths: the state's under-5 deaths x malaria's share of them, set nationally from MAP and spread by DHS "
        "prevalence. Mortality is assumed to fall with clinical cases, and only deaths are valued (GiveWell's "
        "moral weight, no income or morbidity benefit, no leverage or funging), so the multiple is a floor."
    ),
    "map": (
        "Deaths: MAP's modelled malaria deaths for the state x 76% under five. MAP's state pattern does not track "
        "DHS prevalence. Mortality is assumed to fall with clinical cases, and only deaths are valued, so the "
        "multiple is a floor."
    ),
}
NO_DEATHS = "no under-5 malaria death figure"


def sig(n: float | None, figures: int = 2) -> float | None:
    """Round to significant figures, keeping small values (a $0.87 cost per case stays 0.87)."""
    if n is None or not math.isfinite(n):
        return None
    if n == 0:
        return 0
    out = round(n, figures - 1 - int(math.floor(math.log10(abs(n)))))
    return int(out) if out == int(out) else out


_cache: dict = {}


def load_grid(path: str | os.PathLike | None = None) -> dict | None:
    """The per-state grid, or None when it has not been computed yet. Re-read when the file changes."""
    p = Path(path) if path else GRID_PATH
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        return None
    hit = _cache.get(str(p))
    if hit and hit[0] == mtime:
        return hit[1]
    with open(p) as f:
        grid = json.load(f)
    _cache[str(p)] = (mtime, grid)
    return grid


def _clean_name(name: str) -> str:
    """A selection name as the Targeting page writes it ('Kano (NGA)') or as typed ('kano') -> 'kano'."""
    n = name.strip()
    if n.endswith(")") and "(" in n:
        n = n[: n.rindex("(")].strip()
    return n.lower()


#: Common names for a state that the grid spells differently (keys and values lower-case).
ALIASES = {
    "fct": "abuja federal capital territory",
    "federal capital territory": "abuja federal capital territory",
    "abuja": "abuja federal capital territory",
    "abuja fct": "abuja federal capital territory",
}


def _match(key: str, by_lower: dict) -> str | None:
    """The grid's name for a cleaned selection name: exact, an alias, or the ONE grid name whose words
    include all of the selection's words ('cross river state' does not, 'akwa ibom' does)."""
    if key in by_lower:
        return by_lower[key]
    if ALIASES.get(key) in by_lower:
        return by_lower[ALIASES[key]]
    words = set(key.split())
    hits = [name for low, name in by_lower.items() if words and words <= set(low.split())]
    return hits[0] if len(hits) == 1 else None


def _pct(v) -> str:
    return f"{v * 100:.0f}%" if v is not None else "unknown"


def _fit_note(state: dict) -> str:
    fit = state.get("fit") or {}
    target = (state.get("inputs") or {}).get("pfpr_target")
    return f"fitted: PfPR 2-5 {_pct(fit.get('pfpr_2_5y'))} vs DHS {_pct(target)}, season from rainfall"


def _design_label(design: dict) -> str:
    label = design.get("label") or ""
    if design.get("kind") == "smc" and "SPAQ" not in label:
        label = f"{label} (SPAQ)"
    return label


def costs_line(costs: dict) -> str:
    return (
        f"Costs used: ${costs['cost_per_visit']:.2f} a visit + {costs['platform_fee'] * 100:.0f}% platform fee, "
        f"{costs['dose_rate'] * 100:.0f}% of visits give a dose = ${costs['cost_per_dose']:.2f} a dose."
    )


def rank_pairs(
    states=None,
    cost_per_visit: float = 0.8,
    platform_fee: float = 0.2,
    dose_rate: float = 0.95,
    top_n: int = DEFAULT_TOP_N,
    kinds=KINDS,
    *,
    grid: dict,
    deaths: dict[str, float] | None = None,
    deaths_basis: str = mortality.DEFAULT_BASIS,
) -> dict:
    """The top ``top_n`` (state, design) pairs in ``states``, best value first.

    ``states`` None means every state in the grid. Ties (equal exact cost per case) go to the
    pair that averts more cases. With ``deaths`` ({state: under-5 malaria deaths a year}) the order is
    cost per death averted, ties to more deaths averted. Raises ValueError for impossible costs (dose_rate <= 0,
    negative prices), a top_n below 1, an unknown kind or deaths_basis; the tool turns that into a 400.
    """
    per_dose = pmc.cost_per_dose(cost_per_visit, platform_fee, dose_rate)
    if deaths_basis not in mortality.BASES:
        raise ValueError(f"deaths_basis must be one of {', '.join(mortality.BASES)}; got {deaths_basis!r}")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a whole number of at least 1")
    top_n = min(top_n, MAX_TOP_N)
    kinds = tuple(kinds)
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown or not kinds:
        raise ValueError(f"kinds must be drawn from {', '.join(KINDS)}; got {', '.join(unknown) or 'none'}")

    by_lower = {name.lower(): name for name in grid.get("states", {})}
    if states is None:
        wanted = sorted(grid.get("states", {}))
    else:
        wanted, seen = [], set()
        for s in states:
            key = _clean_name(s)
            if key and key not in seen:
                seen.add(key)
                wanted.append(_match(key, by_lower) or s.strip())

    pairs, excluded, excluded_designs = [], [], []
    for name in wanted:
        state = grid["states"].get(name)
        if state is None:
            excluded.append({"state": name, "reason": "not in the per-state model grid"})
            continue
        fit = state.get("fit") or {}
        if fit.get("fit") != "ok":
            target = (state.get("inputs") or {}).get("pfpr_target")
            reason = f"the model could not be fitted to its prevalence (DHS {_pct(target)}"
            reason += f"; closest fit {_pct(fit['pfpr_2_5y'])})" if fit.get("pfpr_2_5y") is not None else ")"
            excluded.append({"state": name, "reason": reason})
            continue
        inputs = state.get("inputs") or {}
        incidence, pop_u5 = inputs.get("incidence_per_1000"), inputs.get("pop_u5")
        if not incidence or not pop_u5:
            excluded.append({"state": name, "reason": "no malaria incidence or under-5 population figure"})
            continue
        state_deaths = None
        if deaths is not None:
            state_deaths = deaths.get(name)
            if not state_deaths:
                excluded.append({"state": name, "reason": NO_DEATHS})
                continue
        note = _fit_note(state)
        mine = 0
        for code, d in (state.get("designs") or {}).items():
            if d.get("kind") not in kinds:
                continue
            pct, ci = d.get("averted_u5_pct"), d.get("averted_u5_ci")
            cases = (pct or 0) / 100 * incidence / 1000 * pop_u5
            # An effect smaller than its own uncertainty is not a result to cost.
            if pct is None or pct <= 0 or (ci is not None and ci >= pct) or cases <= 0:
                excluded_designs.append(
                    {"state": name, "design_code": code, "design_label": _design_label(d), "reason": NO_EFFECT}
                )
                continue
            spend = d["doses_per_child_per_year"] * d["target_pop_fraction"] * pop_u5 * per_dose
            mine += 1
            valued = mortality.value(pct / 100 * state_deaths, spend) if state_deaths else {}
            pairs.append(
                {
                    **valued,
                    "state": name,
                    "design_code": code,
                    "design_label": _design_label(d),
                    "kind": d.get("kind"),
                    "drug": d.get("drug"),
                    "cases_averted_per_year": cases,
                    "spend_per_year": spend,
                    "cost_per_case_averted": spend / cases,
                    "averted_u5_pct": round(pct, 1),
                    "ci": round(ci, 1) if ci is not None else None,
                    "fit_note": note,
                }
            )
        if not mine:
            excluded.append({"state": name, "reason": f"no design with a measurable effect ({NO_EFFECT})"})

    # Exact cost per case (rounded only to absorb float noise), then more cases averted. Display rounds.
    if deaths is not None:
        pairs.sort(key=lambda p: (round(p["cost_per_death_averted"], 9), -p["deaths_averted_per_year"]))
    else:
        pairs.sort(key=lambda p: (round(p["cost_per_case_averted"], 9), -p["cases_averted_per_year"]))

    best_per_state, seen = [], set()
    for p in pairs:
        if p["state"] not in seen:
            seen.add(p["state"])
            best_per_state.append(p)
    totals = {
        "states": len(best_per_state),
        "cases_averted_per_year": sig(sum(p["cases_averted_per_year"] for p in best_per_state), 3),
        "spend_per_year": sig(sum(p["spend_per_year"] for p in best_per_state), 3),
    }
    if deaths is not None:
        totals["deaths_averted_per_year"] = sig(sum(p["deaths_averted_per_year"] for p in best_per_state), 3)

    def present(p, rank=None):
        out = {
            **p,
            "state_design": f"{p['state']} · {p['design_label']}",
            "cases_averted_per_year": sig(p["cases_averted_per_year"]),
            "spend_per_year": sig(p["spend_per_year"]),
            "cost_per_case_averted": sig(p["cost_per_case_averted"]),
        }
        if "multiple_of_benchmark" in p:
            out["deaths_averted_per_year"] = sig(p["deaths_averted_per_year"])
            out["cost_per_death_averted"] = sig(p["cost_per_death_averted"])
            out["multiple_of_benchmark"] = round(p["multiple_of_benchmark"], 1)
        return {"rank": rank, **out} if rank is not None else out

    ranked = [present(p, i + 1) for i, p in enumerate(pairs[:top_n])]
    costs = {
        "cost_per_visit": cost_per_visit,
        "platform_fee": platform_fee,
        "dose_rate": dose_rate,
        "cost_per_dose": round(per_dose, 3),
    }
    if not pairs:
        note = "No state and design in this selection could be ranked; 'excluded' says why for each state."
    elif len(pairs) < top_n:
        note = f"Only {len(pairs)} state × design pairs could be ranked in this selection; all are shown."
    else:
        note = None
    return {
        "label": LABEL,
        "ranked": ranked,
        "pairs_ranked": len(pairs),
        "top_n": top_n,
        "note": note,
        "best_per_state": [present(p) for p in best_per_state],
        "best_per_state_totals": totals,
        "excluded": excluded,
        "excluded_designs": excluded_designs,
        "costs": costs,
        "costs_line": costs_line(costs),
        "caveats": list(CAVEATS) + ([DEATHS_CAVEATS[deaths_basis]] if deaths is not None else []),
        **(
            {
                "ranked_by": "cost per death averted",
                "deaths_basis": deaths_basis,
                "bar": BAR.value,
                "pairs_clearing_bar": sum(p["clears_bar"] for p in pairs),
                "states_clearing_bar": len({p["state"] for p in pairs if p["clears_bar"]}),
            }
            if deaths is not None
            else {"ranked_by": "cost per case averted"}
        ),
    }
