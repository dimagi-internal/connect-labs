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

validate_registry()
