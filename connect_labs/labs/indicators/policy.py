"""Which sources may answer each indicator, in what order, and why.

This is the file that decides what a number is allowed to be made of.

The system used to hold one ranked list of sources and apply it to every
indicator. Ranking is not the same as restricting, so a source that ranked last
still answered when nothing better existed — and "nothing better" was common.
Asking for under-five mortality "as measured by survey" returned 370 regions of
which 285 carried UN IGME's *national* figure, repeated across regions the
survey never reached. The label promised a measurement and three quarters of the
answer was a model. Nothing was hidden, but nothing stopped it either.

One ordering also cannot be right for twenty-nine indicators. A survey beats a
model for malaria prevalence, because prevalence is something you can go and
measure. A model beats a survey for under-five mortality, because IGME's
small-area estimates reconcile every survey a country has ever run and reach
districts no single survey covered. And for malaria case counts there is no
choice to make, because only one source in the world publishes them.

So the opinion belongs to the indicator, and it is written down here with its
reason:

  * **Membership is eligibility.** A source not on an indicator's list is never
    used for it — not ranked last, not used as a fallback, not substituted when
    the answer would otherwise be empty. An empty answer is a true answer.
  * **Order is preference.** The first eligible source with a value wins.
  * **A method narrows, it does not reorder.** ``Method.source_order`` says what
    *kind* of evidence a caller is asking for; the intersection with this policy
    is taken in *this* file's order, because the indicator knows which of its
    own sources is better and a method does not.

Inheritance survives all of this, and should. A district with no survey of its
own legitimately takes its region's rate — that is what makes a rate a rate, and
``Measure.downscale`` already governs it. What changes is that a district may
only inherit a value that was itself eligible, and every selection now reports
how many of its units did so, because "measured here" and "inherited from the
country" are different claims and a reader is entitled to know the mix.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.indicators.models import Source


@dataclass(frozen=True)
class Eligible:
    """One source an indicator may be answered from, and why it sits here."""

    source: str
    why: str


#: Sources that carry a population count. Ordered once because the reasoning is
#: identical for every one of them and repeating it four times would invite the
#: four copies to drift.
_POPULATION = (
    Eligible(
        Source.WORLDPOP_RASTER,
        "WorldPop's 1 km UN-adjusted grids, read on each boundary here. First "
        "because they are reconciled to UN World Population Prospects, which is "
        "the series a funder checks a proposal against. Tested on two countries "
        "and it wins both: for Nigeria the unadjusted product reads 217.0M "
        "against 206.0M here and a UN figure of 206.1M; for Liberia it reads "
        "4.33M against 4.81M here and a UN figure nearer 5.06M -- wrong in "
        "opposite directions, which is what makes 'whichever is loaded' the "
        "wrong rule. It also answers the boundaries the statistics service "
        "refuses outright.",
    ),
    Eligible(
        Source.WORLDPOP,
        "WorldPop's hosted statistics service, queried per boundary. Second, "
        "not gone: it is what most of the continent was originally loaded from, "
        "it covers boundaries the raster misses, and keeping it eligible means "
        "the two calibrations stay comparable rather than one disappearing.",
    ),
    Eligible(
        Source.HAPI,
        "HDX's humanitarian baseline. Last, and only where neither WorldPop "
        "route reached, because its vintage and its boundary matching are "
        "both looser.",
    ),
)

#: Under-five and infant mortality. The one indicator where a model genuinely
#: beats the measurement, and the reasoning is worth stating in full.
_MORTALITY = (
    Eligible(
        Source.IGME_SUBNATIONAL,
        "IGME's small-area model reconciles every survey a country has run "
        "onto one reference year, and reaches district level in most of the "
        "countries it covers. A single survey cannot do either.",
    ),
    Eligible(
        Source.DHS_CALIBRATED,
        "The survey's regional pattern scaled to the present by the national "
        "trend. A third of the continent's subnational mortality comes from "
        "surveys eight or more years old, and several countries were reading "
        "as high-mortality on twenty-year-old numbers.",
    ),
    Eligible(
        Source.DHS,
        "The survey as published. Kept eligible and kept auditable beside the "
        "adjusted row, so the adjustment can be checked rather than trusted.",
    ),
    Eligible(
        Source.IGME,
        "IGME's national series. Eligible only because a country row IS a "
        "national figure -- at ADM0 it is the right answer rather than a "
        "fallback.",
    ),
)


def _survey(what: str) -> tuple[Eligible, ...]:
    """A household-survey measure, which DHS alone answers subnationally."""
    return (
        Eligible(Source.DHS, f"DHS measures {what} directly in the field. No other source answers it subnationally."),
    )


def _wash(what: str) -> tuple[Eligible, ...]:
    """A WASH measure, where the JMP's harmonised pooling is a real second source.

    Order is an opinion, and this one is: the direct measurement first, the
    pooling second. Both answer the same question to the same JMP definition --
    which is why they can share an indicator at all, and why handwashing does
    NOT (DHS observes whether a station exists; the JMP asks whether it has soap
    and water, and those are different facts wearing the same word).
    """
    return (
        Eligible(
            Source.DHS,
            f"DHS measures {what} directly in the field, on the JMP's own service ladder.",
        ),
        Eligible(
            Source.UNICEF_SDMX,
            "The JMP's subnational pooling of DHS, MICS, MIS and national surveys onto "
            "one definition. Second because it may rest on a survey we cannot name, and "
            "present because it is the only route to countries DHS has never surveyed.",
        ),
    )


def _map(what: str, note: str = "") -> tuple[Eligible, ...]:
    """A Malaria Atlas Project surface."""
    return (
        Eligible(
            Source.MAP,
            f"MAP's 5 km modelled surface, annual to 2024. {note}".strip()
            or f"MAP's 5 km modelled surface, annual to 2024, is the only source publishing {what} subnationally.",
        ),
    )


#: The whole opinion, one entry per indicator. Anything absent is ineligible.
POLICY: dict[str, tuple[Eligible, ...]] = {
    # -- Mortality -------------------------------------------------------
    "u5mr": _MORTALITY,
    "imr": _MORTALITY,
    # Neonatal mortality was the thinnest headline measure here -- 30.7% of
    # ADM1 units -- and UNICEF's subnational child-mortality warehouse is the
    # only second source for it. It sits behind IGME's small-area model for
    # the same reason IGME leads u5mr: the model reconciles every survey a
    # country has run, where this is one estimate per area from one of them.
    # It earns its place at ADM2, where it added 1,096 district estimates
    # across 17 countries and took district coverage from 40% to 71%.
    "nmr": (
        _MORTALITY[0],
        Eligible(
            Source.UNICEF_SDMX,
            "UNICEF's subnational child-mortality warehouse, at district level where "
            "the underlying surveys support it. The only second source for neonatal "
            "mortality, which no other source here publishes below the region.",
        ),
    ),
    # -- Population and its derivations -----------------------------------
    "pop_total": _POPULATION,
    "pop_u1": _POPULATION,
    "pop_u5": _POPULATION,
    "pop_f_15_49": _POPULATION,
    "births": (
        Eligible(Source.DERIVED, "Computed from a measured infant cohort, or from fertility where that is missing."),
    ),
    "births_fertility_check": (
        Eligible(Source.DERIVED, "The fertility-based figure, kept so the cohort-based one can be checked."),
    ),
    "expected_deaths": (Eligible(Source.DERIVED, "Births multiplied by the resolved mortality rate."),),
    "households": (Eligible(Source.DERIVED, "Population divided by mean household size."),),
    "pop_growth_rate": (
        Eligible(Source.WORLDBANK, "The World Bank's national series. No other source here publishes it."),
    ),
    "tfr": (
        Eligible(Source.DHS, "Measured directly by the survey."),
        Eligible(Source.WORLDBANK, "The World Bank's national series, for countries with no survey."),
    ),
    # -- Malaria, from a modelled surface ---------------------------------
    "malaria_prevalence": (
        Eligible(
            Source.DHS,
            "A measured prevalence: children tested with a rapid diagnostic in "
            "the field. Preferred over the model where it exists, even though "
            "the model covers roughly twice as many regions.",
        ),
        Eligible(
            Source.MAP,
            "MAP's PfPR2-10 surface. A different age band -- 2 to 10 years, "
            "against the survey's 6 to 59 months -- so source_ref on the row "
            "says which definition produced the number.",
        ),
    ),
    "malaria_cases": _map("clinical case counts"),
    "malaria_deaths": _map("malaria death counts"),
    "malaria_incidence": _map("incidence"),
    "itn_use": _map("population-wide net use"),
    "itn_access": _map("net access"),
    "irs_coverage": _map("indoor residual spraying"),
    "antimalarial_effective": _map("effective treatment"),
    # -- Physical access to care ------------------------------------------
    "travel_time_healthcare": (
        Eligible(Source.MAP_WORLDPOP, "Weiss et al.'s travel-time surface, weighted by the population in each cell."),
    ),
    "share_beyond_2h": (
        Eligible(Source.MAP_WORLDPOP, "Weiss et al.'s travel-time surface, weighted by the population in each cell."),
    ),
    "pop_beyond_2h": (
        Eligible(Source.MAP_WORLDPOP, "Weiss et al.'s travel-time surface, weighted by the population in each cell."),
    ),
    # -- Settlement --------------------------------------------------------
    "share_rural": (Eligible(Source.GHSL, "DEGURBA, the only rural definition comparable between countries."),),
    "pop_rural": (Eligible(Source.GHSL, "DEGURBA, the only rural definition comparable between countries."),),
    # -- Household survey measures ----------------------------------------
    "diarrhoea_prevalence": _survey("diarrhoea in a two-week recall"),
    "diarrhoea_untreated": (Eligible(Source.DERIVED, "Prevalence multiplied by the untreated share."),),
    "ors_coverage": _survey("oral rehydration treatment"),
    "ors_gap_children": (Eligible(Source.DERIVED, "Under-fives with diarrhoea who received no ORS."),),
    "zinc_coverage": _survey("zinc treatment"),
    "malaria_treatment": _survey("antimalarial treatment for fever"),
    "itn_use_children": _survey("net use among under-fives"),
    "stunting": _survey("height-for-age"),
    "wasting": _survey("weight-for-height"),
    "ari_prevalence": _survey("respiratory symptoms"),
    "fever_prevalence": _survey("fever in the last two weeks"),
    "ari_antibiotics": _survey("antibiotic treatment for those symptoms"),
    "exclusive_breastfeeding": _survey("feeding practice"),
    "vitamin_a_coverage": _survey("supplementation"),
    "measles_vaccination": _survey("vaccination from cards and recall"),
    "dpt3_vaccination": _survey("vaccination from cards and recall"),
    "full_immunisation": _survey("the full schedule from cards and recall"),
    "skilled_birth_attendance": _survey("who attended the birth"),
    "anc4": _survey("antenatal visits"),
    # WASH is the one family with a second source worth having. The JMP pools
    # DHS, MICS, MIS and national surveys onto one definition, which is how it
    # reaches Somalia, Sudan, Comoros, Guinea-Bissau and Tunisia -- countries
    # DHS has never surveyed, where these questions previously returned
    # nothing at all.
    #
    # DHS still leads. It is a direct measurement whose field question we can
    # name; the JMP figure is a harmonised pooling that may rest on a survey
    # we cannot see. Preferring the pooling where the survey exists would
    # trade a known instrument for an unknown one for no gain. Preferring it
    # where the survey does NOT exist is the whole reason it is here.
    "improved_water": _wash("the household's water source"),
    "improved_sanitation": _wash("the household's sanitation facility"),
    "mean_household_size": _survey("household roster size"),
    # --- Tier 1 of docs/targeting-data-acquisition.md ----------------------
    #
    # All twenty are household-survey measures, so DHS alone answers them
    # subnationally and each entry says which question in the field produced
    # it. Naming the field question rather than the indicator is the point:
    # "vaccination from cards and recall" is a different kind of evidence from
    # "haemoglobin measured in the field", and a reader weighing a number
    # needs to know which they are holding.
    # Rainfall seasonality. One source, and it is the only one that is both
    # openly licensed and station-blended at this resolution.
    "rain_annual_mm": (
        Eligible(
            Source.CHIRPS,
            "CHIRPS 0.05 degree monthly precipitation crossed with WorldPop's grid, so the "
            "average describes when it rains where people are rather than over empty land.",
        ),
    ),
    "rain_peak_month": (Eligible(Source.CHIRPS, "The same climatology, read for its maximum."),),
    "rain_wettest_quarter": (Eligible(Source.CHIRPS, "The same climatology, read for its concentration."),),
    # Referral access. Same two rasters as the walking surface, different mode.
    "travel_time_motorized": (
        Eligible(
            Source.MAP_WORLDPOP,
            "Weiss et al.'s motorized travel-time surface crossed with WorldPop's grid. "
            "The only global facility-access surface, and useless without a population "
            "grid to weight it by — an average over land lets empty desert vote.",
        ),
    ),
    "share_beyond_2h_motorized": (
        Eligible(Source.MAP_WORLDPOP, "The same crossing, expressed as a share of people."),
    ),
    "pop_beyond_2h_motorized": (Eligible(Source.MAP_WORLDPOP, "The same crossing, expressed as a count of people."),),
    "zero_dose": _survey("vaccination history from cards and recall, counting children with no doses at all"),
    "fp_unmet_need": _survey("fertility intentions against current contraceptive use"),
    "fp_modern_method": _survey("current contraceptive method"),
    "fp_demand_satisfied": _survey("contraceptive demand and how it is met"),
    "itn_household": _survey("nets present in the household, observed on the roster"),
    "itn_pregnant": _survey("where pregnant women slept the previous night"),
    "iptp3": _survey("doses of SP/Fansidar taken during pregnancy"),
    "careseeking_diarrhoea": _survey("whether advice or treatment was sought for a child's diarrhoea"),
    "careseeking_fever": _survey("whether advice or treatment was sought for a child's fever"),
    "underweight": _survey("weight-for-age"),
    "severe_wasting": _survey("weight-for-height, at the three-SD cut"),
    "child_anaemia": _survey("haemoglobin measured in the field, not reported"),
    "women_anaemia": _survey("haemoglobin measured in the field, not reported"),
    "iron_pregnancy": _survey("days of iron supplementation during pregnancy"),
    "min_meal_frequency": _survey("what and how often a child was fed in the last day"),
    "postnatal_2days": _survey("timing of the mother's first postnatal check"),
    "handwashing": _survey("a handwashing place observed by the interviewer"),
    "water_on_premises": _wash("the household's water source and where it is"),
    "open_defecation": _wash("the household's sanitation facility, or absence of one"),
    "birth_certificate": _survey("whether the child has a birth certificate"),
}

#: Every ``*_gap`` count is arithmetic on a coverage rate and a denominator, and
#: every ``*_gap_annual`` is that carried to a year at the survey's own recall
#: window. Both are derived by definition, so neither needs a hand-written entry.
_DERIVED_GAP = (Eligible(Source.DERIVED, "Denominator multiplied by the uncovered share."),)


def for_indicator(indicator: str) -> tuple[Eligible, ...]:
    """The sources this indicator may be answered from, best first."""
    if indicator in POLICY:
        return POLICY[indicator]
    if indicator.endswith("_gap") or indicator.endswith("_gap_annual"):
        return _DERIVED_GAP
    raise KeyError(
        f"no source policy for {indicator!r}. Every indicator states which sources may answer it "
        "and why -- see policy.POLICY. An indicator without one would silently accept any source."
    )


def sources(indicator: str) -> tuple[str, ...]:
    return tuple(e.source for e in for_indicator(indicator))


def order_for(indicator: str, lens: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Eligible sources in policy order, narrowed by a method's lens.

    The lens filters; it never reorders. A method says what kind of evidence is
    wanted, and the indicator says which of its own sources is better — those
    are different questions and the second is not the method's to answer.
    """
    allowed = sources(indicator)
    if lens is None:
        return allowed
    return tuple(s for s in allowed if s in lens)


def why(indicator: str, source: str) -> str:
    for e in for_indicator(indicator):
        if e.source == source:
            return e.why
    return ""


def validate() -> None:
    """Every targetable measure states an opinion, and every opinion is real.

    Run at import. A measure that shipped without a policy would fall through
    to the KeyError above at query time rather than at deploy time, which is
    the wrong end of the pipeline to find out.
    """
    from connect_labs.labs.indicators import measures

    valid = set(Source.values)
    for indicator, entries in POLICY.items():
        if indicator not in measures.MEASURES:
            raise ValueError(f"policy names {indicator!r}, which is not a registered measure")
        if not entries:
            raise ValueError(
                f"{indicator}: an empty policy makes the indicator unanswerable; delete the entry instead"
            )
        seen: set[str] = set()
        for e in entries:
            if e.source not in valid:
                raise ValueError(f"{indicator}: {e.source!r} is not a Source value")
            if e.source in seen:
                raise ValueError(f"{indicator}: {e.source!r} listed twice")
            if not e.why.strip():
                raise ValueError(f"{indicator}: {e.source!r} has no reason; the reason is the point")
            seen.add(e.source)

    missing = [c for c in measures.TARGETABLE if c not in POLICY]
    if missing:
        raise ValueError(
            f"targetable measures with no source policy: {sorted(missing)}. State which sources may "
            "answer them and why, in policy.POLICY."
        )


validate()
