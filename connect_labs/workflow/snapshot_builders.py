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
import re

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

    # The DEFINITION is read by its OWNER, not by the data anchor. `opportunity_id`
    # here is opportunity_ids[0] — where the pipelines live and the rows come from —
    # and a by-id read filters on it whenever it is set. A program-owned report
    # (created from the programme page; #1699) has no opportunity FK at all, so
    # reading it through the anchor found nothing and every such run failed with
    # "could not be read" while its opp-owned twin worked. Observed on workflow
    # 5626 / run 5631, the first program-owned KMC report. Pipelines stay on the
    # anchor: they are always opportunity-owned.
    owner_scope = {"program_id": program_id} if program_id else {"opportunity_id": opportunity_id}

    wda = WorkflowDataAccess(request=request, access_token=access_token, **owner_scope)
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

    # `series` is one family or several. The FIRST is the primary -- it drives
    # programInd / byLLO / byOpp / byFLW / the trend -- and every further one is
    # graded from the SAME rows into `payload.series[<name>]`, so a template can
    # carry its headline registry and a scorecard registry from one evaluation.
    declared = spec.get("series") or "C"
    series_list = [str(x).upper() for x in (declared if isinstance(declared, list) else [declared])]
    primary = series_list[0]

    # AS OF the run's period end. Every maturity gate and, since the compiler
    # change that came with this, the visit set itself are cut at that date -- so
    # a run saved for a past week reports that week, not the day it was saved.
    as_of_date = as_of_iso(context.get("period_end"))
    as_of = f"DATE '{as_of_date}'" if as_of_date else "CURRENT_DATE"

    # ONE pass over every scope a saved run can drill to. GROUPING SETS exist
    # precisely because per-scope calls re-run the whole Layer 1 extraction, and
    # evaluating with no series filter returns every family in that one pass.
    scopes = list(spec.get("scopes") or ["programme"])
    rows = evaluate(
        pipeline_config,
        opportunity_ids,
        extra_fields=extra_fields,
        registry_documents=(props_doc, full_registry),
        series=primary if len(series_list) == 1 else None,
        scopes=scopes,
        scope=scopes[0],
        as_of=as_of,
        llo_map=llo_map or None,
        settings=reg_settings or None,
    )

    measures = measure_catalog(filter_to_series(full_registry, primary))
    extra_series = {name: measure_catalog(filter_to_series(full_registry, name)) for name in series_list[1:]}
    cases = snap.case_rows(pipelines, spec, {int(k): v for k, v in (llo_map or {}).items()})
    visits_alias = spec.get("visits_pipeline")
    visits = ((pipelines or {}).get(visits_alias) or {}).get("rows") or [] if visits_alias else []
    # The pipeline cache is all-time, so the case index and the visit rows must be
    # cut at the same date the evaluation was. Without this a run for a past week
    # reported today's case and visit counts in its banner and let the drill open
    # babies who had not been registered yet.
    cases = cut_as_of(cases, ("reg_date", "first_visit_date"), as_of_date)
    visits = cut_as_of(visits, ("visit_date",), as_of_date)

    meta = {
        "cases": len(cases),
        "visits": len(visits),
        "opportunities": len(opportunity_ids),
        "llos": len({c.get("llo") for c in cases if c.get("llo")}),
        # The date every figure is AS OF. None means "the day it was built".
        "as_of": as_of_date,
    }
    synthetic = _is_synthetic(opportunity_ids)
    if synthetic is not None:
        # The render reads `meta.synthetic` to show its "built on synthetic clones"
        # disclaimer. A live run computes it from scope; a saved run can only know
        # what was captured, so omitting it published a synthetic cohort with the
        # disclaimer silently absent.
        meta["synthetic"] = synthetic

    payload = snap.build(
        spec=spec,
        rows=rows,
        measures=measures,
        deployment=deployment,
        cases=cases,
        meta=meta,
        visit_rows=visits,
        extra_series=extra_series,
        as_of=as_of_date,
    )
    return wrap_for_runner(payload, spec.get("state_key"))


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def cut_as_of(rows: list[dict], date_fields: tuple[str, ...], as_of: str | None) -> list[dict]:
    """Rows whose first present date field is on or before `as_of` (ISO date).

    A row with none of the fields is kept: an undated case is a data-quality fact
    to show, not a reason to hide it. No `as_of` means no cut.
    """
    if not as_of:
        return rows
    out = []
    for r in rows:
        d = next((str(r.get(f))[:10] for f in date_fields if r.get(f)), None)
        if d is None or d <= as_of:
            out.append(r)
    return out


def as_of_iso(period_end) -> str | None:
    """The ISO date a run reports AS OF, or None for "today".

    Accepts a date, a datetime, or their ISO strings, and returns only the
    `YYYY-MM-DD` prefix -- the value is spliced into SQL as a `DATE '...'`
    literal, so anything that is not exactly a date is refused rather than
    passed through.
    """
    if not period_end:
        return None
    s = str(period_end)[:10]
    return s if _ISO_DATE.match(s) else None


def wrap_for_runner(payload: dict, state_key: str | None = None) -> dict:
    """Put a graded payload where a completed run's VIEW will find it.

    This is not the same shape as the payload. `workflow-runner.tsx` builds a
    completed run's view from `instance.snapshot` as `{workers?, pipelines?,
    state?}` and sets `state: snapshot.state ?? instanceState` (:1591), so render
    code reading `view.state.<key>` only resolves if the stored snapshot carries
    `state`.

    Returning the graded payload bare puts every key one level too high: `state` is
    undefined, the view falls back to the run's own (empty) state, the render sees no
    snapshot and silently renders its LIVE path instead. On a completed run that
    means LLO names reading "opp 10021" and every indicator an em-dash, with no error
    anywhere -- which is exactly what the first saved run of this dashboard did, and
    why this is verified by opening the page rather than by the write returning 200.

    The template's predecessor hook had the same defect: it returned
    `{"snapshot": ...}`, also missing `state`. Nothing caught it because no saved run
    had ever been rendered.

    `state_key` is spec-driven so a template names its own key rather than the
    framework assuming one.
    """
    return {"state": {state_key or "snapshot": payload}, "pipelines": {}, "workers": []}


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

# Builders whose payload is a FUNCTION OF `period_end` -- the ones for which
# building a run dated in the past yields THAT date's figures rather than
# today's. `semantic_snapshot` qualifies because its `as_of` cuts the visit set,
# every maturity gate, the case index and the visit rows alike.
#
# This exists so `history_rebuild` can refuse the workflows it must not touch.
# Rebuilding a period series against a builder that ignores the date writes N
# identical snapshots, which the trend then draws as a flat line across real
# dates -- a chart that looks like a programme which did not move, with nothing
# anywhere to say otherwise. That is the only place the mistake is catchable, so
# the declaration lives HERE, beside the builder it describes, rather than in
# the rebuild code where it could drift from what the builder actually does.
#
# `test_periodic_builders.py` holds the proof, not just the claim: it asserts
# each declared builder carries its run's period end into the evaluation.
PERIODIC_BUILDERS = {"semantic_snapshot"}

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
        "state_key",
    },
}
