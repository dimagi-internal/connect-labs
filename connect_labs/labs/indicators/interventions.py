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

That restraint has a visible cost. KMC's real denominator is low-birthweight or
preterm newborns, and DHS's birth-weight indicators are too thin subnationally
to support it — so KMC currently counts *all* births and says so, rather than
silently applying a global 15% and calling it measured. See ``caveat``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from connect_labs.labs.indicators import measures


class UnitBasis(str, Enum):
    """What one unit of cost buys.

    Each maps to a count already in the registry, so an eligible-unit figure is
    always the same arithmetic shown in the table. DISEASE_CASE is the one that
    depends on context — a "case" means untreated diarrhoea when targeting
    diarrhoea and an unvaccinated child when targeting measles — so it resolves
    against the indicator in play.
    """

    BIRTH = "birth"
    UNDER_5 = "under_5"
    PERSON = "person"
    HOUSEHOLD = "household"
    DISEASE_CASE = "case"

    @property
    def label(self) -> str:
        return {
            "birth": "per birth",
            "under_5": "per child under 5",
            "person": "per person",
            "household": "per household",
            "case": "per case",
        }[self.value]

    @property
    def noun(self) -> str:
        return {
            "birth": "newborn",
            "under_5": "child",
            "person": "person",
            "household": "household",
            "case": "case",
        }[self.value]


#: Fixed bases map straight to a count.
_FIXED: dict[UnitBasis, str] = {
    UnitBasis.BIRTH: "births",
    UnitBasis.UNDER_5: "pop_u5",
    UnitBasis.PERSON: "pop_total",
    UnitBasis.HOUSEHOLD: "households",
}


def measure_for(basis: UnitBasis, indicator: str = "u5mr") -> str | None:
    """The count that a basis resolves to for this indicator.

    Returns ``None`` when a case basis has no case count for the indicator —
    a prevalence with no coverage figure cannot say how many cases go untreated,
    and inventing one would be worse than declining.
    """
    if basis in _FIXED:
        return _FIXED[basis]

    # DISEASE_CASE. Prefer a measured untreated count, then the unreached
    # population for a coverage indicator, then expected deaths for mortality.
    if indicator in ("diarrhoea_prevalence", "ors_coverage"):
        return "ors_gap_children"
    gap = f"{indicator}_gap"
    if gap in measures.MEASURES:
        return gap
    if indicator in ("u5mr", "nmr"):
        return "expected_deaths"
    return None


@dataclass(frozen=True)
class Intervention:
    slug: str
    label: str
    #: What one unit of cost buys.
    basis: UnitBasis
    #: Default unit cost in USD. A starting point for a scenario, not a fact.
    unit_cost_usd: float
    #: The indicator this is usually selected on.
    targets: str
    description: str = ""
    caveat: str = ""

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
        basis=UnitBasis.BIRTH,
        unit_cost_usd=60.0,
        targets="u5mr",
        description=(
            "Skin-to-skin care for low-birthweight and preterm newborns, " "targeted where newborn survival is worst."
        ),
        caveat=(
            "Priced per birth, but KMC serves only the low-birthweight subset — "
            "roughly a seventh of them. DHS birth-weight data is too thin "
            "subnationally to carry that denominator, so this is an upper bound "
            "on eligible newborns, not an estimate of them."
        ),
    )
)

register(
    Intervention(
        slug="ors",
        label="Oral rehydration salts",
        basis=UnitBasis.DISEASE_CASE,
        unit_cost_usd=2.50,
        targets="diarrhoea_prevalence",
        description=(
            "ORS for under-5s with diarrhoea not currently receiving it — the "
            "one basis here that is measured rather than approximated."
        ),
        caveat=(
            "A point-prevalence count on a two-week recall: children sick at any "
            "given moment, not an annual episode total. Annual demand is several "
            "times higher."
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
