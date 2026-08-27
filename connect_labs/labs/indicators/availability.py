"""Which countries can answer with which method — and, when they cannot, why.

This exists so the system can say "Chad has no subnational mortality" out loud
instead of quietly answering at national level and letting a reader compare a
Nigerian state against a whole country.

Availability is computed from what is actually stored, not from a hand-kept
list, so it cannot drift away from the data. A country is available for a method
when one of the method's sources has a value for the requested indicator at the
method's resolution.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.africa import ISO_CODES, name_for
from connect_labs.labs.indicators.methods import METHODS, Method, Resolution
from connect_labs.labs.indicators.models import IndicatorValue


@dataclass
class CountryAvailability:
    iso_code: str
    name: str
    available: bool
    #: The source that would answer, when one would.
    source: str = ""
    #: Number of units the method can speak about (1 for national).
    units: int = 0
    #: Newest measurement year behind it.
    year: int | None = None
    reason: str = ""


def _reason(method: Method, has_boundaries: bool) -> str:
    if not has_boundaries:
        level = "country" if method.is_national else "region"
        return f"no {level} boundaries loaded"
    if method.is_national:
        return "no national estimate stored"
    return "no subnational survey for this country"


def for_method(
    method: Method,
    indicator: str = "u5mr",
    iso_codes: list[str] | None = None,
) -> list[CountryAvailability]:
    """Per-country availability of one method, computed from stored values."""
    countries = [c.upper() for c in (iso_codes or ISO_CODES)]
    levels = method.resolution.admin_levels

    have_boundaries = set(
        AdminBoundary.objects.filter(iso_code__in=countries, admin_level__in=levels)
        .values_list("iso_code", flat=True)
        .distinct()
    )

    # One pass over the candidate values, bucketed by country and source.
    rows = IndicatorValue.objects.filter(
        indicator=indicator,
        iso_code__in=countries,
        admin_level__in=levels,
        source__in=method.source_order,
    ).values_list("iso_code", "source", "boundary_id", "year")

    by_country: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    years: dict[tuple[str, str], int] = {}
    for iso, source, boundary_id, year in rows:
        by_country[iso][source].add(boundary_id)
        key = (iso, source)
        years[key] = max(years.get(key, 0), year)

    out: list[CountryAvailability] = []
    for iso in sorted(countries):
        sources = by_country.get(iso, {})
        # Method's own preference order decides which source answers.
        chosen = next((s for s in method.source_order if sources.get(s)), None)
        if chosen is None:
            out.append(
                CountryAvailability(
                    iso_code=iso,
                    name=name_for(iso),
                    available=False,
                    reason=_reason(method, iso in have_boundaries),
                )
            )
            continue
        out.append(
            CountryAvailability(
                iso_code=iso,
                name=name_for(iso),
                available=True,
                source=chosen,
                units=len(sources[chosen]),
                year=years.get((iso, chosen)),
            )
        )
    return out


def matrix(indicator: str = "u5mr", iso_codes: list[str] | None = None) -> dict:
    """Every method against every country, plus a summary per method.

    The shape the UI needs to grey out a method it cannot honour and say how
    much of the continent it would cover.
    """
    result = {"indicator": indicator, "methods": {}}
    for code, method in METHODS.items():
        rows = for_method(method, indicator, iso_codes)
        available = [r for r in rows if r.available]
        result["methods"][code] = {
            "label": method.label,
            "resolution": method.resolution.value,
            "default": method.default,
            "description": method.description,
            "caveat": method.caveat,
            "countries_available": len(available),
            "countries_total": len(rows),
            "units": sum(r.units for r in available),
            "unavailable": [{"iso": r.iso_code, "name": r.name, "reason": r.reason} for r in rows if not r.available],
            "countries": [
                {
                    "iso": r.iso_code,
                    "name": r.name,
                    "available": r.available,
                    "source": r.source,
                    "units": r.units,
                    "year": r.year,
                    "reason": r.reason,
                }
                for r in rows
            ],
        }
    return result


def countries_supporting(method: Method, indicator: str = "u5mr", iso_codes: list[str] | None = None) -> list[str]:
    """ISO codes a method can actually answer for."""
    return [r.iso_code for r in for_method(method, indicator, iso_codes) if r.available]


def resolutions() -> dict[str, list[str]]:
    """Method codes grouped by resolution, defaults first."""
    grouped: dict[str, list[str]] = {r.value: [] for r in Resolution}
    for code, m in METHODS.items():
        grouped[m.resolution.value].append(code)
    for codes in grouped.values():
        codes.sort(key=lambda c: (not METHODS[c].default, c))
    return grouped
