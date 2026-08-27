"""HDX HAPI loader — subnational baseline population, pre-aggregated.

The Humanitarian API already holds population by admin-1 with age–sex bands, so
a country arrives in one request rather than one zonal-stats task per region.
That makes it both a fast path and a genuine second opinion on WorldPop: HAPI's
figures come from national statistical offices and OCHA operational datasets
rather than from a modelled raster.

It is not a replacement. Coverage is humanitarian-led, so a stable country may be
absent entirely, and age banding varies by source. WorldPop stays the universal
backbone; this fills in where it can and gives us something to disagree with.

Because ``(indicator, boundary, year, source)`` is the natural key, HAPI values
sit alongside WorldPop's rather than overwriting them, and ``DEFAULT_SOURCE_ORDER``
decides which one a query sees.
"""

from __future__ import annotations

import base64
import logging
from collections import defaultdict

from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import BoundaryMatcher, Row, http_json

logger = logging.getLogger(__name__)

API = "https://hapi.humdata.org/api/v1/population-social/population"

#: The HDX dataset page behind these figures.
DATASET_PAGE = "https://data.humdata.org/dataset/hdx-hapi-population"

#: HAPI wants an "app identifier" — base64 of "appname:email". It is an
#: attribution courtesy, not a credential, and is documented as such.
APP_IDENTIFIER = base64.b64encode(b"connect-labs-targeting:labs@dimagi.com").decode()

#: HAPI reports five-year bands plus an "all" total, and gender as f / m / all.
#: Only the gender="all" cells are read for both-sex measures — summing f and m
#: alongside "all" would count everyone twice.
U5_BANDS = {"0-4"}
WOMEN_BANDS = {"15-19", "20-24", "25-29", "30-34", "35-39", "40-44", "45-49"}
ALL = "all"

#: HAPI's finest infant band is 0-4. There is no 0-1 cell, so this source cannot
#: supply ``pop_u1`` and therefore cannot feed the cohort-based births estimate.
#: Births for HAPI-only countries come from the fertility method instead — see
#: sources/derive.py.

METHOD = (
    "HDX HAPI baseline population (admin 1), sourced from national statistics "
    "and OCHA operational datasets. Reference period {period}."
)


def _fetch_country(iso: str, admin_level: int = 1) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        payload = http_json(
            API,
            {
                "location_code": iso,
                "admin_level": str(admin_level),
                "limit": "10000",
                "offset": str(offset),
                "app_identifier": APP_IDENTIFIER,
            },
        )
        rows = payload.get("data") or []
        out.extend(rows)
        if len(rows) < 10000:
            break
        offset += len(rows)
    return out


def load(iso_codes: list[str], admin_level: int = 1) -> list[Row]:
    """Population counts per admin unit, for whichever countries HAPI covers.

    ADM2 is worth asking for because mortality now resolves that deep in eleven
    countries, but coverage there is thin and uneven — Zimbabwe has 91 units,
    Zambia none. Whatever it returns is a fast win; WorldPop remains the
    universal fallback.
    """
    rows: list[Row] = []
    name_key = f"admin{admin_level}_name"

    for iso in sorted({c.upper() for c in iso_codes}):
        try:
            records = _fetch_country(iso, admin_level)
        except Exception as exc:  # noqa: BLE001 — absence is normal, not fatal
            logger.info("HAPI: %s unavailable (%s)", iso, exc)
            continue
        if not records:
            continue

        matcher = BoundaryMatcher(iso, admin_level=admin_level)
        if not len(matcher):
            continue

        # Sum the age–sex cells into our four denominators per region.
        totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        periods: dict[str, str] = {}

        for r in records:
            name = r.get(name_key)
            if not name:
                continue
            pop = float(r.get("population") or 0)
            band = r.get("age_range") or ""
            gender = (r.get("gender") or "").lower()
            periods.setdefault(name, (r.get("reference_period_start") or "")[:10])

            # The all-ages, both-sexes cell is the total; summing bands as well
            # would double-count it.
            if band == ALL and gender == ALL:
                totals[name]["pop_total"] += pop
            elif band in U5_BANDS and gender == ALL:
                totals[name]["pop_u5"] += pop
            elif band in WOMEN_BANDS and gender == "f":
                totals[name]["pop_f_15_49"] += pop

        matched = 0
        for name, counts in totals.items():
            boundary = matcher.match(name)
            if boundary is None:
                continue
            matched += 1
            for code, value in counts.items():
                if not value:
                    continue
                rows.append(
                    Row(
                        indicator=code,
                        boundary=boundary,
                        year=int((periods.get(name) or "2022")[:4] or 2022),
                        value=value,
                        source=Source.HAPI,
                        source_ref="HDX HAPI baseline population",
                        source_url=DATASET_PAGE,
                        license_code=License.OPEN_API,
                        method=METHOD.format(period=periods.get(name) or "unstated"),
                    )
                )

        logger.info("HAPI %s ADM%d: %d/%d units matched", iso, admin_level, matched, len(totals))

    return rows
