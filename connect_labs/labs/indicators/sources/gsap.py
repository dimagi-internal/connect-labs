"""Poverty by region, from the World Bank's Global Subnational Atlas of Poverty (GSAP).

The question this answers is "how many people in this region live below a poverty line", so a delivery plan or a
targeting selection can weigh need that mortality and coverage figures do not carry. It is NOT a measure of what a
programme can do; it is who is poor where.

**What a value is.** The share of a region's people below the international line of US$3.00 a day (the current
extreme-poverty line) and US$4.20 a day (the lower-middle-income line), in 2021 purchasing-power terms. $3.00 is
near 100% in the poorest regions and near 0 in cities, so $4.20 discriminates better in the middle; both are kept.

**Line-up, not measurement.** GSAP takes the latest household survey for a country (its ``baseyear``: Nigeria's 2022
Living Standards Survey, Ethiopia's 2021 HCES, ...) and *lines it up* to a reference year with national growth. So a
2024 figure for a region was surveyed years earlier, and ``extra`` carries both years and the survey's name so a reader
sees "surveyed 2022, lined up to 2024" and not a measurement of 2024.

**Placement.** GSAP's regions are keyed to GAUL or GADM codes that do not match our boundaries, so a region is
joined to an admin-1 boundary by name (``base.BoundaryMatcher``, with the aliases in ``base.ALIASES``). A region that
does not match, or a boundary GSAP has no region for (Nigeria's Zamfara and Taraba, Ethiopia's Tigray), is left
WITHOUT a value: never filled from a neighbour or the country. A country GSAP reports as one region is written at the
country level and reaches its territories by inheritance, flagged as inherited like any country figure a territory
takes.

Licence: CC BY 4.0, https://datacatalog.worldbank.org/search/dataset/0042041
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

import openpyxl

from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import BoundaryMatcher, Row, http_get_bytes

logger = logging.getLogger(__name__)

#: The catalog release and its file. A new release is a new ``DR`` path; bump both together.
RELEASE = "AM26"
URL = "https://datacatalogfiles.worldbank.org/ddh-published/0042041/DR0052555/AM26%20-%20GSAP%20data.xlsx"
PORTAL = "https://datacatalog.worldbank.org/search/dataset/0042041"
TIMEOUT = 120

#: GSAP column -> measure. Stored as percent, as every other rate here is.
LINES = {"poor300_ln": "poverty_rate_300", "poor420_ln": "poverty_rate_420"}


@dataclass(frozen=True)
class Region:
    iso: str
    name: str
    baseyear: int | None
    lineupyear: int
    survey: str
    welfare: str
    rates: dict  # measure -> percent


def fetch() -> bytes:
    logger.info("GSAP %s: fetching %s", RELEASE, URL)
    return http_get_bytes(URL, [], timeout=TIMEOUT)


def latest_sheet(names: list[str]) -> str:
    """The "Data YYYY" sheet with the latest line-up year."""
    sheets = [(int(m.group(1)), n) for n in names if (m := re.fullmatch(r"Data (\d{4})", n))]
    if not sheets:
        raise ValueError(f"no 'Data YYYY' sheet in {names}")
    return max(sheets)[1]


def parse(xlsx: bytes, iso_codes: list[str] | None = None) -> list[Region]:
    """The regions of the latest line-up sheet (optionally only ``iso_codes``)."""
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    rows = wb[latest_sheet(wb.sheetnames)].iter_rows(values_only=True)
    head = [str(c).strip() if c is not None else "" for c in next(rows)]
    ix = {h: i for i, h in enumerate(head)}
    wanted = {c.upper() for c in iso_codes} if iso_codes else None
    out: list[Region] = []
    for r in rows:
        iso = str(r[ix["code"]] or "").strip().upper()
        if not iso or (wanted is not None and iso not in wanted):
            continue
        rates = {}
        for col, measure in LINES.items():
            v = r[ix[col]]
            if isinstance(v, int | float):
                rates[measure] = float(v) * 100.0
        if not rates:
            continue
        out.append(
            Region(
                iso,
                str(r[ix["sample"]] or "").strip(),
                int(r[ix["baseyear"]]) if r[ix["baseyear"]] else None,
                int(r[ix["lineupyear"]]),
                str(r[ix["survname"]] or "").strip(),
                str(r[ix["welfaretype"]] or "").strip(),
                rates,
            )
        )
    return out


def _row(measure: str, boundary, region: Region, how: str) -> Row:
    return Row(
        indicator=measure,
        boundary=boundary,
        year=region.lineupyear,
        value=region.rates[measure],
        source=Source.GSAP,
        source_ref=f"GSAP {RELEASE}, {region.survey} {region.baseyear} lined up to {region.lineupyear}",
        source_url=PORTAL,
        license_code=License.CC_BY_4,
        method=(
            f"Share of people below the line (2021 PPP), World Bank GSAP {RELEASE}: household survey "
            f"{region.survey} ({region.baseyear}, {region.welfare.lower() or 'welfare'}) lined up to "
            f"{region.lineupyear} with national growth, so a modelled figure for the line-up year, not a "
            f"measurement of it. Region '{region.name}' {how}."
        ),
        extra={
            "baseyear": region.baseyear,
            "lineupyear": region.lineupyear,
            "survey": region.survey,
            "welfare": region.welfare,
            "region": region.name,
        },
    )


def rows_for_country(regions: list[Region], matcher: BoundaryMatcher, country) -> tuple[list[Row], list[str]]:
    """Rows for one country and the region names that matched nothing.

    A single-region country is one national figure, written on the country; otherwise each region is joined to an
    admin-1 boundary by name, and a boundary two regions both match is written once, by the first, and reported.
    """
    rows: list[Row] = []
    misses: list[str] = []
    if len(regions) == 1:
        for measure in regions[0].rates:
            rows.append(_row(measure, country, regions[0], "is the whole country"))
        return rows, misses
    seen: dict[int, str] = {}
    for region in regions:
        b = matcher.match(region.name)
        if b is None:
            misses.append(region.name)
        elif b.pk in seen:
            misses.append(f"{region.name} (same boundary as {seen[b.pk]})")
        else:
            seen[b.pk] = region.name
            rows.extend(_row(m, b, region, f"matched to '{b.name}' by name") for m in region.rates)
    return rows, misses


def load(iso_codes: list[str] | None = None, payload: bytes | None = None) -> tuple[list[Row], dict[str, list[str]]]:
    """Rows for every African country in the file, and each country's unmatched region names."""
    payload = payload if payload is not None else fetch()
    by_iso: dict[str, list[Region]] = {}
    for region in parse(payload, iso_codes or ISO_CODES):
        by_iso.setdefault(region.iso, []).append(region)
    rows: list[Row] = []
    misses: dict[str, list[str]] = {}
    for iso, regions in sorted(by_iso.items()):
        country = boundary_set.owned().filter(iso_code=iso, admin_level=0).first()
        if country is None:
            continue
        got, missed = rows_for_country(regions, BoundaryMatcher(iso, 1), country)
        rows.extend(got)
        if missed:
            misses[iso] = missed
    logger.info("GSAP: %d rows for %d countries; unmatched: %s", len(rows), len(by_iso), misses)
    return rows, misses
