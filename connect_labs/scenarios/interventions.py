"""Interventions — what a place's numbers mean in cases and money.

This closes the question the whole system was built to answer: *if KMC costs $60
a case, how much could be absorbed in high-mortality areas across Africa?*

**A cost is a unit price and a unit of measure.** You cannot do the arithmetic
until both are fixed, and which unit applies is a property of the intervention,
not of the data: KMC is priced per newborn, a bednet per child, a water point
per household, a treatment per case of disease. So the basis is chosen, and the
named interventions below are presets for a basis and a starting price — not a
closed list.

An intervention is a small declaration, not code:

  * ``cases`` — the measure that counts who is eligible. Always a **count**
    already in the registry, never a formula invented here, so the arithmetic
    behind an eligible-case figure is the same arithmetic shown in the table and
    documented in the methodology.
  * ``unit_cost_usd`` — a default, always overridable per scenario. Unit costs
    move with procurement, geography and year, and pretending otherwise is how a
    plausible number becomes a quoted one.
  * ``targets`` — the indicator this intervention is normally selected on, so
    the UI can offer a sensible pairing.

The deliberate restraint: **no intervention defines its own denominator.** KMC
does not get a bespoke "births x low-birthweight rate" expression buried in this
file. If a case count needs a new quantity, that quantity becomes a measure with
a loader and provenance like everything else, and the intervention points at it.
Otherwise the one number a funder actually asks about would be the one number
with no traceable derivation.

That restraint had a visible cost for a while: KMC's real denominator is
low-birthweight or preterm newborns, DHS's birth-weight indicators are too thin
subnationally to support it, and so KMC counted *all* births and said so rather
than silently applying a global 15% and calling it measured — an overstatement
of eligible babies of roughly sevenfold.

It was paid the way the restraint says it should be. ``births_lbw`` is now a
measure with its own loader and provenance: births x the UNICEF-WHO
low-birthweight rate, derived in ``sources/derive.py`` and loaded by the ``lbw``
stage. KMC points at it through the ``lbw_birth`` basis. Nothing here
multiplies anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.impact import engine
from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.bases import UnitBasis, measure_for  # noqa: F401  (re-exported)


def model_price(model: str) -> float:
    """A model's own price per person, so a preset and the model it follows cannot disagree."""
    return float(engine.defaults(model)["cost"])


@dataclass(frozen=True)
class Intervention:
    slug: str
    label: str
    #: What one unit of cost buys.
    basis: UnitBasis
    #: Default unit cost in USD. A starting point for a scenario, not a fact. Where ``model`` is set it is that
    #: model's own price per person (``model_price``), never typed here a second time.
    unit_cost_usd: float
    #: The indicator this is usually selected on.
    targets: str
    description: str = ""
    caveat: str = ""
    #: The cost-effectiveness model this preset follows, if there is one (``impact/data/<model>.json``).
    model: str | None = None

    @property
    def unit_noun(self) -> str:
        return self.basis.noun

    def cases_measure(self, indicator: str | None = None) -> str | None:
        return measure_for(self.basis, indicator or self.targets)


INTERVENTIONS: dict[str, Intervention] = {}


def register(i: Intervention) -> Intervention:
    if i.slug in INTERVENTIONS:
        raise ValueError(f"duplicate intervention {i.slug!r}")
    measures.get(i.targets)
    m = i.cases_measure()
    if m is None:
        raise ValueError(f"{i.slug}: basis {i.basis.value} has no count for {i.targets}")
    if measures.get(m).is_rate:
        raise ValueError(f"{i.slug}: {m} is a rate, not a count")
    INTERVENTIONS[i.slug] = i
    return i


def get(slug: str) -> Intervention:
    try:
        return INTERVENTIONS[slug]
    except KeyError:
        raise KeyError(f"unknown intervention {slug!r}; known: {sorted(INTERVENTIONS)}") from None


def all_interventions() -> list[Intervention]:
    return list(INTERVENTIONS.values())


register(
    Intervention(
        slug="kmc",
        label="Kangaroo Mother Care",
        basis=UnitBasis.LBW_BIRTH,
        unit_cost_usd=model_price("kc"),
        model="kc",
        targets="u5mr",
        description=(
            "Skin-to-skin care for low-birthweight and preterm newborns, " "targeted where newborn survival is worst."
        ),
        caveat=(
            "Priced per low-birthweight birth: births x the UNICEF-WHO national "
            "low-birthweight rate (under 2,500 g, latest year 2020). That counts term "
            "babies born small for gestational age and most preterm babies, but NOT "
            "preterm babies of 2,500 g or more, who are also KMC-eligible, so it "
            "somewhat understates the preterm half. The rate is national, so every "
            "region carries its country's figure. Sixteen countries publish no national "
            "estimate, Nigeria and Ethiopia among them, and carry their UN subregion's "
            "aggregate instead; regional_proxy_units says how much of a total rests on "
            "those."
        ),
    )
)

register(
    Intervention(
        slug="ors",
        label="Oral rehydration salts",
        basis=UnitBasis.DISEASE_CASE,
        unit_cost_usd=model_price("chc"),
        model="chc",
        targets="diarrhoea_prevalence",
        description=(
            "ORS for under-5s with diarrhoea not currently receiving it — the "
            "one basis here that is measured rather than approximated."
        ),
        caveat=(
            "A point-prevalence count on a two-week recall: children sick at any "
            "given moment, not an annual episode total. Annual demand is several "
            "times higher. Priced at the Child Health Campaign model's cost per child per "
            "visit, which covers ORS, zinc and the vitamin A capsule."
        ),
    )
)

register(
    Intervention(
        slug="ors_annual",
        label="Oral rehydration salts (a year of episodes)",
        basis=UnitBasis.CASE_YEAR,
        unit_cost_usd=model_price("chc"),
        model="chc",
        # Targets coverage, not prevalence, unlike its fortnight sibling. The
        # annual figure answers "how much ORS does a year of untreated episodes
        # need", which is a coverage question -- and a preset that agrees with
        # the indicator already on screen does not throw away the threshold the
        # reader set to get there.
        targets="ors_coverage",
        description=(
            "ORS for every untreated under-5 diarrhoea episode across a year, "
            "rather than the two-week snapshot a survey measures. This is the "
            "basis a year of supply is procured against."
        ),
        caveat=(
            "Annualised as 365 / (recall window + mean episode duration), which "
            "for diarrhoea is 365 / (14 + 4.3) = 19.9 fortnights a year. That "
            "conversion turns Liberia's 15.7% two-week prevalence into 3.13 "
            "episodes per child-year, inside the 2.7-3.3 range Fischer Walker "
            "et al. report for sub-Saharan Africa -- which is the check on it. "
            "It assumes the survey window is representative of the whole year; "
            "diarrhoea is seasonal, so the annual total is sound while any "
            "single month is not. Priced at the Child Health Campaign model's cost per "
            "child per visit, which covers ORS, zinc and the vitamin A capsule."
        ),
    )
)

register(
    Intervention(
        slug="measles_vaccination",
        label="Measles vaccination",
        basis=UnitBasis.DISEASE_CASE,
        unit_cost_usd=1.80,
        targets="measles_vaccination",
        description="Reaching children who have not received a measles-containing vaccine.",
        caveat=(
            "Counts the unvaccinated birth cohort, so it is a one-off catch-up "
            "figure rather than a recurring annual cost."
        ),
    )
)

register(
    Intervention(
        slug="vitamin_a",
        label="Vitamin A supplementation",
        basis=UnitBasis.DISEASE_CASE,
        unit_cost_usd=1.10,
        targets="vitamin_a_coverage",
        description="Twice-yearly vitamin A for children 6-59 months not currently reached.",
        caveat="Unit cost covers one dose; the schedule is two per year.",
    )
)

register(
    Intervention(
        slug="itn",
        label="Insecticide-treated nets",
        basis=UnitBasis.DISEASE_CASE,
        unit_cost_usd=3.50,
        targets="itn_use_children",
        description="Nets for under-5s not currently sleeping under one.",
        caveat=(
            "Counts children, but nets are distributed per sleeping space — "
            "roughly one per two people — so this overstates net volume. A "
            "household basis is often the better fit for net campaigns."
        ),
    )
)

register(
    Intervention(
        slug="household_water",
        label="Household water connection",
        basis=UnitBasis.HOUSEHOLD,
        unit_cost_usd=45.0,
        targets="improved_water",
        description=(
            "Priced per household rather than per person — the unit most "
            "infrastructure is actually costed and delivered in."
        ),
        caveat=(
            "Households are derived (population / mean household size), not "
            "counted: no source counts them subnationally across Africa."
        ),
    )
)


def cost(case_count: float, unit_cost: float) -> float:
    """Absorbable spend. Trivial by design — the work is choosing the basis."""
    return case_count * unit_cost
