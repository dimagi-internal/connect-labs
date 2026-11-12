"""The unit a cost is priced in, and the count in the registry that unit resolves to.

A cost is a unit price and a unit of measure (a newborn, a child, a household, a case). Which counts exist
for an indicator is a fact about the baseline, so this lives here; the scenarios component builds the
intervention presets and the sizing arithmetic on top of it.
"""

from __future__ import annotations

from enum import Enum

from connect_labs.labs.indicators import measures


class UnitBasis(str, Enum):
    """What one unit of cost buys.

    Each maps to a count already in the registry, so an eligible-unit figure is
    always the same arithmetic shown in the table. DISEASE_CASE is the one that
    depends on context — a "case" means untreated diarrhoea when targeting
    diarrhoea and an unvaccinated child when targeting measles — so it resolves
    against the indicator in play.

    DISEASE_CASE and CASE_YEAR are the same cases counted over different spans,
    and the difference is not a rounding. A survey asks about the last two
    weeks, so a prevalence-derived case count is **a fortnight's worth**;
    Liberia's 51,429 untreated diarrhoea episodes become 1,210,108 over a year.
    Costing a year of supply against the fortnight figure under-prices it
    twentyfold. Both are offered because both are legitimate questions — how
    many are sick now, and how many treatments a year needs — but the basis has
    to say which, and neither can be the silent default for the other.
    """

    BIRTH = "birth"
    #: A newborn under 2,500 g. What Kangaroo Mother Care is priced per, and
    #: roughly a seventh of births in sub-Saharan Africa.
    LBW_BIRTH = "lbw_birth"
    UNDER_5 = "under_5"
    PERSON = "person"
    HOUSEHOLD = "household"
    DISEASE_CASE = "case"
    CASE_YEAR = "case_year"

    @property
    def label(self) -> str:
        return {
            "birth": "per birth",
            "lbw_birth": "per low-birthweight birth",
            "under_5": "per child under 5",
            "person": "per person",
            "household": "per household",
            "case": "per case (survey recall window)",
            "case_year": "per case (a year of cases)",
        }[self.value]

    @property
    def noun(self) -> str:
        return {
            "birth": "newborn",
            "lbw_birth": "low-birthweight newborn",
            "under_5": "child",
            "person": "person",
            "household": "household",
            "case": "case",
            "case_year": "annual case",
        }[self.value]


#: Fixed bases map straight to a count.
_FIXED: dict[UnitBasis, str] = {
    UnitBasis.BIRTH: "births",
    UnitBasis.LBW_BIRTH: "births_lbw",
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

    if basis is UnitBasis.CASE_YEAR:
        # No annualisation is possible without a recall window and an episode
        # duration on the underlying prevalence. Declining is the right answer:
        # multiplying by an assumed 26 would be an invention wearing the same
        # units as a measurement.
        # Same aliasing as the fortnight case below: the ORS question is
        # asked on either the prevalence or the coverage measure, and both
        # mean the same untreated episodes.
        if indicator in ("diarrhoea_prevalence", "ors_coverage"):
            indicator = "ors_coverage"
        annual = f"{indicator}_gap_annual"
        return annual if annual in measures.MEASURES else None

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
