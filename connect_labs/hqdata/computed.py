"""Computed sources: a page over a project reads a table a report computes, beside the pipelines it reads.

A pipeline is a table of what the project's forms hold. Some answers are not that: coverage of a campaign against its
plan, say, is counts per health centre and cycle joined to a target. A page names such a table as a source,
``{"computed": "<name>", "alias": "<alias>", "params": {...}}``, and reads it as ``pipelines.<alias>.rows`` exactly
like a pipeline. Each name is registered here with the one function that computes it, as the signed-in member, over
the project's own store; a page cannot name any other code, and its params are validated by that function.
"""

from __future__ import annotations

from collections.abc import Callable

from connect_labs.hqdata.access import require_project
from connect_labs.hqdata.models import HQProject

#: name -> (validate(params) -> params, compute(user, project, params) -> {"rows": [...], "metadata": {...}})
_REGISTRY: dict[str, tuple[Callable, Callable]] = {}


class ComputedProblem(ValueError):
    """The source cannot be computed; the message says why, for the person who wrote the page."""


def register(name: str, validate: Callable, compute: Callable) -> None:
    _REGISTRY[name] = (validate, compute)


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


def read(user, project: HQProject, source: dict) -> dict:
    """``{"rows", "metadata"}`` for one computed source, as ``user`` (a member of the project)."""
    _load()
    require_project(user, project)
    name = source.get("computed")
    if name not in _REGISTRY:
        return _failed(project, name, f"No computed source named {name!r}.")
    try:
        params = _REGISTRY[name][0](source.get("params") or {})
        out = _REGISTRY[name][1](user, project, params)
    except ComputedProblem as exc:
        return _failed(project, name, str(exc))
    except ValueError as exc:
        return _failed(project, name, str(exc))
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
