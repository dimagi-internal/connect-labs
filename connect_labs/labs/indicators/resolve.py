"""Resolution, aggregation, and threshold selection.

Three jobs, in dependency order:

  ``resolve()``       one indicator, one boundary → a value plus where it came
                      from, walking up the hierarchy for measures that inherit.
  ``aggregate()``     many values → one, by the rule the measure registry
                      declares. Counts sum; rates take their declared weighted
                      mean. This is the only place aggregation happens.
  ``select_above()``  the threshold query, rolled up to the coarsest unit that
                      is honestly describable.

Nothing here writes. Inheritance in particular is resolved on read and never
materialised, so re-running an ingest cannot leave stale copies fanned out
across child boundaries.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import measures
from connect_labs.labs.indicators.africa import name_for
from connect_labs.labs.indicators.models import IndicatorValue

logger = logging.getLogger(__name__)

#: When several sources carry the same cell, prefer them in this order.
#: Subnational survey data beats a national model applied downward.
DEFAULT_SOURCE_ORDER = ("dhs", "hapi", "worldpop", "igme", "derived")


@dataclass
class Resolved:
    """One indicator value, with the provenance needed to explain it."""

    indicator: str
    boundary: AdminBoundary
    value: float
    year: int
    source: str
    source_ref: str
    license_code: str
    method: str = ""
    ci_low: float | None = None
    ci_high: float | None = None
    #: The boundary the number was actually measured on. Differs from
    #: ``boundary`` when the value was inherited from a coarser ancestor.
    measured_at: AdminBoundary | None = None

    @property
    def inherited(self) -> bool:
        return self.measured_at is not None and self.measured_at.pk != self.boundary.pk

    @property
    def provenance(self) -> str:
        """One line fit for a table cell or a tooltip."""
        base = f"{self.source_ref or self.source} ({self.year})"
        if self.inherited:
            lvl = f"ADM{self.measured_at.admin_level}"
            return f"{base} — measured at {self.measured_at.name} [{lvl}], applied here"
        return base


def ancestors(boundary: AdminBoundary) -> list[AdminBoundary]:
    """Boundaries above this one, nearest first.

    Prefers the explicit ``parent_boundary_id`` chain. Where a loader did not
    populate it (geoBoundaries ADM1 does not), falls back to the country
    boundary for the same ISO — which is the case that actually matters, since
    the common inheritance is a national rate applied to regions.
    """
    out: list[AdminBoundary] = []
    seen: set[int] = {boundary.pk}
    cur = boundary

    while cur.parent_boundary_id:
        parent = AdminBoundary.objects.filter(source=cur.source, boundary_id=cur.parent_boundary_id).first()
        if parent is None or parent.pk in seen:
            break
        out.append(parent)
        seen.add(parent.pk)
        cur = parent

    # Fallback: ensure the country level is reachable even without a parent chain.
    if boundary.admin_level > 0 and not any(b.admin_level == 0 for b in out):
        country = AdminBoundary.objects.filter(iso_code=boundary.iso_code, admin_level=0).order_by("source").first()
        if country is not None and country.pk not in seen:
            out.append(country)

    return out


def _best_row(
    indicator: str,
    boundary: AdminBoundary,
    year: int | None,
    source_order: tuple[str, ...],
) -> IndicatorValue | None:
    """Best row for this exact boundary: preferred source, then nearest year."""
    rows = list(IndicatorValue.objects.filter(indicator=indicator, boundary=boundary))
    if not rows:
        return None

    def rank(r: IndicatorValue) -> tuple[int, int, int]:
        try:
            src = source_order.index(r.source)
        except ValueError:
            src = len(source_order)
        if year is None:
            return (src, 0, -r.year)
        # Prefer the most recent year at or before the requested one; fall back
        # to the nearest later year rather than returning nothing.
        if r.year <= year:
            return (src, 0, year - r.year)
        return (src, 1, r.year - year)

    return min(rows, key=rank)


def resolve(
    indicator: str,
    boundary: AdminBoundary,
    year: int | None = None,
    source_order: tuple[str, ...] = DEFAULT_SOURCE_ORDER,
) -> Resolved | None:
    """Resolve one indicator for one boundary, inheriting if the measure allows."""
    measure = measures.get(indicator)

    row = _best_row(indicator, boundary, year, source_order)
    measured_at = boundary

    if row is None and measure.downscale:
        for anc in ancestors(boundary):
            row = _best_row(indicator, anc, year, source_order)
            if row is not None:
                measured_at = anc
                break

    if row is None:
        return None

    return Resolved(
        indicator=indicator,
        boundary=boundary,
        value=row.value,
        year=row.year,
        source=row.source,
        source_ref=row.source_ref,
        license_code=row.license_code,
        method=row.method,
        ci_low=row.ci_low,
        ci_high=row.ci_high,
        measured_at=measured_at,
    )


class BulkResolver:
    """Resolve many boundaries at once, without a query per boundary.

    ``resolve()`` is fine for one lookup and ruinous for seven hundred — the map
    and the threshold query both touch every ADM1 unit in Africa across four
    indicators. This loads the relevant values in one query per indicator, indexes
    them in memory, and applies the same rules ``resolve()`` does, including
    inheritance from the country level.

    Inheritance here walks only to ADM0 rather than the full parent chain. At the
    levels this system uses (ADM0 and ADM1) that is the entire chain; if deeper
    levels are ever added, this needs to grow with them.
    """

    def __init__(
        self,
        boundaries: list[AdminBoundary],
        year: int | None = None,
        source_order: tuple[str, ...] = DEFAULT_SOURCE_ORDER,
    ):
        self.boundaries = boundaries
        self.year = year
        self.source_order = source_order
        self._cache: dict[str, dict[int, Resolved]] = {}

        isos = {b.iso_code for b in boundaries}
        self._adm0: dict[str, AdminBoundary] = {}
        for b in AdminBoundary.objects.filter(iso_code__in=isos, admin_level=0).order_by("source"):
            self._adm0.setdefault(b.iso_code, b)

        # Country boundaries must be resolvable as inheritance targets even when
        # they are not themselves in the requested set.
        self._all_pks = {b.pk for b in boundaries} | {b.pk for b in self._adm0.values()}
        self._by_pk = {b.pk: b for b in boundaries}
        self._by_pk.update({b.pk: b for b in self._adm0.values()})

    def _rank(self, row: IndicatorValue) -> tuple[int, int, int]:
        try:
            src = self.source_order.index(row.source)
        except ValueError:
            src = len(self.source_order)
        if self.year is None:
            return (src, 0, -row.year)
        if row.year <= self.year:
            return (src, 0, self.year - row.year)
        return (src, 1, row.year - self.year)

    def _load(self, indicator: str) -> dict[int, Resolved]:
        best: dict[int, IndicatorValue] = {}
        for row in IndicatorValue.objects.filter(indicator=indicator, boundary_id__in=self._all_pks):
            cur = best.get(row.boundary_id)
            if cur is None or self._rank(row) < self._rank(cur):
                best[row.boundary_id] = row

        measure = measures.get(indicator)
        out: dict[int, Resolved] = {}

        for b in self.boundaries:
            row = best.get(b.pk)
            measured_at = b

            if row is None and measure.downscale:
                country = self._adm0.get(b.iso_code)
                if country is not None:
                    row = best.get(country.pk)
                    measured_at = country

            if row is None:
                continue

            out[b.pk] = Resolved(
                indicator=indicator,
                boundary=b,
                value=row.value,
                year=row.year,
                source=row.source,
                source_ref=row.source_ref,
                license_code=row.license_code,
                method=row.method,
                ci_low=row.ci_low,
                ci_high=row.ci_high,
                measured_at=measured_at,
            )
        return out

    def get(self, indicator: str, boundary: AdminBoundary) -> Resolved | None:
        if indicator not in self._cache:
            self._cache[indicator] = self._load(indicator)
        return self._cache[indicator].get(boundary.pk)

    def value(self, indicator: str, boundary: AdminBoundary, default: float = 0.0) -> float:
        r = self.get(indicator, boundary)
        return r.value if r else default


def aggregate(indicator: str, pairs: list[tuple[float, float | None]]) -> float | None:
    """Combine values into one, by the registry's rule for this measure.

    ``pairs`` is ``(value, weight)``; weight is ignored for counts and required
    for rates. A rate whose weights are all missing falls back to an unweighted
    mean and logs — a wrong-ish number beats a blank cell here, but it should be
    visible that it happened.
    """
    measure = measures.get(indicator)
    vals = [(v, w) for v, w in pairs if v is not None]
    if not vals:
        return None

    if measure.agg is measures.Agg.SUM:
        return sum(v for v, _ in vals)

    total_w = sum(w for _, w in vals if w)
    if not total_w:
        logger.warning("aggregate(%s): no weights available, falling back to unweighted mean", indicator)
        return sum(v for v, _ in vals) / len(vals)
    return sum(v * (w or 0) for v, w in vals) / total_w


# ---------------------------------------------------------------------------
# Threshold selection
# ---------------------------------------------------------------------------


@dataclass
class Area:
    """One row of a selection — a place that cleared the threshold."""

    boundary: AdminBoundary
    iso_code: str
    country_name: str
    name: str
    admin_level: int
    #: True when this row stands for a whole country whose every region cleared
    #: the threshold, rather than a single region.
    is_whole_country: bool = False
    #: Number of ADM1 units folded into this row (1 for a plain region).
    units_covered: int = 1
    values: dict[str, Resolved | None] = field(default_factory=dict)
    counts: dict[str, float] = field(default_factory=dict)

    def get(self, indicator: str) -> float | None:
        if indicator in self.counts:
            return self.counts[indicator]
        r = self.values.get(indicator)
        return r.value if r else None


@dataclass
class Selection:
    """The result of a threshold query, plus everything needed to explain it."""

    indicator: str
    threshold: float
    year: int | None
    areas: list[Area]
    totals: dict[str, float | None]
    countries_fully_above: list[str]
    countries_partly_above: list[str]
    skipped_no_data: list[str]

    @property
    def area_count(self) -> int:
        return len(self.areas)

    @property
    def unit_count(self) -> int:
        """ADM1-equivalent units represented, ignoring the rollup."""
        return sum(a.units_covered for a in self.areas)

    @property
    def country_count(self) -> int:
        return len({a.iso_code for a in self.areas})


#: Counts carried alongside the threshold indicator on every selection row.
CARRIED_COUNTS = ("births", "pop_u5", "pop_total")


def _country_name(iso: str, adm0: AdminBoundary | None) -> str:
    """Prefer the common name over the boundary file's formal one.

    geoBoundaries calls Nigeria "the Federal Republic of Nigeria", which is
    correct and useless in a table. The curated list wins where it has the
    country; the boundary name is the fallback.
    """
    curated = name_for(iso)
    if curated and curated != iso.upper():
        return curated
    return adm0.name if adm0 is not None else iso


def select_above(
    indicator: str = "u5mr",
    threshold: float = 80.0,
    year: int | None = None,
    iso_codes: list[str] | None = None,
    source_order: tuple[str, ...] = DEFAULT_SOURCE_ORDER,
) -> Selection:
    """Places where ``indicator`` exceeds ``threshold``, at the coarsest honest unit.

    The rollup rule: if *every* ADM1 unit in a country clears the threshold, the
    country is emitted as one row — saying "Niger" is both truer and more useful
    than listing its eight regions. If only some clear it, those regions are
    emitted individually. Countries with no ADM1 boundaries loaded are evaluated
    at ADM0.

    Counts on a rolled-up country row are summed from its qualifying regions, so
    a country total can never disagree with the regions beneath it.
    """
    measure = measures.get(indicator)

    qs = AdminBoundary.objects.filter(admin_level__in=(0, 1))
    if iso_codes:
        qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    boundaries = list(qs)

    bulk = BulkResolver(boundaries, year=year, source_order=source_order)

    by_iso: dict[str, dict[int, list[AdminBoundary]]] = defaultdict(lambda: defaultdict(list))
    for b in boundaries:
        by_iso[b.iso_code][b.admin_level].append(b)

    areas: list[Area] = []
    fully: list[str] = []
    partly: list[str] = []
    skipped: list[str] = []

    for iso in sorted(by_iso):
        adm0 = (by_iso[iso].get(0) or [None])[0]
        adm1s = by_iso[iso].get(1) or []
        cname = _country_name(iso, adm0)

        units = adm1s or ([adm0] if adm0 is not None else [])
        if not units:
            continue

        evaluated = [(b, r) for b in units if (r := bulk.get(indicator, b)) is not None]
        if not evaluated:
            skipped.append(cname)
            continue

        above = [(b, r) for b, r in evaluated if r.value > threshold]
        if not above:
            continue

        # A country is only rolled up when it has real regions and all of them
        # qualify — a single-region country would otherwise be relabelled as a
        # whole-country row, which reads as a much stronger claim than it is.
        rolled_up = bool(adm1s) and len(above) == len(evaluated) and len(evaluated) > 1

        if rolled_up:
            fully.append(cname)
            area = Area(
                boundary=adm0 or above[0][0],
                iso_code=iso,
                country_name=cname,
                name=cname,
                admin_level=0,
                is_whole_country=True,
                units_covered=len(above),
                values={indicator: _rollup_rate(indicator, above, bulk)},
            )
            for c in CARRIED_COUNTS:
                area.counts[c] = sum(bulk.value(c, b) for b, _ in above)
            areas.append(area)
        else:
            (partly if len(above) < len(evaluated) else fully).append(cname)
            for b, r in above:
                area = Area(
                    boundary=b,
                    iso_code=iso,
                    country_name=cname,
                    name=b.name,
                    admin_level=b.admin_level,
                    values={indicator: r},
                )
                for c in CARRIED_COUNTS:
                    area.counts[c] = bulk.value(c, b)
                areas.append(area)

    totals: dict[str, float | None] = {c: (sum(a.counts.get(c) or 0.0 for a in areas) or None) for c in CARRIED_COUNTS}
    totals[indicator] = aggregate(
        indicator,
        [
            (a.values[indicator].value, a.counts.get(measure.weight_by or "", 0.0))
            for a in areas
            if a.values.get(indicator)
        ],
    )

    areas.sort(key=lambda a: (-(a.counts.get("births") or 0.0), a.country_name, a.name))

    return Selection(
        indicator=indicator,
        threshold=threshold,
        year=year,
        areas=areas,
        totals=totals,
        countries_fully_above=sorted(set(fully)),
        countries_partly_above=sorted(set(partly)),
        skipped_no_data=sorted(set(skipped)),
    )


def _rollup_rate(
    indicator: str,
    pairs: list[tuple[AdminBoundary, Resolved]],
    bulk: BulkResolver,
) -> Resolved | None:
    """Weighted-mean a rate across regions, keeping the provenance of its inputs."""
    measure = measures.get(indicator)
    weighted = [(r.value, bulk.value(measure.weight_by, b) if measure.weight_by else None) for b, r in pairs]

    value = aggregate(indicator, weighted)
    if value is None:
        return None

    first = pairs[0][1]
    sources = sorted({r.source for _, r in pairs})
    return Resolved(
        indicator=indicator,
        boundary=pairs[0][0],
        value=value,
        year=max(r.year for _, r in pairs),
        source="+".join(sources),
        source_ref=f"weighted mean of {len(pairs)} regions",
        license_code=first.license_code,
        method=f"mean over {len(pairs)} ADM1 units, weighted by {measure.weight_by}",
        measured_at=None,
    )
