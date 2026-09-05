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
    #: For a prevalence measured over a survey recall window: the length of that
    #: window in days, and the mean duration of an episode. Together they convert
    #: a point-in-time prevalence into an annual incidence, which is the quantity
    #: a programme is actually sized on. DHS asks about the last two weeks; a
    #: figure derived from that answer is a fortnight's worth of illness and
    #: reads, to anyone who does not check, like a year's.
    recall_days: int | None = None
    episode_days: float | None = None
    #: For a coverage rate measured only among those who had an episode — ORS
    #: among children who *had diarrhoea* — the prevalence measure that says how
    #: many of them there are. Without it the unreached count multiplies the
    #: whole population by the untreated share and overstates by the inverse of
    #: prevalence: Liberia's ORS gap read 295,899 where the truth is 47,035, a
    #: factor of six. Where the rate is unconditional (sanitation, vaccination)
    #: this stays None and the generic derivation is already right.
    conditional_on: str | None = None

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
    """Structural checks over the whole registry. Called by tests and at import.

    Whole-registry rather than per-registration because these are references
    between measures, and a measure may legitimately name one that is declared
    further down the file.
    """
    for m in MEASURES.values():
        if m.conditional_on:
            episode = MEASURES.get(m.conditional_on)
            if episode is None:
                raise ValueError(
                    f"{m.code}: conditional_on={m.conditional_on!r} is not a registered measure. "
                    "A rate measured only among those who had an episode needs the prevalence "
                    "that says how many they are, or its unreached count multiplies the whole "
                    "population by the untreated share."
                )
            if not episode.is_rate:
                raise ValueError(f"{m.code}: conditional_on={m.conditional_on!r} must be a prevalence, not a count")
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
        recall_days=14,  # DHS asks about the last two weeks
        episode_days=4.3,  # Fischer Walker et al. 2012, systematic review of episode duration in LMICs
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
        conditional_on="diarrhoea_prevalence",
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


def _coverage(code, label, weight, denominator, unit, desc, lo=10, hi=95, default=50, conditional_on=None):
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
            conditional_on=conditional_on,
            threshold_min=lo,
            threshold_max=hi,
            threshold_default=default,
        )
    )


def _prevalence(code, label, weight, unit, desc, lo=1, hi=60, default=20, recall_days=None, episode_days=None):
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
            recall_days=recall_days,
            episode_days=episode_days,
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
    conditional_on="diarrhoea_prevalence",
)

_coverage(
    "ari_antibiotics",
    "Antibiotics for ARI",
    "pop_u5",
    "pop_u5",
    "% of under-5s with ARI symptoms",
    "Children with acute respiratory symptoms who received antibiotics.",
    conditional_on="ari_prevalence",
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
    conditional_on="fever_prevalence",
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
    "fever_prevalence",
    "Fever, under-5s",
    "pop_u5",
    "% of children under 5",
    "Children with a fever in the last two weeks. Not a targeting criterion in "
    "its own right — it is the denominator that makes the antimalarial "
    "treatment gap mean children who could have been treated rather than every "
    "child in the country.",
    lo=1,
    hi=60,
    default=20,
    recall_days=14,  # DHS asks about the last two weeks
    episode_days=4.0,  # typical febrile episode; less well characterised than diarrhoea
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
    recall_days=14,  # DHS asks about the last two weeks
    episode_days=7.0,  # typical acute respiratory episode; less well characterised than diarrhoea
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


register(
    Measure(
        code="share_rural",
        label="Rural population share (DEGURBA)",
        kind=Kind.RATE,
        unit="% of population",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Share of the population living in cells DEGURBA classes as rural. "
            "The definition dominates this number: DEGURBA calls 17% of "
            "Rwanda's villages rural against a national figure near 72%, "
            "because Rwanda's density clears its urban threshold nearly "
            "everywhere. Comparable between countries, which is a different "
            "property from being right for any one of them -- always quote it "
            "with the definition that produced it."
        ),
        threshold_min=5,
        threshold_max=95,
        threshold_default=50,
    )
)

register(
    Measure(
        code="pop_rural",
        label="Rural population (DEGURBA)",
        kind=Kind.COUNT,
        unit="people",
        agg=Agg.SUM,
        description=(
            "Population in cells DEGURBA classes as rural. A count, so it sums "
            "exactly and can carry a per-person cost."
        ),
    )
)


register(
    Measure(
        code="pop_growth_rate",
        label="Population growth",
        kind=Kind.RATE,
        unit="% per year",
        agg=Agg.WEIGHTED_MEAN,
        weight_by="pop_total",
        downscale=True,
        description=(
            "Annual population growth, national. Not a targeting criterion — it "
            "exists so a count measured in one year can be carried to the year a "
            "programme actually runs, which is usually several years later and "
            "never the year the data was collected."
        ),
        threshold_min=0,
        threshold_max=6,
        threshold_default=2,
    )
)


def annualisation_factor(prevalence_code: str) -> float | None:
    """Turn a recall-window prevalence into an annual incidence multiplier.

    A survey asks whether a child had diarrhoea in the last two weeks. The share
    that says yes is a fortnight's worth of illness, and a count derived from it
    is a fortnight's worth of cases -- which reads, to anyone who does not check,
    like a year's. Liberia's untreated-episode figure is 60,671 on that basis
    and 1.2 million a year.

    The standard conversion is that a prevalence observed over a recall window
    reflects incidence times the period in which an episode would be caught: the
    window itself plus the episode's own duration, since an episode beginning
    shortly before the window is still reported inside it.

        annual incidence = prevalence x 365 / (recall_days + episode_days)

    For diarrhoea on DHS's fortnight and a 4.3-day episode that is x19.9.
    Applied to Liberia's own 15.7% it gives 3.13 episodes per child-year, which
    sits between the sub-Saharan African average of 3.3 and the global
    low-and-middle-income figure of 2.7 -- a check on the conversion rather than
    an assumption inside it.

    Returns None where a measure does not declare a window, because the
    conversion would then be a guess.
    """
    m = MEASURES.get(prevalence_code)
    if m is None or m.recall_days is None or m.episode_days is None:
        return None
    return 365.0 / (m.recall_days + m.episode_days)


# --- Tier 1 of docs/targeting-data-acquisition.md --------------------------
#
# Twenty measures DHS has always published subnationally and this system never
# asked for. They are grouped by the question they let someone ask, because
# that is what was missing: not more coverage of what we could already target,
# but whole intervention categories -- family planning, immunisation equity,
# anaemia -- that had no measure at all.
#
# Where a measure's true denominator is not one we hold, the closest one is
# used and the description says so, following KMC's precedent: count all
# births and admit it, rather than silently apply a global ratio and call the
# result measured.

# Immunisation equity. A BURDEN, not a coverage measure: zero-dose is already
# the share who received nothing, so its complement is the vaccinated and a
# "gap" would point the wrong way. Selected above a threshold, like stunting.
_prevalence(
    "zero_dose",
    "Zero-dose children",
    "pop_u5",
    "% of children 12-23 months",
    (
        "Children who have received no vaccinations at all. The quantity "
        "immunisation funding is allocated against, and not implied by DPT3 or "
        "full-immunisation coverage -- a child can be far behind schedule "
        "without being zero-dose. Measured among children 12-23 months; "
        "weighted by under-five population, which is the closest denominator "
        "this system holds."
    ),
    lo=1,
    hi=60,
    default=10,
)

# Family planning. Unmet need is a burden; use and demand-satisfied are
# coverage. All three sit on pop_f_15_49, which is complete at ADM1.
_prevalence(
    "fp_unmet_need",
    "Unmet need for family planning",
    "pop_f_15_49",
    "% of married women 15-49",
    (
        "Married women who want to delay or stop childbearing but are using no "
        "contraception. The classic targeting quantity for family planning, "
        "which this system previously could not target at all."
    ),
    lo=5,
    hi=60,
    default=20,
)

_coverage(
    "fp_modern_method",
    "Modern contraceptive use",
    "pop_f_15_49",
    "pop_f_15_49",
    "% of married women 15-49",
    (
        "Married women currently using a modern method (mCPR). The unreached "
        "count is women not using one, which includes those with no unmet need "
        "-- read it alongside fp_unmet_need rather than instead of it."
    ),
)

_coverage(
    "fp_demand_satisfied",
    "FP demand satisfied by modern methods",
    "pop_f_15_49",
    "pop_f_15_49",
    "% of women with demand for family planning",
    (
        "SDG indicator 3.7.1, and what a family-planning programme is actually "
        "judged on: of the women who want contraception, the share getting a "
        "modern method. Its denominator is demand rather than all women, so "
        "the unreached count computed against pop_f_15_49 is an upper bound."
    ),
)

# Malaria prevention, by the unit each is delivered in. A net campaign is
# procured per household and this is the measure the ITN intervention's own
# caveat has been pointing at with nothing to point to.
_coverage(
    "itn_household",
    "Household ITN ownership",
    "pop_total",
    "households",
    "% of households",
    (
        "Households owning at least one insecticide-treated net. Priced and "
        "distributed per household, which is the unit a net campaign is "
        "actually costed in -- unlike itn_use_children, which counts children."
    ),
)

_coverage(
    "itn_pregnant",
    "ITN use, pregnant women",
    "pop_f_15_49",
    "births",
    "% of pregnant women",
    (
        "Pregnant women who slept under an ITN last night. The second priority "
        "group after under-fives. Counted against annual births, the closest "
        "available proxy for women pregnant in a year."
    ),
)

_coverage(
    "iptp3",
    "IPTp, 3+ doses",
    "pop_f_15_49",
    "births",
    "% of pregnant women",
    (
        "Three or more doses of intermittent preventive treatment in pregnancy. "
        "A distinct commodity from nets with its own delivery channel -- "
        "antenatal care -- so a place can be well covered for one and not the "
        "other. Counted against annual births."
    ),
)

# Care-seeking. The distinction between a family that never reached care and
# one that reached it and was sent away untreated. Those need opposite
# interventions -- demand generation against commodity supply -- and without
# these two the system cannot tell them apart in any answer it gives.
_coverage(
    "careseeking_diarrhoea",
    "Care sought for diarrhoea",
    "pop_u5",
    "pop_u5",
    "% of under-5s with diarrhoea",
    (
        "Children with diarrhoea for whom advice or treatment was sought. Read "
        "against ors_coverage this separates a supply problem from a demand "
        "one: where care-seeking is high and ORS is low, the commodity is "
        "missing at the point of contact."
    ),
    conditional_on="diarrhoea_prevalence",
)

_coverage(
    "careseeking_fever",
    "Care sought for fever",
    "pop_u5",
    "pop_u5",
    "% of under-5s with fever",
    (
        "Children with fever for whom advice or treatment was sought -- the "
        "malaria pathway equivalent, and the ceiling on what any "
        "facility-delivered antimalarial can reach."
    ),
    conditional_on="fever_prevalence",
)

# Nutrition. Underweight completes the standard triad with stunting and
# wasting; severe wasting is the SAM denominator, for which plain wasting
# overstates the caseload a therapeutic-feeding programme would face.
_prevalence(
    "underweight",
    "Underweight, under-5s",
    "pop_u5",
    "% of children under 5",
    "Children more than two standard deviations below median weight-for-age.",
    lo=2,
    hi=50,
    default=20,
)

_prevalence(
    "severe_wasting",
    "Severe wasting, under-5s",
    "pop_u5",
    "% of children under 5",
    (
        "Children more than three standard deviations below median "
        "weight-for-height. The denominator a severe-acute-malnutrition "
        "programme is sized on; plain wasting overstates it several times over."
    ),
    lo=0,
    hi=15,
    default=3,
)

_prevalence(
    "child_anaemia",
    "Anaemia, under-5s",
    "pop_u5",
    "% of children 6-59 months",
    (
        "Children with any anaemia, measured by haemoglobin rather than "
        "reported. The target of iron and micronutrient supplementation."
    ),
    lo=10,
    hi=90,
    default=50,
)

_prevalence(
    "women_anaemia",
    "Anaemia, women 15-49",
    "pop_f_15_49",
    "% of women 15-49",
    "Women with any anaemia. The maternal half of the supplementation case.",
    lo=5,
    hi=70,
    default=35,
)

_coverage(
    "iron_pregnancy",
    "Iron supplementation in pregnancy",
    "pop_f_15_49",
    "births",
    "% of pregnant women",
    (
        "Women who took iron supplements for 90 or more days during pregnancy "
        "-- the coverage measure matching the anaemia burden above."
    ),
)

_coverage(
    "min_meal_frequency",
    "Minimum meal frequency, 6-23 months",
    "pop_u5",
    "pop_u5",
    "% of children 6-23 months",
    (
        "Infant and young-child feeding: children fed the minimum number of "
        "times for their age. The behavioural target behind stunting. Measured "
        "among 6-23 month olds, roughly a third of the under-five population, "
        "so an unreached count against pop_u5 is an upper bound."
    ),
)

# Newborn survival -- the coverage companion to the KMC intervention already
# registered against mortality.
_coverage(
    "postnatal_2days",
    "Postnatal check within two days",
    "pop_f_15_49",
    "births",
    "% of live births",
    (
        "Mothers whose first postnatal checkup came within two days of birth. "
        "Most newborn deaths fall inside that window, so this is when a visit "
        "can still change the outcome."
    ),
)

# WASH, at the resolution a programme acts on.
_coverage(
    "handwashing",
    "Handwashing station observed",
    "pop_total",
    "households",
    "% of households",
    (
        "Households with a fixed handwashing place observed by the "
        "interviewer -- observed rather than reported, which is why it reads "
        "far lower than self-reported hygiene."
    ),
)

_coverage(
    "water_on_premises",
    "Improved water on the premises",
    "pop_total",
    "pop_total",
    "% of population",
    (
        "An improved source located on the premises. 'Improved' alone hides "
        "the distance problem, and distance is what a household connection "
        "changes."
    ),
)

_prevalence(
    "open_defecation",
    "Open defecation",
    "pop_total",
    "% of population",
    (
        "Population with no sanitation facility of any kind. A burden measure, "
        "so it selects the worst places directly rather than through the "
        "complement of a coverage rate."
    ),
    lo=1,
    hi=80,
    default=20,
)

# Civil registration -- the gateway to every other entitlement, and a
# plausible delivery use case in its own right.
_coverage(
    "birth_certificate",
    "Birth certificate held",
    "pop_u5",
    "pop_u5",
    "% of children under 5",
    "Children under five who have a birth certificate.",
)


# A conditional coverage measure also gets an ANNUAL unreached count, because
# the fortnight figure is not what a programme is sized on and is the one most
# likely to be quoted as though it were. Both are kept: the fortnight figure is
# what the survey directly supports, the annual one is what a commodity order
# is built from, and having them side by side makes the difference impossible
# to miss.
for _m in [m for m in list(MEASURES.values()) if m.coverage_of and m.conditional_on]:
    _episode = MEASURES[_m.conditional_on]
    _factor = annualisation_factor(_m.conditional_on)
    if _factor is None:
        continue
    register(
        Measure(
            code=f"{_m.code}_gap_annual",
            label=f"Unreached per year: {_m.label.lower()}",
            kind=Kind.COUNT,
            unit="episodes per year",
            agg=Agg.SUM,
            description=(
                f"Episodes a year that go without {_m.label.lower()}. The fortnight figure "
                f"({_m.code}_gap) carried to a year at x{_factor:.1f} -- 365 days over a "
                f"{_episode.recall_days}-day recall window plus a {_episode.episode_days}-day "
                "episode. This is the quantity a commodity order is built from; the fortnight "
                "figure is what the survey directly supports."
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
    "share_rural",
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
    # Tier 1 of docs/targeting-data-acquisition.md. Ordered by the question
    # each one lets someone ask, not alphabetically, because the point of the
    # list is that it is a menu.
    "zero_dose",
    "fp_unmet_need",
    "fp_modern_method",
    "fp_demand_satisfied",
    "itn_household",
    "itn_pregnant",
    "iptp3",
    "careseeking_diarrhoea",
    "careseeking_fever",
    "underweight",
    "severe_wasting",
    "child_anaemia",
    "women_anaemia",
    "iron_pregnancy",
    "min_meal_frequency",
    "postnatal_2days",
    "handwashing",
    "water_on_premises",
    "open_defecation",
    "birth_certificate",
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
