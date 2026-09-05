"""How a number is arrived at — the method registry.

``measures.py`` says what an indicator *is*. This says how a value for it was
*produced*, which is a separate question and the one that varies by country.

The distinction matters because availability is uneven and always will be. Every
African country has a modelled national estimate; only about forty have a
subnational survey; the survey is sometimes twenty years old. A targeting system
that silently picks whatever it can find gives the user no way to ask "compare
these countries on a like-for-like basis" — or to know when it could not.

So a method is declared, not inferred:

  * ``resolution`` — the level it speaks at. A national method describes a whole
    country; a subnational one distinguishes regions within it.
  * ``source_order`` — which stored sources can satisfy it, best first. A method
    is *available* for a country when one of them has data at its resolution.
  * ``caveat`` — the thing a reader should hold in mind when reading its output.

Adding a method is an entry here plus, if it needs one, a loader. Nothing in the
selection or rollup code changes: it asks the registry which sources to prefer
and at what level to work.

Methods deliberately do **not** fall back to each other. If you ask for a
subnational answer and a country cannot give one, that country is reported as
unavailable rather than quietly answered at national level — see
``availability()``. Falling back invisibly is how a map ends up comparing a
district in Nigeria against the whole of Chad.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Resolution(str, Enum):
    NATIONAL = "national"
    SUBNATIONAL = "subnational"

    @property
    def admin_levels(self) -> tuple[int, ...]:
        # Subnational spans both levels: IGME models to ADM2 in most countries
        # it covers, surveys rarely go below ADM1. Which one a given country
        # uses is decided per country by the deepest level that has data — see
        # resolve.select_above — so that a district is never set against the
        # region containing it.
        return (0,) if self is Resolution.NATIONAL else (1, 2)


@dataclass(frozen=True)
class Method:
    code: str
    label: str
    resolution: Resolution
    #: Stored sources that can satisfy this method, best first.
    source_order: tuple[str, ...]
    description: str = ""
    caveat: str = ""
    #: Shown first in the picker for its resolution.
    default: bool = False

    @property
    def is_national(self) -> bool:
        return self.resolution is Resolution.NATIONAL


METHODS: dict[str, Method] = {}


def register(m: Method) -> Method:
    if m.code in METHODS:
        raise ValueError(f"duplicate method {m.code!r}")
    if not m.source_order:
        raise ValueError(f"{m.code}: a method must name at least one source")
    METHODS[m.code] = m
    return m


def get(code: str) -> Method:
    try:
        return METHODS[code]
    except KeyError:
        raise KeyError(f"unknown method {code!r}; known: {sorted(METHODS)}") from None


def for_resolution(resolution: Resolution) -> list[Method]:
    return [m for m in METHODS.values() if m.resolution is resolution]


def default_for(resolution: Resolution) -> Method:
    candidates = for_resolution(resolution)
    for m in candidates:
        if m.default:
            return m
    if not candidates:
        raise ValueError(f"no method registered for {resolution.value}")
    return candidates[0]


# ---------------------------------------------------------------------------
# National
# ---------------------------------------------------------------------------

register(
    Method(
        code="national_igme",
        label="National estimate (UN IGME)",
        resolution=Resolution.NATIONAL,
        source_order=("igme",),
        default=True,
        description=(
            "The UN Inter-agency Group's modelled national series, reconciled "
            "across surveys and vital registration. Available for every country "
            "and current to the latest year."
        ),
        caveat=(
            "One number per country. Says nothing about where within a country "
            "the burden falls, which is usually where it varies most."
        ),
    )
)


register(
    Method(
        code="national_surface",
        label="National estimate from gridded surfaces",
        resolution=Resolution.NATIONAL,
        source_order=("map", "map_worldpop", "ghsl", "chirps"),
        description=(
            "Gridded surfaces summed or averaged to the whole country — MAP's "
            "malaria layers, the Weiss et al. travel-time surface crossed with "
            "WorldPop, and DEGURBA. The only national method that carries "
            "counts rather than rates, so it is the one that can answer 'how "
            "many' instead of 'how bad'."
        ),
        caveat=(
            "Models, not measurements: fitted to survey points and covariates "
            "rather than counted. MAP's national malaria totals sit near WHO's "
            "but do not match them, and that difference is a real disagreement "
            "between modelling groups rather than rounding."
        ),
    )
)


# ---------------------------------------------------------------------------
# Subnational
# ---------------------------------------------------------------------------

register(
    Method(
        code="subnational_igme",
        label="Modelled small-area estimate (UN IGME)",
        resolution=Resolution.SUBNATIONAL,
        source_order=("igme_subnational",),
        default=True,
        description=(
            "IGME's own small-area model, on a common reference year rather "
            "than each survey's year. Reaches district level (ADM2) in most of "
            "the countries it covers — Angola has 146 districts here against the "
            "18 provinces its last survey reports."
        ),
        caveat=(
            "Covers 25 African countries, not all of them. A model, not a "
            "measurement: it interpolates between surveys rather than reading "
            "one."
        ),
    )
)

register(
    Method(
        code="subnational_relevelled",
        label="Survey pattern, re-levelled to today",
        resolution=Resolution.SUBNATIONAL,
        # Falls through to the raw survey where no factor could be formed —
        # both are the same measurement, one adjusted, so this is not the
        # cross-method fallback the module docstring rules out.
        # unicef_sdmx last: the JMP's harmonised pooling is a survey figure
        # too, and excluding it here would make the countries it alone reaches
        # -- Somalia, Sudan, Comoros -- return nothing under the default
        # method while their data sat in the table. Order still prefers the
        # named instrument; the policy decides eligibility per indicator, so
        # this widens nothing that policy has not already allowed.
        source_order=("dhs_calibrated", "dhs", "unicef_sdmx"),
        description=(
            "The survey's regional pattern scaled to the present by the national "
            "trend, so an old survey still says which regions are worse without "
            "asserting a level that stopped being true."
        ),
        caveat=(
            "Assumes relative differences between regions persisted while the "
            "level moved. Weaker the older the survey."
        ),
    )
)

register(
    Method(
        code="subnational_survey",
        label="Survey as measured",
        resolution=Resolution.SUBNATIONAL,
        source_order=("dhs", "unicef_sdmx"),
        description=(
            "The survey's own regional figures, unadjusted. What the fieldwork "
            "actually found, at the time it was carried out."
        ),
        caveat=(
            "Carries the survey's date. A third of African countries were last "
            "surveyed eight or more years ago, and mortality has moved a long "
            "way since."
        ),
    )
)

register(
    Method(
        code="subnational_surface",
        label="Modelled surface, read on this unit",
        resolution=Resolution.SUBNATIONAL,
        source_order=("map", "map_worldpop", "ghsl", "chirps"),
        description=(
            "A continuous grid read on each unit's own geometry, so every "
            "boundary at every level gets a value computed for it rather than "
            "inherited from its parent. Counts are summed over the cells; rates "
            "are averaged weighted by the population in each cell, never by "
            "area. Covers malaria (MAP, annual to 2024), physical access to "
            "care on foot and by vehicle (Weiss et al. 2020 x WorldPop), rural "
            "share (DEGURBA) and rainfall seasonality (CHIRPS climatology)."
        ),
        caveat=(
            "Modelled rather than measured: where the underlying observations "
            "are thin the surface is the model's opinion, smoothly interpolated "
            "and therefore looking more certain than it is. A small district's "
            "value is an average over a handful of cells."
        ),
    )
)
