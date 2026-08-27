"""UN IGME loader — national child mortality, via UNICEF's SDMX endpoint.

IGME is the authoritative national series for under-5 and infant mortality: an
annual modelled estimate for every country, reconciled across surveys and vital
registration. It is the *fallback* rather than the primary source because it is
national only — but for countries with no recent DHS, it is the difference
between a country appearing in the analysis and vanishing from it.

Because ``u5mr`` and ``imr`` are registered as inheriting measures, a national
IGME value automatically stands in for any region lacking its own survey
estimate, and ``resolve()`` reports that it did.
"""

from __future__ import annotations

import logging

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row, http_json

logger = logging.getLogger(__name__)

SDMX = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest/data/UNICEF,CME,1.0"

#: IGME's public explorer, deep-linked to the country in question.
PORTAL = "https://childmortality.org/data?refArea={iso}"

#: our measure code → IGME indicator id
INDICATORS = {
    "u5mr": "CME_MRY0T4",
    "imr": "CME_MRY0",
}

METHOD = (
    "UN Inter-agency Group for Child Mortality Estimation (IGME) {year} national "
    "estimate, retrieved via UNICEF SDMX. Modelled series reconciling surveys and "
    "vital registration; national level only."
)

#: Dimension order in the CME dataflow.
_DIMS = ("REF_AREA", "INDICATOR", "SEX", "WEALTH_QUINTILE", "TIME_PERIOD")


def _parse(payload: dict) -> list[tuple[str, int, float]]:
    """Flatten an SDMX-JSON response to (iso3, year, value), totals only."""
    data = payload.get("data") or {}
    datasets = data.get("dataSets") or []
    structure = data.get("structure") or {}
    dims = structure.get("dimensions", {}).get("observation", [])
    if not datasets or not dims:
        return []

    index = {d["id"]: i for i, d in enumerate(dims)}
    values = {d["id"]: [v.get("id") for v in d["values"]] for d in dims}

    out: list[tuple[str, int, float]] = []
    for key, obs in (datasets[0].get("observations") or {}).items():
        parts = key.split(":")
        picked = {dim: values[dim][int(parts[index[dim]])] for dim in _DIMS if dim in index}
        # Only the both-sexes, all-wealth series; the disaggregations are a
        # different question and would double-count if summed.
        if picked.get("SEX") != "_T" or picked.get("WEALTH_QUINTILE") != "_T":
            continue
        raw = obs[0] if obs else None
        if raw in (None, ""):
            continue
        out.append((picked["REF_AREA"], int(picked["TIME_PERIOD"]), float(raw)))
    return out


def load(measure: str = "u5mr", iso_codes: list[str] | None = None) -> list[Row]:
    """Latest national value per country, attached to the ADM0 boundary."""
    indicator_id = INDICATORS[measure]

    countries = (
        [c.upper() for c in iso_codes]
        if iso_codes
        else list(AdminBoundary.objects.filter(admin_level=0).values_list("iso_code", flat=True).distinct())
    )
    if not countries:
        return []

    adm0 = {
        b.iso_code: b for b in AdminBoundary.objects.filter(admin_level=0, iso_code__in=countries).order_by("source")
    }

    key = f"{'+'.join(sorted(adm0))}.{indicator_id}._T._T."
    payload = http_json(
        f"{SDMX}/{key}",
        {"format": "sdmx-json", "lastNObservations": "1", "dimensionAtObservation": "AllDimensions"},
    )

    rows: list[Row] = []
    for iso, year, value in _parse(payload):
        boundary = adm0.get(iso)
        if boundary is None:
            continue
        rows.append(
            Row(
                indicator=measure,
                boundary=boundary,
                year=year,
                value=value,
                source=Source.IGME,
                source_ref=f"UN IGME {year} (national)",
                source_url=PORTAL.format(iso=iso),
                license_code=License.CC_BY_3_IGO,
                method=METHOD.format(year=year),
            )
        )

    logger.info("IGME: %d national %s values", len(rows), measure)
    return rows
