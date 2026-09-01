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
        Source.WORLDPOP,
        "WorldPop's hosted statistics service, queried per boundary. First "
        "because it is what most of the continent was loaded from and moving "
        "the continent under a reader mid-argument is worse than a 5% "
        "inconsistency at the margin.",
    ),
    Eligible(
        Source.WORLDPOP_RASTER,
        "The same product family read from the 1 km UN-adjusted grid here. "
        "Answers the boundaries the service refuses outright, and reads about "
        "5% below it because this grid is reconciled to UN World Population "
        "Prospects and the age-sex product it serves is not.",
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
    "nmr": (_MORTALITY[0],),
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
    "ari_antibiotics": _survey("antibiotic treatment for those symptoms"),
    "exclusive_breastfeeding": _survey("feeding practice"),
    "vitamin_a_coverage": _survey("supplementation"),
    "measles_vaccination": _survey("vaccination from cards and recall"),
    "dpt3_vaccination": _survey("vaccination from cards and recall"),
    "full_immunisation": _survey("the full schedule from cards and recall"),
    "skilled_birth_attendance": _survey("who attended the birth"),
    "anc4": _survey("antenatal visits"),
    "improved_water": _survey("the household's water source"),
    "improved_sanitation": _survey("the household's sanitation facility"),
    "mean_household_size": _survey("household roster size"),
}

#: Every ``*_gap`` count is arithmetic on a coverage rate and a denominator.
_DERIVED_GAP = (Eligible(Source.DERIVED, "Denominator multiplied by the uncovered share."),)


def for_indicator(indicator: str) -> tuple[Eligible, ...]:
    """The sources this indicator may be answered from, best first."""
    if indicator in POLICY:
        return POLICY[indicator]
    if indicator.endswith("_gap"):
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
