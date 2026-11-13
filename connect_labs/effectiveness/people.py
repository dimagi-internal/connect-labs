"""People per country in the places the baseline selects: the count every market is sized from.

Market size (the page and the ``cea_market_size`` tool) asks the baseline one question, *which places*, and needs back
how many people each model serves in each country there (children under 5 for CHC, low-birthweight births for KC,
births for MBW). This module is that seam: it reads the baseline through its public ``Question`` and hands the pure
sizing code (``market``) plain dictionaries.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache

from connect_labs.effectiveness import market
from connect_labs.labs.indicators import measures, response_cache
from connect_labs.labs.indicators.query import Question, float_param, indicator_param

#: The burden indicators the market page offers for choosing places (higher means more need).
TARGET_INDICATORS = ("u5mr", "nmr", "diarrhoea_prevalence")
#: "Everywhere with data": the indicator that has values in the most countries, with no threshold, so the market is
#: every place the baseline can count people in rather than only the highest-burden ones.
EVERYWHERE = "all"
EVERYWHERE_INDICATOR = "diarrhoea_prevalence"


def places_question(request) -> tuple[Question, bool]:
    """The question that chooses the places, and whether it means everywhere (``indicator=all``)."""
    if request.GET.get("indicator") == EVERYWHERE:
        return Question.from_request(request, indicator=EVERYWHERE_INDICATOR, threshold=0.0), True
    indicator = indicator_param(request)
    threshold = float_param(request, "threshold", measures.get(indicator).threshold_default)
    return Question.from_request(request, indicator=indicator, threshold=threshold), False


def group_selection(selection) -> dict:
    """A baseline selection as people per country, for every model's count, with what it could not count.

    ``partial`` names the counts a country is missing for some of its areas, so its total is a floor.
    """
    by_country: dict[str, dict] = {}
    for area in selection.areas:
        c = by_country.setdefault(area.iso_code, {"name": area.country_name, "counts": {}, "partial": set()})
        for m in market.count_measures():
            v = area.counts.get(m)
            if v is None:
                c["partial"].add(m)
            else:
                c["counts"][m] = c["counts"].get(m, 0) + v
    return {
        "countries": {
            iso: {"name": c["name"], "counts": c["counts"], "partial": sorted(c["partial"])}
            for iso, c in by_country.items()
        },
        "regions": selection.unit_count,
        "unsupported": selection.countries_unsupported,
        "method": selection.method,
    }


def people_by_country(question: Question) -> dict:
    """``group_selection`` for the question, kept with the data (a selection resolves hundreds of regions)."""
    key = (
        "market:people:" + hashlib.sha1(f"{response_cache.data_version()}|{question.cache_key()}".encode()).hexdigest()
    )
    got = cache.get(key)
    if got is not None:
        return got
    out = group_selection(question.select(extra_counts=market.count_measures()))
    cache.set(key, out, response_cache.TTL_SECONDS)
    return out


def for_model(found: dict, model: str) -> dict:
    """One model's people per country from ``people_by_country``/``group_selection``, in ``market.size``'s shape."""
    measure = market.UNITS[model].measure
    return {
        iso: {"name": c["name"], "units": c["counts"].get(measure), "partial": measure in c["partial"]}
        for iso, c in found["countries"].items()
    }
