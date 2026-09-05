"""UNICEF's subnational SDMX warehouse.

This exists to reach places DHS never surveyed. Thirteen African countries have
no DHS at all — 189M people, 13% of the continent — and for them every
child-health question this system is asked returns nothing.

MICS was the obvious answer and is **not directly reachable**. UNICEF's SDMX
warehouse carries MICS results only where they have been harmonised into a
thematic product; the child-health MICS tables are published as per-survey
reports and SPSS microdata behind per-survey registration. Reaching those means
re-tabulating survey microdata — sample weights, cluster design, and matching
each survey's own region vocabulary — which is a different class of work.

``WASH_HOUSEHOLD_SUBNAT`` is the harmonised product, and it is the reachable
form of MICS: the JMP pools DHS, **MICS**, MIS, EDSMICS and national surveys
onto one definition. It adds eight African countries to what we hold, five of
them DHS-less — Algeria, Comoros, Egypt, Guinea-Bissau and Somalia — which is
the whole point of coming here.

Of UNICEF's 42 dataflows exactly four are subnational, and only two are useful
here. ``CME_SUBNATIONAL`` is the other; on its own it would not have justified
this module, adding just Uganda and Zambia to our neonatal mortality, but once
the loader exists it costs nothing to read.

**On the SDMX encoding.** Observations are keyed by a colon-separated string of
*positional indices* into the dimension lists — ``"0:0:0:0:0:0:0:0:0"`` means
the first value of each dimension, in declared order. The dimensions differ
between dataflows, so nothing may assume a fixed position: every lookup here
goes through the declared order rather than a hard-coded offset. Getting this
wrong does not raise; it silently attributes one region's value to another.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import httpx

from connect_labs.labs.indicators.africa import ISO_CODES, name_for
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import BoundaryMatcher, Row

logger = logging.getLogger(__name__)

BASE = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest"
TIMEOUT = 180.0

#: UNICEF names countries in UN style; ours come from geoBoundaries. Only the
#: ones that actually differ are listed — a full table would rot.
COUNTRY_ALIASES = {
    "united republic of tanzania": "TZA",
    "democratic republic of the congo": "COD",
    "congo": "COG",
    "côte d'ivoire": "CIV",
    "cote d'ivoire": "CIV",
    "gambia": "GMB",
    "eswatini": "SWZ",
    "cabo verde": "CPV",
    "guinea-bissau": "GNB",
    "sao tome and principe": "STP",
    "são tomé and príncipe": "STP",
    "central african republic": "CAF",
    "egypt": "EGY",
    "libya": "LBY",
}


def _iso_index() -> dict[str, str]:
    idx = {name_for(c).lower(): c for c in ISO_CODES}
    idx.update(COUNTRY_ALIASES)
    return idx


def fetch(dataflow: str) -> dict:
    """One dataflow, all observations, as SDMX-JSON."""
    url = f"{BASE}/data/UNICEF,{dataflow}/all"
    params = {"format": "sdmx-json", "dimensionAtObservation": "AllDimensions"}
    logger.info("UNICEF SDMX: fetching %s", dataflow)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def decode(payload: dict):
    """Yield one dict per observation, dimensions resolved to their names.

    The positional-index encoding is decoded here, once, so no caller has to
    know about it. An observation whose value is absent is skipped rather than
    yielded as None — SDMX uses a sparse map, so a missing key means no
    observation, not an observation of nothing.
    """
    structure = payload["data"]["structure"]
    dims = structure["dimensions"]["observation"]
    order = [d["id"] for d in dims]
    values = [d["values"] for d in dims]

    for datasets in payload["data"]["dataSets"]:
        for key, obs in (datasets.get("observations") or {}).items():
            idx = [int(i) for i in key.split(":")]
            if obs is None or obs[0] is None:
                continue
            out = {}
            for name, positions, i in zip(order, values, idx, strict=True):
                v = positions[i]
                out[name] = v.get("name", v.get("id"))
            out["_value"] = obs[0]
            yield out


def _latest_series_per_country(records: list[dict], time_key: str) -> dict[str, dict]:
    """One observation per area, all from the country's most recent series.

    Per country, not per area — and the difference is not cosmetic.

    Picking the latest row for each area independently looks obviously right
    and is wrong here, because **UNICEF mixes tessellations within a country**.
    Tunisia's dataflow carries seven economic regions from MICS 2018 *and* a
    handful of individual governorates from MICS 2012. Taking the latest per
    area keeps both, and our boundary names match only the governorates — the
    four poorest interior ones. The country then rolls up to 14.5% open
    defecation, which is not Tunisia's figure (it is near zero) but the mean of
    its four worst governorates, wearing a national label.

    That is the failure mode partial matching always has: it does not produce a
    gap, it produces a *biased* number that looks complete. Holding one series
    per country means an area either belongs to the survey we are reading or it
    is not read at all, so a country whose latest series does not match our
    boundaries drops out entirely — which is the right answer, and the same
    rule the DHS loader already applies for the same reason.
    """
    latest: dict[str, str] = {}
    for r in records:
        iso_key = r["REF_AREA_PARENT"]
        t = str(r.get(time_key, ""))
        if t > latest.get(iso_key, ""):
            latest[iso_key] = t

    best: dict[str, dict] = {}
    for r in records:
        if str(r.get(time_key, "")) != latest[r["REF_AREA_PARENT"]]:
            continue
        best[r["REF_AREA"]] = r
    return best


def load(
    dataflow: str,
    indicator_name: str,
    measure: str,
    *,
    admin_level: int = 1,
    iso_codes: list[str] | None = None,
    payload: dict | None = None,
    invert: bool = False,
) -> list[Row]:
    """Rows for one measure from one UNICEF dataflow.

    ``indicator_name`` is matched exactly against the dataflow's INDICATOR
    dimension. Exactly, not by substring: the WASH dataflow carries four
    different "improved drinking water" indicators whose labels differ only in
    their tail, and a substring match would pick whichever came first.
    """
    payload = payload or fetch(dataflow)
    iso_index = _iso_index()
    wanted = {c.upper() for c in iso_codes} if iso_codes else None

    by_country: dict[str, list[dict]] = defaultdict(list)
    seen_indicators: set[str] = set()
    for rec in decode(payload):
        seen_indicators.add(rec.get("INDICATOR", ""))
        if rec.get("INDICATOR") != indicator_name:
            continue
        if rec.get("ADMIN_LEVEL") != f"Administrative level {admin_level}":
            continue
        iso = iso_index.get((rec.get("REF_AREA_PARENT") or "").strip().lower())
        if not iso or (wanted and iso not in wanted):
            continue
        by_country[iso].append(rec)

    if not by_country:
        logger.warning(
            "UNICEF %s: no African rows for %r at ADM%d. Known indicators: %s",
            dataflow,
            indicator_name,
            admin_level,
            sorted(seen_indicators)[:5],
        )
        return []

    time_key = "TIME_PERIOD"
    rows: list[Row] = []
    for iso, recs in sorted(by_country.items()):
        matcher = BoundaryMatcher(iso, admin_level=admin_level)
        if not len(matcher):
            logger.info("UNICEF %s: %s has no ADM%d boundaries, skipping", dataflow, iso, admin_level)
            continue

        latest = _latest_series_per_country(recs, time_key)
        matched = 0
        for area, rec in latest.items():
            boundary = matcher.match(area)
            if boundary is None:
                continue  # unmatched labels are dropped, never guessed
            try:
                value = float(rec["_value"])
            except (TypeError, ValueError):
                continue
            if invert:
                value = 100.0 - value
            year = str(rec.get(time_key) or "")[:4]
            if not year.isdigit():
                continue
            source_name = rec.get("DATA_SOURCE_MAIN") or rec.get("SERIES_NAME") or dataflow
            rows.append(
                Row(
                    indicator=measure,
                    boundary=boundary,
                    year=int(year),
                    value=value,
                    source=Source.UNICEF_SDMX,
                    source_ref=f"{source_name} {year} via UNICEF {dataflow}",
                    source_url="https://data.unicef.org/",
                    license_code=License.CC_BY_3_IGO,
                    method=(
                        f"UNICEF {dataflow}, indicator {indicator_name!r}, "
                        f"administrative level {admin_level}, harmonised from {source_name} "
                        f"{year}."
                        + (
                            " Stored as the complement of the published figure, "
                            "because this system states it as coverage."
                            if invert
                            else ""
                        )
                    ),
                    extra={
                        "unicef_label": area,
                        "dataflow": dataflow,
                        "survey_source": source_name,
                        "unicef_indicator": indicator_name,
                    },
                )
            )
            matched += 1

        logger.info(
            "UNICEF %s %s: %s %d/%d area labels matched",
            dataflow,
            measure,
            iso,
            matched,
            len(latest),
        )

    logger.info("UNICEF %s: %d %s rows across %d countries", dataflow, len(rows), measure, len(by_country))
    return rows
