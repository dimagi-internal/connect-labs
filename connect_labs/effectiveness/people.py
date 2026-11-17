"""People per place in the places the baseline selects: the count every market is sized from.

Market size (the page and the ``cea_market_size`` tool) asks the baseline one question, *which places*, and needs back
how many people each model serves in each place (children under 5 for CHC, low-birthweight births for KC, births for
MBW), and how risky each place is (newborn mortality, for the models whose risk scales to the place). This module is
that seam: it reads the baseline through its public ``Question`` and hands the pure sizing code (``market``) plain
dictionaries.

A place is a country or, at the ``territory`` grain, a state or province: the same selection, one entry per region.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from django.core.cache import cache

from connect_labs.effectiveness import engine, market
from connect_labs.effectiveness.baseline import Baselines
from connect_labs.effectiveness.scenario import EVERYWHERE, Scenario
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import measures, response_cache
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.query import Question, float_param, indicator_param

#: The burden indicators the market page offers for choosing places (higher means more need).
TARGET_INDICATORS = ("u5mr", "nmr", "diarrhoea_prevalence")
#: "Everywhere with data" (``scenario.EVERYWHERE``): the indicator that has values in the most countries, with no
#: threshold, so the market is every place the baseline can count people in rather than only the highest-burden ones.
EVERYWHERE_INDICATOR = "diarrhoea_prevalence"


def question_for(scenario: Scenario) -> tuple[Question, bool]:
    """The baseline question a scenario's places ask, and whether it means everywhere (``indicator=all``).

    The baseline reads a question from ``request.GET``; a scenario hands it the same keys, so there is one reader."""
    ask = SimpleNamespace(GET=scenario.place_params())
    if scenario.everywhere:
        return Question.from_request(ask, indicator=EVERYWHERE_INDICATOR, threshold=0.0), True
    indicator = indicator_param(ask)
    threshold = float_param(ask, "threshold", measures.get(indicator).threshold_default)
    return Question.from_request(ask, indicator=indicator, threshold=threshold), False


def indicator_choices() -> list[dict]:
    """What a page offers for choosing places: everywhere, then each burden indicator with its usual threshold."""
    return [{"key": EVERYWHERE, "label": "Everywhere with data", "threshold": None}] + [
        {"key": k, "label": measures.get(k).label, "threshold": measures.get(k).threshold_default}
        for k in TARGET_INDICATORS
        if k in measures.MEASURES
    ]


def africa_total() -> int:
    return len(ISO_CODES)


def describe(question: Question, found: dict, everywhere: bool) -> dict:
    """How the places were chosen and what came back, for a page to say beside the numbers."""
    return {
        "indicator": question.indicator,
        "indicator_label": measures.get(question.indicator).label,
        "threshold": question.threshold,
        "method": found["method"],
        "grain": found["grain"],
        "places": len(found["places"]),
        "countries": len({p["iso3"] for p in found["places"].values()}),
        "africa_total": africa_total(),
        "everywhere": everywhere,
        "regions": found["regions"],
        "unsupported": found["unsupported"],
    }


def group_selection(selection, grain: str = "country") -> dict:
    """A baseline selection as people per place, for every model's count, with what it could not count.

    At the ``country`` grain a place is a country and its regions are added together; at ``territory`` each region is
    a place. ``partial`` names the counts a place is missing for some of its areas, so its total is a floor.
    ``boundaries`` (territory grain only, dropped before the answer is kept) are the regions' own boundaries.
    """
    places: dict[str, dict] = {}
    boundaries: dict[str, object] = {}
    for area in selection.areas:
        key = str(area.boundary.pk) if grain == "territory" else area.iso_code
        c = places.setdefault(
            key,
            {
                "name": area.name if grain == "territory" else area.country_name,
                "country": area.country_name,
                "iso3": area.iso_code,
                "counts": {},
                "partial": set(),
            },
        )
        if grain == "territory":
            boundaries[key] = area.boundary
        for m in market.count_measures():
            v = area.counts.get(m)
            if v is None:
                c["partial"].add(m)
            else:
                c["counts"][m] = c["counts"].get(m, 0) + v
    return {
        "places": {k: {**c, "partial": sorted(c["partial"])} for k, c in places.items()},
        "boundaries": boundaries,
        "regions": selection.unit_count,
        "unsupported": selection.countries_unsupported,
        "method": selection.method,
        "grain": grain,
    }


def attach_risk(found: dict) -> dict:
    """Put each place's own figures on it, for the models whose answer depends on the place.

    ``measures``: the place's newborn mortality, and the country's beside it (so a figure far from its country's can be
    flagged and ``trust`` has something to pull toward). A country's is the country's own estimate; a territory's is
    its own figure where Labs has one, else the country's scaled by how the territory's under-five mortality compares
    (``Baselines.newborn_mortality``). A place with no figure is left without one and is sized at the model's own risk.

    ``inputs`` (territories only): for each model, the place inputs the baseline holds for the territory itself, by the
    same recipes Implementations value a delivery with (``Baselines.for_area``): survey coverage for the territory, the
    country's death rates scaled by its mortality. An input with nothing local is left out, so the model's country
    figure stands.
    """
    isos = sorted({p["iso3"] for p in found["places"].values()})
    if not isos:
        return found
    territory = found["grain"] == "territory"
    if territory:
        baselines = Baselines(list(found["boundaries"].values()))
    else:
        baselines = Baselines(list(boundary_set.owned().filter(iso_code__in=isos, admin_level=0)))
    country = {iso: baselines.newborn_mortality(iso, None) for iso in isos}
    for key, p in found["places"].items():
        boundary = found["boundaries"].get(key)
        got = baselines.newborn_mortality(p["iso3"], boundary)
        p["measures"], p["measures_how"] = {}, {}
        if got:
            p["measures"]["nmr"], p["measures_how"]["nmr"] = got
        if country[p["iso3"]]:
            p["measures"]["nmr_country"] = country[p["iso3"]][0]
        if territory:
            local = {}
            for model in engine.MODELS:
                values = baselines.for_area(model, p["iso3"], boundary)
                own = {k: v.value for k, v in values.items() if v.level not in LABS_NOT_LOCAL}
                if own:
                    local[model] = own
            p["inputs"] = local
    return found


#: Where a ``Baselines`` value says it came from when it is the model's own figure, not the place's: nothing local.
LABS_NOT_LOCAL = ("model country figure", "model default")


def people_by_place(question: Question, grain: str = "country") -> dict:
    """``group_selection`` for the question with each place's risk, kept with the data (a selection resolves hundreds
    of regions, and a territory grain resolves every one of them)."""
    key = (
        "market:people:v3:"
        + hashlib.sha1(f"{response_cache.data_version()}|{grain}|{question.cache_key()}".encode()).hexdigest()
    )
    got = cache.get(key)
    if got is not None:
        return got
    out = attach_risk(group_selection(question.select(extra_counts=market.count_measures()), grain))
    out.pop("boundaries", None)
    cache.set(key, out, response_cache.TTL_SECONDS)
    return out


def for_model(found: dict, model: str) -> dict:
    """One model's people per place from ``people_by_place``/``group_selection``, in ``market.size``'s shape."""
    measure = market.UNITS[model].measure
    return {
        key: {
            "name": p["name"],
            "country": p["country"],
            "iso3": p["iso3"],
            "units": p["counts"].get(measure),
            "partial": measure in p["partial"],
            "measures": p.get("measures", {}),
            "measures_how": p.get("measures_how", {}),
            "inputs": p.get("inputs", {}).get(model, {}),
        }
        for key, p in found["places"].items()
    }
