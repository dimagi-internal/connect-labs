"""Rebuild a workflow's periodic run history: the series it WOULD have had.

A periodic report's trend draws one point per SAVED RUN, so a report created
last week has one point and refuses to draw a line. There was no way to get the
missing points except to wait a week at a time, and no way at all to revise them
after an indicator definition changed -- every saved run kept the grading it was
built under, so a corrected definition applied to the newest point and to
nothing behind it.

This makes the history a DERIVED artifact instead of an accumulated one. Given a
cadence and a range it writes one completed run per period, each computed AS OF
that period's end, all against the definitions in force right now. Run it once
to get a line where there was none; run it again after editing the registry to
restate the whole series under the new definitions.

That is a deliberate reversal of the older guarantee. A saved run used to be
evidence of what we said in that week; a rebuilt one is what we would say today
about that week. The second is what you want while the definitions are still
being settled, and the first is what you want once they are -- so rebuilding is
an explicit operation a person invokes, never something that happens on a read.

WHAT MAKES A WORKFLOW ELIGIBLE. Only that its snapshot builder is a function of
`period_end` -- see `PERIODIC_BUILDERS` in snapshot_builders.py. A builder that
ignores the period returns the same payload for every date, which renders as a
flat line across real dates: indistinguishable from a programme that genuinely
did not move. Refusing up front is the only place that failure is visible.

Nothing here knows what KMC is. It reads the definition's own contract, so any
workflow whose builder is periodic gets this for free.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from connect_labs.workflow.snapshot_builders import PERIODIC_BUILDERS
from connect_labs.workflow.snapshot_runtime import SnapshotBuildError, build_snapshot_for_run, cache_state
from connect_labs.workflow.templates import resolve_snapshot_contract
from connect_labs.workflow.visit_cache import DEFAULT_HOLD_MINUTES, ensure_visit_cache

logger = logging.getLogger(__name__)

# Stamped into every run this writes, and the ONLY thing a replace deletes. A
# report a person created and named by hand sits in the same list on the same
# date; without this marker a rebuild would silently consume it.
GENERATED_BY = "history_rebuild"

# Roughly five years of weeks. A mistyped start date is otherwise a request to
# create thousands of runs, each a real write and ~12s of compute -- refused
# with the count so the caller can see what they asked for.
MAX_PERIODS = 400

CADENCES = ("weekly", "daily")

# Where a rebuild looks to find out when the programme actually started, when
# the caller does not say. Ordered by preference; the first field present on a
# row wins, and the earliest across all rows is the start.
ACTIVITY_DATE_FIELDS = ("reg_date", "first_visit_date", "visit_date", "date")


class HistoryRebuildError(Exception):
    """A rebuild could not run, with a stable code each caller maps to its own shape.

    codes: bad_cadence, bad_limit, no_definition, not_periodic, no_start, empty_range,
           too_many_periods, cache_miss, cache_incomplete, build_failed, no_owner
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def periods(cadence: str, start: date, end: date) -> list[tuple[date, date]]:
    """The COMPLETE periods of `cadence` between `start` and `end`, inclusive.

    Weekly periods are Monday-Sunday and are emitted only once their Sunday has
    arrived: the week in progress is not a period, and treating it as one would
    plot a partial week beside full ones as though they were comparable.

    A `start` inside a week yields that whole week rather than a truncated one.
    The evaluation is cut at the period END, so a period that opens before the
    data does costs nothing and keeps every point on a real week boundary.
    """
    if cadence not in CADENCES:
        raise HistoryRebuildError("bad_cadence", f"unknown cadence {cadence!r}; expected one of {', '.join(CADENCES)}")
    out: list[tuple[date, date]] = []
    if cadence == "weekly":
        # weekday(): Monday is 0, so this lands on the Sunday of start's week.
        period_end = start + timedelta(days=6 - start.weekday())
        while period_end <= end:
            out.append((period_end - timedelta(days=6), period_end))
            period_end += timedelta(days=7)
    else:
        day = start
        while day <= end:
            out.append((day, day))
            day += timedelta(days=1)
    return out


def last_complete_period_end(cadence: str, today: date) -> date:
    """The end of the most recent period that has finished on `today`."""
    if cadence == "weekly":
        # The Sunday before this week's Monday.
        return today - timedelta(days=today.weekday() + 1)
    return today - timedelta(days=1)


def earliest_date(rows: list[dict], fields: tuple[str, ...]) -> date | None:
    """The earliest parseable date across `fields` on `rows`, or None.

    Anything unparseable is skipped rather than raising: a rebuild must not be
    blocked by one malformed row, and a missing date is a data-quality fact the
    report itself already surfaces.
    """
    best: date | None = None
    for row in rows or []:
        for field in fields:
            raw = row.get(field)
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw)[:10]).date()
            except ValueError:
                continue
            if best is None or parsed < best:
                best = parsed
    return best


def eligibility(definition) -> tuple[bool, str | None]:
    """Whether `definition`'s history can be rebuilt, and why not if it cannot."""
    contract = resolve_snapshot_contract(definition)
    if not contract["ok"]:
        return False, (
            f"workflow has no usable snapshot contract ({contract['error']}), so there is no "
            "builder to run for a past period"
        )
    if contract["source"] == "template_hook":
        return False, (
            f"template {contract['template_key']!r} builds its snapshot with a Python hook, whose "
            "handling of period_end cannot be inspected from here. Declare a periodic builder in "
            "snapshot_inputs to make this workflow rebuildable"
        )
    builder = (contract.get("snapshot_inputs") or {}).get("builder")
    if builder not in PERIODIC_BUILDERS:
        return False, (
            f"snapshot builder {builder!r} is not a function of period_end, so every rebuilt "
            "period would carry an identical snapshot and the trend would draw a flat line over "
            f"real dates. Periodic builders: {', '.join(sorted(PERIODIC_BUILDERS)) or 'none'}"
        )
    return True, None


def _case_index_rows(data_access, definition, contract, definition_id, opportunity_id) -> list[dict]:
    """The rows a start date can be derived from -- the contract's own case index.

    A COLD CACHE is raised, not swallowed. Both a cold cache and genuinely
    undated rows leave this with nothing to read, but they need opposite
    remedies -- load the pipeline data, versus pass an explicit start -- and
    reporting the second when the first is true sends someone to supply a date
    that will not help, because the rebuild then fails at its first period on
    the very cache it never had. Observed as a `no_start` on definition 5456,
    whose cache was cold.

    Any OTHER read failure still degrades to "no rows": deriving a convenience
    default must not be able to fail a call the caller could have made by
    passing `start` themselves.
    """
    from connect_labs.workflow.data_access import PipelineCacheMiss

    inputs = contract.get("snapshot_inputs") or {}
    alias = (inputs.get("case_index") or {}).get("pipeline") or inputs.get("visits_pipeline")
    if not alias:
        return []
    try:
        pipelines = data_access.get_cached_pipeline_data(definition_id, opportunity_id, aliases=[alias])
    except PipelineCacheMiss as e:
        raise HistoryRebuildError(
            "cache_miss",
            f"no cached data for pipeline {alias!r} (opp {opportunity_id}), so the start date cannot be "
            "derived -- and a rebuild would fail at its first period for the same reason. Load the "
            "workflow's pipeline data first (open the run page, or run the pipelines), then retry.",
        ) from e
    except Exception:  # noqa: BLE001 -- deriving a default must not fail the whole call
        logger.warning("could not read pipeline %r to derive a start date", alias, exc_info=True)
        return []
    return ((pipelines or {}).get(alias) or {}).get("rows") or []


def _ensure_cache(data_access, definition_id, *, opportunity_id, program_id, progress, hold_minutes):
    """Make every opportunity's visit data present and held before anything reads it.

    The indicators read the raw visit cache, which expires in an hour and is then
    deleted, while the pipelines keep answering from a computed cache that outlives
    it. Without this, a build could read a cohort with most of its raw rows gone and
    publish the result -- 1,681 cases for a cohort of 8,823, measured 2026-09-11 --
    with nothing failing. A slot that still cannot be cached stops the build, naming
    the opportunities, rather than letting a partial cohort through.
    """
    report = ensure_visit_cache(
        data_access,
        definition_id,
        opportunity_id=opportunity_id,
        program_id=program_id,
        hold_minutes=hold_minutes,
        progress=progress,
    )
    if report.get("failed"):
        raise HistoryRebuildError(
            "cache_incomplete",
            f"visit data could not be cached for opportunities {report['failed']}, so any figure built now "
            "would silently cover only part of the cohort. Retry, or run workflow_ensure_visit_cache to see "
            "each opportunity's error.",
        )
    return report


def _period_key(value) -> str:
    return str(value or "")[:10]


def rebuild_history(
    data_access,
    definition_id: int,
    *,
    cadence: str = "weekly",
    start: date | None = None,
    end: date | None = None,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    replace: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
    progress=None,
    request=None,
    today: date | None = None,
    ensure_cache: bool = True,
) -> dict:
    """Write one completed run per period, each computed as of that period's end.

    Returns a report: totals plus one entry per period naming what happened to
    it. Individual periods that fail are reported rather than raised, because a
    single bad week should not cost the other fifty-one; the exceptions are the
    conditions that would fail EVERY period identically (an ineligible builder,
    a cold cache), which raise immediately instead of being reported once per
    period.

    ROLLING. `limit` bounds how many periods this call builds. The report then
    carries `done` and a `next_start` cursor, and the caller rolls forward --
    passing `next_start` as `start` and the report's own `end` back as `end` --
    until `done`. A year of weekly history is ~70 full evaluations at ~12s each;
    one call walking all of it is a request held open for a quarter of an hour,
    which no client or load balancer waits for (a client idle timeout already
    killed a fully-successful 11-opportunity call, connect-labs#1220). Batches
    are idempotent -- every run is stamped and a replace touches only stamped
    runs -- so a batch cut off mid-flight is simply re-run.

    `progress(done, total)` is called once per period, which is what resets a
    client's idle timer inside a batch. It can never fail the rebuild.
    """
    if (opportunity_id is None) == (program_id is None):
        raise HistoryRebuildError("no_owner", "provide exactly one of opportunity_id / program_id")
    if limit is not None and limit < 1:
        raise HistoryRebuildError("bad_limit", f"limit must be a positive number of periods; got {limit}")

    definition = data_access.get_definition(definition_id)
    if definition is None:
        raise HistoryRebuildError("no_definition", f"workflow definition {definition_id} not found")

    ok, reason = eligibility(definition)
    if not ok:
        raise HistoryRebuildError("not_periodic", reason or "workflow is not rebuildable")

    contract = resolve_snapshot_contract(definition)
    today = today or date.today()
    end = end or last_complete_period_end(cadence, today)

    if start is None:
        rows = _case_index_rows(data_access, definition, contract, definition_id, opportunity_id)
        start = earliest_date(rows, ACTIVITY_DATE_FIELDS)
        if start is None:
            raise HistoryRebuildError(
                "no_start",
                "could not derive a start date from the workflow's own data (no dated rows in its "
                "case index), so the range would be a guess -- pass an explicit start",
            )

    window = periods(cadence, start, end)
    if not window:
        raise HistoryRebuildError(
            "empty_range",
            f"no complete {cadence} period falls between {start.isoformat()} and {end.isoformat()}",
        )
    if len(window) > MAX_PERIODS:
        raise HistoryRebuildError(
            "too_many_periods",
            f"{len(window)} {cadence} periods between {start.isoformat()} and {end.isoformat()} "
            f"exceeds the {MAX_PERIODS} cap; narrow the range",
        )

    # The cap above is checked against the WHOLE range, never the batch: checked
    # per batch, a mistyped start would sail through three periods at a time and
    # the cap would never fire. A dry run is a plan, so it always shows all of it.
    batch = window if (dry_run or limit is None) else window[:limit]
    rest = window[len(batch) :]

    report: dict = {
        "definition_id": definition_id,
        "cadence": cadence,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "periods": len(window),
        "batch": len(batch),
        "done": not rest,
        # The Monday (or day) the next batch begins on. Pass it back as `start`,
        # with this report's `end`, so every batch walks the same window even if
        # a Sunday passes while the caller is rolling.
        "next_start": rest[0][0].isoformat() if rest else None,
        "created": 0,
        "replaced": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": dry_run,
        "runs": [],
    }

    # Every batch first makes the whole cohort's visit data present, and holds it for
    # longer than the batch takes -- so a walk that outlives one cache lifetime cannot
    # read a cohort half of whose rows have been deleted under it.
    if ensure_cache and not dry_run and batch:
        report["visit_cache"] = _ensure_cache(
            data_access,
            definition_id,
            opportunity_id=opportunity_id,
            program_id=program_id,
            progress=progress,
            hold_minutes=DEFAULT_HOLD_MINUTES,
        )["hold_until"]

    existing: dict[str, list] = {}
    for run in data_access.list_runs(definition_id) or []:
        existing.setdefault(_period_key(run.period_end), []).append(run)

    for index, (period_start, period_end) in enumerate(batch, start=1):
        if progress is not None and not dry_run:
            _tick(progress, index, len(batch), period_end)
        key = period_end.isoformat()
        prior = existing.get(key, [])
        mine = [r for r in prior if (r.state or {}).get("generated_by") == GENERATED_BY]

        if not replace and prior:
            report["skipped"] += 1
            report["runs"].append(
                {
                    "period_start": period_start.isoformat(),
                    "period_end": key,
                    "run_id": prior[0].id,
                    "action": "skipped",
                    "error": None,
                }
            )
            continue

        if dry_run:
            report["runs"].append(
                {
                    "period_start": period_start.isoformat(),
                    "period_end": key,
                    "run_id": None,
                    "action": "would_replace" if mine else "would_create",
                    "error": None,
                }
            )
            continue

        # Build and complete the NEW run before deleting the old one. The
        # reverse order is the tempting one -- clear the slot, then fill it --
        # and it means a build that fails has already destroyed the only copy
        # of that period. A transient duplicate costs nothing: the trend keys
        # its points by as-of date, so two runs for one week render as one
        # point either way.
        run = data_access.create_run(
            definition_id=definition_id,
            opportunity_id=opportunity_id,
            program_id=program_id,
            period_start=period_start.isoformat(),
            period_end=key,
            initial_state={"generated_by": GENERATED_BY},
        )
        try:
            built = build_snapshot_for_run(
                data_access,
                run,
                requested_opportunity_id=opportunity_id,
                request=request,
                program_id=program_id,
            )
            data_access.complete_run(run.id, built["payload"], run=run)
        except SnapshotBuildError as e:
            # Leave no half-built run behind, then decide whether to carry on.
            _discard(data_access, run.id)
            if e.code == "cache_miss":
                # Environmental and identical for every period. Reporting it
                # fifty times would bury the one line that says what to do.
                raise HistoryRebuildError("cache_miss", e.message) from e
            report["failed"] += 1
            report["runs"].append(
                {
                    "period_start": period_start.isoformat(),
                    "period_end": key,
                    "run_id": None,
                    "action": "failed",
                    "error": e.message,
                }
            )
            continue

        for old in mine:
            _discard(data_access, old.id)

        report["replaced" if mine else "created"] += 1
        report["runs"].append(
            {
                "period_start": period_start.isoformat(),
                "period_end": key,
                "run_id": run.id,
                "action": "replaced" if mine else "created",
                "error": None,
            }
        )

    return report


def _tick(progress, done: int, total: int, period_end: date) -> None:
    """Report one period, never letting a dead client fail the rebuild."""
    try:
        progress(done, total, f"building period ending {period_end.isoformat()}")
    except Exception:  # noqa: BLE001 -- telemetry must never fail the work it reports on
        logger.debug("progress callback raised; continuing", exc_info=True)


class _EphemeralRun:
    """A run that exists only in memory, so a preview can use the real build path.

    `build_snapshot_for_run` reads a run's definition id, state, window and owner.
    Handing it one of these computes exactly what completing a run dated `as_of`
    would store -- through the same builder a rebuilt snapshot uses -- while
    creating no record at all.
    """

    id = None
    is_completed = False
    snapshot = None

    def __init__(self, definition_id: int, as_of: date, opportunity_id: int | None):
        self.opportunity_id = opportunity_id
        self.data = {"definition_id": definition_id, "state": {}}
        self.period_start = as_of.isoformat()
        self.period_end = as_of.isoformat()

    @property
    def state(self):
        return self.data["state"]


# What a preview returns out of a payload. Everything else -- the case index, the
# per-worker cells, the monthly series -- is most of a snapshot's megabytes and
# none of what checking a figure needs.
_PREVIEW_KEYS = ("meta", "programInd", "byLLO", "pooledOverCredible", "credibility")
_SERIES_KEYS = ("programme", "byLLO")


def preview_as_of(
    data_access,
    definition_id: int,
    *,
    as_of: date,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    include_opportunities: bool = False,
    request=None,
    progress=None,
    ensure_cache: bool = True,
) -> dict:
    """Grade a workflow's registry as of `as_of`, exactly as a rebuilt run would, and persist nothing.

    This is the check that belongs BEFORE writing history: compare these figures
    against the reference, and only rebuild once they agree. It goes through the
    same `build_snapshot_for_run` the rebuild does, with an in-memory run dated
    `as_of`, so what it shows is what a snapshot for that date will contain --
    not a parallel computation that could agree with the reference while the
    snapshots do not.

    Returns the programme and per-LLO cells for the primary series and every
    extra series (each cell is `{id, value, n, band}`, `n` being its
    denominator), the payload's `meta`, and `cache` -- because a partial cache
    understates every total while looking like a genuine disagreement.
    """
    if (opportunity_id is None) == (program_id is None):
        raise HistoryRebuildError("no_owner", "provide exactly one of opportunity_id / program_id")

    definition = data_access.get_definition(definition_id)
    if definition is None:
        raise HistoryRebuildError("no_definition", f"workflow definition {definition_id} not found")
    ok, reason = eligibility(definition)
    if not ok:
        raise HistoryRebuildError("not_periodic", reason or "workflow cannot be graded at a past date")

    if ensure_cache:
        _ensure_cache(
            data_access,
            definition_id,
            opportunity_id=opportunity_id,
            program_id=program_id,
            progress=progress,
            hold_minutes=DEFAULT_HOLD_MINUTES,
        )

    run = _EphemeralRun(definition_id, as_of, opportunity_id)
    try:
        built = build_snapshot_for_run(
            data_access, run, requested_opportunity_id=opportunity_id, request=request, program_id=program_id
        )
    except SnapshotBuildError as e:
        code = "cache_miss" if e.code == "cache_miss" else "build_failed"
        raise HistoryRebuildError(code, e.message) from e

    state = (built["payload"] or {}).get("state") or {}
    state_key = ((built.get("contract") or {}).get("snapshot_inputs") or {}).get("state_key") or "snapshot"
    payload = state.get(state_key) or {}

    from connect_labs.semantic.workflow_binding import registry_binding

    # Which definitions produced these figures -- a comparison against a reference is
    # meaningless without knowing whether it ran on a bound record or the on-disk copy.
    out: dict = {"definition_id": definition_id, "as_of": as_of.isoformat(), "registry": registry_binding(definition)}
    for key in _PREVIEW_KEYS:
        if key in payload:
            out["programme" if key == "programInd" else key] = payload[key]
    if include_opportunities:
        out["byOpp"] = payload.get("byOpp")
    out["series"] = {}
    for name, series in (payload.get("series") or {}).items():
        keep = {k: series[k] for k in _SERIES_KEYS if k in series}
        if include_opportunities and "byOpp" in series:
            keep["byOpp"] = series["byOpp"]
        out["series"][name] = keep
    out["cache"] = cache_state(built.get("opportunity_ids"))
    return out


def _discard(data_access, run_id: int) -> None:
    """Delete a run, never masking the error that led here."""
    try:
        data_access.delete_run(run_id)
    except Exception:  # noqa: BLE001
        logger.warning("could not delete workflow run %s during history rebuild", run_id, exc_info=True)
