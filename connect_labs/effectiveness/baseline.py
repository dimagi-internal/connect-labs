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
take the country figure and say so. KC has no location inputs at all: the model's risk is
13% for enrolled small babies in every place (SM, 30 Oct 2026). Sizing a market scales it to the place anyway, as a
labelled Labs reading (``market.PLACE_RISK``), from the newborn mortality ``Baselines.newborn_mortality`` returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from connect_labs.effectiveness import engine, mortality
from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators.models import IndicatorValue
from connect_labs.labs.indicators.resolve import BulkResolver, Resolved
from connect_labs.labs.indicators.sources import derive

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


def _se(r: Resolved) -> float | None:
    """Standard error of ln(value) for a resolved figure, from the interval it was published with."""
    return mortality.se_ln(r.ci_low, r.ci_high, (r.extra or {}).get("interval_level", 0.95))


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


#: Area mortality series, and the national series each is levelled to. IGME's
#: subnational model and DHS "re-levelled" (``dhs_calibrated``) are both expressed
#: relative to IGME's national estimate, so an area's ratio is taken against that.
NATIONAL_FOR = {"igme_subnational": "igme", "dhs_calibrated": "igme", "igme": "igme"}


def _ratio(ctx, indicator: str) -> tuple[float, Resolved] | None:
    """The area's figure over the country's, from the same series, nearest year.

    Two series can disagree by more than the subnational differences being
    measured, so a ratio across series would be noise. None when the area has no
    figure of its own, or no national figure in its series.
    """
    area = _local(ctx.get(indicator))
    if area is None:
        return None
    nat = ctx.country_row(indicator, NATIONAL_FOR.get(area.source, area.source), area.year)
    if nat is None or not nat.value:
        return None
    return area.value / nat.value, area


def _scaled(indicator: str, what: str):
    """The model's country figure x the area's ``indicator`` / the country's, as the models do subnationally.

    This keeps every area on the same footing as the published national result:
    the model's own source and year for the level, the area's survey or model
    estimate only for how it differs from the country.
    """

    def recipe(ctx, key):
        got = _ratio(ctx, indicator)
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
            f"{_src(area)} ({indicator})",
            f"the country's {what} x the area's {indicator} / the country's ({ratio:.2f})",
            None,
        )

    return recipe


RECIPES = {
    "chc": {
        "ors": _pct("ors_coverage"),
        "vas": _pct("vitamin_a_coverage"),
        "dw_cov": _pct("deworming_coverage"),
        # The models' North West Nigeria method: the country's diarrhoea death rate,
        # and its child death rate, scaled by the area's under-five mortality.
        "deaths": _scaled("u5mr", "diarrhoea death rate"),
        "mort659": _scaled("u5mr", "child death rate"),
    },
    "mbw": {
        "nmr": _scaled("nmr", "newborn death rate"),
        "imr": _scaled("imr", "infant death rate"),
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


#: Every indicator this layer reads from the baseline, so a rebuild of the baseline can be checked against what the
#: sizing needs (``tests/test_baseline_reads.py``: each is registered and policy-governed, and none is read that is not
#: listed). Which loader stage writes each: u5mr, imr, nmr in ``mortality``; the coverage and survey shares in
#: ``child_health``; births and births_wpp in ``births``; conflict_rate in ``conflict``.
MEASURES_READ = (
    "u5mr",
    "imr",
    "nmr",
    "conflict_rate",
    "births",
    "births_wpp",
    "ors_coverage",
    "vitamin_a_coverage",
    "deworming_coverage",
    "exclusive_breastfeeding",
)

#: The country-level series ``Baselines`` prefetches for a whole country in one query.
COUNTRY_MEASURES = ("u5mr", "imr", "nmr", "conflict_rate")


@dataclass
class _Row:
    source: str
    year: int
    value: float
    ci_low: float | None = None
    ci_high: float | None = None
    #: The coverage of the published interval; surveys publish 95%, IGME 90% (its loaders say so in ``extra``).
    level: float = 0.95


class Nmr(NamedTuple):
    """A place's neonatal mortality (per 1,000 live births) with how it was made and how sure it is."""

    value: float
    how: str
    #: Standard error of ln(value) from the published interval, None when none was kept.
    se: float | None = None
    #: The part of ``se`` every territory of the country shares (the national estimate's), None for a national figure.
    shared_se: float | None = None
    #: "own" (measured below the country), "ratio" (the country's scaled by the area's under-five mortality)
    #: or "country".
    kind: str = "country"


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
        # Nearest year to the area's, so a ratio compares like with like.
        if not rows:
            return None
        return min(rows, key=lambda r: (abs(r.year - year), -r.year))


class Baselines:
    """Resolve location inputs for many areas of one or more countries, in a bounded number of queries."""

    def __init__(self, boundaries: list[AdminBoundary]):
        self._resolver = BulkResolver(boundaries) if boundaries else None
        self._isos = sorted({b.iso_code for b in boundaries})
        self._births_audit: dict[str, dict] | None = None
        self._country_rows: dict[str, dict[str, list]] = {}
        if not boundaries:  # country figures only: no database needed
            return
        isos = {b.iso_code for b in boundaries}
        # Only the numbers: a boundary row carries its whole geometry, and a country's
        # mortality series is ~35 years x 3 measures (loading the boundary with each
        # row ran a production task out of memory, 2026-11-09).
        adm0 = boundary_set.owned().filter(iso_code__in=isos, admin_level=0).values("pk")
        for iso, ind, src, year, value, low, high, extra in IndicatorValue.objects.filter(
            boundary__in=adm0, indicator__in=COUNTRY_MEASURES
        ).values_list("iso_code", "indicator", "source", "year", "value", "ci_low", "ci_high", "extra"):
            self._country_rows.setdefault(iso, {}).setdefault(ind, []).append(
                _Row(src, year, value, low, high, (extra or {}).get("interval_level", 0.95))
            )

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

    def births_check(self, iso3: str) -> dict | None:
        """Our births for a country beside the UN's (``derive.births_audit``): ``{ratio, ours, wpp, off}``, or None
        when either is missing. ``off`` says the country is more than the audit's tolerance from the UN figure."""
        if self._births_audit is None:
            self._births_audit = {a["iso"]: a for a in derive.births_audit(self._isos)} if self._isos else {}
        return self._births_audit.get(iso3)

    def conflict(self, iso3: str, boundary: AdminBoundary | None) -> tuple[float, str] | None:
        """Reported conflict deaths per 100,000 people a year (UCDP) for a place, and where it came from.

        A territory's own figure, or the country's for ``boundary`` None. None when Labs holds none (a place UCDP
        was not loaded for): never a zero, because nothing then says the place is quiet.
        """
        if boundary is not None and self._resolver is not None:
            ctx = _Ctx(self._resolver, boundary, {}, self._country_rows.get(iso3, {}))
            own = _local(ctx.get("conflict_rate"))
            return (own.value, f"{_level(own)[1]}, {_src(own)}") if own else None
        rows = self._country_rows.get(iso3, {}).get("conflict_rate", [])
        latest = max(rows, key=lambda r: r.year) if rows else None
        return (latest.value, f"the country's, {latest.source} {latest.year}") if latest else None

    def newborn_mortality(self, iso3: str, boundary: AdminBoundary | None) -> Nmr | None:
        """Neonatal deaths per 1,000 live births for a place, how the figure was made, and how sure it is. None when
        there is none.

        The place's own figure when Labs has one below the country; else the country's IGME figure scaled by how the
        place's under-five mortality compares with the country's (the models' own recipe for a subnational place, as
        ``RECIPES["mbw"]["nmr"]`` does); else the country's. ``boundary`` is None for the country as a whole. The
        uncertainty is what the published interval says (``mortality.se_ln``), None where Labs kept none.
        """
        rows = self._country_rows.get(iso3, {}).get("nmr", [])
        igme = [r for r in rows if r.source == "igme"] or rows
        latest = max(igme, key=lambda r: r.year) if igme else None
        national = latest.value if latest else None
        national_se = mortality.se_ln(latest.ci_low, latest.ci_high, latest.level) if latest else None
        if boundary is not None and self._resolver is not None:
            ctx = _Ctx(self._resolver, boundary, {}, self._country_rows.get(iso3, {}))
            own = _local(ctx.get("nmr"))
            if own is not None:
                lvl, where = _level(own)
                return Nmr(own.value, f"{where} ({lvl}), {_src(own)}", _se(own), national_se, "own")
            got = _ratio(ctx, "u5mr") if national is not None else None
            if got is not None:
                ratio, area = got
                return Nmr(
                    national * ratio,
                    f"the country's x the area's under-five mortality / the country's ({ratio:.2f})",
                    mortality.combine(_se(area), national_se),
                    national_se,
                    "ratio",
                )
        return Nmr(national, "the country's IGME estimate", national_se) if national is not None else None
