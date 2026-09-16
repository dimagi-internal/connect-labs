"""Turn a completed run's snapshot into a publication of anonymous figures.

The source is the frozen snapshot a saved run already carries -- one semantic
pass already graded every scope -- so publication reads and recomputes nothing.
What is passed in is the GRADED PAYLOAD (`connect_labs.semantic.snapshot.build`'s
return value), which a saved run stores one level down, under
`snapshot["state"][<state_key>]` (see `workflow/snapshot_builders.wrap_for_runner`).

NOT IMPLEMENTED HERE, and deliberately: there is no completion hook and no way to
create or administer a cohort. `BenchmarkCohort.auto_publish_on_completion` is a
column nothing reads yet, and `publish_benchmark` has no caller outside tests.
Both are Plan 2's scope. Publication today is something a human (or a future
caller) does explicitly, by calling this function.

WHAT MAY BE PUBLISHED AT ALL. Only rate-shaped indicators. R3 withholds the
denominator COLUMN, which is no defence when the size IS the value: C01
("Registered cases", `unit: n`) is the opportunity's raw case count, and twelve
anonymous C01 bars are twelve case counts that the programme report lists by
name. So the unit decides: percentages and per-100 rates are publishable, every
count, mean and duration is not, and an indicator whose unit cannot be resolved
is withheld rather than assumed safe. The units come from the snapshot's own
frozen measure catalogs (`cMeasures` / `series[<name>].measures`), which are the
registry's `meta` as it stood when the run was graded -- so a publication cannot
be graded against one set of definitions and filtered against another.
"""

from __future__ import annotations

import logging

from django.db import transaction

from connect_labs.benchmarks.disclosure import PeerObservation, anonymise_point, anonymise_series
from connect_labs.benchmarks.models import BenchmarkPublication, BenchmarkValue

logger = logging.getLogger(__name__)

# The only units whose VALUE is a rate rather than a size. `unit: n` (counts and
# means alike), 'd', 'h', 'wks', 'g' and 'g/kg/d' are all withheld: a count
# re-identifies instantly, and the rest are not rates, so they stay out until
# someone decides case by case. An unknown unit is withheld for the same reason.
RATE_UNITS = frozenset({"%", "/100"})

# The bands on which a graded cell may be published. Everything else the grader
# can emit -- 'nodata', 'insufficient', 'notinapp', 'unrecorded', 'notcredible'
# -- is the registry or its gates saying this figure must not stand on its own,
# which is R7. An allow-list, so a band added to the grader later is withheld
# until it is considered here rather than published by default.
PUBLISHABLE_BANDS = frozenset({"green", "yellow", "red", "unbanded"})

# `monthlyByScope` keys its per-opportunity series `opp:<id>` (snapshot.py's
# render contract); the sibling `llo:<name>` and `all` scopes are not per-peer.
OPP_SCOPE_PREFIX = "opp:"


class SnapshotShapeError(RuntimeError):
    """A snapshot produced no observations at all for any benchmarkable indicator.

    Raised rather than committing an empty publication: the only ways to get here
    are a snapshot in a shape this module does not read (which is how the first
    version of this publisher shipped dead -- it read `series[x]["opportunity"]`,
    a key `snapshot.py` has never emitted) and a cohort with no overlap with the
    run. Both are caller errors, and both otherwise look exactly like "the
    disclosure rules withheld everything", which is a legitimate empty result.
    """


def _series_prefix(indicator_id) -> str:
    """The series an indicator belongs to: the letter prefix of its id (C01 -> C).

    This is `runtime.filter_to_series`' own rule -- a series IS the prefix -- and
    it is needed because the graded payload does not name its primary family
    anywhere: `cMeasures`, `byOpp` and `monthlyByScope` sit unlabelled at the top
    level while the FURTHER families are keyed by name under `series`.
    """
    out = []
    for ch in str(indicator_id or ""):
        if not ch.isalpha():
            break
        out.append(ch)
    return "".join(out).upper()


def _primary_series_name(measures) -> str | None:
    """The one series `cMeasures` describes, or None if it is empty or mixed."""
    names = {_series_prefix(m.get("indicator")) for m in measures or []}
    names.discard("")
    return names.pop() if len(names) == 1 else None


def _blocks(snapshot: dict) -> list[tuple[str, list, list, dict]]:
    """`(series_name, measures, by_opp, monthly_by_scope)` per indicator family.

    Two shapes, because `snapshot.py` emits two. The PRIMARY family is the
    top-level `cMeasures` / `byOpp` / `monthlyByScope`; every FURTHER family is
    `series[<name>]` with its own `measures` and `byOpp` -- and no monthly data
    at all, since `monthlyByScope` is graded with the primary catalog only. A
    further family therefore publishes points and no series, which is a real
    limitation of the snapshot rather than of this module.
    """
    blocks: list[tuple[str, list, list, dict]] = []
    primary_measures = snapshot.get("cMeasures") or []
    primary = _primary_series_name(primary_measures)
    if primary:
        blocks.append((primary, primary_measures, snapshot.get("byOpp") or [], snapshot.get("monthlyByScope") or {}))
    elif primary_measures:
        logger.warning("benchmark publication skipped the primary family: cMeasures name no single series")
    for name, block in sorted((snapshot.get("series") or {}).items()):
        block = block or {}
        blocks.append((str(name), block.get("measures") or [], block.get("byOpp") or [], {}))
    return blocks


def rate_shaped_indicator_ids(measures) -> set[str]:
    """The indicators in one frozen catalog whose value is a rate. See RATE_UNITS."""
    return {
        str(m.get("indicator"))
        for m in measures or []
        if m.get("indicator") and str(m.get("unit") or "") in RATE_UNITS
    }


def _observation(opportunity_id, cell) -> PeerObservation | None:
    """One graded cell -> one observation, or None when there is nothing to publish.

    A cell is `{"id", "n", "value", "band"}` (plus an optional `thinDenominator`).
    `n` IS the denominator -- `grade()` fills it from the measure's own
    `_denominator` column -- and `value` is None whenever the grade did not
    compute. A cell carrying no `n` is marked suppressed rather than given a
    denominator of 0: 0 only excludes a peer while `min_denominator` is above it,
    and "we cannot see the base" must not become "the base is fine" if a cohort
    ever relaxes that threshold.
    """
    if not isinstance(cell, dict):
        return None
    value = cell.get("value")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    n = cell.get("n")
    try:
        denominator = int(float(n))
    except (TypeError, ValueError):
        denominator = 0
    suppressed = (
        n is None
        or str(cell.get("band")) not in PUBLISHABLE_BANDS
        # A rate computed over a self-selected minority of its scope: the
        # registry's own `min_input_coverage` footnote. Comparable to nothing.
        or bool(cell.get("thinDenominator"))
    )
    return PeerObservation(
        opportunity_id=int(opportunity_id),
        value=value,
        denominator=denominator,
        suppressed=suppressed,
    )


def _point_observations(by_opp, indicator_id: str, members: set[int]) -> list[PeerObservation]:
    """`byOpp` rows -> observations, keeping only cohort members.

    A report spans every opportunity it was built over; a cohort may be a subset,
    and an opportunity that is not a member must never be benchmarked.
    """
    out = []
    for entry in by_opp or []:
        opp = (entry or {}).get("opp")
        if opp is None or int(opp) not in members:
            continue
        observation = _observation(opp, ((entry.get("ind") or {}).get(indicator_id)))
        if observation is not None:
            out.append(observation)
    return out


def _months_since(start: str, month: str) -> int | None:
    """Whole months from `start` to `month`, both `YYYY-MM`. None if unparsable."""
    try:
        sy, sm = int(start[:4]), int(start[5:7])
        my, mm = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError, IndexError):
        return None
    return (my - sy) * 12 + (mm - sm)


def _series_observations(monthly_by_scope, indicator_id: str, members: set[int]) -> dict[str, list[PeerObservation]]:
    """`monthlyByScope["opp:<id>"]` -> `{period: observations}` for cohort members.

    The period is MONTHS SINCE THAT OPPORTUNITY'S OWN FIRST MONTH (`M0`, `M1`,
    ...), not the calendar month. Two reasons, and they pull the same way.

    It is the comparison people actually want: a cohort whose opportunities
    started across seven months was, on a calendar axis, comparing somebody's
    first month against somebody else's sixth.

    And it is what makes the series publishable at all. R5 keeps a period only
    if `min_peers` reached it and R6 then keeps only peers present in EVERY
    period of that window -- so on a calendar axis, staggered start dates
    collapsed the intersection to one or two months out of six. Re-based, every
    opportunity has an M0, so the window is bounded by how long the SHORTEST
    peer has been running rather than by when the LATEST one began.

    It also closes a disclosure hole rather than trading against one. A line
    that starts late on a calendar axis says when that opportunity began, and a
    start date is identifying. Re-based, every line starts at M0 and none of
    them says anything about when.
    """
    by_period: dict[str, list[PeerObservation]] = {}
    for key, points in (monthly_by_scope or {}).items():
        if not str(key).startswith(OPP_SCOPE_PREFIX):
            continue
        try:
            opp = int(str(key)[len(OPP_SCOPE_PREFIX) :])
        except ValueError:
            continue
        if opp not in members:
            continue
        months = sorted({str(p.get("month")) for p in (points or []) if (p or {}).get("month")})
        if not months:
            continue
        start = months[0]
        for point in points or []:
            month = (point or {}).get("month")
            if not month:
                continue
            offset = _months_since(start, str(month))
            if offset is None or offset < 0:
                continue
            observation = _observation(opp, (point.get("ind") or {}).get(indicator_id))
            if observation is not None:
                by_period.setdefault(f"M{offset}", []).append(observation)
    return by_period


def _indicator_ids(by_opp, monthly_by_scope) -> set[str]:
    """Every indicator this family actually carries, point or series."""
    ids: set[str] = set()
    for entry in by_opp or []:
        ids |= {str(k) for k in ((entry or {}).get("ind") or {})}
    for key, points in (monthly_by_scope or {}).items():
        if not str(key).startswith(OPP_SCOPE_PREFIX):
            continue
        for point in points or []:
            ids |= {str(k) for k in ((point or {}).get("ind") or {})}
    return ids


def observations_from_snapshot(snapshot: dict, series: str, indicator_id: str, members: set[int]):
    """`(point_observations, {period: observations})` for one indicator."""
    for name, _measures, by_opp, monthly in _blocks(snapshot):
        if name == series:
            return (
                _point_observations(by_opp, indicator_id, members),
                _series_observations(monthly, indicator_id, members),
            )
    return [], {}


@transaction.atomic
def publish_benchmark(
    cohort,
    *,
    snapshot: dict,
    source_workflow_id: int,
    source_run_id: int,
    registry_id: int | None,
    as_of: str,
    published_by: str = "",
    benchmarkable_indicator_ids: set[str] | None = None,
) -> BenchmarkPublication:
    """Publish every benchmarkable indicator in `snapshot` for `cohort`.

    `benchmarkable_indicator_ids` overrides the unit rule with an explicit
    allow-list, for a caller that has decided indicator by indicator. Omitted,
    the rule applies: rate-shaped only, resolved from the snapshot's own frozen
    catalogs, and nothing at all if those are missing. Either way the default is
    to withhold -- an indicator is published because something said it may be,
    never because nothing said it may not.

    Atomic: a publication is either complete or absent, so no partner ever reads
    a half-written benchmark. Raises `SnapshotShapeError` rather than committing
    an empty publication when the snapshot carries no benchmarkable indicator at
    all, or carries one that yields no observations whatsoever -- so a publisher
    reading a snapshot shape that does not exist fails instead of writing zero
    rows and returning. A publication the disclosure RULES emptied is a
    legitimate result and still returns.

    The returned publication carries `withheld_indicator_ids` -- what this run
    refused to publish, `<series>:<indicator>` -- so a caller can report it.
    """
    publication = BenchmarkPublication.objects.create(
        cohort=cohort,
        source_workflow_id=source_workflow_id,
        source_run_id=source_run_id,
        registry_id=registry_id,
        as_of=as_of,
        published_by=published_by,
    )
    members = cohort.opportunity_ids
    thresholds = {"min_peers": cohort.min_peers, "min_denominator": cohort.min_denominator}

    rows: list[BenchmarkValue] = []
    withheld: list[str] = []
    considered = 0
    observed = 0
    for series_name, measures, by_opp, monthly in _blocks(snapshot):
        allowed = (
            {str(i) for i in benchmarkable_indicator_ids}
            if benchmarkable_indicator_ids is not None
            else rate_shaped_indicator_ids(measures)
        )
        # Sorted so publication order is deterministic. The union of both scopes,
        # because an indicator can exist in only one of them.
        for indicator_id in sorted(_indicator_ids(by_opp, monthly)):
            if indicator_id not in allowed:
                withheld.append(f"{series_name}:{indicator_id}")
                continue
            considered += 1
            points, by_period = observations_from_snapshot(snapshot, series_name, indicator_id, members)
            observed += len(points) + sum(len(o) for o in by_period.values())
            for peer_index, value, opportunity_id in anonymise_point(
                points, **thresholds, tie_salt=f"{publication.pk}:{series_name}:{indicator_id}"
            ):
                rows.append(
                    BenchmarkValue(
                        publication=publication,
                        series=series_name,
                        indicator_id=indicator_id,
                        period=None,
                        peer_index=peer_index,
                        value=value,
                        opportunity_id=opportunity_id,
                    )
                )
            for period, points_for_period in anonymise_series(
                by_period, **thresholds, tie_salt=f"{publication.pk}:{series_name}:{indicator_id}"
            ).items():
                for peer_index, value, opportunity_id in points_for_period:
                    rows.append(
                        BenchmarkValue(
                            publication=publication,
                            series=series_name,
                            indicator_id=indicator_id,
                            period=period,
                            peer_index=peer_index,
                            value=value,
                            opportunity_id=opportunity_id,
                        )
                    )

    if withheld:
        logger.info(
            "benchmark publication %s withheld %d non-benchmarkable indicators: %s",
            publication.pk,
            len(withheld),
            ", ".join(sorted(withheld)),
        )
    # A publication the RULES emptied is a result; one that could never have
    # contained anything is a caller error, and the two look identical from the
    # outside. So the second raises: nothing is committed, and the reason is named.
    if not considered:
        raise SnapshotShapeError(
            f"snapshot carries no benchmarkable indicator (cohort {cohort.pk}, run {source_run_id}); "
            f"withheld: {', '.join(sorted(withheld)) or 'nothing -- no indicator was found at all'}"
        )
    if not observed:
        raise SnapshotShapeError(
            f"snapshot yielded no observations for any of {considered} benchmarkable indicators "
            f"(cohort {cohort.pk}, run {source_run_id}) -- wrong snapshot shape, or no cohort member in it"
        )
    BenchmarkValue.objects.bulk_create(rows, batch_size=1000)
    publication.withheld_indicator_ids = sorted(withheld)
    return publication
