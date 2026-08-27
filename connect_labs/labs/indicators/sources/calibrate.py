"""Re-level old subnational surveys to the present.

The problem this exists to fix, found by asking why Uganda's figure was dated
2016: a third of Africa's subnational mortality comes from surveys eight or more
years old, and child mortality has fallen steeply almost everywhere since. Taken
raw, those surveys put countries in the high-mortality bracket on the strength of
numbers that stopped being true a decade ago.

  Eritrea   regions reading 111-154, from a 2002 survey.  National rate today: 34.
  Eswatini  106, from 2006.                                National rate today: 45.
  Sudan     108-122, from 1990.                            National rate today: 62.

Nine countries were selected at an 80-per-1,000 threshold whose *current*
national rate is already below it. For a system whose whole job is deciding
where to send people, that is not a cosmetic error.

**The adjustment.** Keep the survey's subnational *pattern* — which regions are
worse, and by how much relative to each other — and re-level it to the present:

    factor   = igme_national(latest) / igme_national(survey_year)
    adjusted = survey_region_value * factor

Both ends of the ratio come from IGME's own annual series, so the factor is a
pure within-source trend and carries no difference of method between IGME and
DHS. Applying it to a recent survey is close to a no-op (Nigeria 2024 gives
~1.0), which is why it runs uniformly rather than only on old surveys.

**The assumption, stated plainly.** That relative differences between regions
have persisted while the level fell. That is an assumption, and it is weaker the
older the survey. It is nonetheless far better than the alternative it replaces,
which assumed nothing had changed at all. Every adjusted value records its
factor and its endpoints, the raw survey row is kept beside it, and the UI
reports a survey's age so a reader can discount accordingly.

Where IGME has no estimate for the survey year — surveys older than the series —
no factor can be formed and the raw value stands, flagged by its age.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from connect_labs.labs.indicators.models import IndicatorValue, License, Source
from connect_labs.labs.indicators.sources.base import Row

logger = logging.getLogger(__name__)

#: Measures worth re-levelling: rates that trend strongly over time and are
#: measured subnationally only by survey.
CALIBRATED = ("u5mr", "imr")

METHOD = (
    "{survey}, re-levelled from {from_year} to {to_year}. The survey's "
    "subnational pattern is kept and its level scaled by x{factor:.3f}, the "
    "ratio of UN IGME's national estimate for {to_year} ({to_value:.1f}) to its "
    "estimate for {from_year} ({from_value:.1f}). Assumes relative differences "
    "between regions persisted while the level fell. Raw survey value: "
    "{raw:.1f} per 1,000."
)

#: A factor beyond these bounds means the series or the survey year is wrong,
#: not that mortality changed that much. Refuse rather than publish it.
MIN_FACTOR = 0.2
MAX_FACTOR = 2.0


def _national_series(measure: str) -> dict[str, dict[int, float]]:
    """IGME national values, by ISO then year."""
    series: dict[str, dict[int, float]] = defaultdict(dict)
    for v in IndicatorValue.objects.filter(indicator=measure, source=Source.IGME):
        series[v.iso_code][v.year] = v.value
    return series


def load(measure: str = "u5mr", iso_codes: list[str] | None = None) -> list[Row]:
    """Build re-levelled rows from every raw subnational survey value."""
    if measure not in CALIBRATED:
        return []

    series = _national_series(measure)
    if not series:
        logger.warning("calibrate: no IGME series for %s; run the mortality stage first", measure)
        return []

    survey_rows = IndicatorValue.objects.filter(indicator=measure, source=Source.DHS)
    if iso_codes:
        survey_rows = survey_rows.filter(iso_code__in=[c.upper() for c in iso_codes])

    rows: list[Row] = []
    skipped_no_series = 0
    skipped_wild = 0
    adjustments: list[float] = []

    for v in survey_rows.select_related("boundary"):
        national = series.get(v.iso_code)
        if not national:
            skipped_no_series += 1
            continue

        from_value = national.get(v.year)
        if from_value is None or from_value <= 0:
            # Survey predates the IGME series; leave the raw value to stand.
            skipped_no_series += 1
            continue

        to_year = max(national)
        to_value = national[to_year]
        factor = to_value / from_value

        if not (MIN_FACTOR <= factor <= MAX_FACTOR):
            logger.warning(
                "calibrate: refusing factor %.2f for %s %s (%s -> %s)",
                factor,
                v.iso_code,
                measure,
                v.year,
                to_year,
            )
            skipped_wild += 1
            continue

        adjustments.append(factor)
        rows.append(
            Row(
                indicator=measure,
                boundary=v.boundary,
                # Stamped with the year it now describes, not the survey year —
                # that is what makes it the preferred row for "latest".
                year=to_year,
                value=v.value * factor,
                ci_low=v.ci_low * factor if v.ci_low is not None else None,
                ci_high=v.ci_high * factor if v.ci_high is not None else None,
                source=Source.DHS_CALIBRATED,
                source_ref=f"{v.source_ref} re-levelled to {to_year}",
                source_url=v.source_url,
                license_code=License.DERIVED,
                method=METHOD.format(
                    survey=v.source_ref or "DHS survey",
                    from_year=v.year,
                    to_year=to_year,
                    factor=factor,
                    from_value=from_value,
                    to_value=to_value,
                    raw=v.value,
                ),
                extra={
                    "raw_value": v.value,
                    "raw_year": v.year,
                    "factor": round(factor, 4),
                    "survey_age_years": to_year - v.year,
                    "igme_from": from_value,
                    "igme_to": to_value,
                },
            )
        )

    if adjustments:
        biggest = min(adjustments)
        logger.info(
            "calibrate %s: %d rows re-levelled (largest reduction x%.2f); "
            "%d had no IGME series, %d refused as implausible",
            measure,
            len(rows),
            biggest,
            skipped_no_series,
            skipped_wild,
        )
    return rows
