"""UN IGME subnational child mortality — the properly modelled version.

This is what the hand-rolled re-levelling in ``calibrate.py`` was approximating.
IGME publishes its own small-area estimates of under-5 and neonatal mortality:
25 African countries, 18 of them at ADM2, on a common reference year rather than
whatever year each survey happened to run.

The scale of the difference is easiest to see in the country that prompted the
search. Uganda from DHS: **4 regions, measured 2016**. Uganda from IGME:
**146 districts, estimated to 2021**.

Two things make this preferable to our own arithmetic wherever it reaches:

  * It is a model built for the purpose, by the agency whose national series we
    were scaling against, rather than one national ratio applied uniformly to
    every region of a country.
  * It resolves to ADM2 in most places, where a survey's regional strata rarely
    go below ADM1.

It does not replace the survey path. IGME covers 25 African countries; DHS
reaches ~41. So this becomes the preferred subnational method and the re-levelled
survey stays the fallback — which is what ``methods.py`` expresses.

**Series selection.** The dataflow carries both IGME's modelled estimates and the
direct survey values feeding them, distinguished by ``SERIES_NAME``. Only the
modelled series is read; taking the direct values would reproduce the raw-survey
problem while looking like something better.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import License, Source
from connect_labs.labs.indicators.sources.base import BoundaryMatcher, Row, http_json

logger = logging.getLogger(__name__)

SDMX = "https://sdmx.data.unicef.org/ws/public/sdmxapi/rest/data/UNICEF,CME_SUBNATIONAL,1.0"

#: our measure code -> IGME indicator id
INDICATORS = {
    "u5mr": "MRY0T4",
    "nmr": "MRM0",
}

#: Only the modelled series. The direct survey values in this dataflow are the
#: inputs to it, not an alternative to it.
MODELLED_SERIES = "UN IGME estimate"

PORTAL = "https://childmortality.org/data?refArea={iso}"

METHOD = (
    "UN IGME subnational estimate for {period}, admin level {level}. Small-area "
    "model fitted to survey and vital-registration inputs; not a direct survey "
    "reading. Area '{area}' matched to this boundary by name."
)

_DIMS = (
    "REF_AREA",
    "INDICATOR",
    "SEX",
    "WEALTH_QUINTILE",
    "SERIES_NAME",
    "SERIES_YEAR",
    "REF_AREA_PARENT",
    "ADMIN_LEVEL",
    "TIME_PERIOD",
)


def fetch(start_period: int = 2015) -> dict:
    """The whole subnational cube. One request; it is a few megabytes."""
    return http_json(
        f"{SDMX}/....",
        {
            "format": "sdmx-json",
            "startPeriod": str(start_period),
            "dimensionAtObservation": "AllDimensions",
        },
    )


def _parse(payload: dict) -> list[dict]:
    """Flatten SDMX-JSON to dicts, keeping only modelled totals."""
    data = payload.get("data") or {}
    datasets = data.get("dataSets") or []
    structure = data.get("structure") or {}
    dims = structure.get("dimensions", {}).get("observation", [])
    if not datasets or not dims:
        return []

    index = {d["id"]: i for i, d in enumerate(dims)}
    ids = {d["id"]: [v.get("id") for v in d["values"]] for d in dims}
    names = {d["id"]: [v.get("name") for v in d["values"]] for d in dims}

    out: list[dict] = []
    for key, obs in (datasets[0].get("observations") or {}).items():
        parts = key.split(":")

        def pick(dim, _parts=parts):
            return ids[dim][int(_parts[index[dim]])] if dim in index else None

        if names["SERIES_NAME"][int(parts[index["SERIES_NAME"]])] != MODELLED_SERIES:
            continue
        if pick("SEX") not in (None, "_T") or pick("WEALTH_QUINTILE") not in (None, "_T"):
            continue

        raw = obs[0] if obs else None
        if raw in (None, ""):
            continue

        area_i = int(parts[index["REF_AREA"]])
        out.append(
            {
                "area_code": ids["REF_AREA"][area_i],
                "area_name": names["REF_AREA"][area_i],
                "indicator": pick("INDICATOR"),
                "level": int(pick("ADMIN_LEVEL") or 0),
                "period": pick("TIME_PERIOD"),
                "value": float(raw),
            }
        )
    return out


def _iso_of(area_code: str) -> str:
    """IGME area codes carry their ISO-3 as a prefix, with varying separators."""
    return area_code[:3].upper()


def _year_of(period: str) -> int:
    # Periods look like "2021-01" or "2018-04".
    return int(str(period).split("-")[0])


#: Refuse to publish a layer that only half-lines-up with our boundaries. Below
#: this share of IGME areas matched, the country is left to the survey path
#: rather than shipping a partial map that looks complete.
MIN_MATCH_RATE = 0.75


def _best_fit(iso: str, areas: list[dict]) -> tuple[list[dict], int, float]:
    """Find which of our boundary levels these areas actually correspond to.

    IGME's ``ADMIN_LEVEL`` is its own tier numbering and does not line up with
    geoBoundaries' — the mapping differs per country, which is not something to
    assume:

      * Madagascar's IGME "level 2" is its 22 regions, which geoBoundaries calls
        ADM1. Matched against ADM2 it scored 1 of 22.
      * Uganda's IGME level 2 is its 146 districts; geoBoundaries ADM2 for Uganda
        is 151 counties — a different tier with different names.

    So rather than trusting either numbering, try each IGME level against each
    boundary level and keep whichever combination actually matches. Returns the
    chosen areas, the boundary level, and the match rate.
    """
    best: tuple[list[dict], int, float] = ([], 1, 0.0)
    for igme_level in sorted({a["level"] for a in areas}, reverse=True):
        at_level = [a for a in areas if a["level"] == igme_level]
        for boundary_level in (2, 1):
            matcher = BoundaryMatcher(iso, admin_level=boundary_level)
            if not len(matcher):
                continue
            hits = sum(1 for a in at_level if matcher.match(a["area_name"] or ""))
            rate = hits / len(at_level) if at_level else 0.0
            # Prefer a better match; tie-break toward the finer boundary level.
            if rate > best[2] + 1e-9:
                best = (at_level, boundary_level, rate)
    return best


def load(measure: str = "u5mr", iso_codes: list[str] | None = None) -> list[Row]:
    """Latest modelled estimate per area, matched to whichever level fits."""
    indicator_id = INDICATORS.get(measure)
    if indicator_id is None:
        return []

    wanted = {c.upper() for c in (iso_codes or ISO_CODES)}
    records = [r for r in _parse(fetch()) if r["indicator"] == indicator_id]

    # Keep the most recent period per area.
    latest: dict[str, dict] = {}
    for r in records:
        iso = _iso_of(r["area_code"])
        if iso not in wanted:
            continue
        cur = latest.get(r["area_code"])
        if cur is None or r["period"] > cur["period"]:
            latest[r["area_code"]] = r

    by_country: dict[str, list[dict]] = defaultdict(list)
    for r in latest.values():
        by_country[_iso_of(r["area_code"])].append(r)

    rows: list[Row] = []
    rejected: list[str] = []

    for iso, areas in sorted(by_country.items()):
        at_level, boundary_level, rate = _best_fit(iso, areas)
        if not at_level or rate < MIN_MATCH_RATE:
            rejected.append(f"{iso} ({rate:.0%})")
            continue

        matcher = BoundaryMatcher(iso, admin_level=boundary_level)
        matched = 0
        for a in at_level:
            boundary = matcher.match(a["area_name"] or "")
            if boundary is None:
                continue
            matched += 1
            rows.append(
                Row(
                    indicator=measure,
                    boundary=boundary,
                    year=_year_of(a["period"]),
                    value=a["value"],
                    source=Source.IGME_SUBNATIONAL,
                    source_ref=f"UN IGME subnational {_year_of(a['period'])} (ADM{boundary_level})",
                    source_url=PORTAL.format(iso=iso),
                    license_code=License.CC_BY_3_IGO,
                    method=METHOD.format(period=a["period"], level=boundary_level, area=a["area_name"]),
                    extra={
                        "igme_area_code": a["area_code"],
                        "igme_area_name": a["area_name"],
                        "igme_admin_level": a["level"],
                        "boundary_admin_level": boundary_level,
                        "country_match_rate": round(rate, 3),
                    },
                )
            )

        logger.info(
            "IGME subnational %s %s: %d/%d areas matched at ADM%d (%.0f%%)",
            iso,
            measure,
            matched,
            len(at_level),
            boundary_level,
            rate * 100,
        )

    if rejected:
        logger.info(
            "IGME subnational %s: %d countries below the %.0f%% match floor, left to " "the survey path: %s",
            measure,
            len(rejected),
            MIN_MATCH_RATE * 100,
            ", ".join(rejected),
        )
    return rows


def coverage_summary(iso_codes: list[str] | None = None) -> dict:
    """What we would gain, without writing anything. Useful before a load."""
    wanted = {c.upper() for c in (iso_codes or ISO_CODES)}
    records = [r for r in _parse(fetch()) if r["indicator"] == INDICATORS["u5mr"]]
    per: dict[str, dict] = defaultdict(lambda: {"adm1": set(), "adm2": set()})
    for r in records:
        iso = _iso_of(r["area_code"])
        if iso in wanted:
            per[iso][f"adm{r['level']}"].add(r["area_code"])
    have = {
        (b.iso_code, b.admin_level) for b in AdminBoundary.objects.filter(iso_code__in=wanted, admin_level__in=(1, 2))
    }
    return {
        iso: {
            "adm1_estimates": len(v["adm1"]),
            "adm2_estimates": len(v["adm2"]),
            "adm2_boundaries_loaded": (iso, 2) in have,
        }
        for iso, v in sorted(per.items())
    }
