"""Semantic indicators over a CommCare project: the registries, binding and compiler Connect's reports use.

A page over a project names an indicator source beside its pipeline sources::

    {"alias": "kpis", "indicators": {"registry_id": 12}, "scopes": ["worker", "programme"], "series": "SMC"}

``indicators`` binds a registry exactly as a Connect workflow's ``registry_source`` does: a record by id
(saved in the project with ``commcare_registry_create``, or another's shared one with ``"public": true``),
or a built-in by ``name``. The registry's ``pipelines.entity`` (and any ``extra_fields`` / ``lookups``)
name other sources of the same page by alias, so Layer 1 is generated from the project's own saved
pipelines (``semantic.workflow_binding.build_evaluate_inputs``) and compiled by ``semantic.runtime.evaluate``
over the project's scope. Before it runs, each of those pipelines' raw slots is brought up to date with
what the project's syncs changed (``incremental.maintain_raw``), so the figures are current without a
cache to warm.

The page reads ``pipelines.<alias>.rows`` (one row per scope key, as ``evaluate`` returns them) and
``pipelines.<alias>.metadata.measures`` (titles, units, bands), and a saved run freezes them with the rest.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from connect_labs.hqdata.models import HQProject
from connect_labs.hqdata.scope import hq_scope_id

logger = logging.getLogger(__name__)

_SPEC_KEYS = {"registry_id", "name", "public"}


class IndicatorProblem(ValueError):
    """An indicator source that cannot be evaluated, said for the person (or AI) who wrote it."""


def clean_indicator_source(source: dict) -> dict:
    """A page's indicator source with only what it may carry; raises ``IndicatorProblem``."""
    spec = source.get("indicators")
    if not isinstance(spec, dict) or not spec or set(spec) - _SPEC_KEYS:
        raise IndicatorProblem(
            'An indicator source names its registry: {"registry_id": N} (optionally "public": true) or {"name": ...}.'
        )
    if ("registry_id" in spec) == ("name" in spec):
        raise IndicatorProblem("Name the registry by registry_id or by name, not both.")
    if "registry_id" in spec and (isinstance(spec["registry_id"], bool) or not isinstance(spec["registry_id"], int)):
        raise IndicatorProblem("registry_id is a number.")
    if "name" in spec and not isinstance(spec["name"], str):
        raise IndicatorProblem("name is the name of a built-in registry.")
    from connect_labs.semantic.compiler import SCOPES

    scopes = source.get("scopes") or []
    if not isinstance(scopes, list) or any(s not in SCOPES for s in scopes):
        raise IndicatorProblem(f"scopes is a list drawn from {', '.join(SCOPES)}.")
    series = source.get("series")
    if series is not None and not isinstance(series, str):
        raise IndicatorProblem("series is the name of one indicator family in the registry.")
    out = {"alias": source.get("alias"), "indicators": {k: spec[k] for k in spec if k in _SPEC_KEYS}}
    if scopes:
        out["scopes"] = scopes
    if series:
        out["series"] = series
    return out


def registry_dao(user, project: HQProject):
    """The project's semantic registries, for a verified member (``workflows.project_dao``)."""
    from connect_labs.hqdata.workflows import project_dao
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    return project_dao(user, project, SemanticRegistryDataAccess)


def _store_configs(pipeline_config, extra_fields: dict | None):
    """Every pipeline config the evaluation reads a raw slot of: the entity's, extra fields', lookups'."""
    from connect_labs.semantic.layer1 import LOOKUPS_KEY

    yield pipeline_config
    for key, value in (extra_fields or {}).items():
        if key == LOOKUPS_KEY:
            yield from (value or {}).values()
        else:
            yield value


def _carry(user, project: HQProject, config) -> None:
    """A big first read of a pipeline's slot goes to the background (``catchup``): raises ``ComputedWarming`` then."""
    from connect_labs.hqdata.catchup import ensure_current
    from connect_labs.hqdata.pipelines import find_pipeline

    record = find_pipeline(user, project, pipeline_id=config.pipeline_id)
    if record is not None:
        ensure_current(user, project, record.schema, config, raw_only=True)


def evaluate_source(user, project: HQProject, definition, source: dict, *, as_of: str = "CURRENT_DATE") -> dict:
    """``{"rows", "metadata"}`` for one indicator source of a project page. Raises ``IndicatorProblem``."""
    from connect_labs.hqdata.workflows import pipeline_dao
    from connect_labs.labs.analysis.backends.sql.incremental import maintain_raw, maintains_in_place
    from connect_labs.semantic.runtime import (
        SemanticRuntimeError,
        _plain,
        evaluate,
        filter_to_series,
        measure_catalog,
    )
    from connect_labs.semantic.workflow_binding import (
        SemanticBindingError,
        build_evaluate_inputs,
        deployment_facts_for_render,
        resolve_registry_for,
    )

    source = clean_indicator_source(source)
    scope = hq_scope_id(project.pk)
    scopes, series = source.get("scopes") or [], source.get("series")
    try:
        props_doc, registry, llo_map, settings, deployment, binding = resolve_registry_for(
            SimpleNamespace(registry_source=dict(source["indicators"])),
            registry_access_factory=lambda: registry_dao(user, project),
        )
        pipeline_config, extra_fields = build_evaluate_inputs(
            definition, lambda: pipeline_dao(user, project), props_doc=props_doc
        )
        for config in _store_configs(pipeline_config, extra_fields):
            if config is not None and maintains_in_place(config):
                _carry(user, project, config)
                maintain_raw(config, scope)
        rows = evaluate(
            pipeline_config,
            [scope],
            extra_fields=extra_fields,
            registry_documents=(props_doc, registry),
            series=series,
            scopes=scopes or None,
            scope=scopes[0] if scopes else "programme",
            as_of=as_of,
            llo_map=llo_map or None,
            settings=settings or None,
        )
        catalog = measure_catalog(filter_to_series(registry, series) if series else registry)
    except SemanticBindingError as exc:
        raise IndicatorProblem(exc.reason) from exc
    except SemanticRuntimeError as exc:
        raise IndicatorProblem(str(exc)) from exc
    return {
        "rows": [{k: _plain(v) for k, v in row.items()} for row in rows],
        "metadata": {
            "kind": "indicators",
            "measures": catalog,
            "deployment": deployment_facts_for_render(deployment),
            "registry": binding,
            "scopes": scopes or ["programme"],
            "series": series or "all",
            "row_count": len(rows),
            "opportunity_ids": [scope],
        },
    }


def indicator_entry(user, project: HQProject, definition, source: dict) -> dict:
    """``evaluate_source``, with a failure shown on this source alone (as a pipeline source shows its own)."""
    try:
        return evaluate_source(user, project, definition, source)
    except IndicatorProblem as exc:
        return {
            "rows": [],
            "metadata": {"kind": "indicators", "row_count": 0, "error": str(exc), "measures": []},
        }
