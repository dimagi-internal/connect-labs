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
    `series[<name>]` carrying the same four things under its own key.

    A further family used to have no monthly at all -- `monthlyByScope` was
    graded with the primary catalog only -- so it could be benchmarked
    point-in-time and never over time. That is fixed in `snapshot.py`, and this
    reads what it now emits. A snapshot saved BEFORE that carries no
    `series[<name>].monthlyByScope`, and falls back to the old behaviour of
    publishing points and no series rather than failing.
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
        blocks.append(
            (
                str(name),
                block.get("measures") or [],
                block.get("byOpp") or [],
                block.get("monthlyByScope") or {},
            )
        )
    return blocks


# A COUNT is never publishable, whatever anyone declares. Opportunity sizes are
# visible on the programme report (100 .. 2,189 cases), so an "anonymous" bar
# reading 1,692 IS that opportunity to anybody who has seen that page. This is
# the one rule a registry edit must not be able to switch off, which is why it
# is a guard here rather than a default there.
UNPUBLISHABLE_KINDS = frozenset({"count"})


def resolve_benchmarkable_ids(measures) -> set[str]:
    """Which indicators in one frozen catalog may be benchmarked.

    The unit rule is the FLOOR, and the registry adjusts it either way:

    * `benchmarkable: true` adds an indicator the unit rule would have missed.
      A mean early growth rate of 14.0 g/kg/day says nothing about how big an
      opportunity is -- a 50-case and a 2,000-case one can both report 14.0 --
      but its unit is `g/kg/d`, so the unit rule withheld it. Publishability is
      a property of the INDICATOR, and the registry is where that is decided.
    * `benchmarkable: false` removes one the unit rule would have allowed.
    * Silence leaves the unit rule's answer alone, so declaring one indicator
      does not silently withhold every other.

    Then `kind: count` is removed, whatever was declared -- see
    UNPUBLISHABLE_KINDS. An indicator that declares itself benchmarkable and is
    a count is refused, not trusted: the registry is editable with no deploy,
    and a mistake there must not be able to publish case counts.

    Deliberately NOT named `benchmarkable_indicator_ids`: `publish_benchmark`
    takes a parameter by that name, which would shadow this inside it -- the
    fallback then calls None and every publication raises. That was caught only
    by a test exercising the whole publisher; tests calling this directly
    cannot see the shadow.
    """
    allowed = set(rate_shaped_indicator_ids(measures))
    for m in measures or []:
        ind = str(m.get("indicator") or "")
        if not ind:
            continue
        declared = m.get("benchmarkable")
        if declared is True:
            allowed.add(ind)
        elif declared is False:
            allowed.discard(ind)
        if str(m.get("kind")) in UNPUBLISHABLE_KINDS:
            allowed.discard(ind)
    return allowed


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


def _history_observations(history, series: str, indicator_id: str, members: set[int]) -> dict:
    """`{period: observations}` from a workflow's SAVED RUNS, not from months.

    A saved run is one point of a time series: each was computed as of its own
    period end by the same builder, so the series of runs IS the trend, and it is
    what the programme report's own trend charts are drawn from. That is the
    axis a reader means by "how am I trending" -- not the cohort-month series
    this used to publish, which is a different question (how did the babies
    registered in month X fare) on a different axis.

    `history` is `[{"date": ..., "byOpp": {series: [...]}}]`, oldest first.

    The period is that OPPORTUNITY'S OWN Nth report (`R0`, `R1`, ...), not the
    report date, for the same two reasons the point rules exist. Opportunities
    join a programme at different times, so on a report-date axis a cohort
    compares somebody's first report against somebody else's twentieth. And a
    line that starts late on a dated axis says when that opportunity began,
    which identifies it; re-based, every line starts at R0 and says nothing.
    """
    per_opp: dict[int, list] = {}
    for point in history or []:
        for opp, cells in ((point or {}).get("byOpp") or {}).get(series, {}).items():
            opp = int(opp)
            if opp not in members:
                continue
            observation = _observation(opp, (cells or {}).get(indicator_id))
            # Only a SCORING report advances this opportunity's index -- a run
            # where it had too few cases to score is not its first report, and
            # counting it would slide its whole line one place left of everyone
            # whose first report scored.
            if observation is not None:
                per_opp.setdefault(opp, []).append(observation)
    by_period: dict[str, list] = {}
    for opp, observations in per_opp.items():
        for i, observation in enumerate(observations):
            by_period.setdefault(f"R{i}", []).append(observation)
    return by_period


def _indicator_ids(by_opp, monthly_by_scope, history=None, series: str | None = None) -> set[str]:
    """Every indicator this family actually carries, point or series.

    The history is scanned too, not just the snapshot: an indicator can be
    absent from the run being published and present in earlier ones, and
    iterating only the snapshot's keys would skip it silently -- zero rows, no
    exception, no log.
    """
    ids: set[str] = set()
    for entry in by_opp or []:
        ids |= {str(k) for k in ((entry or {}).get("ind") or {})}
    for key, points in (monthly_by_scope or {}).items():
        if not str(key).startswith(OPP_SCOPE_PREFIX):
            continue
        for point in points or []:
            ids |= {str(k) for k in ((point or {}).get("ind") or {})}
    for point in history or []:
        for cells in ((point or {}).get("byOpp") or {}).get(series, {}).values():
            ids |= {str(k) for k in (cells or {})}
    return ids


def observations_from_snapshot(snapshot: dict, series: str, indicator_id: str, members: set[int], history=None):
    """`(point_observations, {period: observations})` for one indicator.

    The point comes from the snapshot being published; the series comes from the
    workflow's saved runs, which is a different source on purpose -- see
    `_history_observations`. With no history the indicator publishes a point and
    no series rather than failing.
    """
    for name, _measures, by_opp, _monthly in _blocks(snapshot):
        if name == series:
            return (
                _point_observations(by_opp, indicator_id, members),
                _history_observations(history, name, indicator_id, members),
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
    history=None,
) -> BenchmarkPublication:
    """Publish every benchmarkable indicator in `snapshot` for `cohort`.

    `history` is the source workflow's completed runs, oldest first, as
    `[{"date": ..., "byOpp": {series: {opp: cells}}}]`. The POINT values come
    from `snapshot`; the SERIES comes from these, because a trend is the series
    of saved reports rather than anything inside one of them. Omitted, every
    indicator publishes a point and no series.

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
            else resolve_benchmarkable_ids(measures)
        )
        # Sorted so publication order is deterministic. The union of both scopes,
        # because an indicator can exist in only one of them.
        for indicator_id in sorted(_indicator_ids(by_opp, monthly, history, series_name)):
            if indicator_id not in allowed:
                withheld.append(f"{series_name}:{indicator_id}")
                continue
            considered += 1
            points, by_period = observations_from_snapshot(snapshot, series_name, indicator_id, members, history)
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
                by_period,
                **thresholds,
                tie_salt=f"{publication.pk}:{series_name}:{indicator_id}",
                require_complete=cohort.require_complete_series,
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
