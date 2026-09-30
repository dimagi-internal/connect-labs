"""Layer 1: build the visit extraction from the PIPELINE, never by hand.

WHY THIS MODULE EXISTS
`compile_indicator_sql` / `compile_rollup_sql` take `visit_sql` as a string, and
the first real parity run against opportunity 10042 was driven by a hand-written
one. It looked complete and was not: measured against the pipeline's own field
expressions it carried 3 of 10 danger-sign paths, 3 of 6 referral paths and 3 of 4
kmc-hours paths. Those three fields are exactly the ones whose indicators
disagreed with the existing dashboard (C19, C20, C23) -- the paraphrase WAS the
bug.

The fallback path lists are the expensive, hard-won part of Layer 1. Retyping them
is the one thing guaranteed to lose them, so this module generates the extraction
from the pipeline's own schema instead. Nothing downstream should ever build that
string itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from connect_labs.semantic.model import WINDOW_KINDS, resolve_model

# `visit_filter` keys that are real columns of labs_raw_visit_cache, and so can be
# applied in the extraction's own WHERE. A computed key (a registry's entity key)
# cannot; the compiler applies it after Layer 1, as it applies every key.
_SCAN_FILTER_COLUMNS = ("opportunity_id", "username")


def _scan_filter_sql(visit_filter: dict[str, Any] | None) -> str:
    if not visit_filter:
        return ""
    from connect_labs.semantic.compiler import visit_filter_sql

    scan = {k: v for k, v in visit_filter.items() if k in _SCAN_FILTER_COLUMNS}
    # The compiler's own clause builder: whitelisted keys, escaped values.
    return visit_filter_sql(scan)


def build_visit_sql(
    pipeline_schema: dict[str, Any],
    opportunity_ids: Iterable[int],
    *,
    generate_sql_preview=None,
    extra_fields: dict[str, Any] | None = None,
    visit_filter: dict[str, Any] | None = None,
    props_doc: dict[str, Any],
) -> str:
    """Return the visit-level SQL for a set of opportunities.

    The extraction itself comes from the pipeline engine, so every fallback path
    is whatever the pipeline actually uses. This function only:

      1. widens the WHERE from one opportunity to the requested set,
      2. de-duplicates across cache partitions -- `labs_raw_visit_cache` is keyed
         by (opportunity, pipeline), so the same visit is present once per
         pipeline that has cached it, and counting it twice inflates everything,
      3. adds `opportunity_id` (the extraction does not select it) and the
         registry's `visit_columns` (see `visit_columns_sql`),
      4. merges fields from OTHER pipelines via `extra_fields`,
      5. applies `visit_filter`'s base-column keys (opportunity, worker) in the scan.

    It also:

      6. scopes the scan to the entity pipeline's OWN raw-cache slot (see
         `_slot_predicate`),
      7. joins the registry's `pipelines.lookups` (fields from another pipeline's
         rows -- CommCare HQ forms, say -- matched on a key; see `lookups_sql`),
      8. computes window visit columns (`previous`, `distance_from_previous`) over
         the de-duplicated, filtered visits, before the row-level visit columns so
         those may read them.

    (5) is what makes a one-worker evaluation cost one worker. The compiler also
    applies the filter, but after this subquery -- and Postgres cannot push a
    `username` predicate below the DISTINCT ON (it is not a DISTINCT key), so the
    whole opportunity's visits were extracted, every form path pulled out of every
    copy, sorted and de-duplicated, only to keep one worker's rows. On the real
    KMC cohort that put a worker's case table past a minute. In the WHERE, the
    rows are dropped before the extraction runs. It is the same set: every cached
    copy of a visit carries the same worker.

    (4) is not a convenience -- which pipelines supply which fields is the
    registry's `pipelines` model. KMC's case: the dashboard reads its weight series
    from a SECOND pipeline ("KMC Weight Series", 5109) whose `weight_g` has five fallback
    paths, while the case pipeline's `weights` has six -- the extra
    `form.case.update.child_weight_visit`. Deriving the series from the case
    pipeline therefore produces a different set of readings, which changes
    weight_consistent and the early-growth window and moves C07-C13. Whichever set
    is *right* is a question for the workbook; for a like-for-like comparison the
    series has to come from the pipeline the dashboard actually uses.
    """
    if generate_sql_preview is None:  # pragma: no cover - import at call time
        from connect_labs.labs.analysis.backends.sql.query_builder import generate_sql_preview as _gen

        generate_sql_preview = _gen

    opps = [int(o) for o in opportunity_ids]
    if not opps:
        raise ValueError("build_visit_sql needs at least one opportunity")

    # Lookup pipelines travel in `extra_fields` under a key no column can have
    # (`LOOKUPS_KEY`), so the two callers that build these inputs -- the semantic
    # endpoint and the snapshot builder -- pass them through unchanged.
    extra_fields = dict(extra_fields or {})
    lookup_configs = extra_fields.pop(LOOKUPS_KEY, None) or {}
    model = resolve_model(props_doc)
    windows = [c for c in model.visit_columns if c.kind in WINDOW_KINDS]
    # A window reads a case's OTHER visits, which may be another worker's: a visit's
    # distance from the previous visit to the same mother does not depend on who
    # made the previous one. Dropping other workers' rows in the scan would change
    # it, so with windows declared the worker filter waits for the compiler (which
    # applies it after Layer 1, as it always has). Slower for one worker; the same
    # number either way.
    if windows and visit_filter:
        visit_filter = {k: v for k, v in visit_filter.items() if k != "username"}

    preview = generate_sql_preview(pipeline_schema, opps[0])
    ex = preview["visit_extraction_sql"]

    # The extraction selects visit columns but not opportunity_id/pipeline_id;
    # the rollup groups by the former and the dedup orders by the latter.
    ex = ex.replace(
        "SELECT\nvisit_id,",
        "SELECT DISTINCT ON (opportunity_id, visit_id)\nopportunity_id,\npipeline_id,\nvisit_id,",
        1,
    )
    opp_list = ",".join(str(o) for o in opps)
    old_where = f"WHERE opportunity_id = {opps[0]} AND pipeline_id"
    idx = ex.find(old_where)
    if idx == -1:
        raise ValueError("could not locate the extraction's WHERE clause to widen")
    head = ex[:idx]
    # Re-apply the pipeline's own row filters. Cutting the WHERE at the scope
    # predicate dropped everything after it -- including a declared status filter,
    # so rule 0 (only approved and over_limit visits are valid) reached the
    # pipeline's rows and none of the metrics built from them.
    filters = "".join(f" AND {p}" for p in preview.get("visit_filter_predicates") or [])
    # `visit_count > 0` excludes an in-progress streaming generation -- rows written
    # under a NEGATIVE visit_count until the download finalizes (#1684). The scope
    # predicate carries it, and cutting the WHERE at the scope dropped it, so Layer 1
    # could read a half-written second copy of an opportunity's visits.
    #
    # The dedupe keeps the most recently fetched copy of each visit (latest expiry),
    # not the lowest pipeline_id: the raw cache holds one copy per pipeline that has
    # cached the opportunity, and picking by id read whichever OTHER workflow happened
    # to own the lowest-numbered pipeline -- possibly a stale copy, with outdated
    # statuses -- so what the indicators saw depended on what else existed.
    ex = (
        head
        + f"WHERE opportunity_id IN ({opp_list}){_slot_predicate(pipeline_schema)} AND visit_count > 0"
        + f"{filters}{_scan_filter_sql(visit_filter)}\n"
        + "ORDER BY opportunity_id, visit_id, expires_at DESC, pipeline_id"
    )

    extra_cols = ""
    if extra_fields:
        parts = []
        for name, cfg in extra_fields.items():
            prev = generate_sql_preview(cfg, opps[0])
            expr = prev["field_expressions"][name]["transformed_sql"]
            parts.append(f"{expr} as {name}")
        extra_cols = ",\n" + ",\n".join(parts)
    ex = ex.replace("\nFROM labs_raw_visit_cache", extra_cols + "\nFROM labs_raw_visit_cache", 1)

    # The visit columns are spliced into Layer 1, which runs BEFORE the compiler's
    # own validation gets to look at the statement -- so they are checked here too.
    from connect_labs.semantic.compiler import model_problems

    problems = model_problems(props_doc)
    if problems:
        raise ValueError("registry model does not validate:\n  " + "\n  ".join(problems))
    extra = visit_columns_sql([c for c in model.visit_columns if c.kind not in WINDOW_KINDS])
    if not model.lookups and not windows:
        # The shape every registry compiled to before lookups and windows existed
        # (with the scan now scoped to its slot).
        return f"""SELECT
  x.*{extra}
FROM (
{ex}
) x"""

    lookup_cols, lookup_joins = lookups_sql(
        model.lookups, lookup_configs, opps, generate_sql_preview=generate_sql_preview
    )
    window_cols = "".join(f",\n  {window_column_sql(c)}" for c in windows)
    return f"""SELECT
  x.*{extra}
FROM (
SELECT
  b.*{lookup_cols}{window_cols}
FROM (
{ex}
) b{lookup_joins}
) x"""


def visit_columns_sql(columns) -> str:
    """The registry's per-visit derived columns, as comma-led `<expr> AS <name>` terms.

    A `word_match` is the pipeline's own `contains_word` test, materialised per
    visit (`x.col ~* '\\y<word>\\y'`); the engine writes the regex anchors itself
    because the word is structured data the validator restricts to [A-Za-z0-9_] --
    fragments may not carry a backslash at all. A `sql` column is a row-level
    Layer-2 fragment over Layer-1 columns; a `column` is a plain alias.
    """
    from connect_labs.semantic.compiler import fragment_columns, qualify_columns

    terms = []
    for col in columns:
        if col.kind == "word_match":
            terms.append(f"(x.{col.column} ~* '\\y{col.word}\\y') AS {col.name}")
        elif col.kind == "sql":
            terms.append(f"({qualify_columns(col.sql, 'x', fragment_columns(col.sql))}) AS {col.name}")
        else:
            terms.append(f"x.{col.column} AS {col.name}")
    return "".join(f",\n  {t}" for t in terms)


def _slot_predicate(pipeline_config: Any) -> str:
    """` AND pipeline_id = <slot>` for the entity pipeline's raw-cache slot.

    `labs_raw_visit_cache` holds, per opportunity, one slot shared by every pipeline
    on the Connect visits export AND one slot per pipeline on any other source --
    CommCare HQ registration forms, a supervisor app's checklist (#116, #1921). The
    extraction the engine generates is scoped to its own slot; widening its WHERE to
    a set of opportunities dropped that scope, so every OTHER source's rows on the
    opportunity reached Layer 1 as visits. On MBW (opp 765) that is each
    registration form and each Gold Standard checklist, counted as a visit by the
    worker who submitted it.

    A config without a slot -- a test double, a raw schema -- keeps the old
    unscoped read.
    """
    if isinstance(pipeline_config, dict) or not hasattr(pipeline_config, "raw_slot_id"):
        return ""
    slot = pipeline_config.raw_slot_id
    return " AND pipeline_id IS NULL" if slot is None else f" AND pipeline_id = {int(slot)}"


# `extra_fields` key carrying `{lookup name: pipeline config}`. Not an identifier, so
# no registry column can collide with it.
LOOKUPS_KEY = "@lookups"

# Base columns of labs_raw_visit_cache a lookup may key or read on directly -- the
# ones a CommCare HQ form row carries meaningfully (`username` is the submitting
# worker; `visit_date` is when HQ received it).
_LOOKUP_BASE_COLUMNS = ("visit_id", "username", "visit_date", "entity_id")


def _computed_in_python(field: Any) -> bool:
    """The engine's own rule (`query_builder.build_visit_extraction_query`): a field
    SQL cannot compute is one with an `extractor`, or a transform that reads the
    whole visit (`visit_data`, or no parameters). An ordinary callable transform --
    `gps_lat`, a float cast -- is translated to SQL from its source and is fine."""
    import inspect

    if callable(getattr(field, "extractor", None)):
        return True
    transform = getattr(field, "transform", None)
    if not callable(transform):
        return False
    try:
        params = list(inspect.signature(transform).parameters)
    except (TypeError, ValueError):
        return False
    return "visit_data" in params or not params


def _lookup_value_sql(config: Any, preview: dict[str, Any], name: str, lookup: str, role: str) -> str:
    """The SQL for one field of a lookup pipeline, from that pipeline's own schema."""
    field = None
    for f in getattr(config, "fields", None) or []:
        if getattr(f, "name", None) == name:
            field = f
            break
    if field is not None and _computed_in_python(field):
        raise ValueError(
            f"lookup {lookup!r}: {role} {name!r} is computed in Python by its pipeline "
            "(an extractor or a full-context transform), so SQL cannot read it -- declare it with paths instead"
        )
    expr = (preview.get("field_expressions") or {}).get(name)
    if expr:
        return expr["transformed_sql"]
    if name in _LOOKUP_BASE_COLUMNS:
        return name
    raise ValueError(f"lookup {lookup!r}: {role} {name!r} is not a field of its pipeline")


def _lookup_pick_sql(pick: str, value: str) -> str:
    if pick == "count":
        return f"COUNT({value})"
    if pick in ("max", "min"):
        # A form value is text; comparing it as text ranks "90" above "100". Only a
        # value that IS a number takes part.
        numeric = (
            f"CASE WHEN ({value})::text ~ '^\\s*-?[0-9]+(\\.[0-9]+)?\\s*$' THEN trim(({value})::text)::numeric END"
        )
        return f"{pick.upper()}({numeric})"
    raise ValueError(f"unknown pick {pick!r}")


def lookups_sql(lookups, configs: dict[str, Any], opps: list[int], *, generate_sql_preview) -> tuple[str, str]:
    """`(select terms, join clauses)` for the registry's lookups over Layer 1's `b`.

    Each lookup reads its pipeline's rows from that pipeline's OWN raw-cache slot,
    de-duplicated per row like Layer 1, reduced to one row per (opportunity, key) by
    `pick`, and LEFT JOINed on `b.<on> = key` within the opportunity -- so a visit
    with no matching row keeps its place with NULLs, and never multiplies.
    """
    opp_list = ",".join(str(o) for o in opps)
    cols: list[str] = []
    joins: list[str] = []
    for lk in lookups:
        config = configs.get(lk.name)
        if config is None:
            raise ValueError(f"lookup {lk.name!r}: the workflow has no pipeline source with alias {lk.pipeline!r}")
        preview = generate_sql_preview(config, opps[0])
        key_sql = _lookup_value_sql(config, preview, lk.key, lk.name, "key")
        values = {out: _lookup_value_sql(config, preview, src, lk.name, "field") for out, src in lk.fields.items()}
        filters = "".join(f" AND {p}" for p in preview.get("visit_filter_predicates") or [])
        value_terms = "".join(f",\n      {v} AS {out}" for out, v in values.items())
        rows = f"""SELECT DISTINCT ON (opportunity_id, visit_id)
      opportunity_id,
      visit_id,
      visit_date,
      ({key_sql})::text AS lookup_key{value_terms}
    FROM labs_raw_visit_cache
    WHERE opportunity_id IN ({opp_list}){_slot_predicate(config)} AND visit_count > 0{filters}
    ORDER BY opportunity_id, visit_id, expires_at DESC"""
        alias = f"lk_{lk.name}"
        if lk.pick in ("latest", "earliest"):
            direction = "DESC" if lk.pick == "latest" else "ASC"
            outs = "".join(f", r.{out}" for out in values)
            reduced = f"""SELECT DISTINCT ON (r.opportunity_id, r.lookup_key)
    r.opportunity_id, r.lookup_key{outs}
  FROM (
    {rows}
  ) r
  WHERE r.lookup_key IS NOT NULL AND r.lookup_key <> ''
  ORDER BY r.opportunity_id, r.lookup_key, r.visit_date {direction} NULLS LAST, r.visit_id {direction}"""
        else:
            aggs = "".join(f", {_lookup_pick_sql(lk.pick, 'r.' + out)} AS {out}" for out in values)
            reduced = f"""SELECT r.opportunity_id, r.lookup_key{aggs}
  FROM (
    {rows}
  ) r
  WHERE r.lookup_key IS NOT NULL AND r.lookup_key <> ''
  GROUP BY r.opportunity_id, r.lookup_key"""
        joins.append(
            f"\nLEFT JOIN (\n  {reduced}\n) {alias}\n"
            f"  ON {alias}.opportunity_id = b.opportunity_id AND {alias}.lookup_key = (b.{lk.on})::text"
        )
        cols.extend(f",\n  {alias}.{out} AS {out}" for out in values)
    return "".join(cols), "".join(joins)


def window_column_sql(col) -> str:
    """One window visit column over Layer 1's `b`, as `<expr> AS <name>`.

    The window is partitioned by opportunity first (a case id can recur across
    opportunities -- 829 did in the KMC cohort) and ordered by `order_by` then
    visit_id, so equal timestamps resolve the same way every run.

    `skip_null` (for `previous`) and every `distance_from_previous` pair a visit
    with the previous visit that HAS the value: the null rows are put in a
    partition of their own, which is Postgres's equivalent of LAG ... IGNORE NULLS
    for exactly this purpose. That matches the pipeline engine's `lag_haversine`,
    which filters GPS-less visits out before pairing (`extracted_filters`).
    """
    partition = ", ".join(["b.opportunity_id", *[f"b.{p}" for p in col.partition_by]])
    order = f"b.{col.order_by}, b.visit_id"
    if col.kind == "previous":
        value = f"b.{col.column}"
        if not col.skip_null:
            return f"LAG({value}) OVER (PARTITION BY {partition} ORDER BY {order}) AS {col.name}"
        w = f"PARTITION BY {partition}, ({value} IS NULL) ORDER BY {order}"
        return f"CASE WHEN {value} IS NULL THEN NULL ELSE LAG({value}) OVER ({w}) END AS {col.name}"
    if col.kind == "distance_from_previous":
        lat = f"NULLIF((b.{col.lat})::text, '')::float"
        lon = f"NULLIF((b.{col.lon})::text, '')::float"
        missing = f"({lat} IS NULL OR {lon} IS NULL)"
        w = f"PARTITION BY {partition}, {missing} ORDER BY {order}"
        return (
            f"CASE WHEN {missing} THEN NULL ELSE haversine_meters("
            f"LAG({lat}) OVER ({w}), LAG({lon}) OVER ({w}), {lat}, {lon}) END AS {col.name}"
        )
    raise ValueError(f"{col.name}: {col.kind!r} is not a window column")
