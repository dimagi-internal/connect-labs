"""Interventions — what a place's numbers mean in cases and money.

This closes the question the whole system was built to answer: *if KMC costs $60
a case, how much could be absorbed in high-mortality areas across Africa?*

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

That restraint has a visible cost. KMC's real denominator is low-birthweight or
preterm newborns, and DHS's birth-weight indicators are too thin subnationally
to support it — so KMC currently counts *all* births and says so, rather than
silently applying a global 15% and calling it measured. See ``caveat``.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.indicators import measures


@dataclass(frozen=True)
class Intervention:
    slug: str
    label: str
    #: Registry measure giving the eligible-case count for an area.
    cases: str
    #: Default unit cost in USD. A starting point for a scenario, not a fact.
    unit_cost_usd: float
    #: The indicator this is usually targeted on.
    targets: str
    description: str = ""
    caveat: str = ""
    #: Words for one unit, e.g. "newborn" / "child". Used in copy.
    unit_noun: str = "case"


INTERVENTIONS: dict[str, Intervention] = {}


def register(i: Intervention) -> Intervention:
    if i.slug in INTERVENTIONS:
        raise ValueError(f"duplicate intervention {i.slug!r}")
    # Both references must exist, so a typo fails at import rather than
    # silently costing zero cases.
    measures.get(i.cases)
    measures.get(i.targets)
    if measures.get(i.cases).is_rate:
        raise ValueError(f"{i.slug}: cases must be a count, not a rate")
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
        cases="births",
        unit_cost_usd=60.0,
        targets="u5mr",
        unit_noun="newborn",
        description=(
            "Skin-to-skin care for low-birthweight and preterm newborns, " "targeted where newborn survival is worst."
        ),
        caveat=(
            "Counts ALL births, not the low-birthweight subset KMC actually "
            "serves — roughly a seventh of them. DHS birth-weight data is too "
            "thin subnationally to carry a real denominator, so this figure is "
            "an upper bound on eligible newborns, not an estimate of them."
        ),
    )
)

register(
    Intervention(
        slug="ors",
        label="Oral rehydration salts",
        cases="ors_gap_children",
        unit_cost_usd=2.50,
        targets="diarrhoea_prevalence",
        unit_noun="child",
        description=(
            "ORS for under-5s with diarrhoea who are not currently receiving "
            "it. The one intervention here whose eligible count is measured "
            "rather than approximated."
        ),
        caveat=(
            "A point-prevalence count on a two-week recall, so it is children "
            "sick at any given moment — not an annual episode total. Annual "
            "demand is several times higher."
        ),
    )
)

register(
    Intervention(
        slug="measles_vaccination",
        label="Measles vaccination",
        cases="measles_vaccination_gap",
        unit_cost_usd=1.80,
        targets="measles_vaccination",
        unit_noun="child",
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
        cases="vitamin_a_coverage_gap",
        unit_cost_usd=1.10,
        targets="vitamin_a_coverage",
        unit_noun="child",
        description="Twice-yearly vitamin A for children 6-59 months not currently reached.",
        caveat="Unit cost covers one dose; the schedule is two per year.",
    )
)

register(
    Intervention(
        slug="itn",
        label="Insecticide-treated nets",
        cases="itn_use_children_gap",
        unit_cost_usd=3.50,
        targets="malaria_prevalence",
        unit_noun="child",
        description="Nets for under-5s not currently sleeping under one.",
        caveat=(
            "Counts children, but nets are distributed per sleeping space — "
            "roughly one net per two people — so this overstates net volume."
        ),
    )
)


def cost(intervention: Intervention, case_count: float, unit_cost: float | None = None) -> float:
    """Absorbable spend for a given number of cases."""
    return case_count * (intervention.unit_cost_usd if unit_cost is None else unit_cost)
