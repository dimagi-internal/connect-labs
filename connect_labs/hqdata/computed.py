"""Computed sources: a page over a project reads a table a report computes, beside the pipelines it reads.

A pipeline is a table of what the project's forms hold. Some answers are not that: coverage of a campaign against its
plan, say, is counts per health centre and cycle joined to a target. A page names such a table as a source,
``{"computed": "<name>", "alias": "<alias>", "params": {...}}``, and reads it as ``pipelines.<alias>.rows`` exactly
like a pipeline. Each name is registered here with the one function that computes it, as the signed-in member, over
the project's own store; a page cannot name any other code, and its params are validated by that function.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from django.core.cache import cache

from connect_labs.hqdata.access import require_project
from connect_labs.hqdata.models import HQProject

#: name -> (validate(params) -> params, compute(user, project, params, report) -> {"rows": [...], "metadata": {...}})
_REGISTRY: dict[str, tuple[Callable, Callable]] = {}
#: Names whose compute is too slow for a request: read from a kept result, or filled in the background (see ``read``).
_SLOW: set[str] = set()
#: Each source's version (``register``), part of where its results are kept.
_VERSIONS: dict[str, int] = {}
#: How long a filled result is kept. It is also keyed by when the project last synced, so new data never reads old.
RESULT_SECONDS = 24 * 60 * 60


class ComputedContinues(Exception):
    """A fill used its time budget with its progress saved: it stops cleanly and the next read starts the next part."""


class ComputedWarming(Exception):
    """The source is being computed in the background; ask again. Carries what the fill says it is doing."""

    def __init__(self, state, message: str):
        super().__init__(message)
        self.state = state
        self.message = message


class ComputedProblem(ValueError):
    """The source cannot be computed; the message says why, for the person who wrote the page."""


#: What a person calls each source while waiting for it.
LABELS = {"campaign_coverage": "Coverage figures", "campaign_reach": "Satellite check"}


def label(source: dict) -> str:
    return LABELS.get(source.get("computed"), source.get("alias") or "Computing")


def merge_warming(waiting: list[tuple[str, ComputedWarming]]) -> ComputedWarming:
    """One ``ComputedWarming`` for several sources that are all being computed: what each is doing, side by side.

    Every source's fill is already running (they are all queued before this is raised), so the page waits for the
    slowest and tells the person what each is up to."""
    parts = []
    for alias, w in waiting:
        state = w.state or {}
        parts.append(f"{alias}: {state.get('message') or 'starting'}")
    states = [w.state for _alias, w in waiting if w.state]
    merged = {
        "message": " | ".join(parts)[:200],
        "started_at": min((s.get("started_at") or 0 for s in states), default=0) or None,
        "progress": next((s["progress"] for s in states if s.get("progress")), {}),
    }
    return ComputedWarming(merged, merged["message"])


def register(name: str, validate: Callable, compute: Callable, *, slow: bool = False, version: int = 1) -> None:
    """``version`` is part of where a result is kept: raise it when what the source computes changes, so a result
    kept from before the change (for up to a day) is never shown as if it were the new one."""
    _REGISTRY[name] = (validate, compute)
    _VERSIONS[name] = version
    if slow:
        _SLOW.add(name)


def names() -> list[str]:
    _load()
    return sorted(_REGISTRY)


def _load() -> None:
    # Imported on first use: the registrations live with the reports, which import this package's pieces.
    from connect_labs.hqdata import computed_sources  # noqa: F401


def clean(source: dict) -> dict:
    """A page's computed source, validated: ``{"computed", "alias", "params"}``, or ``ComputedProblem``."""
    _load()
    name = source.get("computed")
    if name not in _REGISTRY:
        raise ComputedProblem(f"computed must be one of {sorted(_REGISTRY)}.")
    if set(source) - {"computed", "alias", "params"}:
        raise ComputedProblem('A computed source is {"computed": <name>, "alias": <alias>, "params": {...}}.')
    try:
        params = _REGISTRY[name][0](source.get("params") or {})
    except ValueError as exc:
        raise ComputedProblem(str(exc)) from exc
    return {"computed": name, "alias": source.get("alias"), "params": params}


def _digest(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]


#: A result is recomputed at most this often as the project syncs (a sync that never ends, like the first read of a big
#: project, would otherwise start a recompute after every batch and finish none).
RECOMPUTE_EVERY_SECONDS = 6 * 60 * 60


def _data_version(project: HQProject) -> str:
    """Which stretch of the project's syncing a result belongs to: the sync moment, in blocks of six hours."""
    if not project.last_synced_at:
        return "never"
    return str(int(project.last_synced_at.timestamp() // RECOMPUTE_EVERY_SECONDS))


def _name(name: str) -> str:
    return f"{name}.v{_VERSIONS.get(name, 1)}"


def result_key(project: HQProject, name: str, params: dict) -> str:
    """Where a slow source's result is kept: by project, name and version, settings and the data it was computed
    from."""
    return f"hqcomputed:{project.pk}:{_name(name)}:{_digest(params)}:{_data_version(project)}"


def latest_key(project: HQProject, name: str, params: dict) -> str:
    """The newest result computed for these settings whatever data it came from: what a page shows while the
    result for the project's newest data is being computed."""
    return f"hqcomputed:{project.pk}:{_name(name)}:{_digest(params)}:latest"


def _shape(project: HQProject, name: str, out: dict) -> dict:
    from connect_labs.hqdata.scope import hq_scope_id

    scope = hq_scope_id(project.pk)
    rows = out["rows"]
    return {
        "rows": rows,
        "metadata": {
            "computed": name,
            "row_count": len(rows),
            "opportunity_ids": [scope],
            "per_opp": {str(scope): {"row_count": len(rows), "from_cache": False}},
            **(out.get("metadata") or {}),
        },
    }


def read(user, project: HQProject, source: dict) -> dict:
    """``{"rows", "metadata"}`` for one computed source, as ``user`` (a member of the project).

    A quick source is computed now. A slow one answers from its kept result, or starts the fill in the background and
    raises ``ComputedWarming`` (the web answers 202 and the page asks again: ``workflow/cache_warm.py``'s protocol),
    so a request never holds a worker for minutes."""
    _load()
    require_project(user, project)
    name = source.get("computed")
    if name not in _REGISTRY:
        return _failed(project, name, f"No computed source named {name!r}.")
    try:
        params = _REGISTRY[name][0](source.get("params") or {})
    except ValueError as exc:
        return _failed(project, name, str(exc))
    if name in _SLOW:
        return _read_slow(user, project, name, params)
    try:
        return _shape(project, name, _REGISTRY[name][1](user, project, params, lambda *a, **k: None))
    except ValueError as exc:
        return _failed(project, name, str(exc))


def _read_slow(user, project: HQProject, name: str, params: dict) -> dict:
    from connect_labs.workflow.cache_warm import WarmSlot

    key = result_key(project, name, params)
    kept = cache.get(key)
    if kept is not None:
        return kept
    slot = WarmSlot(key + ":fill")
    outcome = slot.outcome()
    stale = cache.get(latest_key(project, name, params))
    if outcome and outcome.get("error"):
        slot.take_outcome()  # spent: the next read tries again
        if stale is None:
            return _failed(project, name, outcome["error"])

    def enqueue(token: str):
        from connect_labs.hqdata.tasks import fill_computed_source

        fill_computed_source.delay(project.pk, user.pk, name, params, token)

    slot.queue_once(enqueue)
    if stale is not None:
        # New data arrived since it was computed: show what we have, marked, while the new one is computed.
        return {**stale, "metadata": {**stale["metadata"], "stale": True}}
    raise ComputedWarming(slot.state(), "Starting. The first time takes a few minutes.")


def fill(user, project: HQProject, name: str, params: dict, token: str) -> None:
    """The background fill of one slow source (``tasks.fill_computed_source``): compute, keep, leave an outcome."""
    from connect_labs.workflow.cache_warm import WarmSlot

    _load()
    key = result_key(project, name, params)
    slot = WarmSlot(key + ":fill", token)
    with slot.running() as outcome:
        try:
            done = _shape(project, name, _REGISTRY[name][1](user, project, params, slot.report))
        except ComputedContinues:
            return  # no result and no error: the next read queues the next part, which carries on
        except ValueError as exc:
            outcome["error"] = str(exc)
            return
        cache.set(key, done, RESULT_SECONDS)
        cache.set(latest_key(project, name, params), done, 30 * RESULT_SECONDS)


def _failed(project: HQProject, name, error: str) -> dict:
    from connect_labs.hqdata.scope import hq_scope_id

    scope = hq_scope_id(project.pk)
    return {
        "rows": [],
        "metadata": {
            "computed": name,
            "row_count": 0,
            "opportunity_ids": [scope],
            "per_opp": {str(scope): {"row_count": 0, "from_cache": False, "error": error}},
        },
    }


def refresh(user, project: HQProject) -> list[str]:
    """Start the background fill of every slow computed source the project's pages read, unless a result for the
    project's current data is already kept or a fill is under way. Returns the names started.

    Run on a schedule (``tasks.refresh_computed_sources``) so a page is ready when someone opens it, instead of its
    first viewer starting (and having to wait out) the work."""
    from connect_labs.hqdata.workflows import workflow_dao
    from connect_labs.workflow.cache_warm import WarmSlot

    _load()
    dao = workflow_dao(user, project)
    try:
        sources = [s for d in dao.list_definitions() for s in (d.pipeline_sources or []) if isinstance(s, dict)]
    finally:
        dao.close()
    started, seen = [], set()
    for source in sources:
        name = source.get("computed")
        if name not in _SLOW:
            continue
        try:
            params = _REGISTRY[name][0](source.get("params") or {})
        except ValueError:
            continue
        key = result_key(project, name, params)
        if key in seen or cache.get(key) is not None:
            continue
        seen.add(key)
        slot = WarmSlot(key + ":fill")
        if slot.state() is not None:
            continue
        if slot.outcome():
            slot.take_outcome()  # a failure is retried on the schedule too

        def enqueue(token: str, name=name, params=params):
            from connect_labs.hqdata.tasks import fill_computed_source

            fill_computed_source.delay(project.pk, user.pk, name, params, token)

        slot.queue_once(enqueue)
        started.append(name)
    return started
