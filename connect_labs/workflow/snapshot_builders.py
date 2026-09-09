"""Named snapshot builders a workflow can select by DECLARING one.

The framework had three snapshot contract sources and none of them could compute:

  definition       `snapshot_inputs` on the definition record — dynamic, patchable
                   with no deploy, and able only to COPY pipeline rows / state /
                   workers verbatim.
  template_inputs  the same manifest, from the repo.
  template_hook    a Python `build_snapshot` in the repo — the only route to a
                   COMPUTED snapshot, and a deploy for every change.

So any template whose snapshot is a computation had to ship code. That is why
`kmc_programme_metrics` carried a 351-line hand-port of its own render's
JavaScript, and why every indicator change was a PR, a merge and a deploy —
precisely the cost registries-as-records was built to remove.

A named builder closes the gap. `snapshot_inputs.builder` selects one of these,
and the rest of `snapshot_inputs` is that builder's spec. Because it rides on the
definition, a template's snapshot becomes editable through
`workflow_update_definition` — no deploy — while the code that executes it stays
generic and shared.

Adding a builder here is a framework capability, not a per-template file. If you
find yourself writing a second one that grades a semantic registry, extend the
spec instead.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SnapshotBuilderError(RuntimeError):
    """A declared builder could not produce a snapshot, with a reportable reason."""


def semantic_snapshot(
    *,
    spec: dict,
    pipelines: dict,
    opportunity_id: int,
    context: dict,
) -> dict:
    """Grade a workflow's bound semantic registry into a saved-run payload.

    Every number comes from the same `evaluate()` the live dashboard calls, through
    the same binding (`semantic/workflow_binding.py`) and the same registry the
    workflow NAMES — so a saved run and the live view cannot disagree about a value
    or about the threshold it was graded against.

    The scope set, the case index, the credibility mapping and the series are all
    spec. Nothing here knows what KMC is.
    """
    from connect_labs.semantic import snapshot as snap
    from connect_labs.semantic.runtime import evaluate, filter_to_series, measure_catalog
    from connect_labs.semantic.workflow_binding import build_evaluate_inputs, resolve_registry_for
    from connect_labs.workflow.data_access import PipelineDataAccess, SemanticRegistryDataAccess, WorkflowDataAccess

    definition_id = context.get("definition_id")
    opportunity_ids = [int(o) for o in (context.get("opportunity_ids") or [opportunity_id])]
    request = context.get("request")
    access_token = context.get("access_token")
    program_id = context.get("program_id")

    # EVERY data accessor below carries the run's scope. On the web path `request`
    # supplies it; on the MCP path there is no request, and an accessor built from a
    # token alone is unscoped — `get_definition` then cannot see the very workflow it
    # was called for. Stated once here rather than at three call sites, because that
    # same omission was made three times in one day.
    scope = {"opportunity_id": opportunity_id, "program_id": program_id}

    wda = WorkflowDataAccess(request=request, access_token=access_token, **scope)
    try:
        definition = wda.get_definition(definition_id)
    finally:
        wda.close()
    if definition is None:
        raise SnapshotBuilderError(f"workflow {definition_id} could not be read")

    pipeline_config, extra_fields = build_evaluate_inputs(
        definition, lambda: PipelineDataAccess(request=request, access_token=access_token, **scope)
    )

    props_doc, full_registry, llo_map, reg_settings, deployment, _source = resolve_registry_for(
        definition,
        registry_access_factory=lambda: SemanticRegistryDataAccess(
            request=request, access_token=access_token, **scope
        ),
    )

    series = spec.get("series") or "C"
    # ONE pass over every scope a saved run can drill to. GROUPING SETS exist
    # precisely because per-scope calls re-run the whole Layer 1 extraction.
    scopes = list(spec.get("scopes") or ["programme"])
    rows = evaluate(
        pipeline_config,
        opportunity_ids,
        extra_fields=extra_fields,
        registry_documents=(props_doc, full_registry),
        series=series,
        scopes=scopes,
        scope=scopes[0],
        llo_map=llo_map or None,
        settings=reg_settings or None,
    )

    measures = measure_catalog(filter_to_series(full_registry, series))
    cases = snap.case_rows(pipelines, spec, {int(k): v for k, v in (llo_map or {}).items()})
    visits_alias = spec.get("visits_pipeline")
    visits = ((pipelines or {}).get(visits_alias) or {}).get("rows") or [] if visits_alias else []

    meta = {
        "cases": len(cases),
        "visits": len(visits),
        "opportunities": len(opportunity_ids),
        "llos": len({c.get("llo") for c in cases if c.get("llo")}),
    }
    synthetic = _is_synthetic(opportunity_ids)
    if synthetic is not None:
        # The render reads `meta.synthetic` to show its "built on synthetic clones"
        # disclaimer. A live run computes it from scope; a saved run can only know
        # what was captured, so omitting it published a synthetic cohort with the
        # disclaimer silently absent.
        meta["synthetic"] = synthetic

    return snap.build(spec=spec, rows=rows, measures=measures, deployment=deployment, cases=cases, meta=meta)


def _is_synthetic(opportunity_ids: list[int]) -> bool | None:
    """True iff EVERY opportunity in scope is a registered synthetic opportunity.

    Asked of the registry that owns the answer rather than ported from the render's
    `Number(opp) >= 10000`, which happens to match `LABS_ONLY_OPP_ID_FLOOR`: a real
    opp above the floor would read as synthetic, and a fixture-backed real opp below
    it (`labs_only=False`) would read as real. `all()` matches the render's
    `opps.every(...)` — a mixed cohort is not "synthetic data" and must not carry
    the disclaimer.

    Returns None when the question cannot be answered, so the flag is ABSENT rather
    than a confident False claiming real programme data.
    """
    if not opportunity_ids:
        return None
    try:
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        known = set(
            SyntheticOpportunity.objects.filter(opportunity_id__in=opportunity_ids, enabled=True).values_list(
                "opportunity_id", flat=True
            )
        )
    except Exception:  # noqa: BLE001 — a disclaimer must not be able to fail a snapshot
        logger.warning("could not determine synthetic status for %s", opportunity_ids, exc_info=True)
        return None
    return all(int(o) in known for o in opportunity_ids)


BUILDERS = {"semantic_snapshot": semantic_snapshot}

# The spec keys each builder accepts, declared BESIDE the builder so the two cannot
# drift. `workflow_update_definition` validates an instance manifest against this,
# which is what lets a builder spec be written to a definition at all -- and so what
# makes a computed snapshot editable without a deploy.
#
# Strictness is kept on purpose: an unrecognised key is refused rather than ignored,
# because a typo'd manifest would otherwise silently change what every completed run
# captures, forever. Widening the allowed set is a deliberate act, here.
BUILDER_SPEC_KEYS = {
    "semantic_snapshot": {
        "series",
        "scopes",
        "case_index",
        "visits_pipeline",
        "credibility",
        "min_denominator_default",
    },
}
