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

from connect_labs.semantic.legacy import DEFAULT_REGISTRY_NAME


class SemanticBindingError(Exception):
    """A workflow cannot be evaluated, with a caller-reportable reason.

    Carries `reason` so the view can keep returning a 400 that names WHICH pipeline
    could not be read — the difference between a fix and a guess.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def resolve_registry_for(definition, registry_access_factory=None, registry_id_override: int | None = None):
    """Resolve the registry a workflow computes from, and its deployment facts.

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

    Returns `(properties, indicators, llo_map, settings, deployment, source)`.
    `deployment` is the whole fact set — llo_map, settings, app_asks, asks_as — and
    it is returned because the availability gates read `app_asks`, which used to be
    a static dict in `semantic/gates.py`. A workflow bound to a RECORD therefore
    took its bands from the record and its gates from the repo, with nothing to
    notice when they disagreed.
    """
    from connect_labs.semantic.runtime import resolve_registry

    source = dict(getattr(definition, "registry_source", None) or {}) if definition else {}
    if registry_id_override is not None:
        source = {"registry_id": int(registry_id_override)}

    access = None
    if source.get("registry_id") and registry_access_factory is not None:
        access = registry_access_factory()
    try:
        props_doc, full_registry, llo_map, settings, deployment = resolve_registry(source, access)
    finally:
        if access is not None and hasattr(access, "close"):
            access.close()
    return props_doc, full_registry, llo_map, settings, deployment, source


def build_evaluate_inputs(
    definition, pipeline_access_factory, *, props_doc: dict[str, Any]
) -> tuple[Any, dict[str, Any] | None]:
    """Return `(pipeline_config, extra_fields)` for `semantic.runtime.evaluate`.

    Which pipelines those are is the registry's `pipelines` model:

      entity        the alias of the pipeline Layer 1 is generated from. It carries
                    the fallback path lists, which are the expensive part and the
                    thing a hand-written extraction has repeatedly lost.
      extra_fields  column -> alias of another pipeline that supplies it. KMC's
                    per-visit WEIGHT is not in its entity pipeline -- the weight
                    series is its own pipeline -- and its properties are written
                    against a `weight_g` column; without this the compiled SQL
                    fails with `column "weight_g" does not exist`.

    An extra-field pipeline the workflow does not carry is skipped, as it always
    was: the compile then names the missing column.
    """
    from connect_labs.semantic.model import resolve_model

    model = resolve_model(props_doc)
    sources = getattr(definition, "pipeline_sources", None) or []
    alias = model.entity_pipeline
    if not alias:
        raise SemanticBindingError(
            "the registry declares no entity pipeline (properties_doc.pipelines.entity), so there is "
            "nothing to generate Layer 1 from"
        )
    entity_source = next((s for s in sources if s.get("alias") == alias), None)
    # Checked BEFORE any data access is constructed. Constructing one needs an OAuth
    # token, so doing it first turns "this workflow has no entity pipeline" — a
    # reportable 400 — into a 500 about credentials.
    if not entity_source:
        raise SemanticBindingError(f"workflow has no entity pipeline source (alias {alias!r})")

    pipeline_access = pipeline_access_factory()
    # A source may name where its pipeline lives -- a synthetic workflow on a real
    # pipeline -- and Layer 1 must read the same record the pipelines run.
    if hasattr(pipeline_access, "use_sources"):
        pipeline_access.use_sources(sources)
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
        extra_fields: dict[str, Any] = {}
        for column, source_alias in model.extra_fields.items():
            source = next((s for s in sources if s.get("alias") == source_alias), None)
            if not source:
                continue
            source_def = pipeline_access.get_definition(source["pipeline_id"])
            if source_def and source_def.schema:
                # Keyed by the column the registry expects, which is also the
                # field's own name in that pipeline.
                extra_fields[column] = pipeline_access._schema_to_config(source_def.schema, source["pipeline_id"])
    except SemanticBindingError:
        raise
    except Exception as exc:
        raise SemanticBindingError(f"entity pipeline schema is not usable ({type(exc).__name__}): {exc}") from exc
    finally:
        pipeline_access.close()

    return pipeline_config, extra_fields or None


def registry_binding(definition) -> dict:
    """Which registry a workflow's indicators come from, stated so a reader cannot miss it.

    `{"source": "record", "registry_id": N, <home scope>}` for a bound record, else
    `{"source": "disk", "name": <name>, "note": ...}`. The note matters: an unbound
    workflow reads the on-disk registry, so an indicator edit made to any record
    does not reach it, and its definitions change only on a deploy. That was the
    whole failure -- real KMC reports on disk, edits going to a record bound only to
    the synthetic workflow -- and it was invisible because no surface said which.
    """
    source = dict(getattr(definition, "registry_source", None) or {}) if definition else {}
    if source.get("registry_id") is not None:
        return {"source": "record", **source}
    return {
        "source": "disk",
        "name": source.get("name") or DEFAULT_REGISTRY_NAME,
        "note": (
            "Unbound: computes from the on-disk registry, which changes only on a deploy. "
            "Edits to a registry record do not reach this workflow until it is bound to one "
            "(workflow_update_definition patch registry_source)."
        ),
    }
