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
from connect_labs.labs.indicators import boundaries as boundary_set
from connect_labs.labs.indicators import measures, methods, policy
from connect_labs.labs.indicators.africa import name_for
from connect_labs.labs.indicators.models import SMALL_SAMPLE_UNWEIGHTED, IndicatorValue

logger = logging.getLogger(__name__)


def project_count(resolved: Resolved, to_year: int | None, growth_pct: float | None) -> float:
    """Carry a count forward to the year a programme actually runs.

    A count is measured in the year it was measured; a programme runs later.
    Answering a 2027 question with a 2022 population is not conservative, it is
    a different question, and the difference compounds — Liberia grows at 2.1% a
    year, so five years is 11%.

    Only counts are projected. A rate has no growth series behind it and
    inventing one would be worse than leaving it as of its own year, which is
    what the row already says.

    Without a growth rate, or without a target, the value passes through
    unchanged. Silence beats a guessed rate.
    """
    if to_year is None or growth_pct is None or resolved.year is None:
        return resolved.value
    years = to_year - resolved.year
    if years == 0:
        return resolved.value
    return resolved.value * (1.0 + growth_pct / 100.0) ** years


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
    source_url: str = ""
    method: str = ""
    ci_low: float | None = None
    ci_high: float | None = None
    #: The boundary the number was actually measured on. Differs from
    #: ``boundary`` when the value was inherited from a coarser ancestor.
    measured_at: AdminBoundary | None = None
    extra: dict = field(default_factory=dict)

    @property
    def measured_year(self) -> int:
        """The year the underlying measurement was taken.

        Differs from ``year`` for a re-levelled survey, which is stamped with the
        year it now describes. A reader deciding whether to trust a number wants
        the year of the survey, not the year of the arithmetic.
        """
        return self.extra.get("raw_year") or self.year

    @property
    def adjusted(self) -> bool:
        return "factor" in self.extra

    @property
    def inherited(self) -> bool:
        return self.measured_at is not None and self.measured_at.pk != self.boundary.pk

    @property
    def sample_unweighted(self) -> int | None:
        """Unweighted cases behind a survey estimate, where the source says."""
        return (self.extra or {}).get("sample_unweighted")

    @property
    def small_sample(self) -> bool:
        """True when the source itself would flag this figure as unreliable.

        Bomi's ORS coverage reads 75.5% and rests on 35 unweighted cases —
        21 once weighted. Presented in a column beside a figure resting on
        three hundred, it looks like the same kind of number and is not. DHS
        marks these; carrying the mark costs one field on the request.
        """
        n = self.sample_unweighted
        return n is not None and n < SMALL_SAMPLE_UNWEIGHTED

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
        country = boundary_set.owned().filter(iso_code=boundary.iso_code, admin_level=0).first()
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

    order = policy.order_for(indicator, source_order)
    rows = [r for r in rows if r.source in order]
    if not rows:
        return None

    def rank(r: IndicatorValue) -> tuple[int, int, int]:
        src = order.index(r.source)
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
    source_order: tuple[str, ...] | None = None,
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
        source_url=row.source_url,
        method=row.method,
        ci_low=row.ci_low,
        ci_high=row.ci_high,
        measured_at=measured_at,
        extra=row.extra or {},
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
        source_order: tuple[str, ...] | None = None,
        lens_on: str | None = None,
    ):
        self.boundaries = boundaries
        self.year = year
        self.source_order = source_order
        # A method says how ONE indicator is measured. Narrowing the carried
        # counts by it too is a category error: births and population are
        # denominators with their own policy, and a mortality method has no
        # opinion about where a population figure comes from. Left unbounded,
        # asking for IGME's small-area model erased every birth count on the
        # map, because "derived" is not in that method's source list.
        self.lens_on = lens_on
        self._cache: dict[str, dict[int, Resolved]] = {}

        isos = {b.iso_code for b in boundaries}
        self._adm0: dict[str, AdminBoundary] = {}
        for b in boundary_set.owned().filter(iso_code__in=isos, admin_level=0):
            self._adm0.setdefault(b.iso_code, b)

        # Country boundaries must be resolvable as inheritance targets even when
        # they are not themselves in the requested set.
        self._all_pks = {b.pk for b in boundaries} | {b.pk for b in self._adm0.values()}
        self._by_pk = {b.pk: b for b in boundaries}
        self._by_pk.update({b.pk: b for b in self._adm0.values()})

    def _rank(self, row: IndicatorValue, order: tuple[str, ...]) -> tuple[int, int, int]:
        src = order.index(row.source)
        if self.year is None:
            return (src, 0, -row.year)
        if row.year <= self.year:
            return (src, 0, self.year - row.year)
        return (src, 1, row.year - self.year)

    def _load(self, indicator: str) -> dict[int, Resolved]:
        # Eligibility first, and it is a filter rather than a last-place rank:
        # a source this indicator does not name is not a worse answer, it is
        # not an answer. See policy.py.
        lens = self.source_order if self.lens_on in (None, indicator) else None
        order = policy.order_for(indicator, lens)
        best: dict[int, IndicatorValue] = {}
        if order:
            rows = IndicatorValue.objects.filter(indicator=indicator, boundary_id__in=self._all_pks, source__in=order)
            for row in rows:
                cur = best.get(row.boundary_id)
                if cur is None or self._rank(row, order) < self._rank(cur, order):
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
                source_url=row.source_url,
                method=row.method,
                ci_low=row.ci_low,
                ci_high=row.ci_high,
                measured_at=measured_at,
                extra=row.extra or {},
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
    #: A count is ``None`` when no estimate exists — never 0. A missing births
    #: figure rendered as "0" reads as "nobody is born here" rather than "we
    #: could not work it out", and quietly drags a continental total down.
    counts: dict[str, float | None] = field(default_factory=dict)
    #: Per count measure, (units contributing a value, units in this row). Lets
    #: a caller say how much of a rolled-up row is actually covered.
    coverage: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Units in this row whose rate was measured somewhere coarser and applied
    #: here. Recorded at construction because a rolled-up country row averages
    #: its regions away — by the time the row exists, which of them borrowed
    #: their country's figure is no longer recoverable from it.
    inherited_units: int = 0
    #: Units whose rate the source itself flags as resting on too few cases.
    #: Same reason for recording it here: a country row averages the flag away.
    small_sample_units: int = 0

    def get(self, indicator: str) -> float | None:
        if indicator in self.counts:
            return self.counts[indicator]
        r = self.values.get(indicator)
        return r.value if r else None

    def is_complete(self, indicator: str) -> bool:
        got, total = self.coverage.get(indicator, (0, 0))
        return total > 0 and got == total


@dataclass
class Selection:
    """The result of a threshold query, plus everything needed to explain it."""

    indicator: str
    threshold: float
    year: int | None
    areas: list[Area]
    totals: dict[str, float | None]
    #: Per count measure, (units with a value, units selected). When these
    #: differ the total is a floor, not a measurement, and callers must say so.
    coverage: dict[str, tuple[int, int]]
    countries_fully_above: list[str]
    countries_partly_above: list[str]
    skipped_no_data: list[str]
    #: The method this selection was produced with, and the countries it could
    #: not answer for. Never silently answered at another resolution — a region
    #: compared against a whole country is not a comparison.
    method: str = ""
    resolution: str = ""
    countries_unsupported: list[str] = field(default_factory=list)
    #: The year every count was carried to, if one was asked for. A count in
    #: this selection is then a projection, not a measurement, and the surface
    #: has to say so — the whole reason for naming it here rather than quietly
    #: returning a bigger number.
    projected_to: int | None = None
    #: Countries with no growth series, whose counts were left at their own
    #: year. Their totals are therefore on a different basis from the rest.
    projected_without_rate: list[str] = field(default_factory=list)

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

    def is_complete(self, indicator: str) -> bool:
        got, total = self.coverage.get(indicator, (0, 0))
        return total > 0 and got == total

    def missing_units(self, indicator: str) -> int:
        got, total = self.coverage.get(indicator, (0, 0))
        return max(0, total - got)

    @property
    def inherited_units(self) -> int:
        """Units whose value was measured somewhere coarser and applied here.

        This replaced ``off_method_units``, and the rename is the point. That
        field counted units answered by a source the method did not declare —
        an internal notion, and one that is now structurally impossible, since
        an ineligible source is no longer used at all (see ``policy.py``).

        What a reader actually needs to know survived the fix: a rate legitimately
        inherits downward, so a selection can be a mixture of regions measured in
        their own right and regions carrying their country's figure. Both are
        defensible and each row says which it is, but the mixture changes what a
        total *means* — and the difference between being able to ask "how much of
        this is really local?" and having to take it on faith is one number.

        A rolled-up country row contributes the number of its regions that
        borrowed, not all-or-nothing — a country that measured three of its five
        regions is two-fifths inherited, and saying so is more use than either
        rounding.
        """
        return sum(area.inherited_units for area in self.areas)

    @property
    def small_sample_units(self) -> int:
        """Units whose rate the source itself flags as resting on too few cases.

        Distinct from ``coverage``, which asks whether a figure exists, and from
        ``inherited_units``, which asks where it was measured. This asks how much
        it is worth: DHS suppresses an estimate below 25 unweighted cases and
        parenthesises one below 50, and a regional table that drops the bracket
        presents a figure resting on twenty-one children as the equal of one
        resting on three hundred.

        The gap this closes was found by comparing a generated county table
        against one a person built by hand: theirs carried DHS's small-sample
        flag in its own column and six of Liberia's fifteen counties wore it.
        """
        return sum(area.small_sample_units for area in self.areas)


#: Counts carried on every selection regardless of indicator.
CARRIED_COUNTS = (
    "births",
    "expected_deaths",
    "pop_u5",
    "pop_total",
)


def carried_for(indicator: str, extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Counts to carry for this indicator.

    The base set plus the unreached count belonging to the chosen measure, so a
    coverage view shows the population it is actually about. Resolving all
    eleven gap measures on every query would cost eleven lookups per boundary to
    display one.
    """
    wanted: list[str] = [e for e in extra if e in measures.MEASURES]
    gap = f"{indicator}_gap"
    if gap in measures.MEASURES:
        wanted.append(gap)
    if indicator in ("diarrhoea_prevalence", "ors_coverage"):
        wanted.append("ors_gap_children")
    return CARRIED_COUNTS + tuple(dict.fromkeys(wanted))


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
    source_order: tuple[str, ...] | None = None,
    method: str | None = None,
    extra_counts: tuple[str, ...] = (),
    target_year: int | None = None,
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
    # A caller may need a count the indicator would not normally carry — a
    # costing scenario needs its intervention's case measure whatever it is
    # thresholding on.
    carried = carried_for(indicator, extra_counts)

    # A method fixes both which sources may answer and what level to work at.
    # Without one the historical default stands, so existing callers are
    # unaffected.
    chosen = methods.get(method) if method else None
    if chosen is not None:
        source_order = chosen.source_order
        levels = chosen.resolution.admin_levels
        national_only = chosen.is_national
    else:
        source_order = source_order or None
        levels = (0, 1)
        national_only = False

    unsupported: list[str] = []
    if chosen is not None:
        from connect_labs.labs.indicators import availability

        supported = set(availability.countries_supporting(chosen, indicator, iso_codes))
        wanted = [c.upper() for c in (iso_codes or [])] or None
        qs = boundary_set.owned().filter(admin_level__in=levels, iso_code__in=supported)
        if wanted:
            qs = qs.filter(iso_code__in=wanted)
        unsupported = sorted(
            name_for(r.iso_code) for r in availability.for_method(chosen, indicator, iso_codes) if not r.available
        )
    else:
        qs = boundary_set.owned().filter(admin_level__in=levels)
        if iso_codes:
            qs = qs.filter(iso_code__in=[c.upper() for c in iso_codes])
    boundaries = list(qs)

    # Counts (population, births) are stored on regions, never on the country
    # outline — a country's population is the sum of its regions, and measuring
    # it twice could only disagree with itself. So a national-resolution row
    # still has to reach its regions to report how many people it covers.
    count_units: list[AdminBoundary] = []
    if national_only:
        count_qs = boundary_set.owned().filter(admin_level=1, iso_code__in={b.iso_code for b in boundaries})
        count_units = list(count_qs)

    bulk = BulkResolver(boundaries + count_units, year=year, source_order=None)
    rate_bulk = BulkResolver(boundaries, year=year, source_order=source_order, lens_on=indicator)

    # Counts are carried to the year the programme runs, if one was named. A
    # 2027 question answered with a 2022 population is not a conservative
    # answer, it is a different one — and the error compounds at the country's
    # own growth rate. Rates are left alone: nothing here models how coverage
    # or mortality moves, and a projected rate would be invention.
    growth: dict[str, float | None] = {}
    if target_year is not None:
        for b in boundaries:
            if b.iso_code not in growth:
                g = bulk.get("pop_growth_rate", b)
                growth[b.iso_code] = g.value if g is not None else None
    projected_without_rate = sorted({iso for iso, g in growth.items() if g is None})

    def _count(resolved: Resolved) -> float:
        return project_count(resolved, target_year, growth.get(resolved.boundary.iso_code))

    by_iso: dict[str, dict[int, list[AdminBoundary]]] = defaultdict(lambda: defaultdict(list))
    for b in boundaries:
        by_iso[b.iso_code][b.admin_level].append(b)

    areas: list[Area] = []
    fully: list[str] = []
    partly: list[str] = []
    skipped: list[str] = []

    for iso in sorted(by_iso):
        adm0 = (by_iso[iso].get(0) or [None])[0]
        cname = _country_name(iso, adm0)

        if national_only:
            subs = []
        else:
            # Deepest level with actual values for this country. Mixing ADM1 and
            # ADM2 inside one country would count a district and the region
            # containing it as two separate places.
            subs = []
            for lvl in (2, 1):
                candidates = by_iso[iso].get(lvl) or []
                if any(rate_bulk.get(indicator, b) is not None for b in candidates):
                    subs = candidates
                    break

        units = subs or ([adm0] if adm0 is not None else [])
        if not units:
            continue

        evaluated = [(b, r) for b in units if (r := rate_bulk.get(indicator, b)) is not None]
        if not evaluated:
            skipped.append(cname)
            continue

        # For a coverage measure the problem is a LOW value, so "selected" means
        # below the threshold. Thresholding above would pick the places already
        # doing well, which is the opposite of targeting.
        if indicator in measures.LOWER_IS_WORSE:
            above = [(b, r) for b, r in evaluated if r.value < threshold]
        else:
            above = [(b, r) for b, r in evaluated if r.value > threshold]
        if not above:
            continue

        # A country is only rolled up when it has real regions and all of them
        # qualify — a single-region country would otherwise be relabelled as a
        # whole-country row, which reads as a much stronger claim than it is.
        # "Every region cleared it" has to mean every region, not every region we
        # happened to have a reading for. Sudan has nineteen; three carried a
        # survey; all three were above — and the country was emitted as one
        # whole-country row carrying all nineteen regions' population. The claim
        # was much stronger than the evidence, and the row looked like the
        # strongest kind of finding rather than the thinnest.
        #
        # This was always wrong and was always latent. It only became common
        # when sources stopped being substituted for one another: before that,
        # nearly every region resolved something, so the two counts agreed by
        # accident. A country that cannot be evaluated whole is emitted as the
        # regions that were.
        rolled_up = bool(subs) and len(above) == len(evaluated) == len(units) and len(evaluated) > 1

        # A national-resolution row is one country; its counts come from its
        # regions, the same way a rolled-up country row's do.
        if national_only:
            fully.append(cname)
            for b, r in above:
                children = [c for c in count_units if c.iso_code == b.iso_code]
                area = Area(
                    boundary=b,
                    iso_code=iso,
                    country_name=cname,
                    name=cname,
                    admin_level=0,
                    is_whole_country=True,
                    units_covered=max(len(children), 1),
                    values={indicator: r},
                    inherited_units=max(len(children), 1) if r is not None and r.inherited else 0,
                    small_sample_units=max(len(children), 1) if r is not None and r.small_sample else 0,
                )
                for c in carried:
                    got = [_count(v) for ch in children if (v := bulk.get(c, ch)) is not None]
                    area.counts[c] = sum(got) if got else None
                    area.coverage[c] = (len(got), max(len(children), 1))
                areas.append(area)
            continue

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
                inherited_units=sum(1 for _, r in above if r is not None and r.inherited),
                small_sample_units=sum(1 for _, r in above if r is not None and r.small_sample),
            )
            for c in carried:
                got = [_count(r) for b, _ in above if (r := bulk.get(c, b)) is not None]
                area.counts[c] = sum(got) if got else None
                area.coverage[c] = (len(got), len(above))
            areas.append(area)
        else:
            # Measured against every region, not every evaluated one — same
            # reason as the rollup above. A country with three surveyed regions
            # all above the threshold is partly above, not fully.
            (fully if len(above) == len(units) else partly).append(cname)
            for b, r in above:
                area = Area(
                    boundary=b,
                    iso_code=iso,
                    country_name=cname,
                    name=b.name,
                    admin_level=b.admin_level,
                    values={indicator: r},
                    inherited_units=1 if r is not None and r.inherited else 0,
                    small_sample_units=1 if r is not None and r.small_sample else 0,
                )
                for c in carried:
                    got = bulk.get(c, b)
                    area.counts[c] = _count(got) if got else None
                    area.coverage[c] = (1 if got else 0, 1)
                areas.append(area)

    totals: dict[str, float | None] = {}
    coverage: dict[str, tuple[int, int]] = {}
    for c in carried:
        present = [a.counts[c] for a in areas if a.counts.get(c) is not None]
        # None, not 0, when nothing is known — the caller decides how to say so.
        totals[c] = sum(present) if present else None
        coverage[c] = (
            sum(a.coverage.get(c, (0, 0))[0] for a in areas),
            sum(a.coverage.get(c, (0, 0))[1] for a in areas),
        )

    totals[indicator] = aggregate(
        indicator,
        [(a.values[indicator].value, a.counts.get(measure.weight_by or "")) for a in areas if a.values.get(indicator)],
    )

    # Rank on the quantity a programme is actually sized by. Births is right
    # for a mortality question and wrong for a coverage one: asking where
    # sanitation is worst and getting an order driven by births ranks a place
    # by how many children are born there rather than by how many are
    # unreached. Where the indicator has an unreached count, that leads.
    rank_by = f"{indicator}_gap" if f"{indicator}_gap" in measures.MEASURES else "births"
    areas.sort(key=lambda a: (-(a.counts.get(rank_by) or 0.0), a.country_name, a.name))

    return Selection(
        indicator=indicator,
        threshold=threshold,
        year=year,
        areas=areas,
        totals=totals,
        coverage=coverage,
        countries_fully_above=sorted(set(fully)),
        countries_partly_above=sorted(set(partly)),
        skipped_no_data=sorted(set(skipped)),
        method=chosen.code if chosen else "",
        resolution=chosen.resolution.value if chosen else "",
        countries_unsupported=unsupported,
        projected_to=target_year,
        projected_without_rate=projected_without_rate,
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
