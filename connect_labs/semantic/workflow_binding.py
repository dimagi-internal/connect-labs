"""One place that turns a workflow definition into `evaluate()` arguments.

Both callers that evaluate the registry for a workflow must build these the SAME
way, or a frozen run and the live dashboard disagree about a number:

  * `workflow/views.py:semantic_indicators_api` — the live dashboard
  * `templates/kmc_snapshot` via the template's `build_snapshot` hook — the saved run

They were about to be two copies. Today's APP_ASKS bug was exactly that shape: one
copy in `gates.py`, one in the render JS, and the render's was the one that decided
what a user saw. So this is the single source, and the pieces that had to stay in the
view are only its HTTP error formatting.
"""

from __future__ import annotations

from typing import Any


class SemanticBindingError(Exception):
    """A workflow cannot be evaluated, with a caller-reportable reason.

    Carries `reason` so the view can keep returning a 400 that names WHICH pipeline
    could not be read — the difference between a fix and a guess.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def resolve_registry_for(definition, registry_access_factory=None, registry_id_override: int | None = None):
    """Resolve the registry a workflow computes from, and its llo_map + settings.

    A workflow BINDS a registry (`definition.registry_source`), and that binding is the
    whole point of registries-as-records: indicators become editable without a deploy.
    Anything that evaluates on a workflow's behalf has to honour it, or it computes
    from the on-disk copy while the dashboard computes from the record — and the two
    disagree silently, which is the failure mode a snapshot can least afford.

    `llo_map` and `settings` travel WITH the registry deliberately: without them the
    `llo` scope cannot compile at all, and `_suppression_columns` returns early on
    falsy settings, publishing a mortality figure for an LLO the workbook says does
    not record deaths credibly.

    `registry_id_override` supports reading a CANDIDATE registry against real data
    before it is bound — the dry run that makes editing indicators live safe.
    """
    from connect_labs.semantic.runtime import resolve_registry

    source = dict(getattr(definition, "registry_source", None) or {}) if definition else {}
    if registry_id_override is not None:
        source = {"registry_id": int(registry_id_override)}

    access = None
    if source.get("registry_id") and registry_access_factory is not None:
        access = registry_access_factory()
    try:
        props_doc, full_registry, llo_map, settings = resolve_registry(source, access)
    finally:
        if access is not None and hasattr(access, "close"):
            access.close()
    return props_doc, full_registry, llo_map, settings, source


def build_evaluate_inputs(definition, pipeline_access_factory) -> tuple[Any, dict[str, Any] | None]:
    """Return `(pipeline_config, extra_fields)` for `semantic.runtime.evaluate`.

    The ENTITY pipeline is the one Layer 1 is generated from — it carries the fallback
    path lists, which are the expensive part and the thing a hand-written extraction
    has repeatedly lost.

    The per-visit WEIGHT is not in it. The entity pipeline carries the registration
    fields and the visit markers; the weight series is its own pipeline, and
    properties.yml is written against a `weight_g` column. Without `extra_fields` the
    compiled SQL fails with `column "weight_g" does not exist`, hinting at the entity
    pipeline's list-valued `weights`, which is a different thing.
    """
    sources = getattr(definition, "pipeline_sources", None) or []
    entity_source = next((s for s in sources if s.get("alias") == "children"), None)
    # Checked BEFORE any data access is constructed. Constructing one needs an OAuth
    # token, so doing it first turns "this workflow has no entity pipeline" — a
    # reportable 400 — into a 500 about credentials.
    if not entity_source:
        raise SemanticBindingError("workflow has no entity pipeline source (alias 'children')")

    pipeline_access = pipeline_access_factory()
    try:
        pipeline_def = pipeline_access.get_definition(entity_source["pipeline_id"])
    except Exception as exc:
        pipeline_access.close()
        raise SemanticBindingError(
            f"entity pipeline {entity_source['pipeline_id']} could not be read ({type(exc).__name__})"
        ) from exc

    if not pipeline_def or not pipeline_def.schema:
        pipeline_access.close()
        raise SemanticBindingError("entity pipeline has no schema")

    try:
        # Same conversion get_pipeline_data runs before executing a pipeline, so the
        # extraction the semantic layer compiles over is the extraction the dashboard's
        # own pipeline runs — the entire reason Layer 1 is generated, not hand-written.
        pipeline_config = pipeline_access._schema_to_config(pipeline_def.schema, entity_source["pipeline_id"])
        extra_fields = None
        visit_source = next((s for s in sources if s.get("alias") == "visits"), None)
        if visit_source:
            visit_def = pipeline_access.get_definition(visit_source["pipeline_id"])
            if visit_def and visit_def.schema:
                visit_config = pipeline_access._schema_to_config(visit_def.schema, visit_source["pipeline_id"])
                # Keyed by the column properties.yml expects, which is also the
                # field's own name in that pipeline.
                extra_fields = {"weight_g": visit_config}
    except SemanticBindingError:
        raise
    except Exception as exc:
        raise SemanticBindingError(f"entity pipeline schema is not usable ({type(exc).__name__}): {exc}") from exc
    finally:
        pipeline_access.close()

    return pipeline_config, extra_fields
