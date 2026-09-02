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
from connect_labs.labs.indicators import boundaries as boundary_set
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
    # Includes ADM2: mortality now resolves that deep in eleven countries, and a
    # district with a rate but no births contributes nothing to a burden total.
    qs = boundary_set.owned().filter(admin_level__in=(0, 1, 2))
    if iso_codes:
        qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    return list(qs)


def load(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Compute births for every boundary that has the inputs.

    Prefers the infant-cohort method. Where ``pop_u1`` is unavailable — HDX HAPI
    reports no 0-1 band, so any boundary covered only by HAPI lacks it — falls
    back to the fertility method rather than leaving the place with no births at
    all. Which method was used is recorded in ``method`` and in
    ``extra.method_key``, because the two are not equally good and a reader
    should be able to tell them apart.
    """
    rows: list[Row] = []
    by_cohort = 0
    by_fertility = 0
    missing = 0

    for boundary in _boundaries(iso_codes):
        pop_u1 = resolve("pop_u1", boundary, year)
        imr = resolve("imr", boundary, year)

        survivorship = 1.0 - (imr.value / 1000.0) if imr else 0.0
        if pop_u1 and imr and survivorship > 0:
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
            by_cohort += 1
            continue

        women = resolve("pop_f_15_49", boundary, year)
        tfr = resolve("tfr", boundary, year)
        if women and tfr:
            rows.append(
                Row(
                    indicator="births",
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
                    extra={
                        "method_key": FERTILITY,
                        "women_15_49": women.value,
                        "tfr": tfr.value,
                        "tfr_inherited": tfr.inherited,
                        "note": "no under-1 population available for the cohort method",
                    },
                )
            )
            by_fertility += 1
            continue

        missing += 1

    logger.info(
        "derive: %d births rows (%d by infant cohort, %d by fertility), %d boundaries lacked inputs",
        len(rows),
        by_cohort,
        by_fertility,
        missing,
    )
    return rows


def load_expected_deaths(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Annual under-5 deaths implied by this area's rate and birth cohort.

    The quantity an intervention actually acts on. Targeting on rate alone
    excluded Oromia — the third-largest concentration of under-5 deaths in
    Africa — because its rate is 60 while its cohort is enormous.
    """
    rows: list[Row] = []
    for boundary in _boundaries(iso_codes):
        rate = resolve("u5mr", boundary, year)
        births = resolve("births", boundary, year)
        if not (rate and births):
            continue
        rows.append(
            Row(
                indicator="expected_deaths",
                boundary=boundary,
                year=min(rate.year, births.year),
                value=births.value * rate.value / 1000.0,
                source=Source.DERIVED,
                source_ref=f"u5mr x births ({rate.source} + {births.source})",
                license_code=License.DERIVED,
                method=(
                    f"Derived: expected under-5 deaths = births x u5mr / 1000. "
                    f"Inputs: births {births.value:,.0f} from {births.source_ref or births.source}; "
                    f"u5mr {rate.value:.1f} from {rate.provenance}."
                ),
                extra={
                    "u5mr": rate.value,
                    "births": births.value,
                    "u5mr_source": rate.source,
                    "births_source": births.source,
                },
            )
        )
    logger.info("derive: %d expected-deaths rows", len(rows))
    return rows


def load_ors_gap(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Under-5s with diarrhoea who are not getting ORS.

    The quantity an ORS deployment acts on:

        children = pop_u5 x diarrhoea_prevalence x (1 - ors_coverage)

    Deliberately a point-prevalence count on DHS's two-week recall, not an
    annual figure. Annualising needs an episode-frequency assumption (commonly
    ~3 episodes per child-year) that the survey does not supply, and burying
    that in a headline would make a modelled number look measured.
    """
    rows: list[Row] = []
    for boundary in _boundaries(iso_codes):
        pop = resolve("pop_u5", boundary, year)
        prev = resolve("diarrhoea_prevalence", boundary, year)
        ors = resolve("ors_coverage", boundary, year)
        if not (pop and prev):
            continue

        # No ORS reading means we cannot say what share is unmet; treating that
        # as zero coverage would invent a gap.
        if ors is None:
            continue

        unmet = max(0.0, 1.0 - ors.value / 100.0)
        rows.append(
            Row(
                indicator="ors_gap_children",
                boundary=boundary,
                year=min(pop.year, prev.year, ors.year),
                value=pop.value * (prev.value / 100.0) * unmet,
                source=Source.DERIVED,
                source_ref=f"ORS gap ({prev.source} + {ors.source})",
                license_code=License.DERIVED,
                method=(
                    "Derived: under-5s with diarrhoea and no ORS = pop_u5 x "
                    f"diarrhoea prevalence ({prev.value:.1f}%) x (1 - ORS coverage "
                    f"({ors.value:.1f}%)). Point prevalence on a two-week recall, "
                    "not an annual episode count. Inputs: "
                    f"pop_u5 from {pop.source_ref or pop.source}; "
                    f"prevalence from {prev.provenance}; ORS from {ors.provenance}."
                ),
                extra={
                    "pop_u5": pop.value,
                    "diarrhoea_prevalence": prev.value,
                    "ors_coverage": ors.value,
                    "prevalence_inherited": prev.inherited,
                    "ors_inherited": ors.inherited,
                },
            )
        )
    logger.info("derive: %d ORS-gap rows", len(rows))
    return rows


def load_households(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Households = population / mean household size.

    Worth being explicit about the division of labour, because it is the thing
    most likely to be misread: the **population** comes from WorldPop or a
    national statistics office via HAPI, and only the **ratio** comes from DHS.
    A household survey samples tens of thousands of households — it cannot count
    a population, but estimating an average is precisely what it is for.

    No source counts households subnationally across Africa, so this is derived
    rather than measured, and says so.
    """
    rows: list[Row] = []
    for boundary in _boundaries(iso_codes):
        pop = resolve("pop_total", boundary, year)
        size = resolve("mean_household_size", boundary, year)
        if not (pop and size) or size.value <= 0:
            continue
        rows.append(
            Row(
                indicator="households",
                boundary=boundary,
                year=min(pop.year, size.year),
                value=pop.value / size.value,
                source=Source.DERIVED,
                source_ref=f"population / household size ({pop.source} + {size.source})",
                license_code=License.DERIVED,
                method=(
                    f"Derived: households = total population / mean household size "
                    f"({size.value:.2f}). Population {pop.value:,.0f} from "
                    f"{pop.source_ref or pop.source}; household size from "
                    f"{size.provenance}."
                ),
                extra={
                    "pop_total": pop.value,
                    "mean_household_size": size.value,
                    "size_inherited": size.inherited,
                },
            )
        )
    logger.info("derive: %d household rows", len(rows))
    return rows


def load_coverage_gaps(iso_codes: list[str] | None = None, year: int | None = None) -> list[Row]:
    """Unreached population for every coverage measure, generically.

        unreached = denominator x (1 - coverage)

    Driven entirely by ``Measure.coverage_of``, so adding an indicator to the
    registry brings its gap with it rather than needing a bespoke derivation.
    The ORS gap stays hand-written because it carries a second factor —
    prevalence — that this shape does not express.

    A missing coverage reading yields no row. Treating absent coverage as zero
    would invent an unreached population the size of the whole denominator.
    """
    from connect_labs.labs.indicators import measures as _measures

    rows: list[Row] = []
    boundaries = _boundaries(iso_codes)

    for measure in _measures.coverage_measures():
        denominator = measure.coverage_of
        made = 0
        for boundary in boundaries:
            cov = resolve(measure.code, boundary, year)
            denom = resolve(denominator, boundary, year)
            if not (cov and denom):
                continue
            # A rate measured only among those who had an episode applies only
            # to them. ORS coverage is among children who HAD diarrhoea, so the
            # unreached count is prevalence x population x (1 - coverage), not
            # population x (1 - coverage) — which is the whole country times the
            # untreated share, and reads six times too high for Liberia.
            #
            # No prevalence, no gap. A number that overstates by the inverse of
            # a prevalence we cannot see is worse than the absence of one.
            episode = None
            if measure.conditional_on:
                episode = resolve(measure.conditional_on, boundary, year)
                if episode is None:
                    continue
            unreached = max(0.0, 1.0 - cov.value / 100.0)
            if episode is not None:
                unreached *= episode.value / 100.0
            rows.append(
                Row(
                    indicator=f"{measure.code}_gap",
                    boundary=boundary,
                    year=min(cov.year, denom.year),
                    value=denom.value * unreached,
                    source=Source.DERIVED,
                    source_ref=f"unreached ({cov.source} + {denom.source})",
                    license_code=License.DERIVED,
                    method=(
                        f"Derived: unreached = {denominator} x (1 - {measure.label} "
                        f"({cov.value:.1f}%))"
                        + (
                            f" x {measure.conditional_on} ({episode.value:.1f}%), because the rate is "
                            "measured only among those who had an episode"
                            if episode is not None
                            else ""
                        )
                        + f". Inputs: {denominator} "
                        f"{denom.value:,.0f} from {denom.source_ref or denom.source}; "
                        f"coverage from {cov.provenance}."
                    ),
                    extra={
                        "coverage": cov.value,
                        "denominator": denom.value,
                        "denominator_measure": denominator,
                        "coverage_inherited": cov.inherited,
                    },
                )
            )
            made += 1
        if made:
            logger.info("derive: %d %s_gap rows", made, measure.code)

    return rows


def births_divergence(iso_codes: list[str] | None = None) -> dict[int, float]:
    """How far apart the two births methods are, per boundary.

    Built as a cross-check and then never consulted, which is its own lesson:
    16% of regions disagree by more than a quarter and 3% by more than half. A
    births figure two independent methods cannot agree on should not be summed
    into a headline with the same confidence as one they do.
    """
    from connect_labs.labs.indicators.models import IndicatorValue

    primary = {v.boundary_id: v.value for v in IndicatorValue.objects.filter(indicator="births") if v.value}
    check = {
        v.boundary_id: v.value for v in IndicatorValue.objects.filter(indicator="births_fertility_check") if v.value
    }
    out: dict[int, float] = {}
    for pk, a in primary.items():
        b = check.get(pk)
        if not b:
            continue
        out[pk] = abs(a - b) / max(a, b)
    return out


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
