"""Cost-effectiveness — what a place's numbers buy, not just what reaching it costs.

``interventions`` answers *how much could be absorbed*: unit cost x eligible
people. A funder's next question is *what does that money buy*, and that needs
an outcome: deaths averted, and how that compares with the funder's bar. This
module is that second half, for the one intervention we can model today without
a simulator: **door-to-door ORS**, on GiveWell's chain.

**The chain is GiveWell's, reproduced, not re-derived.** GiveWell's ORS and zinc
CEA (August 2023), as applied to its CHAI Bauchi grant: an uptake gain from free
household delivery (Wagner et al. 2019), a non-ORS diarrhoea mortality backed out
of the baseline, ORS's mortality effect among users, then GiveWell's moral
weights and adjustments. Every parameter carries its source, and the test suite
pins that the chain reproduces GiveWell's published Bauchi column, so a change
that moves it is caught rather than quoted.

**The place-specific inputs come from the registry, with their provenance.**
Two numbers make a place's answer its own:

* baseline ORS coverage — the ``ors_coverage`` measure, the same value the map
  shows for that area (DHS where it exists);
* direct diarrhoea mortality per 1,000 under-fives — which no source in the
  registry publishes subnationally. It is derived: GiveWell's Bauchi figure
  (6.34, backed out of its 1.3% non-ORS mortality) scaled by the area's under-5
  mortality over Bauchi's, both read from the registry with the same method.
  That is an assumption, stated as one (diarrhoea's share of under-5 deaths is
  held at Bauchi's), and either input can be overridden by a caller who has a
  better figure.

**What it deliberately does not count:** zinc, water treatment, hygiene
promotion, and any transmission effect. A transmission effect is real for
cholera and needs a dynamic model (IDM's MOSAIC, LASER) to value; until one is
wired in as an effect source, those benefits are listed as uncounted rather than
approximated. Leaving them out makes every figure here a floor on the ORS
benefit alone.

Pure functions only: nothing here touches the database, so the MCP tool and the
page can share one implementation and the tests need no fixtures for the
arithmetic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: The intervention codes this module can value. One today; the shape is the one a
#: second effect model (a precomputed transmission-model response curve) would
#: slot into.
INTERVENTIONS = ("ors",)


@dataclass(frozen=True)
class Parameter:
    value: float
    source: str


#: GiveWell's ORS chain, as published. Changing any of these changes every answer;
#: the tests pin the published Bauchi column so a change cannot pass unnoticed.
WAGNER_NONTREATMENT_REDUCTION = Parameter(
    0.469, "Wagner et al. 2019 (PLOS Medicine), 'free and convenient' arm: reduction in non-treatment"
)
INTERNAL_VALIDITY = Parameter(0.80, "GiveWell ORS/zinc CEA (Aug 2023), CHAI Bauchi: internal validity")
EXTERNAL_VALIDITY = Parameter(0.85, "GiveWell ORS/zinc CEA (Aug 2023), CHAI Bauchi: external validity")
INDIRECT_MULTIPLIER = Parameter(1.5, "GiveWell: 0.5 indirect deaths per direct diarrhoea death")
ORS_MORTALITY_EFFECT = Parameter(0.595, "GiveWell: ORS reduces diarrhoea mortality by 59.5% among users")
MORAL_WEIGHT = Parameter(113.0, "GiveWell moral weight, under-5 death averted")
MORTALITY_SHARE_OF_BENEFITS = Parameter(0.9, "GiveWell: mortality's share of total benefits")
EXCLUDED_EFFECTS = Parameter(1.011, "GiveWell: adjustment for excluded effects")
LEVERAGING = Parameter(1.0, "GiveWell CHAI Bauchi: no government co-financing (generic Nigeria case used 0.797)")
FUNGING = Parameter(0.968, "GiveWell CHAI Bauchi: funging adjustment")
BENCHMARK_UOV_PER_DOLLAR = Parameter(
    0.003, "GiveWell cost-effectiveness models page (2026): ~0.003 units of value per $ is 1x"
)
BAR = Parameter(6.0, "GiveWell's bar for new grants, as of May 2026")

#: The anchor that turns an area's under-5 mortality into direct diarrhoea
#: mortality. GiveWell's Bauchi figure, backed out of its 1.3% non-ORS mortality.
ANCHOR_ISO = "NGA"
ANCHOR_AREA = "Bauchi"
ANCHOR_DIARRHOEA_MORTALITY = Parameter(
    6.34, "GiveWell CHAI Bauchi: direct diarrhoea deaths per 1,000 under-5s per year (from its 1.3% non-ORS mortality)"
)

#: Rows and columns of the sensitivity grid. Prices bracket a door-to-door visit;
#: coverage brackets what household surveys report across northern Nigeria.
SENSITIVITY_UNIT_COSTS = (2.00, 2.50, 3.00, 3.50, 4.34, 5.00)
SENSITIVITY_COVERAGES = (0.40, 0.50, 0.60, 0.70, 0.80)

NOT_COUNTED = (
    "zinc",
    "water treatment (chlorine)",
    "hygiene promotion",
    "immunization counselling",
    "transmission effects (cholera) — need a dynamic model such as IDM's MOSAIC to value",
)


def units_of_value_per_death() -> float:
    """GiveWell's units of value per under-5 death averted, after adjustments (= 122.9)."""
    return (
        MORAL_WEIGHT.value
        / MORTALITY_SHARE_OF_BENEFITS.value
        * EXCLUDED_EFFECTS.value
        * LEVERAGING.value
        * FUNGING.value
    )


def derived_diarrhoea_mortality(area_u5mr: float, anchor_u5mr: float) -> float:
    """Direct diarrhoea deaths per 1,000 under-5s a year, scaled from Bauchi.

    Holds diarrhoea's share of under-5 deaths at Bauchi's. Survey diarrhoea
    prevalence does not always agree with that ratio (it points higher in some
    states), which is why the caller can override the result.
    """
    if anchor_u5mr <= 0:
        raise ValueError("anchor under-5 mortality must be positive")
    return ANCHOR_DIARRHOEA_MORTALITY.value * area_u5mr / anchor_u5mr


@dataclass(frozen=True)
class OrsResult:
    households: float
    children_reached: float
    cost_per_child: float
    uptake_gain: float
    non_ors_mortality_per_1000: float
    deaths_averted: float
    cost_per_death_averted: float
    units_of_value_per_dollar: float
    multiple_of_benchmark: float
    clears_bar: bool
    breakeven_unit_cost: float

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def ors_chain(
    *,
    spend: float,
    unit_cost: float,
    children_per_household: float,
    baseline_coverage: float,
    diarrhoea_mortality: float,
) -> OrsResult:
    """GiveWell's ORS chain for one delivery round.

    ``unit_cost`` is per household visit; ``baseline_coverage`` is the share of
    diarrhoea episodes already treated with ORS (0-1); ``diarrhoea_mortality`` is
    direct diarrhoea deaths per 1,000 under-5s a year. One round is credited with
    a full year of benefit, as GiveWell's model does.
    """
    if spend <= 0 or unit_cost <= 0 or children_per_household <= 0:
        raise ValueError("spend, unit_cost and children_per_household must be positive")
    if not 0 <= baseline_coverage < 1:
        raise ValueError("baseline_coverage is a share between 0 and 1")
    if diarrhoea_mortality <= 0:
        raise ValueError("diarrhoea_mortality must be positive")

    households = spend / unit_cost
    children = households * children_per_household
    uptake = (
        (1 - baseline_coverage)
        * WAGNER_NONTREATMENT_REDUCTION.value
        * INTERNAL_VALIDITY.value
        * EXTERNAL_VALIDITY.value
    )
    non_ors = diarrhoea_mortality * INDIRECT_MULTIPLIER.value / (1 - ORS_MORTALITY_EFFECT.value * baseline_coverage)
    deaths = children * uptake * non_ors / 1000 * ORS_MORTALITY_EFFECT.value
    cost_per_death = spend / deaths
    uov_per_dollar = units_of_value_per_death() / cost_per_death
    multiple = uov_per_dollar / BENCHMARK_UOV_PER_DOLLAR.value
    return OrsResult(
        households=households,
        children_reached=children,
        cost_per_child=unit_cost / children_per_household,
        uptake_gain=uptake,
        non_ors_mortality_per_1000=non_ors,
        deaths_averted=deaths,
        cost_per_death_averted=cost_per_death,
        units_of_value_per_dollar=uov_per_dollar,
        multiple_of_benchmark=multiple,
        clears_bar=multiple >= BAR.value,
        # The multiple is inverse in price, so the price that lands exactly on the bar.
        breakeven_unit_cost=unit_cost * multiple / BAR.value,
    )


def sensitivity(
    *, spend: float, children_per_household: float, diarrhoea_mortality: float, baseline_coverage: float
) -> dict:
    """Multiple of the benchmark across price (rows) x baseline ORS coverage (columns).

    The area's own coverage is added as a column if it is not already one, so the
    grid always contains the answer being quoted.
    """
    covs = sorted({*SENSITIVITY_COVERAGES, round(baseline_coverage, 3)})
    grid = [
        [
            round(
                ors_chain(
                    spend=spend,
                    unit_cost=price,
                    children_per_household=children_per_household,
                    baseline_coverage=c,
                    diarrhoea_mortality=diarrhoea_mortality,
                ).multiple_of_benchmark,
                1,
            )
            for c in covs
        ]
        for price in SENSITIVITY_UNIT_COSTS
    ]
    return {"unit_costs": list(SENSITIVITY_UNIT_COSTS), "baseline_coverages": covs, "multiples": grid}


def parameters() -> dict:
    """Every fixed parameter with its source — what a reviewer checks first."""
    named = {
        "wagner_nontreatment_reduction": WAGNER_NONTREATMENT_REDUCTION,
        "internal_validity": INTERNAL_VALIDITY,
        "external_validity": EXTERNAL_VALIDITY,
        "indirect_multiplier": INDIRECT_MULTIPLIER,
        "ors_mortality_effect": ORS_MORTALITY_EFFECT,
        "moral_weight": MORAL_WEIGHT,
        "mortality_share_of_benefits": MORTALITY_SHARE_OF_BENEFITS,
        "excluded_effects": EXCLUDED_EFFECTS,
        "leveraging": LEVERAGING,
        "funging": FUNGING,
        "benchmark_units_of_value_per_dollar": BENCHMARK_UOV_PER_DOLLAR,
        "bar": BAR,
        "anchor_diarrhoea_mortality": ANCHOR_DIARRHOEA_MORTALITY,
    }
    out = {k: {"value": p.value, "source": p.source} for k, p in named.items()}
    out["units_of_value_per_death"] = {
        "value": round(units_of_value_per_death(), 2),
        "source": "moral_weight / mortality_share_of_benefits x excluded_effects x leveraging x funging",
    }
    return out
