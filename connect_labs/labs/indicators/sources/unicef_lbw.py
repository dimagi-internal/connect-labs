"""Low birthweight, from the UNICEF-WHO Global Low Birthweight Estimates.

This exists so Kangaroo Mother Care can be sized on the babies it is for. KMC
serves low-birthweight and preterm newborns, which are roughly a seventh of
births in sub-Saharan Africa; priced per birth, it overstated eligible babies
about sevenfold. The quantity it needs is ``births x low-birthweight rate``, and
that rate is what this loader supplies.

**Where it comes from.** UNICEF's global SDMX warehouse, dataflow
``GLOBAL_DATAFLOW``, indicator ``NT_BW_LBW``, both sexes. The series is the
UNICEF-WHO modelled estimate (July 2023 release), annual to 2020, with
published uncertainty bounds. It is national only — no subnational series
exists — so every region inherits its country's rate, which the resolver
already reports as ``inherited``.

**Filter on the query, not on a label.** The release name ("UNICEF-WHO Global
Low Birthweight Estimates Databases, July 2023") sits in the ``SERIES_FOOTNOTE``
attribute, while ``DATA_SOURCE`` is the literal filename ``CMRS_SERIES_LBW.csv``.
Filtering on ``DATA_SOURCE`` containing "UNICEF-WHO" silently discards every
country. The indicator and sex are fixed in the request key instead, and the
footnote is carried into ``source_ref`` rather than used as a gate.

**Sixteen countries have no national estimate at all.** Nigeria, Ethiopia,
Egypt, Uganda, Sudan, Niger, Mali, Somalia, Chad, Guinea, South Sudan,
Mauritania, Equatorial Guinea, Djibouti, Cabo Verde and Libya return no row for
any year (the API answers 404). UNICEF-WHO do not publish a country estimate
where the input data are too thin, but they do compute one for the regional
aggregates, which report 100% population coverage. So those countries are
*inside* the regional figure.

Two honest options existed for them, and this takes the second:

  1. carry no rate, and let ``coverage`` report the shortfall; or
  2. carry the region's rate under a distinct source and label.

The first makes every eligible-baby total a floor that silently omits Nigeria
and Ethiopia, two of the three largest birth cohorts on the continent. For the
question this was built for ("how many LBW newborns could KMC reach?") that
answer is useless, and a floor that drops the biggest countries reads as a
measurement to anyone who does not check ``coverage``.

The second is only honest if it is impossible to mistake for a national
figure. So a regional row:

  * has its own source code, ``unicef_lbw_region``, which ``policy.py`` ranks
    strictly after the national estimate. It is used only where no national
    row exists, never in preference to one.
  * sets ``extra.regional_proxy``, which makes ``Resolved.inherited`` true.
    Every selection's ``inherited_units`` counts it, every row says
    ``inherited``, and its provenance names the region it was measured for.
  * names the region in ``source_ref`` and ``method``, and carries the
    region's own uncertainty bounds.
  * propagates to every count derived from it (see
    ``derive.load_lbw_births``), and a selection reports how many units' counts
    rest on it as ``regional_proxy_units``.

The region is the country's **UN M49 subregion** (``africa.M49_SUBREGION``),
not UNICEF's reporting region. The M49 subregions are finer: five for Africa
against UNICEF's two, which split the continent roughly in half. Western Africa
reads 14.26% where UNICEF's West and Central Africa reads 13.42%, and the
difference is Middle Africa's lower rate being averaged in.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import httpx

from connect_labs.labs.indicators import boundaries
from connect_labs.labs.indicators.africa import ISO_CODES, M49_SUBREGION, name_for
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import Row

logger = logging.getLogger(__name__)

URL = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest/data/UNICEF,GLOBAL_DATAFLOW,1.0/.NT_BW_LBW._T"
TIMEOUT = 120.0

#: Oldest year worth asking for. The series is annual to 2020; the latest year
#: is what is stored, so this only bounds the payload.
START_PERIOD = "2015"

#: Where a reader can check the figure. UNICEF's own topic page, which carries
#: the country and regional tables this dataflow serves.
PORTAL = "https://data.unicef.org/topic/nutrition/low-birthweight/"

MEASURE = "lbw_rate"


def fetch(start_period: str = START_PERIOD) -> dict:
    """Every area's LBW series, as SDMX-JSON with attributes."""
    params = {
        "format": "sdmx-json",
        "startPeriod": start_period,
        "dimensionAtObservation": "AllDimensions",
    }
    logger.info("UNICEF LBW: fetching %s", URL)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        r = client.get(URL, params=params)
        r.raise_for_status()
        return r.json()


def decode(payload: dict):
    """One dict per observation: dimensions by id, plus the attributes we use.

    SDMX-JSON keys an observation by positional indices into the dimension
    lists, and carries each attribute as a positional index into that
    attribute's value list, in the order ``structure.attributes.observation``
    declares. Both are looked up by declared position rather than by a fixed
    offset, because a reordered dimension or attribute would otherwise
    attribute one country's value to another without raising.
    """
    structure = payload["data"]["structure"]
    dims = structure["dimensions"]["observation"]
    attrs = (structure.get("attributes") or {}).get("observation") or []

    for dataset in payload["data"]["dataSets"]:
        for key, obs in (dataset.get("observations") or {}).items():
            if not obs or obs[0] in (None, ""):
                continue
            out: dict = {}
            for dim, i in zip(dims, (int(p) for p in key.split(":")), strict=True):
                v = dim["values"][i]
                out[dim["id"]] = v.get("id")
                out[f"{dim['id']}_name"] = v.get("name", v.get("id"))
            for n, attr in enumerate(attrs, start=1):
                j = obs[n] if n < len(obs) else None
                if j is None:
                    continue
                v = attr["values"][j]
                out[attr["id"]] = v.get("id", v.get("name"))
            out["_value"] = obs[0]
            yield out


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest(records: list[dict]) -> dict | None:
    return max(records, key=lambda r: str(r.get("TIME_PERIOD", "")), default=None)


def load(iso_codes: list[str] | None = None, payload: dict | None = None) -> list[Row]:
    """One ``lbw_rate`` row per country, on its ADM0 boundary.

    A country's latest national estimate where one exists; otherwise its M49
    subregion's aggregate for that region's latest year, stored under
    ``Source.UNICEF_LBW_REGIONAL`` and flagged as a regional proxy. A country
    with neither — no national series and no subregion — gets no row, and the
    selections that would have used it report the shortfall in ``coverage``.
    """
    payload = payload or fetch()
    wanted = [c.upper() for c in (iso_codes or ISO_CODES)]

    by_area: dict[str, list[dict]] = defaultdict(list)
    for rec in decode(payload):
        if rec.get("INDICATOR") != "NT_BW_LBW" or rec.get("SEX") != "_T":
            continue
        by_area[rec["REF_AREA"]].append(rec)

    adm0 = {b.iso_code: b for b in boundaries.owned().filter(admin_level=0, iso_code__in=wanted)}

    rows: list[Row] = []
    national = regional = 0
    missing: list[str] = []
    for iso in sorted(wanted):
        boundary = adm0.get(iso)
        if boundary is None:
            continue

        rec = _latest(by_area.get(iso, []))
        if rec is not None and (value := _float(rec["_value"])) is not None:
            year = int(str(rec["TIME_PERIOD"])[:4])
            release = rec.get("SERIES_FOOTNOTE") or "UNICEF-WHO Low Birthweight Estimates"
            rows.append(
                Row(
                    indicator=MEASURE,
                    boundary=boundary,
                    year=year,
                    value=value,
                    ci_low=_float(rec.get("LOWER_BOUND")),
                    ci_high=_float(rec.get("UPPER_BOUND")),
                    source=Source.UNICEF_LBW,
                    source_ref=f"{release}, {year} (national)",
                    source_url=rec.get("SOURCE_LINK") or PORTAL,
                    license_code=License.CC_BY_3_IGO,
                    method=(
                        f"{release}: modelled national prevalence of low birthweight (<2,500 g) "
                        f"among live births, {year}, both sexes. Retrieved from UNICEF SDMX "
                        "GLOBAL_DATAFLOW, indicator NT_BW_LBW. National only; any region below "
                        "the country inherits this figure."
                    ),
                    extra={"unicef_indicator": "NT_BW_LBW", "release": release, "regional_proxy": False},
                )
            )
            national += 1
            continue

        region = M49_SUBREGION.get(iso)
        reg = _latest(by_area.get(region or "", []))
        if reg is None or (value := _float(reg["_value"])) is None:
            missing.append(iso)
            continue

        year = int(str(reg["TIME_PERIOD"])[:4])
        region_name = reg.get("REF_AREA_name") or region
        release = reg.get("SERIES_FOOTNOTE") or "UNICEF-WHO Low Birthweight Estimates"
        coverage_note = reg.get("OBS_FOOTNOTE") or ""
        rows.append(
            Row(
                indicator=MEASURE,
                boundary=boundary,
                year=year,
                value=value,
                ci_low=_float(reg.get("LOWER_BOUND")),
                ci_high=_float(reg.get("UPPER_BOUND")),
                source=Source.UNICEF_LBW_REGIONAL,
                # Capped: source_ref is a 200-character column, and the region
                # name is the part that must survive.
                source_ref=f"{region_name} regional aggregate, {year} — no national estimate"[:200],
                source_url=reg.get("SOURCE_LINK") or PORTAL,
                license_code=License.CC_BY_3_IGO,
                method=(
                    f"REGIONAL PROXY. {release} publishes no national low-birthweight estimate for "
                    f"{name_for(iso)}. This is the aggregate for its UN M49 subregion, {region_name} "
                    f"({region}), {year}, applied to the whole country. UNICEF-WHO compute the "
                    "aggregate over every country in the region, including those whose own estimate "
                    f"is not published ({coverage_note.strip() or 'coverage note not supplied'}), so "
                    f"{name_for(iso)} is inside it, but this is a regional figure, not a measurement "
                    "of this country."
                ),
                extra={
                    "unicef_indicator": "NT_BW_LBW",
                    "release": release,
                    "regional_proxy": True,
                    "proxy_region": region,
                    "proxy_region_name": region_name,
                    "region_coverage_note": coverage_note,
                },
            )
        )
        regional += 1

    logger.info(
        "UNICEF LBW: %d national, %d regional-proxy rows; no figure at all for %s",
        national,
        regional,
        ", ".join(missing) or "none",
    )
    return rows
