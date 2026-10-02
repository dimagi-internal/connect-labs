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

from connect_labs.labs.analysis.backends.sql.query_builder import (
    build_multi_opportunity_visit_extraction,
    field_value_sql,
)
from connect_labs.semantic.model import WINDOW_KINDS, resolve_model

# `visit_filter` keys that are real columns of labs_raw_visit_cache, and so can be
# applied in the extraction's own WHERE. A computed key (a registry's entity key)
# cannot; the compiler applies it after Layer 1, as it applies every key.
_SCAN_FILTER_COLUMNS = ("opportunity_id", "username")


def _scan_predicates(visit_filter: dict[str, Any] | None) -> list[str]:
    """`visit_filter`'s base-column keys (opportunity, worker) as scan predicates."""
    if not visit_filter:
        return []
    from connect_labs.semantic.compiler import visit_filter_predicates

    scan = {k: v for k, v in visit_filter.items() if k in _SCAN_FILTER_COLUMNS}
    # The compiler's own clause builder: whitelisted keys, escaped values.
    return visit_filter_predicates(scan)


def build_visit_sql(
    pipeline_schema: dict[str, Any],
    opportunity_ids: Iterable[int],
    *,
    extra_fields: dict[str, Any] | None = None,
    visit_filter: dict[str, Any] | None = None,
    props_doc: dict[str, Any],
) -> str:
    """Return the visit-level SQL for a set of opportunities.

    The extraction itself -- every fallback path, the scope to the pipeline's own
    raw-cache slot, its row filters, the in-progress-download guard and the
    de-duplication -- is built by the pipeline ENGINE
    (`query_builder.build_multi_opportunity_visit_extraction`). This function never
    edits that SQL; it only:

      1. asks the engine for it over the requested opportunities,
      2. adds the registry's `visit_columns` (see `visit_columns_sql`),
      3. adds fields from OTHER pipelines on the same forms via `extra_fields`,
      4. passes `visit_filter`'s base-column keys (opportunity, worker) to the scan,
      5. joins the registry's `pipelines.lookups` (fields from another pipeline's
         own rows -- CommCare HQ forms, say -- matched on a key; see `lookups_sql`),
      6. computes window visit columns (`previous`, `distance_from_previous`) over
         the de-duplicated, filtered visits, before the row-level visit columns so
         those may read them.

    (4) is what makes a one-worker evaluation cost one worker. The compiler also
    applies the filter, but after this subquery -- and Postgres cannot push a
    `username` predicate below the DISTINCT ON (it is not a DISTINCT key), so the
    whole opportunity's visits were extracted, every form path pulled out of every
    copy, sorted and de-duplicated, only to keep one worker's rows. On the real
    KMC cohort that put a worker's case table past a minute. In the WHERE, the
    rows are dropped before the extraction runs. It is the same set: every cached
    copy of a visit carries the same worker.

    (3) is not a convenience -- which pipelines supply which fields is the
    registry's `pipelines` model. KMC's case: the dashboard reads its weight series
    from a SECOND pipeline ("KMC Weight Series", 5109) whose `weight_g` has five fallback
    paths, while the case pipeline's `weights` has six -- the extra
    `form.case.update.child_weight_visit`. Deriving the series from the case
    pipeline therefore produces a different set of readings, which changes
    weight_consistent and the early-growth window and moves C07-C13. Whichever set
    is *right* is a question for the workbook; for a like-for-like comparison the
    series has to come from the pipeline the dashboard actually uses.
    """
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
    #
    # The same holds when the entity's worker follows visit order (`entity.worker`):
    # whose mother it is depends on the other workers' visits too, so the compiler
    # keeps them and filters on the entity's worker instead.
    if (windows or model.worker_follows_visits) and visit_filter:
        visit_filter = {k: v for k, v in visit_filter.items() if k != "username"}

    # The ENGINE builds the multi-opportunity extraction -- scope, the pipeline's row
    # filters, the in-progress-download guard, de-duplication -- so none of it can be
    # lost here. This module used to take the single-opportunity SQL as text and
    # rewrite its WHERE, and each rewrite dropped something the engine had put there
    # (a status filter; `visit_count > 0`, #1684; the raw-cache slot).
    extra_select = []
    for name, cfg in extra_fields.items():
        expr = field_value_sql(cfg, name)
        if expr is None:
            raise ValueError(f"extra_fields: {name!r} is not a SQL field of its pipeline")
        extra_select.append(f"{expr} as {name}")
    ex = build_multi_opportunity_visit_extraction(
        pipeline_schema,
        opps,
        extra_select=extra_select,
        extra_predicates=_scan_predicates(visit_filter),
    )

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

    lookup_cols, lookup_joins = lookups_sql(model.lookups, lookup_configs, opps)
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


# `extra_fields` key carrying `{lookup name: pipeline config}`. Not an identifier, so
# no registry column can collide with it.
LOOKUPS_KEY = "@lookups"

# Base columns of labs_raw_visit_cache a lookup may key or read on directly -- the
# ones a CommCare HQ form row carries meaningfully (`username` is the submitting
# worker; `visit_date` is when HQ received it).
_LOOKUP_BASE_COLUMNS = ("visit_id", "username", "visit_date", "entity_id")


def _lookup_value_sql(config: Any, name: str, lookup: str, role: str) -> str:
    """One field of a lookup pipeline, as the engine computes it -- or a base column."""
    expr = field_value_sql(config, name)
    if expr is not None:
        return expr
    if any(getattr(f, "name", None) == name for f in getattr(config, "fields", None) or []):
        raise ValueError(
            f"lookup {lookup!r}: {role} {name!r} is computed in Python by its pipeline "
            "(an extractor or a full-context transform), so SQL cannot read it -- declare it with paths instead"
        )
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


def lookups_sql(lookups, configs: dict[str, Any], opps: list[int]) -> tuple[str, str]:
    """`(select terms, join clauses)` for the registry's lookups over Layer 1's `b`.

    Each lookup's rows come from the engine's extraction of ITS pipeline -- that
    pipeline's own slot, filters and de-duplication -- reduced to one row per
    (opportunity, key) by `pick`, and LEFT JOINed on `b.<on> = key` within the
    opportunity: a visit with no matching row keeps its place with NULLs, and never
    multiplies.
    """
    cols: list[str] = []
    joins: list[str] = []
    for lk in lookups:
        config = configs.get(lk.name)
        if config is None:
            raise ValueError(f"lookup {lk.name!r}: the workflow has no pipeline source with alias {lk.pipeline!r}")
        key_sql = _lookup_value_sql(config, lk.key, lk.name, "key")
        values = {out: _lookup_value_sql(config, src, lk.name, "field") for out, src in lk.fields.items()}
        extraction = build_multi_opportunity_visit_extraction(
            config,
            opps,
            only_fields=[],
            extra_select=[f"({key_sql})::text AS lookup_key", *(f"{v} AS {out}" for out, v in values.items())],
        )
        rows = extraction.replace("\n", "\n    ")
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
