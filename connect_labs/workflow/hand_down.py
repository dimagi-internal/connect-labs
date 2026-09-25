"""Hand a saved programme run down to each opportunity's own report.

A programme report's saved run already graded every opportunity in it: the
opportunity's scorecard, its workers, its cases, its weekly and monthly series.
The opportunity report (`kmc_opp_report`) shows exactly that, for one
opportunity, to the people who run it -- who do NOT have access to the
programme report. So the figures are PUSHED at save time, never pulled at read
time: when a programme run completes, a background task running as the person
who saved it cuts each opportunity's slice out of the snapshot and writes it as
a completed run of that opportunity's report. The network manager only ever
reads runs of their own report.

An opportunity report still works with no programme report at all: it has its
own saved runs, built by the same builder over its one opportunity. A handed-
down run is labelled (`meta.handed_down_from`, and `handed_down_from` on the
run's state), so the page can say where its figures came from.

WHICH REPORT RECEIVES A SLICE. In each opportunity the snapshot covers, a
workflow whose template declares `receives_hand_down` and that names this
programme report as its source -- `config.source_workflow_id` on the
definition, or, for a report created before that key existed, a benchmark
cohort containing its opportunity whose `source_workflow_id` is this report.
Naming the source is what stops a slice landing in a report that follows a
different programme.

A SLICE CARRIES ONLY ITS OWN OPPORTUNITY. Every other opportunity's rows, cases,
series and credibility facts are removed, never merely hidden: the run is read
by people who must not see its peers except through the anonymous benchmark.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# A run saved before the KMC indicator set was unified (#2004) carries coded ids
# (C01, N13) and a `series.N` family. The opportunity report reads today's ids
# only, so such a run is not handed down; the programme report translates it on
# read, and the next save hands down a current one.
_LEGACY_ID = re.compile(r"^[CN]\d\d$")

OPP = "opp:"


class HandDownError(Exception):
    """A run that cannot be handed down, and why."""


def _is_legacy(payload: dict) -> bool:
    if "N" in (payload.get("series") or {}):
        return True
    return any(_LEGACY_ID.match(str(k)) for k in (payload.get("programInd") or {}))


def _count(ind) -> tuple[int, int]:
    reds = yellows = 0
    for cell in (ind or {}).values():
        band = (cell or {}).get("band")
        reds += band == "red"
        yellows += band == "yellow"
    return reds, yellows


def _slice_family(block: dict, opp: int, llo: str | None, positions: dict[int, int]) -> dict:
    """One indicator family (the top level, or `series[<name>]`) cut to one opportunity."""
    by_opp = [dict(r) for r in block.get("byOpp") or [] if _int(r.get("opp")) == opp]
    own = by_opp[0] if by_opp else None
    out: dict[str, Any] = {"byOpp": by_opp}
    if own is not None:
        reds, yellows = _count(own.get("ind"))
        out["byLLO"] = [
            {
                "llo": llo or own.get("llo"),
                "ind": own.get("ind") or {},
                "opps": [own],
                "rows": [],
                "reds": reds,
                "yellows": yellows,
            }
        ]
        out["programme"] = own.get("ind") or {}
    else:
        out["byLLO"] = []
        out["programme"] = {}
    workers = []
    for f in block.get("byFLW") or []:
        if _int(f.get("opp")) != opp:
            continue
        f = dict(f)
        rows = f.get("rows") or []
        if rows and isinstance(rows[0], int):
            f["rows"] = [positions[i] for i in rows if i in positions]
        workers.append(f)
    out["byFLW"] = workers
    scoped = (block.get("monthlyByScope") or {}).get(f"{OPP}{opp}") or []
    out["monthlyByScope"] = {"all": scoped, f"{OPP}{opp}": scoped}
    if llo:
        out["monthlyByScope"][f"llo:{llo}"] = scoped
    out["monthly"] = scoped
    return out


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def slice_for_opportunity(payload: dict, opportunity_id: int, *, source: dict | None = None) -> dict:
    """The graded payload of a programme run, cut to ONE opportunity.

    The result is the same shape a saved run of the opportunity report stores, so
    the page reads it the same way: `programInd` is the opportunity's own row,
    `all` in every series is its own series. `source` is recorded as
    `meta.handed_down_from`.
    """
    opp = int(opportunity_id)
    if _is_legacy(payload):
        raise HandDownError("the run predates the unified KMC indicator set (#2004)")
    if not any(_int(r.get("opp")) == opp for r in payload.get("byOpp") or []):
        raise HandDownError(f"opportunity {opp} is not in this run")

    llo_map = (payload.get("deployment") or {}).get("llo_map") or {}
    llo = llo_map.get(str(opp)) or llo_map.get(opp)

    # The case index, and where each kept case moved to: `byFLW[].rows` are
    # positions into it.
    cases, positions = [], {}
    for i, case in enumerate(payload.get("cases") or []):
        if _int((case or {}).get("opportunity_id")) == opp:
            positions[i] = len(cases)
            cases.append(case)

    top = _slice_family(payload, opp, llo, positions)
    series = {
        name: {
            **{k: v for k, v in (block or {}).items() if k == "measures"},
            **_slice_family(block or {}, opp, llo, positions),
        }
        for name, block in (payload.get("series") or {}).items()
    }

    weekly_own = (payload.get("weekly") or {}).get(f"{OPP}{opp}") or []
    weekly = {"all": weekly_own, f"{OPP}{opp}": weekly_own}
    if llo:
        weekly[f"llo:{llo}"] = weekly_own

    deployment = dict(payload.get("deployment") or {})
    deployment["llo_map"] = {str(opp): llo} if llo else {}
    deployment["app_asks"] = {k: v for k, v in (deployment.get("app_asks") or {}).items() if str(k) == str(opp)}
    credibility = {
        ind: {k: v for k, v in (table or {}).items() if k == llo}
        for ind, table in (payload.get("credibility") or {}).items()
    }

    meta = dict(payload.get("meta") or {})
    meta.update(
        {
            "cases": len(cases),
            "visits": sum(int(w.get("visits") or 0) for w in weekly_own),
            "opportunities": 1,
            "llos": 1 if llo else 0,
            "handed_down_from": dict(source or {}),
        }
    )

    out = {k: v for k, v in payload.items() if k not in _REPLACED}
    out.update(
        {
            "programInd": top["programme"],
            "byOpp": top["byOpp"],
            "byLLO": top["byLLO"],
            "byFLW": top["byFLW"],
            "monthly": top["monthly"],
            "monthlyByScope": top["monthlyByScope"],
            "series": series,
            "weekly": weekly,
            "cases": cases,
            "deployment": deployment,
            "credibility": credibility,
            # Pooled over the programme's credible recorders: a programme figure,
            # and one that names which organisations recorded credibly.
            "pooledOverCredible": {},
            "meta": meta,
        }
    )
    return out


# Every key `slice_for_opportunity` rebuilds. Anything NOT listed is carried over
# as it is -- measure catalogs, the schema version, band edges -- so a key the
# builder adds later is either listed here or reviewed: `test_hand_down` pins
# that no key carrying another opportunity's data reaches a slice.
_REPLACED = frozenset(
    {
        "programInd",
        "byOpp",
        "byLLO",
        "byFLW",
        "monthly",
        "monthlyByScope",
        "series",
        "weekly",
        "cases",
        "deployment",
        "credibility",
        "pooledOverCredible",
        "meta",
    }
)


# ── Receivers ────────────────────────────────────────────────────────────────


def _cohort_sources(opportunity_id: int) -> set[int]:
    from connect_labs.benchmarks.models import BenchmarkCohort

    return {
        int(c.source_workflow_id)
        for c in BenchmarkCohort.for_opportunity(opportunity_id)
        if c.source_workflow_id is not None
    }


def names_source(definition, source_workflow_id: int, opportunity_id: int) -> bool:
    """Whether this opportunity report takes its hand-downs from `source_workflow_id`."""
    config = ((getattr(definition, "data", None) or {}).get("config")) or {}
    declared = config.get("source_workflow_id")
    if declared not in (None, ""):
        return _int(declared) == int(source_workflow_id)
    return int(source_workflow_id) in _cohort_sources(opportunity_id)


def receives_hand_down(definition) -> bool:
    from connect_labs.workflow.templates import get_template

    template = get_template(getattr(definition, "template_type", None) or "") or {}
    return bool(template.get("receives_hand_down"))


def receivers(wda_for: Callable[[int], Any], source_workflow_id: int, opportunity_ids) -> dict[int, list]:
    """`{opportunity_id: [(wda, definition), ...]}` -- the reports each slice goes to."""
    out: dict[int, list] = {}
    for opp in opportunity_ids:
        wda = wda_for(int(opp))
        try:
            found = [
                d
                for d in wda.list_definitions()
                if receives_hand_down(d) and names_source(d, source_workflow_id, int(opp))
            ]
        except Exception:  # noqa: BLE001 -- one opportunity must not cost the others
            logger.warning("hand-down could not list workflows in opportunity %s", opp, exc_info=True)
            found = []
        if found:
            out[int(opp)] = [(wda, d) for d in found]
        else:
            wda.close()
    return out


# ── Writing ──────────────────────────────────────────────────────────────────

GENERATED_BY = "hand_down"


def _state_payload(run, state_key: str) -> dict:
    return (((run.snapshot or {}).get("state")) or {}).get(state_key) or {}


def _period(run) -> tuple[str, str]:
    end = str(run.period_end or "")[:10]
    start = str(run.period_start or end)[:10]
    return start, end


def write_slice(
    wda, definition, source_run, payload: dict, *, opportunity_id: int, source_workflow_id: int, state_key: str
) -> dict:
    """Write one opportunity's slice of `source_run` as a completed run of `definition`.

    Idempotent. A report that already holds this source run's slice is left alone;
    one that holds an OLDER hand-down for the same week has it replaced -- the new
    run is completed before the old one is deleted, so a failure never leaves the
    week empty. A run the report saved itself is never touched.
    """
    from connect_labs.workflow.snapshot_builders import wrap_for_runner

    start, end = _period(source_run)
    source = {"workflow_id": int(source_workflow_id), "run_id": int(source_run.id), "as_of": end}
    prior = [
        r
        for r in wda.list_runs(definition.id) or []
        if str(r.period_end or "")[:10] == end and ((r.state or {}).get("handed_down_from") or {})
    ]
    if any(
        int(((r.state or {}).get("handed_down_from") or {}).get("run_id") or 0) == int(source_run.id) for r in prior
    ):
        return {"action": "unchanged", "run_id": None}

    opp = int(opportunity_id)
    sliced = slice_for_opportunity(payload, opp, source=source)
    run = wda.create_run(
        definition_id=definition.id,
        opportunity_id=opp,
        period_start=start,
        period_end=end,
        initial_state={"generated_by": GENERATED_BY, "handed_down_from": source},
    )
    try:
        completed = wda.complete_run(run.id, wrap_for_runner(sliced, state_key), run=run)
        if completed is None:
            raise HandDownError(f"run {run.id} could not be completed")
    except Exception:
        _discard(wda, run.id)
        raise
    for old in prior:
        _discard(wda, old.id)
    return {"action": "replaced" if prior else "created", "run_id": run.id}


def _discard(wda, run_id: int) -> None:
    try:
        wda.delete_run(run_id)
    except Exception:  # noqa: BLE001
        logger.warning("hand-down could not delete run %s", run_id, exc_info=True)


def hand_down_run(
    wda_for: Callable[[int], Any], source_workflow_id: int, source_run, *, state_key: str = "snapshot"
) -> list[dict]:
    """Hand one completed programme run down to every opportunity report that follows it."""
    if not getattr(source_run, "is_completed", False):
        raise HandDownError(f"run {source_run.id} is not completed")
    payload = _state_payload(source_run, state_key)
    if _is_legacy(payload):
        return [{"opportunity_id": None, "action": "skipped", "error": "run predates the unified indicator set"}]
    opps = [_int(r.get("opp")) for r in payload.get("byOpp") or []]
    report: list[dict] = []
    found = receivers(wda_for, source_workflow_id, [o for o in opps if o is not None])
    try:
        for opp, pairs in found.items():
            for wda, definition in pairs:
                try:
                    out = write_slice(
                        wda,
                        definition,
                        source_run,
                        payload,
                        opportunity_id=opp,
                        source_workflow_id=source_workflow_id,
                        state_key=state_key,
                    )
                    report.append({"opportunity_id": opp, "workflow_id": definition.id, **out, "error": None})
                except Exception as exc:  # noqa: BLE001 -- one report must not cost the others
                    logger.warning("hand-down to workflow %s failed", definition.id, exc_info=True)
                    report.append(
                        {"opportunity_id": opp, "workflow_id": definition.id, "action": "failed", "error": str(exc)}
                    )
    finally:
        for pairs in found.values():
            for wda, _d in pairs:
                wda.close()
    return report


def latest_run_per_period(runs) -> list:
    """One completed run per period end, the latest completion winning, oldest period first."""
    best: dict[str, Any] = {}
    for run in runs or []:
        if not getattr(run, "is_completed", False):
            continue
        key = str(run.period_end or "")[:10]
        if not key:
            continue
        if key not in best or str(run.completed_at or "") >= str(best[key].completed_at or ""):
            best[key] = run
    return [best[k] for k in sorted(best)]


# ── Queuing ──────────────────────────────────────────────────────────────────


def hands_down(template_type: str | None) -> bool:
    """Whether saving a run of this template hands slices down to opportunity reports."""
    from connect_labs.workflow.templates import get_template

    return bool((get_template(template_type or "") or {}).get("hands_down_to_opportunity_reports"))


def queue_hand_down(data_access, *, workflow_id, template_type: str | None, run_id: int | None = None) -> bool:
    """Queue a hand-down after a save (`run_id`) or a history rebuild (no `run_id`).

    Never raises: the save that calls this has already succeeded and must stay
    succeeded. The saver's own token travels with the task, because the slices
    are written as that user -- into opportunities they hold.
    """
    try:
        if not hands_down(template_type):
            return False
        from connect_labs.workflow.tasks import hand_down_task

        hand_down_task.delay(
            getattr(data_access, "access_token", None),
            workflow_id=int(workflow_id),
            run_id=run_id,
            opportunity_id=getattr(data_access, "opportunity_id", None),
            program_id=getattr(data_access, "program_id", None),
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("could not queue a hand-down for workflow %s", workflow_id, exc_info=True)
        return False


def run_hand_down(
    access_token: str,
    *,
    workflow_id: int,
    run_id: int | None = None,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    wda_factory: Callable[..., Any] | None = None,
) -> dict:
    """Hand one run (or, without `run_id`, the whole saved history) down.

    The history walk hands down one run per period -- the latest completion -- so a
    rebuilt history and a later hand-saved run for the same week do not both land.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.templates import resolve_snapshot_contract

    make = wda_factory or WorkflowDataAccess
    source_wda = make(access_token=access_token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        definition = source_wda.get_definition(int(workflow_id))
        if definition is None:
            raise HandDownError(f"workflow {workflow_id} could not be read")
        contract = resolve_snapshot_contract(definition)
        state_key = ((contract.get("snapshot_inputs") or {}).get("state_key")) or "snapshot"
        if run_id is not None:
            run = source_wda.get_run(int(run_id))
            runs = [run] if run is not None else []
        else:
            runs = latest_run_per_period(source_wda.list_runs(int(workflow_id)))
    finally:
        source_wda.close()

    def wda_for(opp: int):
        return make(access_token=access_token, opportunity_id=opp)

    report = {"runs": 0, "created": 0, "replaced": 0, "unchanged": 0, "skipped": 0, "failed": 0, "errors": []}
    for run in runs:
        for row in hand_down_run(wda_for, int(workflow_id), run, state_key=state_key):
            report[row["action"] if row["action"] in report else "failed"] += 1
            if row.get("error"):
                report["errors"].append({"run_id": run.id, **row})
        report["runs"] += 1
    report["errors"] = report["errors"][:20]
    return report
