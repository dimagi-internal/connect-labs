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
import time

logger = logging.getLogger(__name__)


class SnapshotBuilderError(RuntimeError):
    """A declared builder could not produce a snapshot, with a reportable reason."""


def _resolve_semantic(spec: dict, opportunity_id: int, context: dict) -> dict:
    """Everything a semantic evaluation of this workflow needs, read once.

    Shared by `semantic_snapshot` (a whole saved run) and `opportunity_cases` (one
    week's case list for hand-down), so the two always grade the same registry over
    the same pipelines.
    """
    from connect_labs.semantic.model import resolve_model, series_prefixes
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

    # A history rebuild passes one `memo` per batch: the definition, the registry and
    # the evaluation inputs do not depend on the period, so they are read once for
    # the batch. Only `evaluate()` below runs per period.
    memo = context.get("memo")

    def _once(key, load):
        if memo is None:
            return load()
        if key not in memo:
            memo[key] = load()
        return memo[key]

    def _read_definition():
        wda = WorkflowDataAccess(request=request, access_token=access_token, **owner_scope)
        try:
            return wda.get_definition(definition_id)
        finally:
            wda.close()

    definition = _once(("builder_definition", definition_id), _read_definition)
    if definition is None:
        raise SnapshotBuilderError(f"workflow {definition_id} could not be read")

    props_doc, full_registry, llo_map, reg_settings, deployment, _source = _once(
        ("registry", definition_id),
        lambda: resolve_registry_for(
            definition,
            # The registry is read by its OWNER too, for the reason the definition is:
            # a record seeded at creation lives in the workflow's own scope, and a
            # program-owned workflow's scope has no opportunity. Reading through the
            # data anchor (opportunity + program) matched nothing, so every program-
            # owned report with a SEEDED registry failed to save ("no semantic registry
            # with id ..."); reports bound to a public record were unaffected. A binding
            # that names a home scope still reads there (runtime.resolve_registry).
            registry_access_factory=lambda: SemanticRegistryDataAccess(
                request=request, access_token=access_token, **owner_scope
            ),
        ),
    )
    # Which pipelines feed Layer 1 is the registry's own model, so it is resolved
    # first.
    pipeline_config, extra_fields = _once(
        ("evaluate_inputs", definition_id),
        lambda: build_evaluate_inputs(
            definition,
            lambda: PipelineDataAccess(request=request, access_token=access_token, **scope),
            props_doc=props_doc,
        ),
    )
    model = resolve_model(props_doc, full_registry)
    # A spec may leave the registry-shaped keys out (the generic indicator
    # templates do): they are then derived from the registry's own model and the
    # pipelines it names, so one template serves any registry. A spec that states
    # them -- every KMC report -- is used exactly as written.
    spec = resolve_spec_defaults(spec, model, pipeline_config, extra_fields, llo_map, full_registry)

    # `series` is one family or several. The FIRST is the primary -- it drives
    # programInd / byLLO / byOpp / byFLW / the trend -- and every further one is
    # graded from the SAME rows into `payload.series[<name>]`, so a template can
    # carry its headline registry and a scorecard registry from one evaluation.
    # Undeclared means the registry's first family, whatever it is called.
    declared = spec.get("series") or (series_prefixes(full_registry) or ("",))[0]
    series_list = [str(x).upper() for x in (declared if isinstance(declared, list) else [declared])]
    primary = series_list[0]

    return {
        "definition_id": definition_id,
        "opportunity_ids": opportunity_ids,
        "request": request,
        "definition": definition,
        "props_doc": props_doc,
        "full_registry": full_registry,
        "llo_map": llo_map,
        "reg_settings": reg_settings,
        "deployment": deployment,
        "pipeline_config": pipeline_config,
        "extra_fields": extra_fields,
        "model": model,
        "spec": spec,
        "series_list": series_list,
        "primary": primary,
    }


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
    from connect_labs.semantic.runtime import evaluate, evaluate_with_cases, filter_to_series, measure_catalog
    from connect_labs.semantic.workflow_binding import registry_binding

    r = _resolve_semantic(spec, opportunity_id, context)
    definition_id, opportunity_ids, request = r["definition_id"], r["opportunity_ids"], r["request"]
    definition, props_doc, full_registry = r["definition"], r["props_doc"], r["full_registry"]
    llo_map, reg_settings, deployment = r["llo_map"], r["reg_settings"], r["deployment"]
    pipeline_config, extra_fields, model, spec = r["pipeline_config"], r["extra_fields"], r["model"], r["spec"]
    series_list, primary = r["series_list"], r["primary"]

    # AS OF the run's period end. Every maturity gate and, since the compiler
    # change that came with this, the visit set itself are cut at that date -- so
    # a run saved for a past week reports that week, not the day it was saved.
    as_of_date = as_of_iso(context.get("period_end"))
    as_of = f"DATE '{as_of_date}'" if as_of_date else "CURRENT_DATE"

    # ONE pass over every scope a saved run can drill to. GROUPING SETS exist
    # precisely because per-scope calls re-run the whole Layer 1 extraction, and
    # evaluating with no series filter returns every family in that one pass.
    scopes = list(spec.get("scopes") or ["programme"])
    evaluate_kwargs = dict(
        extra_fields=extra_fields,
        registry_documents=(props_doc, full_registry),
        series=primary if len(series_list) == 1 else None,
        as_of=as_of,
        llo_map=llo_map or None,
        settings=reg_settings or None,
    )
    case_cfg = spec.get("case_index") or {}
    llo_by_opp = {int(k): v for k, v in (llo_map or {}).items()}
    memo = context.get("memo")
    clock = _StageClock(memo)
    if memo is not None:
        # A batch of dates over ONE held cache: extract Layer 1 once and grade every
        # date off it (`runtime.materialize_visits`). The date cuts the visit set
        # inside the compiled chain, so the figures are exactly those of a fresh
        # extraction -- only the repeated JSON parsing is gone.
        with clock("layer1"):
            table = _layer1_table(
                memo, definition_id, opportunity_ids, pipeline_config, extra_fields, props_doc, full_registry
            )
            evaluate_kwargs["visit_sql"] = f"SELECT * FROM {table}"
    done_property = registry_done_property(props_doc, full_registry)
    with clock("evaluate"):
        if case_cfg.get("source") == "semantic":
            # The case list from the SAME extraction as the indicators (see
            # `semantic_case_rows`): as of the run, and the same cases the scores counted.
            case_fields = semantic_case_fields(case_cfg)
            if done_property:
                case_fields.setdefault(done_property, done_property)
            rows, cases, dropped = evaluate_with_cases(
                pipeline_config,
                opportunity_ids,
                scopes=scopes,
                case_fields=case_fields,
                **evaluate_kwargs,
            )
            if dropped:
                logger.info("workflow %s: case fields the registry does not serve: %s", definition_id, dropped)
        elif done_property and case_cfg.get("pipeline"):
            # The case list is read off a pipeline, which does not carry a Layer-2
            # property; the done flag comes from the same graded `props` rows as the
            # indicators, in the same pass, and is stamped onto each case.
            rows, done_rows, _dropped = evaluate_with_cases(
                pipeline_config,
                opportunity_ids,
                scopes=scopes,
                case_fields={done_property: done_property},
                **evaluate_kwargs,
            )
            cases = stamp_done(snap.case_rows(pipelines, spec, llo_by_opp), done_rows, done_property)
        else:
            rows = evaluate(pipeline_config, opportunity_ids, scopes=scopes, scope=scopes[0], **evaluate_kwargs)
            cases = snap.case_rows(pipelines, spec, llo_by_opp)

    measures = measure_catalog(filter_to_series(full_registry, primary))
    extra_series = {name: measure_catalog(filter_to_series(full_registry, name)) for name in series_list[1:]}
    visits_alias = spec.get("visits_pipeline")
    visits = ((pipelines or {}).get(visits_alias) or {}).get("rows") or [] if visits_alias else []
    # The pipeline cache is all-time, so the case index and the visit rows must be
    # cut at the same date the evaluation was. Without this a run for a past week
    # reported today's case and visit counts in its banner and let the drill open
    # cases that had not been registered yet.
    cases = cut_as_of(cases, snap.case_date_fields(spec), as_of_date)
    visits = cut_as_of(visits, ("visit_date",), as_of_date)

    meta = {
        "cases": len(cases),
        "visits": len(visits),
        "opportunities": len(opportunity_ids),
        "llos": len({c.get("llo") for c in cases if c.get("llo")}),
        # The date every figure is AS OF. None means "the day it was built".
        "as_of": as_of_date,
        # WHICH REGISTRY graded these figures -- a bound record, or the on-disk
        # built-in. A rebuilt history restates every point under the definitions in
        # force when it ran, so a point has to be able to say which those were.
        "registry": {k: v for k, v in registry_binding(definition).items() if k != "note"},
        # When these figures stop moving: the case-index date each maturity window
        # counts from, and the longest window any indicator waits on. The benchmark
        # publisher ends an opportunity's trend line there (`benchmarks/publish.py::
        # opportunity_ends`), under the rule this run was graded with.
        "settles": {
            **settles_meta(spec, props_doc, full_registry),
            "latest": latest_anchor_by_opportunity(cases, spec.get("maturity_anchor")),
        },
    }
    meta.update(_data_provenance_meta(opportunity_ids))

    deployment = {
        **(deployment or {}),
        "opportunity_labels": opportunity_labels(
            opportunity_ids, request=request, declared=(deployment or {}).get("opportunity_labels")
        ),
    }

    from connect_labs.semantic.display import resolve_display

    names = _once_in(
        context.get("memo"),
        ("worker_names", tuple(int(o) for o in opportunity_ids)),
        lambda: worker_names(opportunity_ids, access_token=context.get("access_token"), request=request),
    )
    display = resolve_display(
        props_doc,
        full_registry,
        visits_pipeline=spec.get("visits_pipeline"),
    )
    embed = case_cfg.get("embed", True) is not False
    clock.start("assemble")
    payload = snap.build(
        display=display,
        spec=spec,
        rows=rows,
        measures=measures,
        deployment=deployment,
        cases=cases,
        meta=meta,
        visit_rows=visits,
        extra_series=extra_series,
        as_of=as_of_date,
        registry_min_denominator=model.min_denominator,
        embed_cases=embed,
        worker_names=names,
    )
    clock.stop()
    if not embed and context.get("memo") is not None:
        # The cases this run was graded from, for the caller that hands it down to
        # the opportunity reports while they are in memory (history_rebuild, which
        # is the one caller that passes a memo). Never stored on this run: see
        # `case_index.embed`. Without a memo, hand-down computes the week's list.
        context["memo"][LAST_CASE_INDEX] = cases
    return wrap_for_runner(payload, spec.get("state_key"))


# Reader name -> registry column, for the case-index fields whose names differ
# between the two. A case index keeps the names its readers have always used; a
# field that is not listed here is read from the registry column of the same name.
# Where a memo-carrying build leaves the cases it graded (see semantic_snapshot).
LAST_CASE_INDEX = ("last_case_index",)

_CASE_FIELD_SOURCES = {
    "first_visit_date": "first_visit",
    "last_visit_date": "last_visit",
    "total_visits": "num_visits",
}


class _StageClock:
    """Seconds spent per build stage, summed into a batch memo's `timings`.

    A history rebuild reports them, so where a week's time goes is a measured fact
    rather than a guess. Without a memo (a single save) it records nothing.
    """

    def __init__(self, memo):
        self.timings = memo.setdefault("timings", {}) if memo is not None else None
        self._open = None

    def __call__(self, stage):
        clock = self

        class _Span:
            def __enter__(self):
                clock.start(stage)

            def __exit__(self, *exc):
                clock.stop()

        return _Span()

    def start(self, stage):
        self.stop()
        self._open = (stage, time.perf_counter())

    def stop(self):
        if self._open is None or self.timings is None:
            self._open = None
            return
        stage, began = self._open
        self.timings[stage] = round(self.timings.get(stage, 0.0) + time.perf_counter() - began, 3)
        self._open = None


# Where a batch memo keeps its materialised Layer 1 tables (see _layer1_table).
LAYER1_TABLES = ("layer1_tables",)


def _layer1_table(
    memo, definition_id, opportunity_ids, pipeline_config, extra_fields, props_doc, full_registry
) -> str:
    """The batch's materialised Layer 1 table for these opportunities, made on first use.

    Re-made if it has vanished -- a dropped database connection takes its temporary
    tables with it -- so a long batch can never read a table that is not there.
    """
    from connect_labs.semantic.runtime import materialize_visits, materialized_exists

    tables = memo.setdefault(LAYER1_TABLES, {})
    key = (definition_id, tuple(int(o) for o in opportunity_ids))
    table = tables.get(key)
    if table is None or not materialized_exists(table):
        table = materialize_visits(
            pipeline_config,
            list(key[1]),
            extra_fields=extra_fields,
            registry_documents=(props_doc, full_registry),
        )
        tables[key] = table
    return table


def drop_layer1_tables(memo) -> None:
    """Drop a batch's materialised Layer 1 tables. Called once the batch is done."""
    from connect_labs.semantic.runtime import drop_materialized

    for table in (memo or {}).pop(LAYER1_TABLES, {}).values():
        drop_materialized(table)


def registry_done_property(props_doc: dict | None, indicators_doc: dict | None) -> str | None:
    """The registry's `display.entity.done_property`, when it names a bool property."""
    from connect_labs.semantic.display import done_property_problems, resolve_display

    name = (resolve_display(props_doc, indicators_doc).get("entity") or {}).get("done_property")
    if not name or done_property_problems(name, props_doc or {}):
        return None
    return name


def stamp_done(cases: list[dict], done_rows: list[dict], done_property: str) -> list[dict]:
    """Each case with `done_property` set from the graded rows, matched on (opportunity, entity).

    `done_rows` are `evaluate_with_cases` case rows, whose `entity_id` is the
    registry's entity key -- the same value a pipeline-derived case index carries.
    A case with no graded row (cut by the as-of date, or never graded) gets False:
    unknown is never finished, so it stays subject to the staleness rule.
    """
    done = {
        (str(r.get("opportunity_id")), str(r.get("entity_id"))): bool(r.get(done_property)) for r in done_rows or []
    }
    for c in cases:
        c[done_property] = done.get((str(c.get("opportunity_id")), str(c.get("entity_id"))), False)
    return cases


def semantic_case_fields(case_cfg: dict) -> dict[str, str]:
    """`case_index.fields` as {output name: registry column}.

    A list names output fields, each read from `_CASE_FIELD_SOURCES` or the column
    of the same name; a mapping states the column for each field outright.
    """
    fields = (case_cfg or {}).get("fields") or []
    if isinstance(fields, dict):
        return {str(k): str(v) for k, v in fields.items()}
    return {str(f): _CASE_FIELD_SOURCES.get(str(f), str(f)) for f in fields}


_ALL_SCOPES = ["programme", "llo", "opportunity", "flw", "month", "llo_month", "opportunity_month"]


def _stage(config) -> str:
    stage = getattr(config, "terminal_stage", None)
    return str(getattr(stage, "value", stage) or "")


def resolve_spec_defaults(spec, model, pipeline_config, extra_fields, llo_map, indicators_doc) -> dict:
    """The builder spec with every registry-derivable key filled in.

    Only ABSENT keys are filled, so a spec that states a key keeps it. What is
    derived, and from where:

      scopes          every scope the registry can compile (no `llo*` scopes
                      without an llo_map -- the compiler refuses them by name)
      case_index      the registry's entity pipeline: its rows as they are when it
                      is an ENTITY pipeline, grouped per entity key when it is a
                      VISIT-level one (a registry over the visit rows alone)
      visits_pipeline the visit-level pipeline: the entity pipeline itself when it
                      is visit-level, else the first extra-field pipeline that is
      maturity_anchor first_visit_date, which every case index carries
      credibility     each indicator's `meta.credibility` table, merged under any
                      the spec states
    """
    from connect_labs.semantic.compiler import available_scopes
    from connect_labs.semantic.display import credibility_from_meta

    out = dict(spec or {})
    allowed = available_scopes(llo_map or None)
    if "scopes" not in out:
        out["scopes"] = [s for s in _ALL_SCOPES if s in allowed]
    else:
        # A generic spec listing llo scopes over a registry with no llo_map would
        # fail to compile; KMC-shaped specs over KMC registries are unaffected.
        out["scopes"] = [s for s in out["scopes"] if s in allowed] or ["programme"]

    entity_alias = model.entity_pipeline
    entity_visit_level = _stage(pipeline_config) == "visit_level"
    if "visits_pipeline" not in out:
        if entity_visit_level:
            out["visits_pipeline"] = entity_alias
        else:
            aliases = model.extra_fields or {}
            out["visits_pipeline"] = next(
                (a for col, a in aliases.items() if _stage((extra_fields or {}).get(col)) == "visit_level"),
                None,
            )
    if "case_index" not in out and entity_alias:
        from connect_labs.semantic.display import resolve_display

        resolved = resolve_display(None, indicators_doc)
        wanted = [f["field"] for f in resolved["case_fields"]]
        # A case's human label (`display.entity.label_field`), so the case table and
        # the worker review can name a case rather than print its id.
        label_field = (resolved.get("entity") or {}).get("label_field")
        if label_field:
            wanted.append(label_field)
        # Whether a case's work is finished (`display.entity.done_property`), so the
        # renders do not call a finished case -- or a worker of only finished cases --
        # stale. A Layer-2 property, so the builder fills it from the graded `props`
        # rows when the case index is read off a pipeline (`semantic_snapshot`).
        done_property = (resolved.get("entity") or {}).get("done_property")
        if done_property:
            wanted.append(done_property)
        base = ["entity_id", "username", "opportunity_id", "first_visit_date", "last_visit_date", "total_visits"]
        fields = base + [f for f in dict.fromkeys(wanted) if f not in base]
        out["case_index"] = {"pipeline": entity_alias, "fields": fields}
        if entity_visit_level:
            out["case_index"]["group_by"] = model.key
    if "maturity_anchor" not in out:
        out["maturity_anchor"] = "first_visit_date"
    meta_cred = credibility_from_meta(indicators_doc)
    if meta_cred:
        out["credibility"] = {**meta_cred, **(out.get("credibility") or {})}
    return out


def opportunity_cases(*, opportunity_ids: list[int], period_end, context: dict) -> list[dict]:
    """One week's case list for some of a programme's opportunities, as of `period_end`.

    What hand-down uses when the programme run it is handing down was not built in
    this process: a programme run stores no case list (`case_index.embed: false`),
    so each opportunity's list is computed for the run's week from the programme's
    own registry and pipelines -- the same extraction its indicators were graded
    from, filtered at the scan to the opportunities asked for. `context` is the
    programme's builder context (definition_id, access_token, scope).
    """
    from connect_labs.semantic.runtime import evaluate_with_cases
    from connect_labs.workflow.templates import resolve_snapshot_contract

    if not opportunity_ids:
        return []
    probe = _resolve_semantic({}, int(context.get("opportunity_id") or opportunity_ids[0]), context)
    spec = (resolve_snapshot_contract(probe["definition"]).get("snapshot_inputs")) or {}
    r = _resolve_semantic(spec, int(context.get("opportunity_id") or opportunity_ids[0]), context)
    as_of_date = as_of_iso(period_end)
    _rows, cases, _dropped = evaluate_with_cases(
        r["pipeline_config"],
        [int(o) for o in opportunity_ids],
        scopes=["programme"],
        case_fields=semantic_case_fields(r["spec"].get("case_index") or {}),
        extra_fields=r["extra_fields"],
        registry_documents=(r["props_doc"], r["full_registry"]),
        as_of=f"DATE '{as_of_date}'" if as_of_date else "CURRENT_DATE",
        llo_map=r["llo_map"] or None,
        settings=r["reg_settings"] or None,
        visit_filter={"opportunity_id": int(opportunity_ids[0])} if len(opportunity_ids) == 1 else None,
    )
    return cases


def settles_meta(spec: dict, props_doc: dict, indicators_doc: dict) -> dict:
    """`{"after_days", "anchor"}` for `meta.settles`. See `semantic/maturity.py`.

    `anchor` is the template's `maturity_anchor` (a case-index date field). Absent,
    it is None and a reader anchors on the opportunity's last visit, which is later
    than any maturity anchor and so always safe.
    """
    from connect_labs.semantic.maturity import settle_after_days

    spec = spec or {}
    anchor = spec.get("maturity_anchor")
    return {"after_days": settle_after_days(props_doc, indicators_doc), "anchor": str(anchor) if anchor else None}


def latest_anchor_by_opportunity(cases: list[dict], anchor: str | None) -> dict[str, str]:
    """Each opportunity's latest maturity-anchor date (ISO), keyed by its id as a string.

    The benchmark publisher ends an opportunity's trend line `after_days` past this
    date (`benchmarks/publish.py::opportunity_ends`). It used to scan the stored
    case list for it; recorded here, a run that stores no case list still answers.
    Falls back to the last visit, as the publisher does, when a case has no anchor.
    """
    latest: dict[str, str] = {}
    for c in cases or []:
        opp = c.get("opportunity_id")
        day = c.get(anchor) if anchor else None
        day = str(day or c.get("last_visit_date") or "")[:10]
        if opp is None or not _ISO_DATE.match(day):
            continue
        key = str(int(opp))
        if day > latest.get(key, ""):
            latest[key] = day
    return latest


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


def _data_provenance_meta(opportunity_ids: list[int]) -> dict:
    """``meta.synthetic`` and, when any opp in scope holds copied real values, ``meta.real_values``.

    The render reads `meta.synthetic` to show its "built on synthetic clones"
    disclaimer. A live run computes it from scope; a saved run can only know
    what was captured, so omitting it published a synthetic cohort with the
    disclaimer silently absent.

    A clone carrying values copied verbatim from its source (connect-labs#2150) is
    NOT synthetic data: the report must say it contains real values from that
    opportunity, and must never also claim to be built on synthetic data.
    """
    out: dict = {}
    synthetic = _is_synthetic(opportunity_ids)
    try:
        from connect_labs.labs.synthetic.verbatim import real_value_sources

        real_values = real_value_sources(opportunity_ids)
    except Exception:  # noqa: BLE001 — fail toward NOT claiming synthetic
        logger.warning("could not determine real-value sources for %s", opportunity_ids, exc_info=True)
        real_values, synthetic = [], None
    if real_values:
        out["real_values"] = real_values
        synthetic = False
    if synthetic is not None:
        out["synthetic"] = synthetic
    return out


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


def opportunity_labels(opportunity_ids, request=None, declared=None) -> dict[str, str]:
    """opportunity id (str) -> the name a reader knows it by, for every id in scope.

    Two sources, the second winning:
      1. the opportunity's own record -- a labs-only opportunity's registry row
         (`SyntheticOpportunity.label`), a real one's entry in the requester's
         Connect org data (the web path; the MCP path has no session, so a real
         opportunity saved there is named by the render from the viewer's own
         opportunity list instead);
      2. the registry's `deployment.opportunity_labels`, for an author who wants
         the report to say something other than the opportunity's own name.

    Nothing is guessed: an id with no name is left out, and the render says
    "Opportunity <id>" rather than inventing one.

    Best-effort by construction: a name is a label, and must not be able to fail
    a snapshot.
    """
    ids = {int(o) for o in opportunity_ids or []}
    out: dict[str, str] = {}
    try:
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        for oid, label in SyntheticOpportunity.objects.filter(opportunity_id__in=ids, enabled=True).values_list(
            "opportunity_id", "label"
        ):
            if label:
                out[str(oid)] = str(label)
    except Exception:  # noqa: BLE001
        logger.warning("could not read synthetic opportunity labels for %s", sorted(ids), exc_info=True)
    missing = {i for i in ids if str(i) not in out}
    if missing and request is not None:
        try:
            from connect_labs.labs.context import get_org_data

            for o in get_org_data(request).get("opportunities") or []:
                try:
                    oid = int(o.get("id"))
                except (TypeError, ValueError):
                    continue
                if oid in missing and o.get("name"):
                    out[str(oid)] = str(o["name"])
        except Exception:  # noqa: BLE001
            logger.warning("could not read opportunity names from org data", exc_info=True)
    for k, v in (declared or {}).items():
        try:
            if int(k) in ids and v:
                out[str(int(k))] = str(v)
        except (TypeError, ValueError):
            continue
    return out


def _once_in(memo, key, load):
    """`load()`, memoised in a batch memo when there is one (see `_resolve_semantic`)."""
    if memo is None:
        return load()
    if key not in memo:
        memo[key] = load()
    return memo[key]


def _request_token(request) -> str | None:
    try:
        return (request.session.get("labs_oauth") or {}).get("access_token")
    except Exception:  # noqa: BLE001 -- no session (MCP, Celery) means no token here
        return None


def _serves_fixtures(opportunity_id: int) -> bool:
    try:
        from connect_labs.labs.synthetic.registry import get_synthetic_opp

        return get_synthetic_opp(int(opportunity_id)) is not None
    except Exception:  # noqa: BLE001
        return False


def worker_names(opportunity_ids, access_token=None, request=None) -> dict[str, dict[str, str]]:
    """opportunity id (str) -> {username: display name}, for every opportunity in scope.

    The worker table used to print the Connect username -- `cbf_a07` on a synthetic
    programme, a 32-character hex id on a real one -- because nothing resolved a
    name. Connect has one: its `user_data` export carries each worker's `name`, and
    `fetch_flw_names` (the audit views' resolver, cached) reads it. A labs-only
    synthetic opportunity is served the same export from its fixtures, whose
    `name` is the manifest persona's `display_name` -- so one call covers both.

    Only names that differ from the username are kept: a worker with no name is
    left out and the render shows the username, exactly as before. Resolved once
    per build and stored on the run, so a saved week keeps the names it was built
    with. Best-effort by construction: a name is a label, and must not be able to
    fail a snapshot.
    """
    from connect_labs.labs.analysis.data_access import fetch_flw_names

    token = access_token or _request_token(request) or ""
    out: dict[str, dict[str, str]] = {}
    for oid in sorted({int(o) for o in opportunity_ids or []}):
        # Without a token only a fixture-backed (synthetic) opportunity can answer;
        # asking Connect anonymously would just fail, slowly.
        if not token and not _serves_fixtures(oid):
            continue
        try:
            names = fetch_flw_names(token, oid)
        except Exception:  # noqa: BLE001
            logger.warning("could not read worker names for opportunity %s", oid, exc_info=True)
            continue
        kept = {str(u): str(n) for u, n in (names or {}).items() if u and n and str(n) != str(u)}
        if kept:
            out[str(oid)] = kept
    return out


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
        "maturity_anchor",
        # {flag: <case-index field>}: registrations by day (semantic/snapshot.py daily_counts)
        "daily",
    },
}
