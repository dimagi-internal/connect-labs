"""The measure registry — what each indicator *is*, and how it aggregates.

This is the file that keeps continental rollups honest. Two kinds of number live
in ``IndicatorValue`` and they behave differently:

  * **Counts** (population, births) sum up the boundary hierarchy.
  * **Rates** (under-5 mortality, fertility) must never be summed. They take a
    weighted mean, and the weight differs per measure — weighting U5MR by total
    population instead of by births would bias a continental figure toward
    places with few children. So each rate declares its own weight.

Rates also **inherit downward**: a country's U5MR is the best available estimate
for a region that has no survey of its own. Counts never inherit — a country's
population is emphatically not its region's population. That asymmetry is why
``downscale`` is a per-measure property rather than a global policy.

Adding an indicator means adding an entry here plus a loader in ``sources/``.
No aggregation code changes: ``resolve.py`` reads this registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    COUNT = "count"
    RATE = "rate"


class Agg(str, Enum):
    SUM = "sum"
    WEIGHTED_MEAN = "weighted_mean"


@dataclass(frozen=True)
class Measure:
    """One indicator's identity and aggregation semantics."""

    code: str
    label: str
    kind: Kind
    unit: str
    agg: Agg
    # For rates: the COUNT measure used as the weight when aggregating. Required
    # for every rate; meaningless (and rejected) for counts.
    weight_by: str | None = None
    # Rates inherit from a coarser ancestor when a boundary has no value of its
    # own. Counts must not — see module docstring.
    downscale: bool = False
    description: str = ""
    #: Threshold slider range and starting point, in this measure's own units.
    #: Carrying 80 across from a per-1,000 mortality rate to a percentage
    #: indicator selects almost nothing and reads as missing data, so each
    #: targetable measure states its own scale.
    threshold_min: float = 0
    threshold_max: float = 100
    threshold_default: float = 50
    #: For a coverage measure: the count it applies to. Lets the unreached
    #: population be derived generically — ``denominator x (1 - coverage)`` —
    #: instead of hand-writing a gap per indicator.
    coverage_of: str | None = None

    @property
    def is_rate(self) -> bool:
        return self.kind is Kind.RATE


MEASURES: dict[str, Measure] = {}


def register(m: Measure) -> Measure:
    if m.code in MEASURES:
        raise ValueError(f"duplicate measure {m.code!r}")
    # Structural guarantees, enforced at import so a bad entry can't ship.
    if m.is_rate:
        if m.agg is not Agg.WEIGHTED_MEAN:
            raise ValueError(f"{m.code}: rates must aggregate by weighted mean, not {m.agg.value}")
        if not m.weight_by:
            raise ValueError(f"{m.code}: rates must declare weight_by")
    else:
        if m.agg is not Agg.SUM:
            raise ValueError(f"{m.code}: counts must aggregate by sum, not {m.agg.value}")
        if m.weight_by:
            raise ValueError(f"{m.code}: counts must not declare weight_by")
        if m.downscale:
            raise ValueError(f"{m.code}: counts must not inherit downward")
    MEASURES[m.code] = m
    return m


def get(code: str) -> Measure:
    try:
        return MEASURES[code]
    except KeyError:
        raise KeyError(f"unknown measure {code!r}; known: {sorted(MEASURES)}") from None


def validate_registry() -> None:
    """Every rate's weight must exist and be a count. Called by tests and checks."""
    for m in MEASURES.values():
        if not m.is_rate:
            continue
        w = MEASURES.get(m.weight_by)
        if w is None:
            raise ValueError(f"{m.code}: weight_by={m.weight_by!r} is not a registered measure")
        if w.is_rate:
            raise ValueError(f"{m.code}: weight_by={m.weight_by!r} is a rate; weights must be counts")


# --------------------------------------------------------------------------
# Counts — denominators. These sum, and never inherit.
# --------------------------------------------------------------------------

register(
    Measure(
        code="pop_total",
        label="Total population",
        kind=Kind.COUNT,
        unit="people",
        agg=Agg.SUM,
        description="All ages, both sexes.",
    )
)

register(
    Measure(
        code="pop_u1",
        label="Population under 1",
        kind=Kind.COUNT,
        unit="people",
        agg=Agg.SUM,
        description=(
            "Infants aged 0–1. Approximately one birth cohort, less infant "
            "deaths — the basis of the primary births estimate."
        ),
    )
)

register(
    Measure(
        code="pop_u5",
        label="Population under 5",
        kind=Kind.COUNT,
        unit="people",
        agg=Agg.SUM,
        description="Children aged 0–5. The denominator for most child-health interventions.",
    )
)

register(
    Measure(
        code="pop_f_15_49",
        label="Women of childbearing age",
        kind=Kind.COUNT,
        unit="women",
        agg=Agg.SUM,
        description="Females aged 15–49. Denominator for the fertility-based births cross-check.",
    )
)

register(
    Measure(
        code="births",
        label="Annual births",
        kind=Kind.COUNT,
        unit="births/year",
        agg=Agg.SUM,
        description="Estimated live births per year. Always derived — see sources/derive.py.",
    )
)

register(
    Measure(
        code="births_fertility_check",
        label="Annual births (fertility cross-check)",
        kind=Kind.COUNT,
        unit="births/year",
        agg=Agg.SUM,
        description=(
            "Independent births estimate from women of childbearing age x TFR. "
            "Never the headline number; exists so the primary estimate can be "
            "checked against a separate measurement."
        ),
    )
)


# --------------------------------------------------------------------------
# Rates. These take a weighted mean and inherit downward.
# --------------------------------------------------------------------------

register(
    Measure(
        code="u5mr",
        threshold_min=10,
        threshold_max=200,
        threshold_default=80,
        label="Under-5 mortality rate",
        kind=Kind.RATE,
        unit="per 1,000 live births",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="births",
        downscale=True,
        description=(
            "Deaths before age 5 per 1,000 live births. Weighted by births, not "
            "population: a mortality rate is a property of a birth cohort."
        ),
    )
)

register(
    Measure(
        code="imr",
        label="Infant mortality rate",
        kind=Kind.RATE,
        unit="per 1,000 live births",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="births",
        downscale=True,
        description=(
            "Deaths before age 1 per 1,000 live births. Used to recover births " "from the under-1 population."
        ),
    )
)

register(
    Measure(
        code="nmr",
        threshold_min=5,
        threshold_max=80,
        threshold_default=30,
        label="Neonatal mortality rate",
        kind=Kind.RATE,
        unit="per 1,000 live births",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="births",
        downscale=True,
        description="Deaths in the first 28 days per 1,000 live births.",
    )
)

register(
    Measure(
        code="expected_deaths",
        label="Expected under-5 deaths",
        kind=Kind.COUNT,
        unit="deaths/year",
        agg=Agg.SUM,
        description=(
            "Annual under-5 deaths implied by this area's mortality rate and "
            "birth cohort. The quantity an intervention actually acts on — a "
            "high rate over few children is fewer deaths than a moderate rate "
            "over many."
        ),
    )
)

# --------------------------------------------------------------------------
# Child health. Proportions, so they weight by the population they describe and
# inherit downward like any other rate.
# --------------------------------------------------------------------------

register(
    Measure(
        code="diarrhoea_prevalence",
        threshold_min=2,
        threshold_max=40,
        threshold_default=15,
        label="Under-5 diarrhoea prevalence",
        kind=Kind.RATE,
        unit="% of under-5s, two-week recall",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_u5",
        downscale=True,
        description=(
            "Share of children under five reported to have had diarrhoea in the "
            "two weeks before the survey. A point prevalence, not an annual "
            "episode count."
        ),
    )
)

register(
    Measure(
        code="ors_coverage",
        coverage_of="pop_u5",
        threshold_min=10,
        threshold_max=90,
        threshold_default=50,
        label="ORS treatment coverage",
        kind=Kind.RATE,
        unit="% of under-5s with diarrhoea",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_u5",
        downscale=True,
        description=(
            "Share of children with diarrhoea who received oral rehydration "
            "solution. The complement is the gap an ORS programme addresses."
        ),
    )
)

register(
    Measure(
        code="diarrhoea_untreated",
        label="Diarrhoea receiving no ORS, RHF or fluids",
        kind=Kind.RATE,
        unit="% of under-5s with diarrhoea",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_u5",
        downscale=True,
        description=(
            "DHS's own untreated share. Kept beside the ORS-coverage complement "
            "because the two are not quite the same question — a child may get "
            "increased fluids without ORS."
        ),
    )
)

register(
    Measure(
        code="ors_gap_children",
        label="Children with untreated diarrhoea",
        kind=Kind.COUNT,
        unit="children",
        agg=Agg.SUM,
        description=(
            "Under-5s with diarrhoea not receiving ORS, at any given moment. "
            "The denominator an ORS deployment acts on. A point-prevalence "
            "count — not annualised, because that needs an episode-frequency "
            "assumption the survey does not supply."
        ),
    )
)

register(
    Measure(
        code="exclusive_breastfeeding",
        threshold_min=10,
        threshold_max=90,
        threshold_default=50,
        label="Exclusive breastfeeding under 6 months",
        kind=Kind.RATE,
        unit="% of infants under 6 months",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="births",
        downscale=True,
        description=(
            "Share of infants under six months exclusively breastfed. Weighted "
            "by births, the closest available proxy for the infant cohort it "
            "describes."
        ),
    )
)

register(
    Measure(
        code="tfr",
        label="Total fertility rate",
        kind=Kind.RATE,
        unit="births per woman",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_f_15_49",
        downscale=True,
        description="Lifetime births per woman. Weighted by women of childbearing age.",
    )
)

# --------------------------------------------------------------------------
# Wider child-health and household indicators. All proportions, so each weights
# by the population it describes and inherits downward like any other rate.
# A `coverage_of` entry means an unreached count is derived automatically.
# --------------------------------------------------------------------------


def _coverage(code, label, weight, denominator, unit, desc, lo=10, hi=95, default=50):
    return register(
        Measure(
            code=code,
            label=label,
            kind=Kind.RATE,
            unit=unit,
            agg=Agg.WEIGHTED_MEAN,
            weight_by=weight,
            downscale=True,
            description=desc,
            coverage_of=denominator,
            threshold_min=lo,
            threshold_max=hi,
            threshold_default=default,
        )
    )


def _prevalence(code, label, weight, unit, desc, lo=1, hi=60, default=20):
    return register(
        Measure(
            code=code,
            label=label,
            kind=Kind.RATE,
            unit=unit,
            agg=Agg.WEIGHTED_MEAN,
            weight_by=weight,
            downscale=True,
            description=desc,
            threshold_min=lo,
            threshold_max=hi,
            threshold_default=default,
        )
    )


_coverage(
    "vitamin_a_coverage",
    "Vitamin A supplementation",
    "pop_u5",
    "pop_u5",
    "% of children 6-59 months",
    "Children who received a vitamin A supplement in the last six months.",
)

_coverage(
    "itn_use_children",
    "ITN use, under-5s",
    "pop_u5",
    "pop_u5",
    "% of children under 5",
    "Children who slept under an insecticide-treated net the previous night.",
)

_coverage(
    "measles_vaccination",
    "Measles vaccination",
    "births",
    "births",
    "% of children 12-23 months",
    "Children who received a measles-containing vaccine.",
)

_coverage(
    "dpt3_vaccination",
    "DPT3 vaccination",
    "births",
    "births",
    "% of children 12-23 months",
    "Children who received the third dose of DPT.",
)

_coverage(
    "full_immunisation",
    "Fully vaccinated",
    "births",
    "births",
    "% of children 12-23 months",
    "Children who received all eight basic antigens.",
)

_coverage(
    "skilled_birth_attendance",
    "Skilled birth attendance",
    "births",
    "births",
    "% of live births",
    "Births assisted by a skilled provider.",
)

_coverage(
    "anc4",
    "Antenatal care, 4+ visits",
    "births",
    "births",
    "% of pregnancies",
    "Pregnancies with four or more antenatal visits.",
)

_coverage(
    "zinc_coverage",
    "Zinc for diarrhoea",
    "pop_u5",
    "pop_u5",
    "% of under-5s with diarrhoea",
    "Children with diarrhoea who received zinc supplements.",
    lo=1,
    hi=80,
    default=30,
)

_coverage(
    "ari_antibiotics",
    "Antibiotics for ARI",
    "pop_u5",
    "pop_u5",
    "% of under-5s with ARI symptoms",
    "Children with acute respiratory symptoms who received antibiotics.",
)

_coverage(
    "improved_water",
    "Improved drinking water",
    "pop_total",
    "pop_total",
    "% of population",
    "Population using an improved drinking-water source.",
)

_coverage(
    "improved_sanitation",
    "Improved sanitation",
    "pop_total",
    "pop_total",
    "% of population",
    "Population with an improved sanitation facility.",
)

_coverage(
    "malaria_treatment",
    "Antimalarial for fever",
    "pop_u5",
    "pop_u5",
    "% of under-5s with fever",
    "Children with fever who took an antimalarial drug.",
)

register(
    Measure(
        code="mean_household_size",
        label="Mean household size",
        kind=Kind.RATE,
        unit="people per household",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Average usual members per household. A ratio, which is what a "
            "household survey is designed to measure — the population it is "
            "divided into comes from WorldPop and national statistics, not "
            "from DHS."
        ),
    )
)

register(
    Measure(
        code="households",
        label="Households",
        kind=Kind.COUNT,
        unit="households",
        agg=Agg.SUM,
        description=(
            "Estimated households: total population divided by mean household "
            "size. Derived, because no source counts households subnationally "
            "across Africa."
        ),
    )
)

_prevalence(
    "malaria_prevalence",
    "Malaria prevalence",
    "pop_u5",
    "% of children",
    "Children carrying detectable malaria parasites. Two sources answer this "
    "and they do not use the same age band: DHS measures 6-59 months by rapid "
    "diagnostic test, MAP models 2-10 years (PfPR2-10) on a 5 km grid. The unit "
    "stays deliberately vague because pretending they are one definition would "
    "be the dishonest option -- source_ref on every row names which one "
    "produced it, and the sanity checks compare them where both exist.",
    lo=1,
    hi=70,
    default=20,
)

_prevalence(
    "stunting",
    "Stunting, under-5s",
    "pop_u5",
    "% of children under 5",
    "Children more than two standard deviations below median height-for-age.",
    lo=5,
    hi=60,
    default=30,
)

_prevalence(
    "wasting",
    "Wasting, under-5s",
    "pop_u5",
    "% of children under 5",
    "Children more than two standard deviations below median weight-for-height.",
    lo=1,
    hi=30,
    default=10,
)

_prevalence(
    "ari_prevalence",
    "ARI symptoms, under-5s",
    "pop_u5",
    "% of children under 5",
    "Children with symptoms of acute respiratory infection in the last two weeks.",
    lo=1,
    hi=30,
    default=8,
)

#: Rates a user can sensibly threshold on to pick places. Counts are outcomes
#: of a selection, not criteria for one, so they are excluded.
# --------------------------------------------------------------------------
# Malaria, from the Malaria Atlas Project's modelled surfaces.
#
# These are a different kind of number from everything above. A DHS figure is a
# measurement at a sample of points, inherited outward to the region that
# contains it. A MAP figure is a geostatistical surface evaluated on a 5 km grid
# for every year to 2024, so it has a value for every boundary at every level
# without inheriting anything — and it carries counts, which no survey does.
#
# The counts are why this matters for costing. Almost every indicator we hold is
# a rate, and a rate cannot answer "how many cases would we be treating"; the
# per-case cost basis has to be refused. Malaria is now the exception.
# --------------------------------------------------------------------------

register(
    Measure(
        code="malaria_cases",
        label="Malaria cases (P. falciparum)",
        kind=Kind.COUNT,
        unit="clinical cases per year",
        agg=Agg.SUM,
        description=(
            "Clinical Plasmodium falciparum episodes in a year, from MAP's "
            "modelled incidence surface. A count, so it sums up the hierarchy "
            "exactly and can carry a per-case cost."
        ),
    )
)

register(
    Measure(
        code="malaria_deaths",
        label="Malaria deaths (P. falciparum)",
        kind=Kind.COUNT,
        unit="deaths per year",
        agg=Agg.SUM,
        description=(
            "Deaths attributed to Plasmodium falciparum in a year, from MAP's " "modelled mortality surface."
        ),
    )
)

register(
    Measure(
        code="malaria_incidence",
        label="Malaria incidence",
        kind=Kind.RATE,
        unit="cases per 1,000 people per year",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Clinical P. falciparum episodes per 1,000 people per year. MAP "
            "publishes this per person; it is stored per 1,000 so it reads on "
            "the same scale as the mortality rates beside it."
        ),
        threshold_min=10,
        threshold_max=800,
        threshold_default=250,
    )
)

_coverage(
    "itn_use",
    "ITN use, all ages",
    "pop_total",
    "pop_total",
    "% of population",
    "People who slept under an insecticide-treated net the previous night. "
    "Population-wide, and modelled continuously across Africa — distinct from "
    "the under-5 figure a household survey measures.",
)

_coverage(
    "itn_access",
    "ITN access",
    "pop_total",
    "pop_total",
    "% of population",
    "People living in a household with enough insecticide-treated nets for "
    "every two members. The gap against ITN use is the behavioural half of the "
    "problem, and the two are worth reading together.",
)

_coverage(
    "irs_coverage",
    "Indoor residual spraying",
    "pop_total",
    "pop_total",
    "% of population",
    "People in dwellings sprayed with a residual insecticide in the last year.",
    lo=0,
    hi=60,
    default=10,
)

_coverage(
    "antimalarial_effective",
    "Effective antimalarial treatment",
    "pop_total",
    "malaria_cases",
    "% of cases",
    "Clinical cases that receive an effective antimalarial. Its denominator is "
    "a case count rather than a population, so the unreached figure it yields "
    "is untreated cases — the one number a treatment programme is sized on.",
)


# --------------------------------------------------------------------------
# Physical access to care, from MAP's accessibility surfaces crossed with
# WorldPop's population grid.
#
# The reason both rasters are needed is the reason this was not loaded earlier:
# a district's *average* travel time is close to meaningless, because an area
# mean over a large unit is dominated by land nobody lives on. What a programme
# argues from is the number of people beyond a threshold, and that cannot be
# computed from the travel surface alone.
# --------------------------------------------------------------------------

register(
    Measure(
        code="travel_time_healthcare",
        label="Walking time to healthcare",
        kind=Kind.RATE,
        unit="minutes on foot",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Minutes to reach the nearest health facility on foot, averaged "
            "over the people rather than over the land. Weiss et al.'s 2020 "
            "friction surface, evaluated where WorldPop puts the population."
        ),
        threshold_min=10,
        threshold_max=240,
        threshold_default=60,
    )
)

register(
    Measure(
        code="share_beyond_2h",
        label="Beyond two hours' walk",
        kind=Kind.RATE,
        unit="% of population",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Share of the population living more than two hours' walk from a "
            "health facility. Two hours is the threshold the access literature "
            "settled on, and it is the one a community-health programme is "
            "usually justified against."
        ),
        threshold_min=5,
        threshold_max=90,
        threshold_default=25,
    )
)

register(
    Measure(
        code="pop_beyond_2h",
        label="People beyond two hours' walk",
        kind=Kind.COUNT,
        unit="people",
        agg=Agg.SUM,
        description=(
            "Population in cells more than two hours' walk from a health "
            "facility. A count, so it sums exactly and can carry a per-person "
            "cost — the quantity a deployment is sized on."
        ),
    )
)


# Every coverage measure gets a matching unreached count, registered here so the
# rollup treats it as the summable quantity it is. This runs last, after every
# coverage measure exists — a gap registered before its denominator would be a
# gap in something undefined.
for _m in [m for m in list(MEASURES.values()) if m.coverage_of]:
    # The gap is counted in whatever the denominator counts. Most denominators
    # are populations, so most gaps are people; the effective-treatment gap is
    # denominated in cases, and calling those people would be wrong in the one
    # place a reader is most likely to multiply by a unit cost.
    _denominator = MEASURES[_m.coverage_of]
    register(
        Measure(
            code=f"{_m.code}_gap",
            label=f"Unreached: {_m.label.lower()}",
            kind=Kind.COUNT,
            unit=_denominator.unit,
            agg=Agg.SUM,
            description=(f"Not covered by {_m.label.lower()} — " f"{_m.coverage_of} x (1 - coverage)."),
        )
    )


TARGETABLE = (
    "u5mr",
    "nmr",
    "diarrhoea_prevalence",
    "ors_coverage",
    "malaria_prevalence",
    "malaria_treatment",
    "malaria_incidence",
    "travel_time_healthcare",
    "share_beyond_2h",
    "itn_use",
    "itn_access",
    "irs_coverage",
    "antimalarial_effective",
    "stunting",
    "wasting",
    "ari_prevalence",
    "exclusive_breastfeeding",
    "vitamin_a_coverage",
    "itn_use_children",
    "measles_vaccination",
    "dpt3_vaccination",
    "full_immunisation",
    "skilled_birth_attendance",
    "anc4",
    "zinc_coverage",
    "ari_antibiotics",
    "improved_water",
    "improved_sanitation",
)


def targetable() -> list[Measure]:
    return [MEASURES[c] for c in TARGETABLE if c in MEASURES]


#: Measures where a LOW value is the problem. Thresholding "above" a coverage
#: figure would select the places already doing well, which is the opposite of
#: targeting.
#: Derived from the registry: any measure that describes coverage of something
#: is worse when low. Kept as a computed set so adding a coverage measure cannot
#: forget to invert its selection.
LOWER_IS_WORSE = frozenset({"exclusive_breastfeeding"} | {c for c, m in MEASURES.items() if m.coverage_of is not None})


def percent_equivalent(code: str, value: float) -> float | None:
    """The same threshold as a percentage — when that is genuinely a different number.

    Under-5 mortality is quoted per 1,000 live births, so a threshold of 80 is
    8.0% of a birth cohort, and "8% of children die before five" is the sentence
    people actually reason in. Worth showing.

    An indicator already measured in percent has no second reading. Rendering a
    50% sanitation threshold as 5.0% is not a conversion, it is an error — and it
    shipped, which is why every indicator now answers this question for itself
    rather than the surface assuming per-1,000.
    """
    return value / 10.0 if get(code).unit.startswith("per 1,000") else None


def coverage_measures() -> list[Measure]:
    """Measures with a denominator, so an unreached count can be derived."""
    return [m for m in MEASURES.values() if m.coverage_of]


validate_registry()
