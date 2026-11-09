"""The place layer: each model's location inputs, from the most local figure Labs holds.

A model's location inputs (``block == "location"`` in its spec) describe the
place it runs in: how many children die of diarrhoea, how many already get ORS.
Each opportunity is valued with the figures for where its visits actually
happened. For every input and every area:

1. The area's own figure, if Labs has one (a district, else its state or
   province). ``indicators.resolve.BulkResolver`` walks up from the area to the
   nearest ancestor that has the measure, and says where it was measured.
2. Else the model's own figure for the country (the spec's ``places``), which is
   what the published per-country results were run at.

The recipes follow the models' own method for a subnational place, as their North
West Nigeria scenario does (connect-cea-models ``build_chc_model.py``): survey
coverage for the zone, and the country's diarrhoea death rate scaled by the
zone's child mortality.

Inputs with no recipe (CHC's worm burden and rotavirus gain, for example) always
take the country figure and say so. KC has no location inputs at all: its risk is
the model's 13% for enrolled small babies, not scaled by place (SM, 30 Oct 2026).
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.impact import engine
from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import IndicatorValue
from connect_labs.labs.indicators.resolve import BulkResolver, Resolved

LEVEL_NAMES = {0: "country", 1: "state or province", 2: "district"}


@dataclass
class Value:
    """One resolved location input, with where it came from."""

    key: str
    value: float
    level: str  # "district", "state or province", "country" (Labs) or "model country figure"
    measured_at: str  # the area's name
    source: str  # e.g. "dhs 2018", "igme_subnational 2021; country rate scaled", "connect-cea-models"
    how: str  # one line: how the number was made
    country_value: float | None  # the model's own figure for the country, to compare

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "level": self.level,
            "measured_at": self.measured_at,
            "source": self.source,
            "how": self.how,
            "country_value": self.country_value,
        }


def _local(r: Resolved | None) -> Resolved | None:
    """Only a figure measured below the country counts as local.

    At country level the model's own figure wins, so an opportunity with nothing
    more local is valued exactly as the published per-country result is.
    """
    if r is None:
        return None
    b = r.measured_at or r.boundary
    return r if b.admin_level > 0 else None


def _level(r: Resolved) -> tuple[str, str]:
    b = r.measured_at or r.boundary
    return LEVEL_NAMES.get(b.admin_level, f"ADM{b.admin_level}"), b.name


def _src(r: Resolved) -> str:
    return f"{r.source} {r.year}"


def _pct(indicator: str):
    """A survey percentage, as the share the model takes."""

    def recipe(ctx, key):
        r = _local(ctx.get(indicator))
        if r is None:
            return None
        lvl, where = _level(r)
        return Value(key, r.value / 100.0, lvl, where, _src(r), f"Labs {indicator}, as a share", None)

    return recipe


def _direct(indicator: str):
    def recipe(ctx, key):
        r = _local(ctx.get(indicator))
        if r is None:
            return None
        lvl, where = _level(r)
        return Value(key, r.value, lvl, where, _src(r), f"Labs {indicator}", None)

    return recipe


def _u5_ratio(ctx) -> tuple[float, Resolved] | None:
    """The area's under-five mortality over the country's, from the SAME source.

    Two sources disagree by more than the subnational differences being measured,
    so a ratio across sources would be noise. If the area's figure is the
    country's own (inherited), the ratio is 1 and the recipe says country.
    """
    area = ctx.get("u5mr")
    if area is None or (area.measured_at and area.measured_at.admin_level == 0):
        return None
    nat = ctx.country_row("u5mr", area.source, area.year)
    if nat is None or not nat.value:
        return None
    return area.value / nat.value, area


def _scaled_deaths(ctx, key):
    """CHC ``deaths``: the country's diarrhoea death rate x the area's child mortality / the country's."""
    got = _u5_ratio(ctx)
    country = ctx.country_inputs.get(key)
    if got is None or country is None:
        return None
    ratio, area = got
    lvl, where = _level(area)
    return Value(
        key,
        country * ratio,
        lvl,
        where,
        f"{_src(area)} (child mortality)",
        f"the country's rate x the area's under-five mortality / the country's ({ratio:.2f})",
        None,
    )


def _mort659(ctx, key):
    """CHC ``mort659``: from the area's under-five and infant mortality, by the model's own formula.

    1 - (1 - (U5MR - IMR) / (1000 - IMR)) ^ (1/4), as the input's own note says.
    Both must be measured for the same area by the same source; otherwise the
    country figure is scaled by the area's under-five mortality.
    """
    u5, im = ctx.get("u5mr"), ctx.get("imr")
    if (
        u5
        and im
        and u5.measured_at
        and im.measured_at
        and u5.measured_at.pk == im.measured_at.pk
        and u5.measured_at.admin_level > 0
        and u5.source == im.source
    ):
        v = 1 - (1 - (u5.value - im.value) / (1000 - im.value)) ** 0.25
        lvl, where = _level(u5)
        return Value(key, v, lvl, where, _src(u5), "from the area's under-five and infant mortality", None)
    got = _u5_ratio(ctx)
    country = ctx.country_inputs.get(key)
    if got is None or country is None:
        return None
    ratio, area = got
    lvl, where = _level(area)
    return Value(
        key,
        country * ratio,
        lvl,
        where,
        f"{_src(area)} (child mortality)",
        f"the country's rate x the area's under-five mortality / the country's ({ratio:.2f})",
        None,
    )


RECIPES = {
    "chc": {
        "ors": _pct("ors_coverage"),
        "vas": _pct("vitamin_a_coverage"),
        "dw_cov": _pct("deworming_coverage"),
        "deaths": _scaled_deaths,
        "mort659": _mort659,
    },
    "mbw": {
        "nmr": _direct("nmr"),
        "imr": _direct("imr"),
        "ebf": _pct("exclusive_breastfeeding"),
    },
    "kc": {},
}

#: Every Labs measure a recipe reads, so one BulkResolver load covers them all.
MEASURES = (
    "ors_coverage",
    "vitamin_a_coverage",
    "deworming_coverage",
    "u5mr",
    "imr",
    "nmr",
    "exclusive_breastfeeding",
)


class _Ctx:
    """What a recipe sees for one area."""

    def __init__(self, resolver: BulkResolver, boundary: AdminBoundary, country_inputs: dict, country_rows: dict):
        self._resolver, self._boundary = resolver, boundary
        self.country_inputs = country_inputs
        self._country_rows = country_rows

    def get(self, indicator: str) -> Resolved | None:
        try:
            return self._resolver.get(indicator, self._boundary)
        except KeyError:  # a measure this database has not registered yet
            return None

    def country_row(self, indicator: str, source: str, year: int):
        rows = [r for r in self._country_rows.get(indicator, ()) if r.source == source]
        if not rows:
            return None
        return min(rows, key=lambda r: (abs(r.year - year), -r.year))


class Baselines:
    """Resolve location inputs for many areas of one or more countries, in a bounded number of queries."""

    def __init__(self, boundaries: list[AdminBoundary]):
        self._resolver = BulkResolver(boundaries) if boundaries else None
        self._country_rows: dict[str, dict[str, list]] = {}
        if not boundaries:  # country figures only: no database needed
            return
        isos = {b.iso_code for b in boundaries}
        adm0 = list(boundary_set.owned().filter(iso_code__in=isos, admin_level=0))
        for row in IndicatorValue.objects.filter(boundary__in=adm0, indicator__in=("u5mr",)).select_related(
            "boundary"
        ):
            self._country_rows.setdefault(row.boundary.iso_code, {}).setdefault(row.indicator, []).append(row)

    def for_area(self, model: str, iso3: str, boundary: AdminBoundary | None) -> dict[str, Value]:
        """Every location input of ``model`` for one area (or the country, when ``boundary`` is None)."""
        place = engine.country(model, iso3) or {"inputs": {}}
        country_inputs = place["inputs"]
        out: dict[str, Value] = {}
        ctx = (
            _Ctx(self._resolver, boundary, country_inputs, self._country_rows.get(iso3, {}))
            if (boundary is not None and self._resolver is not None)
            else None
        )
        for key in engine.location_keys(model):
            country = country_inputs.get(key)
            v = None
            recipe = RECIPES.get(model, {}).get(key)
            if ctx is not None and recipe is not None:
                v = recipe(ctx, key)
            if v is None:
                v = Value(
                    key,
                    country if country is not None else engine.defaults(model)[key],
                    "model country figure" if country is not None else "model default",
                    place.get("name") or iso3,
                    "connect-cea-models",
                    (
                        "the model's figure for the country"
                        if recipe is not None
                        else "the model's figure for the country (Labs has no area figure for this input)"
                    ),
                    None,
                )
            v.country_value = country
            out[key] = v
        return out
