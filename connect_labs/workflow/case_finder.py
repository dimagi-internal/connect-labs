"""The case finder: per worker on an opportunity, which cases have a story to coach on.

Owner's loop (2026-10-09; ACE spec "## The loop"): a worker gets about one case
conversation a week, on one case and one story. Labs is deterministic -- which cases
are eligible for which story, and the facts -- and the canopy agent on the
opportunity's workflow plans: it reads this view, proposes one session per worker with
its reason, and the person approves the send.

Labs keeps NO coaching history of its own (owner, 2026-10-09). What the agent needs to
avoid repeating itself is already on record: each saved run's snapshot carries this
view as of that run (``snapshot.caseCoaching``, built by ``snapshot_builders``), and a
case conversation's task records the case and story it was about
(``task.data["case_coaching"]``). The agent reads past runs through the existing tools
(``workflow_history_runs`` with snapshots, ``workflow_action_status``).

``worker_view`` is pure; ``coaching_cases`` reads the visits as the person.
"""

from __future__ import annotations

import datetime as dt
import logging

from connect_labs.workflow import case_coaching as cc

logger = logging.getLogger(__name__)

#: How many example cases per story per worker the view carries (the count is whole).
DEFAULT_PER_STORY = 3
MAX_PER_STORY = 10

#: Rules as the agent is told them: what each story means and what its session does.
STORY_GUIDE = {
    cc.DANGER: (
        "A visit recorded a danger sign and the baby was not referred. The coach explains why that sign "
        "needs a health facility and asks the worker to check on the family and refer if it is still there."
    ),
    cc.WEIGHT_CHECK: (
        "A weighing is hard to believe (an impossible step -- outside -20..45 g/kg/day, the KMC "
        "registry's rule -- the same weight three times in a row, or a weight outside 800-5,000 g). "
        "A non-judgemental data-quality conversation with the weighing checklist. A case with such "
        "a weighing has no other story."
    ),
    cc.FALTERING: (
        "Weight has stalled (under 5 g/kg/day over the last two intervals) and skin-to-skin time fell. "
        "The coach asks how the family is managing, reinforces KMC practice and agrees a sooner visit."
    ),
    cc.THRIVING: (
        "Every interval of 3+ days at 15 g/kg/day or more and the weight up 20% or more. Recognition: "
        "what worked with this family, then next steps."
    ),
}

PLANNING_GUIDE = (
    "One session per worker, about one case and one story. Labs gives eligibility and facts; you plan. "
    "History is yours to read: earlier runs of this workflow carry this same view as of that run "
    "(workflow_history_runs with include_snapshot -> snapshot.caseCoaching), and each case conversation's "
    "task records `case_coaching: {case_id, story}` (workflow_action_status lists a run's sends). Prefer a "
    "worker's spotlight case (the baby coached about last time) when it has new visits since, else rotate to "
    "a story they have not had recently. To send: workflow_run_action with the worker's item "
    "`case: {id, story}` -- and, for a follow-up, `earlier: {date, label, agreed}` from what you read. "
    "The person clicks Send."
)


def _case_row(c: cc.Case, s: cc.Story, latest: dt.date) -> dict:
    return {
        "case_id": c.case_id,
        "case_name": c.name or c.case_id,
        "story": s.key,
        "facts": s.facts,
        "evidence_date": s.evidence_date.isoformat(),
        "days_before_latest": (latest - s.evidence_date).days,
        "last_visit": c.visits[-1].date.isoformat() if c.visits else None,
        "visits": len(c.visits),
    }


def worker_view(
    cases: list[cc.Case],
    *,
    latest_by_opp: dict[int, dt.date],
    names: dict[str, str] | None = None,
    window_days: int = cc.DEFAULT_WINDOW_DAYS,
    per_story: int = DEFAULT_PER_STORY,
    worker_keys: list[str] | None = None,
    guide: bool = True,
) -> dict:
    """Per worker: each eligible story's count and best cases (most recent evidence
    first). A case has one story (its own); a story counts only when its evidence falls
    within ``window_days`` of its opportunity's latest visit. Workers are listed most
    urgent story first."""
    from connect_labs.workflow.agent_sharing import worker_key as wkey

    names = names or {}
    by_worker: dict[str, dict] = {}
    for c in cases:
        latest = latest_by_opp.get(c.opportunity_id)
        if latest is None or not c.username:
            continue
        stories = cc.classify(c)
        if not stories:
            continue
        s = stories[0]
        if s.evidence_date < latest - dt.timedelta(days=window_days):
            continue
        key = wkey(c.opportunity_id, c.username)
        if worker_keys and key not in worker_keys:
            continue
        w = by_worker.setdefault(key, {"key": key, "name": names.get(key) or c.username, "eligible": {}})
        slot = w["eligible"].setdefault(s.key, {"label": s.label, "count": 0, "best": []})
        slot["count"] += 1
        slot["best"].append(_case_row(c, s, latest))

    for w in by_worker.values():
        for slot in w["eligible"].values():
            slot["best"].sort(key=lambda r: (r["days_before_latest"], r["case_name"]))
            slot["best"] = slot["best"][:per_story]
        w["eligible"] = {k: w["eligible"][k] for k in cc.STORIES if k in w["eligible"]}

    def urgency(w: dict) -> tuple:
        first = next(iter(w["eligible"]))
        return (cc.STORIES.index(first), -w["eligible"][first]["count"], w["name"])

    workers = sorted(by_worker.values(), key=urgency)
    out = {
        "window_days": window_days,
        "latest_visit": {str(k): v.isoformat() for k, v in sorted(latest_by_opp.items())},
        "counts": {k: sum(w["eligible"].get(k, {}).get("count", 0) for w in workers) for k in cc.STORIES},
        "workers": workers,
    }
    if guide:
        out["stories"] = {k: {"label": cc.LABELS[k], "means": STORY_GUIDE[k]} for k in cc.STORIES}
        out["planning"] = PLANNING_GUIDE
    return out


# ---------------------------------------------------------------------------
# Reading it as the person
# ---------------------------------------------------------------------------


class FinderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.public_message = message


def config_for(definition, *, constants: dict | None = None, access_token: str | None = None) -> dict | None:
    """The workflow's case-coaching config, with the bound registry's ``constants``
    (the impossible-step thresholds, ``case_coaching.rules_from_constants``): given, or
    read with ``access_token`` -- best effort; the mirrored defaults otherwise."""
    from connect_labs.workflow.templates import with_inherited_config_flags

    data = with_inherited_config_flags(
        getattr(definition, "data", None) or {}, getattr(definition, "template_type", None)
    )
    config = cc.config_of(data.get("config") or {})
    if config is None:
        return None
    if constants is None and access_token is not None:
        constants = registry_constants(definition, access_token)
    return {**config, "constants": constants} if constants else config


def registry_constants(definition, access_token: str | None) -> dict | None:
    """The bound registry's constants (``properties.constants``), or None."""
    from connect_labs.semantic.workflow_binding import resolve_registry_for
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    try:
        props_doc, *_ = resolve_registry_for(
            definition,
            registry_access_factory=lambda: SemanticRegistryDataAccess(
                access_token=access_token,
                opportunity_id=getattr(definition, "opportunity_id", None),
                program_id=(
                    None if getattr(definition, "opportunity_id", None) else getattr(definition, "program_id", None)
                ),
            ),
        )
    except Exception:  # noqa: BLE001 -- the mirrored thresholds are the fallback
        logger.warning("case coaching: could not read the registry constants", exc_info=True)
        return None
    constants = (props_doc or {}).get("constants")
    return constants if isinstance(constants, dict) else None


def data_opportunities(definition, run) -> list[int]:
    from connect_labs.workflow.visit_cache import workflow_opportunity_ids

    return workflow_opportunity_ids(definition, getattr(run, "opportunity_id", None))


def coaching_cases(
    user,
    *,
    wda,
    run,
    definition,
    access_token: str | None,
    request=None,
    worker_keys: list[str] | None = None,
    window_days: int = cc.DEFAULT_WINDOW_DAYS,
    per_story: int = DEFAULT_PER_STORY,
) -> dict:
    """The per-worker view for a workflow run, read live as ``user``."""
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.workflow import case_visits
    from connect_labs.workflow.actions import run_roster
    from connect_labs.workflow.agent_sharing import split_worker_key

    config = config_for(definition, access_token=access_token)
    if config is None:
        raise FinderError(
            "no_case_coaching", "This workflow has no case coaching (its config has no `case_coaching` block)."
        )
    opps = data_opportunities(definition, run)
    if worker_keys:
        opps = sorted({split_worker_key(k)[0] for k in worker_keys} & set(opps))
    if not opps:
        raise FinderError("no_opportunities", "There is no opportunity of this workflow to look for cases in.")
    caller = Caller(user=user, request=request, access_token=access_token)
    only = split_worker_key(worker_keys[0])[1] if worker_keys and len(worker_keys) == 1 else None
    try:
        rows = case_visits.load_rows(caller, opps, config, username=only)
        latest = case_visits.latest_visit_dates(caller, opps)
    except case_visits.CaseDataError as e:
        raise FinderError(e.code, e.public_message) from e
    roster = run_roster(wda, run, definition)
    view = worker_view(
        cc.cases_from_rows(rows, config),
        latest_by_opp=latest,
        names={k: w["name"] for k, w in roster.items()},
        window_days=window_days,
        per_story=per_story,
        worker_keys=worker_keys,
    )
    view["opportunity_ids"] = opps
    return view


def snapshot_view(
    definition, opportunity_ids: list[int], *, as_of: str | None, names: dict | None, constants: dict | None = None
) -> dict | None:
    """The per-worker view as of a saved run's period end, for its snapshot
    (``snapshot.caseCoaching``). The builder runs where the run's own visits are already
    authorised, so this reads the cache directly. None when the workflow has no case
    coaching."""
    from connect_labs.workflow import case_visits

    config = config_for(definition, constants=constants)
    if config is None:
        return None
    rows = case_visits.load_rows_for(opportunity_ids, config, as_of=as_of)
    latest = case_visits.latest_from_rows(rows)
    view = worker_view(cc.cases_from_rows(rows, config), latest_by_opp=latest, names=names, guide=False)
    view["as_of"] = as_of
    return view
