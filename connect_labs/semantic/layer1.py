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

from connect_labs.semantic.model import resolve_model

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
        + f"WHERE opportunity_id IN ({opp_list}) AND visit_count > 0{filters}{_scan_filter_sql(visit_filter)}\n"
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
    extra = visit_columns_sql(resolve_model(props_doc).visit_columns)
    return f"""SELECT
  x.*{extra}
FROM (
{ex}
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
