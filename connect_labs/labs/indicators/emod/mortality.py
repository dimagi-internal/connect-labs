"""Under-5 malaria deaths per Nigerian state, and what averting them is worth on GiveWell's scale.

Cost per case cannot say whether a state is worth doing at all. Clinical incidence saturates as
transmission rises: the fitted EMOD baselines give 1,050-1,570 under-5 cases per 1,000 a year across
states whose DHS prevalence runs from 20% to 60%, and MAP's incidence is just as flat. What separates a
high-burden state from a low one is deaths, so the ranking needs deaths averted and a bar.

**Two bases for a state's under-5 malaria deaths, both read from the registry.**

* ``prevalence_scaled`` (the default): the state's all-cause under-5 deaths (``expected_deaths``: births x
  its under-5 mortality) x malaria's share of under-5 deaths. The share is set nationally (MAP's national
  malaria deaths x the under-5 share of malaria deaths, over Nigeria's all-cause under-5 deaths) and spread
  across states by DHS prevalence relative to the national figure -- the same prevalence each state's
  EMOD transmission is fitted to. An assumption, stated as one: malaria's share of a state's under-5 deaths
  is proportional to its prevalence.
* ``map``: MAP's own modelled malaria deaths for the state x the under-5 share. MAP's subnational
  mortality surface does not track DHS prevalence (Imo, 26% prevalence, has more than twice Kebbi's
  deaths at 76%), which is why it is the cross-check, not the default.

**Valuing a death averted.** Deaths averted = the design's EMOD reduction in under-5 cases x the
state's under-5 malaria deaths: mortality is assumed to fall in proportion to clinical cases. Each death
averted is GiveWell's moral weight for an under-5 death; no other benefit (morbidity, income) is counted
and no leverage or funging adjustment is applied, so the multiple of GiveWell's benchmark is a floor on
the mortality benefit alone.

Pure functions apart from ``registry_burden``; the MCP tool resolves and passes the burden in.
"""

from __future__ import annotations

from connect_labs.labs.indicators.cost_effectiveness import BAR, BENCHMARK_UOV_PER_DOLLAR, MORAL_WEIGHT, Parameter

BASES = ("prevalence_scaled", "map")
DEFAULT_BASIS = "prevalence_scaled"

U5_SHARE_OF_MALARIA_DEATHS = Parameter(
    0.76, "WHO World Malaria Report 2023: children under 5 were ~76% of malaria deaths in the African Region"
)
#: A state's malaria share of its under-5 deaths is capped here, so a prevalence far above the national
#: figure cannot attribute an implausible fraction of all child deaths to malaria.
MAX_MALARIA_SHARE = 0.5


def malaria_u5_deaths(states: dict[str, dict], basis: str = DEFAULT_BASIS) -> dict[str, float]:
    """Under-5 malaria deaths a year per state, on ``basis``.

    ``states`` maps EVERY state (the national share needs them all) to ``{u5_deaths, map_malaria_deaths,
    pfpr, pop_u5}``; a state missing a value is left out of the answer.
    """
    if basis not in BASES:
        raise ValueError(f"deaths_basis must be one of {', '.join(BASES)}; got {basis!r}")
    share_u5 = U5_SHARE_OF_MALARIA_DEATHS.value
    if basis == "map":
        return {n: s["map_malaria_deaths"] * share_u5 for n, s in states.items() if s.get("map_malaria_deaths")}
    full = {n: s for n, s in states.items() if s.get("u5_deaths") and s.get("pfpr") and s.get("pop_u5")}
    all_cause = sum(s["u5_deaths"] for s in full.values())
    malaria = sum(s.get("map_malaria_deaths") or 0 for s in full.values()) * share_u5
    if not all_cause or not malaria:
        return {}
    national_share = malaria / all_cause
    national_pfpr = sum(s["pfpr"] * s["pop_u5"] for s in full.values()) / sum(s["pop_u5"] for s in full.values())
    return {
        n: s["u5_deaths"] * min(MAX_MALARIA_SHARE, national_share * s["pfpr"] / national_pfpr) for n, s in full.items()
    }


def value(deaths_averted: float, spend: float) -> dict:
    """Cost per death averted and the multiple of GiveWell's 1x benchmark (deaths only, a floor)."""
    multiple = MORAL_WEIGHT.value * deaths_averted / spend / BENCHMARK_UOV_PER_DOLLAR.value
    return {
        "deaths_averted_per_year": deaths_averted,
        "cost_per_death_averted": spend / deaths_averted,
        "multiple_of_benchmark": multiple,
        "clears_bar": multiple >= BAR.value,
    }


def registry_burden() -> dict[str, dict]:
    """``{state: {u5_deaths, map_malaria_deaths, pfpr, pop_u5}}`` for every Nigerian state, from the registry."""
    from connect_labs.labs.indicators import boundaries as boundary_set
    from connect_labs.labs.indicators.pmc import ISO
    from connect_labs.labs.indicators.resolve import BulkResolver

    units = list(boundary_set.owned().filter(iso_code=ISO, admin_level=1).order_by("name"))
    bulk = BulkResolver(units)
    out = {}
    for b in units:
        got = {c: bulk.get(c, b) for c in ("expected_deaths", "malaria_deaths", "malaria_prevalence", "pop_u5")}
        out[b.name] = {
            "u5_deaths": got["expected_deaths"].value if got["expected_deaths"] else None,
            "map_malaria_deaths": got["malaria_deaths"].value if got["malaria_deaths"] else None,
            "pfpr": got["malaria_prevalence"].value / 100 if got["malaria_prevalence"] else None,
            "pop_u5": got["pop_u5"].value if got["pop_u5"] else None,
        }
    return out
