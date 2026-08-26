"""Derived measures — currently births.

WorldPop publishes no births product through its stats service, so annual births
are computed. Two independent methods are stored side by side, both marked
``source='derived'`` with their formula in ``method``:

**Infant-cohort (primary).** ``births = pop_u1 / (1 - imr/1000)``

The 0–1 age band counts infants alive at the reference date — that is one birth
cohort, minus the infants who died. Dividing the survivorship back out recovers
births. It uses the WorldPop call already required for ``pop_u5``, varies
subnationally without further assumptions, and is a short chain from measured
data to result.

**Fertility (cross-check).** ``births = pop_f_15_49 × TFR / 35``

Total fertility is lifetime births per woman across the 35-year reproductive
span; dividing gives an annual general fertility rate. Cruder — it assumes a
flat age distribution of fertility — but it is derived from an entirely separate
measurement (DHS fertility histories rather than WorldPop's modelled age
structure), so agreement between the two is real corroboration.

Both are kept. Divergence is a data-quality signal, and hiding it behind one
number would throw away the only cross-check available.
"""

from __future__ import annotations

import logging

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.resolve import resolve
from connect_labs.labs.indicators.sources.base import Row

logger = logging.getLogger(__name__)

#: Width in years of the reproductive span 15–49, used to turn TFR into an
#: annual rate.
REPRODUCTIVE_SPAN = 35.0

METHOD_COHORT = (
    "Derived: births = pop_u1 / (1 - imr/1000). The under-1 population is one "
    "birth cohort less infant deaths; dividing out survivorship recovers annual "
    "births. Inputs: pop_u1 from {pop_ref}; imr {imr:.1f}/1000 from {imr_ref}."
)

METHOD_FERTILITY = (
    "Derived: births = women_15_49 x TFR / 35. TFR is lifetime births per woman "
    "over a 35-year reproductive span, so dividing gives an annual rate; assumes "
    "fertility is flat across ages. Inputs: women_15_49 from {pop_ref}; "
    "TFR {tfr:.2f} from {tfr_ref}."
)

#: Marker used in ``extra`` so the two estimates stay distinguishable after
#: they land in the same table under the same source.
COHORT = "infant_cohort"
FERTILITY = "fertility"


def _boundaries(iso_codes: list[str] | None) -> list[AdminBoundary]:
    qs = AdminBoundary.objects.filter(admin_level__in=(0, 1))
    if iso_codes:
        qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    return list(qs)


def load(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Compute births for every boundary that has the inputs."""
    rows: list[Row] = []
    missing = 0

    for boundary in _boundaries(iso_codes):
        pop_u1 = resolve("pop_u1", boundary, year)
        imr = resolve("imr", boundary, year)

        if pop_u1 and imr:
            survivorship = 1.0 - (imr.value / 1000.0)
            if survivorship > 0:
                rows.append(
                    Row(
                        indicator="births",
                        boundary=boundary,
                        year=pop_u1.year,
                        value=pop_u1.value / survivorship,
                        source=Source.DERIVED,
                        source_ref=f"infant cohort ({pop_u1.source} + {imr.source})",
                        license_code=License.DERIVED,
                        method=METHOD_COHORT.format(
                            pop_ref=pop_u1.source_ref or pop_u1.source,
                            imr=imr.value,
                            imr_ref=imr.provenance,
                        ),
                        extra={
                            "method_key": COHORT,
                            "pop_u1": pop_u1.value,
                            "imr": imr.value,
                            "imr_inherited": imr.inherited,
                        },
                    )
                )
            else:
                missing += 1
        else:
            missing += 1

    logger.info("derive: %d births rows written, %d boundaries lacked inputs", len(rows), missing)
    return rows


def load_fertility_crosscheck(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """The independent fertility-based estimate, for comparison.

    Stored under its own indicator code rather than as a second ``births`` row:
    both estimates are ``source='derived'``, so they would collide on the
    natural key. Keeping them as distinct measures means the comparison is a
    join rather than a special case, and nothing downstream can mistake the
    cross-check for the headline number.
    """
    rows: list[Row] = []
    for boundary in _boundaries(iso_codes):
        women = resolve("pop_f_15_49", boundary, year)
        tfr = resolve("tfr", boundary, year)
        if not (women and tfr):
            continue
        rows.append(
            Row(
                indicator="births_fertility_check",
                boundary=boundary,
                year=women.year,
                value=women.value * tfr.value / REPRODUCTIVE_SPAN,
                source=Source.DERIVED,
                source_ref=f"fertility ({women.source} + {tfr.source})",
                license_code=License.DERIVED,
                method=METHOD_FERTILITY.format(
                    pop_ref=women.source_ref or women.source,
                    tfr=tfr.value,
                    tfr_ref=tfr.provenance,
                ),
                extra={"method_key": FERTILITY, "women_15_49": women.value, "tfr": tfr.value},
            )
        )
    return rows
