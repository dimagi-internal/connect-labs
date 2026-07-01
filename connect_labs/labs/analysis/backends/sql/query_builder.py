"""
SQL query builder for translating AnalysisPipelineConfig to SQL.

Translates field computations to PostgreSQL queries that:
1. Extract values from JSONB form_json
2. Apply transforms using SQL CASE statements
3. Aggregate using GROUP BY
4. Compute histograms
"""

import logging

from django.db import connection

from connect_labs.labs.analysis.config import (
    RAW_VISIT_BASE_COLUMNS,
    AnalysisPipelineConfig,
    FieldComputation,
    HistogramComputation,
    JoinConfig,
)

logger = logging.getLogger(__name__)


def _pipeline_scope_where(opportunity_id: int, pipeline_id: int | None, *, alias: str = "") -> str:
    """SQL WHERE-fragment scoping a labs_raw_visit_cache read to one pipeline's slot.

    All extraction queries must include this — without filtering by
    pipeline_id we'd read another pipeline's rows for the same
    opportunity (#116). Pass `alias=""` for unqualified column refs in
    the outer query, or the alias name (e.g. "sub") for qualified refs
    inside correlated subqueries.

    pipeline_id=None matches the legacy/ad-hoc-caller slot (rows written
    by callers without a workflow definition id, where the column is
    NULL). The check is `IS NULL` rather than `= NULL` because SQL.
    """
    prefix = f"{alias}." if alias else ""
    if pipeline_id is None:
        return f"{prefix}opportunity_id = {opportunity_id} AND {prefix}pipeline_id IS NULL"
    return f"{prefix}opportunity_id = {opportunity_id} AND {prefix}pipeline_id = {pipeline_id}"


def _date_window_where(config: AnalysisPipelineConfig, *, alias: str = "") -> str:
    """SQL WHERE-fragment restricting visits to config's half-open [date_from,
    date_to) window, or "" when neither bound is set.

    Returns a leading-" AND " fragment so callers can append it directly to an
    existing WHERE clause. The window is half-open — `>= date_from AND <
    date_to` — so back-to-back periods (week N ending == week N+1 starting) do
    not double-count the boundary day.

    SAFETY: both bounds are parsed through `date.fromisoformat`, so only a
    validated literal `YYYY-MM-DD` is ever interpolated into SQL. A malformed
    or empty bound contributes no predicate (fails open to "no bound on that
    side"). The bounds originate server-side from a run's period, never from
    user free-text, but this keeps the builder injection-proof regardless.
    """
    from datetime import date as _date

    def _parse(s: str):
        if not s or not isinstance(s, str):
            return None
        try:
            return _date.fromisoformat(s[:10])
        except ValueError:
            return None

    lo = _parse(getattr(config, "date_from", ""))
    hi = _parse(getattr(config, "date_to", ""))
    prefix = f"{alias}." if alias else ""
    parts = []
    if lo is not None:
        parts.append(f"{prefix}visit_date >= '{lo.isoformat()}'")
    if hi is not None:
        parts.append(f"{prefix}visit_date < '{hi.isoformat()}'")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _jsonb_path_to_sql(path: str, column: str = "form_json") -> str:
    """
    Convert a dot-notation path to PostgreSQL JSONB extraction.

    Example: "form.case.update.muac_cm" -> "form_json->'form'->'case'->'update'->>'muac_cm"
    """
    parts = path.split(".")
    if not parts:
        return "NULL"

    sql_parts = [column]
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            sql_parts.append(f"->>'{part}'")
        else:
            sql_parts.append(f"->'{part}'")

    return "".join(sql_parts)


def _build_join_subquery(joins: list[JoinConfig], opportunity_id: int, pipeline_id: int | None) -> str:
    """Build a parenthesized subquery that mirrors labs_raw_visit_cache, with
    each join's fields injected into form_json under `joined.<from_alias>.<field>`.

    Designed to be substituted everywhere `labs_raw_visit_cache` appears in a
    query, including correlated subqueries that reference column-qualified names
    like `labs_raw_visit_cache.opportunity_id` and `sub.username`. The returned
    string is a parenthesized SELECT that the caller MUST alias.

    Each join LEFT JOINs labs_computed_visit_cache filtered by the joined
    pipeline's `resolved_config_hash`, pre-aggregated by remote_key_field via
    DISTINCT ON to prevent row blow-up if multiple cache rows share the same
    join key (e.g., a mother with two registration forms — we keep the latest).

    pipeline_id scopes the inner read of labs_raw_visit_cache so JOIN-extension
    only sees rows from THIS pipeline's slot. Without it, multiple pipelines
    sharing an opportunity would commingle visits in the joined source.
    """
    join_clauses = []
    join_obj_kvs = []  # for outer jsonb_build_object building `joined`

    for j in joins:
        if not j.resolved_config_hash:
            raise ValueError(
                f"JoinConfig from_alias={j.from_alias!r}: resolved_config_hash not set; "
                "the orchestration layer must populate it before SQL build."
            )

        cache_alias = f"_jcache_{j.from_alias}"

        # Per-row joined object keyed by output names — built from the
        # pre-aggregated subquery's pre-sliced JSONB column.
        kv_pairs = [f"'{jf['name']}', {cache_alias}.computed_fields_json->'{jf['name']}'" for jf in j.fields]
        per_alias_object = f"COALESCE(jsonb_build_object({', '.join(kv_pairs)}), '{{}}'::jsonb)"
        join_obj_kvs.append(f"'{j.from_alias}', {per_alias_object}")

        # Local key extraction (JSONB path on the visit's form_json).
        local_key_sql = _jsonb_path_to_sql(j.local_key, "rv.form_json")

        # Pre-aggregate the joined cache by remote_key_field; pick the latest
        # per key so multiple cache rows for the same key don't multiply visit rows.
        # Build a sliced JSONB object containing only the requested fields.
        remote_kv_pairs = [f"'{jf['name']}', computed_fields->'{jf['from']}'" for jf in j.fields]
        remote_obj = f"jsonb_build_object({', '.join(remote_kv_pairs)})"

        join_clauses.append(
            f"""LEFT JOIN (
                SELECT DISTINCT ON (computed_fields->>'{j.remote_key_field}')
                    computed_fields->>'{j.remote_key_field}' AS join_key,
                    {remote_obj} AS computed_fields_json
                FROM labs_computed_visit_cache
                WHERE opportunity_id = {opportunity_id}
                  AND config_hash = '{j.resolved_config_hash}'
                  AND computed_fields->>'{j.remote_key_field}' IS NOT NULL
                ORDER BY
                    computed_fields->>'{j.remote_key_field}',
                    visit_date DESC NULLS LAST,
                    visit_id DESC
            ) {cache_alias}
                ON {cache_alias}.join_key = {local_key_sql}"""
        )

    joined_object = f"jsonb_build_object({', '.join(join_obj_kvs)})"
    join_clauses_sql = "\n            ".join(join_clauses)

    # SELECT the same columns as labs_raw_visit_cache so callers see no
    # difference except form_json now contains a `joined` key. We list every
    # column explicitly because `rv.*` would collide with our replaced
    # form_json. Order matches the model definition.
    return f"""(
            SELECT
                rv.opportunity_id,
                rv.pipeline_id,
                rv.visit_count,
                rv.expires_at,
                rv.created_at,
                rv.visit_id,
                rv.username,
                rv.deliver_unit,
                rv.deliver_unit_id,
                rv.entity_id,
                rv.entity_name,
                rv.visit_date,
                rv.status,
                rv.reason,
                rv.location,
                rv.flagged,
                rv.flag_reason,
                jsonb_set(
                    COALESCE(rv.form_json, '{{}}'::jsonb),
                    '{{joined}}',
                    {joined_object},
                    true
                ) AS form_json,
                rv.completed_work,
                rv.status_modified_date,
                rv.review_status,
                rv.review_created_on,
                rv.justification,
                rv.date_created,
                rv.completed_work_id,
                rv.images
            FROM labs_raw_visit_cache rv
            {join_clauses_sql}
            WHERE {_pipeline_scope_where(opportunity_id, pipeline_id, alias="rv")}
        )"""


_JOINED_VISITS_CTE = "_visits_with_joins"


def _visit_source_sql(config: AnalysisPipelineConfig, opportunity_id: int) -> str:
    """Return the FROM-source token for visit reads inside aggregation queries.

    No joins → the literal table name `labs_raw_visit_cache`.
    With joins → the CTE alias `_visits_with_joins` (the CTE itself is
    emitted by `build_flw_aggregation_query` so it materializes once).

    The CTE indirection matters at scale: without it, every inner
    correlated subquery (per FLW × per field) re-executes the
    LEFT JOIN to `labs_computed_visit_cache`, blowing query time
    from seconds to tens of minutes. With CTE materialization the
    JOIN runs once and is hash-probed by the inner aggregations.

    Caller still aliases this token like `FROM {source} AS labs_raw_visit_cache`
    or `FROM {source} sub`; both work whether the source is a real table
    name or a CTE alias.
    """
    if not config.joins:
        return "labs_raw_visit_cache"
    return _JOINED_VISITS_CTE


def _visit_source_cte_body(config: AnalysisPipelineConfig, opportunity_id: int) -> str | None:
    """Return the CTE body for the join-extended visit source, or None.

    Used by `build_flw_aggregation_query` (and other top-level builders) to
    emit a `WITH _visits_with_joins AS MATERIALIZED (...)` prologue when
    joins are configured. None when there are no joins.

    pipeline_id from `config.pipeline_id` scopes the inner read of
    labs_raw_visit_cache so the JOIN-extended view only includes rows
    from this pipeline's slot.
    """
    if not config.joins:
        return None
    # `_build_join_subquery` wraps its SELECT in parens so it can be used as
    # a `FROM (subquery)` token. CTE bodies don't take outer parens — strip them.
    body = _build_join_subquery(config.joins, opportunity_id, config.pipeline_id).strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    return body


def _paths_to_coalesce_sql(paths: list[str], column: str = "form_json") -> str:
    """Convert multiple paths to a COALESCE expression.

    Wraps each path in NULLIF(..., '') so empty strings are treated as NULL
    and COALESCE falls through to the next path.
    """
    if not paths:
        return "NULL"

    sql_paths = [f"NULLIF({_jsonb_path_to_sql(p, column)}, '')" for p in paths]
    return f"COALESCE({', '.join(sql_paths)})"


def _get_transform_pattern(field: FieldComputation | HistogramComputation) -> str | None:
    """Identify the transform pattern from the field."""
    if field.transform is None:
        return None

    import inspect

    try:
        source = inspect.getsource(field.transform)
    except (OSError, TypeError):
        source = ""

    name = field.name.lower()

    # GPS-string parsing — packed "lat lon altitude accuracy" → individual float
    # columns. Two distinct patterns matching the lambdas in the transform
    # registry: split()[0] for latitude, split()[1] for longitude.
    if "split()[0]" in source and "float(" in source:
        return "gps_lat"
    if "split()[1]" in source and "float(" in source:
        return "gps_lon"

    if "yes" in source and "true" in source:
        return "yes_no_to_1"

    if "_is_valid_muac" in source:
        # Check specific patterns FIRST before generic ones
        # Order matters: check SAM/MAM before generic float conversion
        # Note: MAM uses "11.5 <=" not ">= 11.5" (Python chained comparison)
        if "< 11.5" in source and "11.5 <=" not in source:
            return "muac_sam"
        elif ("11.5 <=" in source or ">= 11.5" in source) and "< 12.5" in source:
            return "muac_mam"
        elif "float(x)" in source:
            return "is_valid_muac_to_float"
        else:
            return "is_valid_muac_to_1"

    # Numeric conversions with validation
    if "_is_valid_weight" in source or ("isdigit()" in source and "replace" in source):
        if "int(x)" in source:
            return "validated_int"
        elif "float(x)" in source:
            return "validated_float"

    # Simple numeric conversions
    if "float(x)" in source and "if x else None" in source:
        return "simple_float"

    if "int(x)" in source and "if x else None" in source:
        return "simple_int"

    if "male" in source.lower():
        if "female" in name or "'female'" in source.lower():
            return "gender_female"
        else:
            return "gender_male"

    if "strip()" in source or "and str(x)" in source:
        return "non_empty_to_1"

    return None


def _transform_to_sql(field: FieldComputation | HistogramComputation, value_expr: str) -> str:
    """Convert a field's transform to SQL CASE statement."""
    if field.transform is None:
        return value_expr

    transform_src = _get_transform_pattern(field)

    if transform_src == "yes_no_to_1":
        return f"""CASE WHEN LOWER({value_expr}) IN ('yes', '1', 'true') THEN 1 ELSE NULL END"""

    elif transform_src == "is_valid_muac_to_1":
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' THEN 1 ELSE NULL END"""

    elif transform_src == "is_valid_muac_to_float":
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' THEN ({value_expr})::FLOAT ELSE NULL END"""

    elif transform_src == "muac_sam":
        return (
            f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' """
            f"""AND ({value_expr})::FLOAT < 11.5 THEN 1 ELSE NULL END"""
        )

    elif transform_src == "muac_mam":
        return (
            f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' """
            f"""AND ({value_expr})::FLOAT >= 11.5 AND ({value_expr})::FLOAT < 12.5 THEN 1 ELSE NULL END"""
        )

    elif transform_src == "validated_int":
        # int(x) with validation (checks isdigit/numeric)
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]+$' THEN ({value_expr})::INTEGER ELSE NULL END"""

    elif transform_src == "validated_float":
        # float(x) with validation (checks isdigit/numeric)
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' THEN ({value_expr})::FLOAT ELSE NULL END"""

    elif transform_src == "simple_float":
        # Simple float(x) if x else None - tries conversion, NULL on error
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]*\\.?[0-9]+$' THEN ({value_expr})::FLOAT ELSE NULL END"""

    elif transform_src == "simple_int":
        # Simple int(x) if x else None - tries conversion, NULL on error
        return f"""CASE WHEN {value_expr} ~ '^-?[0-9]+$' THEN ({value_expr})::INTEGER ELSE NULL END"""

    elif transform_src == "gender_male":
        return f"""CASE WHEN LOWER({value_expr}) IN ('male', 'm', 'boy', 'male_child') THEN 1 ELSE NULL END"""

    elif transform_src == "gender_female":
        return f"""CASE WHEN LOWER({value_expr}) IN ('female', 'f', 'girl', 'female_child') THEN 1 ELSE NULL END"""

    elif transform_src == "non_empty_to_1":
        return f"""CASE WHEN {value_expr} IS NOT NULL AND TRIM({value_expr}) != '' THEN 1 ELSE NULL END"""

    elif transform_src == "gps_lat":
        # Packed "lat lon alt acc" → first whitespace-separated float.
        # Uses regexp_split_to_array to match v1's `.split()` behaviour
        # (any whitespace, ignores empty results from runs of whitespace).
        # split_part(' ') would mis-handle "1.0  2.0" (double-space) by
        # returning '' for the second part. The regex requires decimal-
        # convertible numerics — accepts -1.234, .5, 5, 5.0; rejects empty
        # strings and JSON-serialized dicts (when GPS is missing).
        return (
            f"CASE WHEN {value_expr} IS NOT NULL AND TRIM({value_expr}) != '' "
            f"AND (regexp_split_to_array(TRIM({value_expr}), '\\s+'))[1] ~ '^-?[0-9]*\\.?[0-9]+$' "
            f"THEN ((regexp_split_to_array(TRIM({value_expr}), '\\s+'))[1])::FLOAT ELSE NULL END"
        )

    elif transform_src == "gps_lon":
        # Same as gps_lat but second token.
        return (
            f"CASE WHEN {value_expr} IS NOT NULL AND TRIM({value_expr}) != '' "
            f"AND (regexp_split_to_array(TRIM({value_expr}), '\\s+'))[2] ~ '^-?[0-9]*\\.?[0-9]+$' "
            f"THEN ((regexp_split_to_array(TRIM({value_expr}), '\\s+'))[2])::FLOAT ELSE NULL END"
        )

    else:
        logger.warning(f"Unknown transform for field {field.name}, using passthrough")
        return value_expr


def _aggregation_to_sql(
    agg: str,
    value_expr: str,
    field_name: str,
    filter_path: str = "",
    filter_value: str = "",
    filter_op: str = "eq",
    filter_paths: list[str] | None = None,
    inner_source: str = "labs_raw_visit_cache",
    group_column_outer_expr: str = "labs_raw_visit_cache.username",
    pipeline_id: int | None = None,
) -> str:
    """Convert aggregation type to SQL aggregate function.

    Args:
        agg: Aggregation type (count, sum, avg, first, last, list, count_distinct, etc.)
        value_expr: SQL expression for the value being aggregated
        filter_path: Optional dot-notation path for a FILTER (WHERE ...) clause
        filter_value: Optional value to compare against in the filter clause
        filter_op: How to compare filter_path against filter_value. "eq" for
            exact equality (default), "contains_word" for whitespace-tokenized
            membership (mirrors V1 logic like `"ebf" in bf_status.split()`).

    Notes on `first` / `last`:
        Both use ARRAY_AGG with explicit ORDER BY (visit_date ASC|DESC, visit_id ASC|DESC).
        This is a true aggregate over the GROUP — no correlated subquery — which means
        it works for any GROUP BY expression (FLW's `username`, entity's JSONB-extracted
        linking_field, etc.) without needing to resolve outer-vs-inner column qualification.
        The previous correlated-subquery implementation broke at entity stage because
        Postgres rejected ungrouped `form_json` references when the linking_field was a
        JSONB path expression. Tiebreaker is visit_id ASC for `first`, DESC for `last`,
        consistent at every stage.

    Notes on `mode_share` / `pre_aggregate_by`:
        Both still use correlated subqueries scoped to (opportunity_id, username), so
        they only work at FLW (`username`) grouping, NOT entity-stage. Widening them
        is straightforward — replace the WHERE clause with a parameterized outer
        grouping key — but unblocking that wasn't needed for the MBW v3 work that
        introduced them. Tracked as a known gap.
    """
    # NOTE: main refactored first/last from correlated subqueries to
    # ARRAY_AGG with explicit ORDER BY — no subquery, no outer-vs-inner
    # column qualification needed. The outer query's WHERE already
    # filters by pipeline_id so the aggregate stays in this pipeline's
    # slot. group_column_outer_expr / pipeline_id parameters are still
    # accepted for forward-compat with any future agg type that adds a
    # correlated subquery (mode_share / pre_aggregate_by per docstring).
    # When that happens, build the sub-pipe scope here:
    #   sub_pipe = "AND sub.pipeline_id IS NULL" if pipeline_id is None
    #              else f"AND sub.pipeline_id = {pipeline_id}"
    if agg == "count":
        base = f"COUNT({value_expr})"
    elif agg == "sum":
        base = f"SUM({value_expr})"
    elif agg == "avg":
        base = f"AVG({value_expr})"
    elif agg == "first":
        return (
            f"(ARRAY_AGG({value_expr} ORDER BY visit_date ASC NULLS LAST, visit_id ASC) "
            f"FILTER (WHERE {value_expr} IS NOT NULL))[1]"
        )
    elif agg == "count_distinct" or agg == "count_unique":
        base = f"COUNT(DISTINCT {value_expr})"
    elif agg == "last":
        return (
            f"(ARRAY_AGG({value_expr} ORDER BY visit_date DESC NULLS LAST, visit_id DESC) "
            f"FILTER (WHERE {value_expr} IS NOT NULL))[1]"
        )
    elif agg == "list":
        # Aggregate as array, will be converted to Python list
        # Note: list already has its own FILTER clause, skip per-field filter
        return f"ARRAY_AGG({value_expr}) FILTER (WHERE {value_expr} IS NOT NULL)"
    elif agg == "min":
        base = f"MIN({value_expr})"
    elif agg == "max":
        base = f"MAX({value_expr})"
    elif agg == "median":
        # Postgres interpolated median; ignores NULLs implicitly.
        base = f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {value_expr})"
    elif agg == "mode":
        # MODE() returns the most frequent non-null value; ties resolved by Postgres.
        base = f"MODE() WITHIN GROUP (ORDER BY {value_expr})"
    elif agg == "mode_share":
        # Share (0..1) of non-null rows whose value equals the mode.
        # Used for fraud-concentration: 1.0 means every value is identical.
        #
        # Implementation: correlated subquery rather than
        #   COUNT(*) FILTER (WHERE v = MODE() WITHIN GROUP (ORDER BY v))
        # because Postgres rejects aggregate functions inside FILTER clauses
        # ("aggregate functions are not allowed in FILTER"). Instead we group
        # the same FLW's rows by value, then take the max group-count over
        # the total non-null count.
        #
        # pipeline_id is injected as a SQL constant (not correlated to outer)
        # to avoid pollution of the outer GROUP BY. Adding pipeline_id to the
        # correlated WHERE clause would force callers to include it in their
        # GROUP BY — breaks tests that build minimal queries around just this
        # SQL fragment, and changes the contract of every existing extraction.
        #
        # Mirrors the first/last subquery pattern; like those, the per-field
        # FILTER (path/value) clause isn't supported on this aggregation —
        # early return below.
        sub_pipeline_scope = _pipeline_scope_where(0, pipeline_id, alias="sub")
        # Strip the leading opp clause (which we already correlate against
        # the outer row) — keep just the pipeline_id portion.
        sub_pipeline_clause = sub_pipeline_scope.split(" AND ", 1)[1]
        return f"""(
            SELECT MAX(c)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM {inner_source} sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND {sub_pipeline_clause}
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                GROUP BY {value_expr}
            ) freq
        )"""
    elif agg == "dup_share":
        # Share (0..1) of non-null rows whose value is part of a duplicate group
        # (i.e., the value appears more than once in the FLW's data). Mirrors
        # v1's _compute_value_concentration.pct_duplicate logic:
        #     duplicate_count = sum(c for c in counter.values() if c > 1)
        #     pct_duplicate = duplicate_count / total
        #
        # Used alongside mode_share for fraud detection: high dup_share means
        # the FLW reports lots of repeating values, even if no single value
        # dominates. Same correlated-subquery shape as mode_share.
        sub_pipeline_scope = _pipeline_scope_where(0, pipeline_id, alias="sub")
        sub_pipeline_clause = sub_pipeline_scope.split(" AND ", 1)[1]
        return f"""(
            SELECT COALESCE(SUM(c) FILTER (WHERE c > 1), 0)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM {inner_source} sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND {sub_pipeline_clause}
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                GROUP BY {value_expr}
            ) freq
        )"""
    else:
        # Fail loudly on unknown aggregations rather than silently substituting
        # MIN(). Prior behaviour made typos produce wrong data without warning.
        raise ValueError(
            f"Unknown aggregation {agg!r} on field {field_name!r}. "
            "Valid: count, sum, avg, min, max, first, last, count_distinct, "
            "count_unique, list, median, mode, mode_share, dup_share."
        )

    # Apply per-field FILTER clause if filter_value is provided. The filter
    # source is either a single path (filter_path) or a list coalesced via
    # NULLIF (filter_paths) — the latter mirrors the field's `paths` semantics
    # so a multi-path field can filter on the SAME coalesced value, required
    # for v1 fidelity on metrics like EBF where the field coalesces 5 form
    # paths and the check applies to whichever one matched.
    #
    # filter_op switches the comparison shape: "eq" (default) is exact
    # equality; "contains_word" treats the value as a whitespace-tokenized
    # list and matches when filter_value is one of the tokens.
    if (filter_path or filter_paths) and filter_value:
        if filter_paths:
            filter_sql = _paths_to_coalesce_sql(filter_paths)
        else:
            filter_sql = _jsonb_path_to_sql(filter_path)
        # TRIM the extracted value before comparison — production form data
        # often has trailing whitespace (e.g., MBW form_name = "ANC Visit "
        # with a trailing space). v1 always strips before comparing; without
        # TRIM here, v3 would fail to match and miss rows entirely.
        # contains_word does its own tokenization (string_to_array) which
        # naturally handles whitespace; TRIM is unnecessary there.
        if filter_op == "eq":
            predicate = f"TRIM({filter_sql}) = '{filter_value}'"
        elif filter_op == "contains_word":
            predicate = f"'{filter_value}' = ANY(string_to_array(COALESCE({filter_sql}, ''), ' '))"
        else:
            raise ValueError(f"Unknown filter_op {filter_op!r} on field {field_name!r}. Valid: 'eq', 'contains_word'.")
        base = f"{base} FILTER (WHERE {predicate})"

    return base


def _build_histogram_fields(hist: HistogramComputation, opportunity_id: int) -> list[tuple[str, str]]:
    """
    Build SQL expressions for histogram bin counts.

    Returns list of (field_name, sql_expression) tuples.
    """
    paths = hist.paths if hist.paths else [hist.path]
    value_expr = _paths_to_coalesce_sql(paths)

    # Apply transform to get float value
    float_expr = _transform_to_sql(hist, value_expr)

    # Calculate bin width
    bin_width = (hist.upper_bound - hist.lower_bound) / hist.num_bins

    fields = []

    # Generate a field for each bin
    for i in range(hist.num_bins):
        bin_lower = hist.lower_bound + (i * bin_width)
        bin_upper = bin_lower + bin_width

        # Bin name like "muac_9_5_10_5_visits"
        lower_str = str(bin_lower).replace(".", "_")
        upper_str = str(bin_upper).replace(".", "_")
        bin_name = f"{hist.bin_name_prefix}_{lower_str}_{upper_str}_visits"

        # SQL: count values in this bin range
        # Note: include_out_of_range means values below lower_bound go to first bin,
        # values above upper_bound go to last bin
        if i == 0 and hist.include_out_of_range:
            # First bin: include values below lower_bound
            bin_sql = f"""COUNT(*) FILTER (WHERE {float_expr} < {bin_upper})"""
        elif i == hist.num_bins - 1 and hist.include_out_of_range:
            # Last bin: include values >= upper_bound
            bin_sql = f"""COUNT(*) FILTER (WHERE {float_expr} >= {bin_lower})"""
        elif i == hist.num_bins - 1:
            # Last bin includes upper bound (but not beyond)
            bin_sql = f"""COUNT(*) FILTER (WHERE {float_expr} >= {bin_lower} AND {float_expr} <= {bin_upper})"""
        else:
            bin_sql = f"""COUNT(*) FILTER (WHERE {float_expr} >= {bin_lower} AND {float_expr} < {bin_upper})"""

        fields.append((bin_name, bin_sql))

    # Add summary statistics (round mean to 2 decimal places for parity with Python)
    fields.append((f"{hist.name}_mean", f"ROUND(AVG({float_expr})::numeric, 2)"))
    fields.append((f"{hist.name}_count", f"COUNT({float_expr})"))

    return fields


def _inner_agg_expr(agg: str, value_expr: str) -> str:
    """SQL fragment for the inner (pre_aggregation) collapse step.

    Used inside `_pre_aggregated_field_sql`. Produces a single value per
    pre_aggregate_by group. Unlike _aggregation_to_sql which emits
    correlated subqueries against labs_raw_visit_cache for first/last,
    this stays in the same scope as the inner GROUP BY, so it uses
    ARRAY_AGG ordering or simple aggregates.
    """
    if agg == "first":
        return f"(ARRAY_AGG({value_expr} ORDER BY visit_id ASC) FILTER (WHERE {value_expr} IS NOT NULL))[1]"
    if agg == "last":
        return f"(ARRAY_AGG({value_expr} ORDER BY visit_id DESC) FILTER (WHERE {value_expr} IS NOT NULL))[1]"
    if agg == "count":
        return f"COUNT({value_expr})"
    if agg in ("count_unique", "count_distinct"):
        return f"COUNT(DISTINCT {value_expr})"
    if agg == "sum":
        return f"SUM({value_expr})"
    if agg == "avg":
        return f"AVG({value_expr})"
    if agg == "min":
        return f"MIN({value_expr})"
    if agg == "max":
        return f"MAX({value_expr})"
    if agg == "median":
        return f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {value_expr})"
    if agg == "mode":
        return f"MODE() WITHIN GROUP (ORDER BY {value_expr})"
    raise ValueError(f"pre_aggregation {agg!r} not supported as inner step (mode_share/list invalid here)")


def _pre_aggregated_field_sql_via_per_mother_cte(field: FieldComputation) -> str:
    """SQL for an attribute_to=last_username field, reading from `_per_mother_<path>` CTE.

    The CTE pre-collapses visits to one row per (pre_group, owner_username)
    with the field's per-mother value already computed. Per-FLW outer
    aggregations just slice the CTE by `owner_username = outer.username`
    — no per-row re-collapse, ~270-row scan instead of 86k-visit rescans.

    Field aggregation maps to the same outer shape as before: mode_share /
    dup_share count value frequencies; everything else uses _outer_agg_over_v.
    """
    cte = _per_mother_cte_name(field.pre_aggregate_by)
    column = f"{field.name}_per_mother"
    if field.aggregation == "mode_share":
        return f"""(
            SELECT MAX(c)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM {cte}
                WHERE owner_username = labs_raw_visit_cache.username
                  AND {column} IS NOT NULL
                GROUP BY {column}
            ) freq
        )"""
    if field.aggregation == "dup_share":
        return f"""(
            SELECT COALESCE(SUM(c) FILTER (WHERE c > 1), 0)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM {cte}
                WHERE owner_username = labs_raw_visit_cache.username
                  AND {column} IS NOT NULL
                GROUP BY {column}
            ) freq
        )"""
    outer_expr = _outer_agg_over_v(field.aggregation).replace("v", column)
    return f"""(
        SELECT {outer_expr}
        FROM {cte}
        WHERE owner_username = labs_raw_visit_cache.username
          AND {column} IS NOT NULL
    )"""


def _pre_aggregated_field_sql(
    field: FieldComputation,
    inner_source: str = "labs_raw_visit_cache",
    pipeline_id: int | None = None,
) -> str:
    """SQL for a field with `pre_aggregate_by` set.

    Two-pass aggregation: inner GROUP BY pre_aggregate_by collapses with
    `pre_aggregation`; outer reads the per-pre-group `v` column and
    aggregates with `aggregation`. Filter (path/value/op) on the outer
    isn't supported in this path — use a wrapping `WHERE v ...` clause
    on the inner subquery if you need pre-filtering.

    Mirrors the correlated-subquery pattern of first/last: the inner
    select scopes to (sub.opportunity_id, sub.username) of the outer row.

    `aggregation == "mode_share"` is the only case that requires an extra
    nesting level (group by value, take max-count / sum-count). All other
    aggregations are SELECT-list expressions over the per-group `v` column.
    """
    # Fast path for attributed fields: read from the shared per-pre_group
    # CTE built by `_build_per_mother_cte`. The CTE pre-collapses visits →
    # mothers ONCE for all attributed fields sharing this pre_aggregate_by;
    # per-FLW aggregations here just slice it by owner_username (~270 rows
    # per FLW vs the previous shape's full-CTE rescan per field per FLW).
    # 99 FLWs × 7 fields = 693 rescans → 693 small slices, ~50× speedup.
    if field.pre_aggregate_attribute_to == "last_username":
        return _pre_aggregated_field_sql_via_per_mother_cte(field)

    paths = field.paths if field.paths else [field.path]
    value_expr = _paths_to_coalesce_sql(paths)
    transformed_expr = _transform_to_sql(field, value_expr)
    pre_path_sql = _jsonb_path_to_sql(field.pre_aggregate_by)
    inner_collapse = _inner_agg_expr(field.pre_aggregation, transformed_expr)

    # Build the inner-subquery WHERE clause. Always restrict to the outer
    # row's opportunity_id via correlated reference, plus pre_aggregate_by
    # IS NOT NULL. The username correlation depends on attribute_to mode:
    #
    # - default ("" attribute_to): scope inner rows to outer FLW's visits,
    #   so the pre_group is counted once per FLW that visited it.
    #
    # - "last_username": drop the per-visit username scope; instead require
    #   the outer FLW to be the LAST-visit FLW for the pre_group. Mirrors
    #   v1's winner-takes-all mother→FLW attribution. Each pre_group then
    #   appears under exactly one outer FLW — the right semantics for
    #   fraud-detection metrics where double-counting shared mothers
    #   inflates the signal.
    #
    # If the field declares a filter, apply the SAME filter to the inner
    # subquery — without this, pre_aggregation collapses ALL rows per
    # pre-group, including ones the outer filter would have excluded. v1
    # fidelity requires the filter applied at row level (e.g., MBW's
    # parity_concentration only looks at ANC visits; collapsing across
    # non-ANC visits would pick the wrong "last parity per mother").
    # pipeline_id is injected as a SQL constant (via _pipeline_scope_where)
    # rather than correlated against the outer row. Correlation would force
    # the outer query to add pipeline_id to its GROUP BY, which breaks
    # callers that build minimal queries around this SQL fragment. Constant
    # injection achieves the same isolation (sibling pipelines on the same
    # opp don't commingle) without the GROUP BY contract change.
    sub_pipeline_scope = _pipeline_scope_where(0, pipeline_id, alias="sub")
    sub_pipeline_clause = sub_pipeline_scope.split(" AND ", 1)[1]
    inner_where_clauses = [
        "sub.opportunity_id = labs_raw_visit_cache.opportunity_id",
        sub_pipeline_clause,
        f"{pre_path_sql} IS NOT NULL",
    ]
    if field.pre_aggregate_attribute_to == "last_username":
        # Filter handled below via JOIN to the owner-attribution CTE; no
        # username-correlation predicate goes in inner_where here. The
        # earlier per-row correlated-subquery shape was correct semantically
        # but timed out at production scale (>14 min on opp 765). The CTE
        # version computes mother→owner ONCE per opp; the field-level inner
        # subquery just hash-joins to it.
        pass
    else:
        inner_where_clauses.append("sub.username = labs_raw_visit_cache.username")
    if (field.filter_path or field.filter_paths) and field.filter_value:
        if field.filter_paths:
            inner_filter_sql = _paths_to_coalesce_sql(field.filter_paths)
        else:
            inner_filter_sql = _jsonb_path_to_sql(field.filter_path)
        if field.filter_op == "eq":
            inner_where_clauses.append(f"TRIM({inner_filter_sql}) = '{field.filter_value}'")
        elif field.filter_op == "contains_word":
            inner_where_clauses.append(
                f"'{field.filter_value}' = ANY(string_to_array(COALESCE({inner_filter_sql}, ''), ' '))"
            )

    inner_where = " AND ".join(inner_where_clauses)

    # Inner subquery FROM clause: JOIN to the owner-attribution CTE when
    # attribute_to=last_username so each pre_group is filtered to the
    # outer FLW that "owns" it (last-visit FLW). Otherwise plain FROM.
    if field.pre_aggregate_attribute_to == "last_username":
        cte_alias = _owner_cte_name(field.pre_aggregate_by)
        sub_pre_path = _jsonb_path_to_sql(field.pre_aggregate_by, "sub.form_json")
        from_clause = (
            f"{inner_source} sub "
            f"INNER JOIN {cte_alias} "
            f"ON {cte_alias}.pre_group_key = {sub_pre_path} "
            f"AND {cte_alias}.owner_username = labs_raw_visit_cache.username"
        )
    else:
        from_clause = f"{inner_source} sub"

    inner_subquery = f"""SELECT {pre_path_sql} AS pre_group, {inner_collapse} AS v
            FROM {from_clause}
            WHERE {inner_where}
            GROUP BY {pre_path_sql}"""

    if field.aggregation == "mode_share":
        # mode_share needs a second GROUP BY (by value) over the per-group
        # rows, then max-count / sum-count. Add one more nesting level.
        return f"""(
            SELECT MAX(c)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM (
                    {inner_subquery}
                ) per_group
                WHERE per_group.v IS NOT NULL
                GROUP BY per_group.v
            ) freq
        )"""

    if field.aggregation == "dup_share":
        # Same shape as mode_share but takes "values appearing >1 time" / total
        # rather than "max group / total".
        return f"""(
            SELECT COALESCE(SUM(c) FILTER (WHERE c > 1), 0)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM (
                    {inner_subquery}
                ) per_group
                WHERE per_group.v IS NOT NULL
                GROUP BY per_group.v
            ) freq
        )"""

    outer_expr = _outer_agg_over_v(field.aggregation)
    return f"""(
        SELECT {outer_expr}
        FROM (
            {inner_subquery}
        ) per_group
    )"""


def _outer_agg_over_v(agg: str) -> str:
    """SQL fragment for the outer (aggregation) step that operates on
    the column `v` produced by the inner subquery (one row per pre-group).
    Returns a complete SELECT-list expression.

    `mode_share` is handled by a dedicated branch in `_pre_aggregated_field_sql`
    because it needs an extra GROUP BY level — don't call this for it.
    """
    if agg == "count":
        return "COUNT(v)"
    if agg in ("count_unique", "count_distinct"):
        return "COUNT(DISTINCT v)"
    if agg == "sum":
        return "SUM(v::float)"
    if agg == "avg":
        return "AVG(v::float)"
    if agg == "min":
        return "MIN(v)"
    if agg == "max":
        return "MAX(v)"
    if agg == "median":
        return "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v::float)"
    if agg == "mode":
        return "MODE() WITHIN GROUP (ORDER BY v)"
    raise ValueError(f"aggregation {agg!r} not supported as outer step over pre-aggregated values")


def _owner_cte_name(pre_aggregate_by: str) -> str:
    """Sanitize a JSONB path into a deterministic CTE alias.

    `form.parents.parent.case.@case_id` → `_owners_form_parents_parent_case_case_id`.
    Used to deduplicate the per-opp owner-attribution CTE: every field that
    declares the same `pre_aggregate_by` + `attribute_to=last_username` shares
    one CTE so the per-mother last-visit lookup runs ONCE, not once per field
    per outer row.
    """
    sanitized = pre_aggregate_by.replace(".", "_").replace("@", "")
    return f"_owners_{sanitized}"


def _build_owner_attribution_cte(pre_aggregate_by: str, opportunity_id: int, pipeline_id: int | None) -> str:
    """SQL CTE body that maps each pre_aggregate_by key to its last-visit username.

    Replaces the per-row correlated subquery that timed out at production
    scale (14+ minutes on opp 765 with 99 FLWs × 26k mothers × 4 attributed
    fields). The CTE runs once per opportunity, and the per-field
    aggregation joins to it on the pre_group key — Postgres can hash-join
    the JSONB-extracted key once instead of correlated-scanning per row.

    Reads from the literal `labs_raw_visit_cache` (NOT the join-extended
    source) because owner attribution uses base columns only — joined
    fields are irrelevant to "which FLW visited this mother last".

    pipeline_id scopes the read so multi-pipeline opps don't attribute a
    mother to a worker who only visited her on a different pipeline.
    """
    pre_path_sql = _jsonb_path_to_sql(pre_aggregate_by)
    return f"""SELECT DISTINCT ON ({pre_path_sql})
        {pre_path_sql} AS pre_group_key,
        username AS owner_username
    FROM labs_raw_visit_cache
    WHERE {_pipeline_scope_where(opportunity_id, pipeline_id)}
      AND {pre_path_sql} IS NOT NULL
    ORDER BY {pre_path_sql}, visit_date DESC NULLS LAST, visit_id DESC"""


def _collect_owner_ctes(config: AnalysisPipelineConfig, opportunity_id: int) -> dict[str, str]:
    """Return {cte_alias: cte_body} for every distinct pre_aggregate_by path
    referenced by a field with attribute_to=last_username. Empty dict if none."""
    seen: dict[str, str] = {}
    for f in config.fields:
        if f.pre_aggregate_attribute_to == "last_username" and f.pre_aggregate_by:
            alias = _owner_cte_name(f.pre_aggregate_by)
            if alias not in seen:
                seen[alias] = _build_owner_attribution_cte(f.pre_aggregate_by, opportunity_id, config.pipeline_id)
    return seen


def _per_mother_cte_name(pre_aggregate_by: str) -> str:
    """CTE alias for the per-pre_group collapse. One per distinct pre_aggregate_by."""
    sanitized = pre_aggregate_by.replace(".", "_").replace("@", "")
    return f"_per_mother_{sanitized}"


def _build_per_mother_cte(
    pre_aggregate_by: str,
    fields: list[FieldComputation],
    visit_source: str,
    pipeline_id: int | None,
) -> str:
    """Per-pre_group collapse CTE — one row per (pre_group_key, owner_username).

    Replaces the previous shape where each attributed field ran its own
    correlated subquery, doing the per-mother visit→value collapse 7 times
    (once per attributed field × per FLW = 99 × 7 = 693 evaluations of
    "scan visits, JOIN owners, GROUP BY mother"). The new shape collapses
    visits → mothers ONCE, emitting one column per attributed field. The
    outer per-FLW aggregations then read tiny per-FLW slices (~270 rows)
    instead of rescanning the full 86k-visit CTE.

    Each field column inside the CTE applies that field's own
    `pre_aggregation` and per-field FILTER (e.g. parity filters
    form_name='ANC Visit'; phone has no filter). Fields that share
    pre_aggregate_by share this CTE.

    Owner-username is selected directly so the outer aggregation joins
    on it (FLW = mother's owner) instead of running a separate JOIN per
    inner subquery.
    """
    pre_path_sub = _jsonb_path_to_sql(pre_aggregate_by, "sub.form_json")
    cte_alias_owners = _owner_cte_name(pre_aggregate_by)

    select_parts = [
        f"{pre_path_sub} AS pre_group_key",
        f"{cte_alias_owners}.owner_username AS owner_username",
    ]
    for f in fields:
        paths = f.paths if f.paths else [f.path]
        value_expr = _paths_to_coalesce_sql(paths, column="sub.form_json")
        transformed_expr = _transform_to_sql(f, value_expr)
        # Apply the field's per-row filter, if any, inside the inner collapse.
        # Mirrors what `_pre_aggregated_field_sql` did pre-refactor.
        filter_clause = ""
        if (f.filter_path or f.filter_paths) and f.filter_value:
            if f.filter_paths:
                filter_sql = _paths_to_coalesce_sql(f.filter_paths, column="sub.form_json")
            else:
                filter_sql = _jsonb_path_to_sql(f.filter_path, "sub.form_json")
            if f.filter_op == "eq":
                filter_clause = (
                    f" FILTER (WHERE TRIM({filter_sql}) = '{f.filter_value}' AND {transformed_expr} IS NOT NULL)"
                )
            elif f.filter_op == "contains_word":
                filter_clause = (
                    f" FILTER (WHERE '{f.filter_value}' = ANY(string_to_array(COALESCE({filter_sql}, ''), ' ')) "
                    f"AND {transformed_expr} IS NOT NULL)"
                )
        else:
            filter_clause = f" FILTER (WHERE {transformed_expr} IS NOT NULL)"

        # Per-field collapse: pre_aggregation determines ARRAY_AGG ordering or
        # plain aggregation. Mirrors `_inner_agg_expr` exactly so semantics
        # match the previous correlated-subquery path.
        agg = f.pre_aggregation
        if agg == "first":
            collapse = f"(ARRAY_AGG({transformed_expr} ORDER BY sub.visit_id ASC){filter_clause})[1]"
        elif agg == "last":
            collapse = f"(ARRAY_AGG({transformed_expr} ORDER BY sub.visit_id DESC){filter_clause})[1]"
        elif agg == "count":
            collapse = f"COUNT({transformed_expr}){filter_clause}"
        elif agg in ("count_unique", "count_distinct"):
            # DISTINCT inside aggregation can't combine with FILTER cleanly —
            # rare in practice; fall back to the un-filtered form. If a caller
            # hits this with a real filter, raise rather than silently drop.
            if filter_clause and "FILTER" in filter_clause and (f.filter_path or f.filter_paths):
                raise ValueError(
                    f"pre_aggregation={agg!r} with a per-row filter is not supported in the "
                    f"per-mother CTE — combine the filter into a separate field or drop the filter."
                )
            collapse = f"COUNT(DISTINCT {transformed_expr})"
        elif agg == "sum":
            collapse = f"SUM({transformed_expr}){filter_clause}"
        elif agg == "avg":
            collapse = f"AVG({transformed_expr}){filter_clause}"
        elif agg == "min":
            collapse = f"MIN({transformed_expr}){filter_clause}"
        elif agg == "max":
            collapse = f"MAX({transformed_expr}){filter_clause}"
        elif agg == "median":
            collapse = f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {transformed_expr}){filter_clause}"
        elif agg == "mode":
            collapse = f"MODE() WITHIN GROUP (ORDER BY {transformed_expr}){filter_clause}"
        else:
            raise ValueError(f"pre_aggregation {agg!r} not supported in per-mother CTE")

        select_parts.append(f"{collapse} AS {f.name}_per_mother")

    select_clause = ",\n        ".join(select_parts)

    # Source scope: when visit_source is the JOIN-extended CTE
    # (`_visits_with_joins`), it already filtered by opp+pipeline and we'd
    # re-filter unnecessarily here. When it's the literal labs_raw_visit_cache
    # (no joins case), we MUST filter — otherwise sibling pipelines on the
    # same opp commingle. The owner CTE itself is opp+pipeline-scoped, so the
    # INNER JOIN provides one layer of safety; we add explicit scope as the
    # belt-and-suspenders second layer for the raw-table case.
    sub_pipeline_scope = _pipeline_scope_where(0, pipeline_id, alias="sub")
    sub_pipeline_clause = sub_pipeline_scope.split(" AND ", 1)[1]
    return f"""SELECT
        {select_clause}
    FROM {visit_source} sub
    INNER JOIN {cte_alias_owners} ON {cte_alias_owners}.pre_group_key = {pre_path_sub}
    WHERE {sub_pipeline_clause}
      AND {pre_path_sub} IS NOT NULL
    GROUP BY {pre_path_sub}, {cte_alias_owners}.owner_username"""


def _collect_per_mother_ctes(config: AnalysisPipelineConfig, opportunity_id: int, visit_source: str) -> dict[str, str]:
    """Return {cte_alias: cte_body} for the per-pre_group collapse CTEs.

    Groups attributed fields by their `pre_aggregate_by` path. Each group
    becomes one CTE that pre-collapses visits → pre_groups, with a column
    per field. The outer query then reads small per-FLW slices instead of
    re-doing the collapse per field per FLW.
    """
    by_path: dict[str, list[FieldComputation]] = {}
    for f in config.fields:
        if f.pre_aggregate_attribute_to == "last_username" and f.pre_aggregate_by:
            by_path.setdefault(f.pre_aggregate_by, []).append(f)
    out: dict[str, str] = {}
    for path, fields in by_path.items():
        alias = _per_mother_cte_name(path)
        out[alias] = _build_per_mother_cte(path, fields, visit_source, config.pipeline_id)
    return out


def _build_cte_prologue(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
    include_owners: bool,
) -> str:
    """Build the leading `WITH ...` clause for top-level builders.

    Two kinds of CTE may stack:
      1. `_visits_with_joins` — JOIN-extended visit source, materialized
         once so per-row JSON path extraction below hash-probes one
         instance instead of re-running the LEFT JOIN to the joined
         pipeline's computed cache. MATERIALIZED is required: without it
         Postgres' default CTE inlining defeats the optimization at scale
         (>14 minutes on opp 765 with 99 FLWs × 4 attributed fields).
      2. `_owners_<path>` — one per distinct `pre_aggregate_by` used with
         `attribute_to=last_username`. Maps pre_group key (e.g.,
         mother_case_id) → owner username (last-visit FLW), so the inner
         per-field aggregations hash-join instead of running a per-row
         correlated subquery. Only emitted when `include_owners=True`
         (FLW-stage builder) — visit-extraction and entity-stage paths
         don't run pre_aggregate_by today, so they pass `False`.

    Returns "" when no CTE is needed.
    """
    cte_parts: list[str] = []
    visits_cte_body = _visit_source_cte_body(config, opportunity_id)
    if visits_cte_body is not None:
        cte_parts.append(f"{_JOINED_VISITS_CTE} AS MATERIALIZED (\n{visits_cte_body}\n)")
    if include_owners:
        for alias, body in _collect_owner_ctes(config, opportunity_id).items():
            cte_parts.append(f"{alias} AS MATERIALIZED (\n{body}\n)")
        # Per-pre_group collapse CTEs come AFTER the owner + visits CTEs they
        # reference. One per distinct pre_aggregate_by used by an attributed
        # field. Each is a one-pass GROUP BY that emits one column per
        # attributed field sharing this pre_group — replacing what used to
        # be 693 separate correlated subqueries on opp 765 (99 FLWs × 7
        # fields) with one materialized table the outer query slices cheaply.
        visit_source = _visit_source_sql(config, opportunity_id)
        for alias, body in _collect_per_mother_ctes(config, opportunity_id, visit_source).items():
            cte_parts.append(f"{alias} AS MATERIALIZED (\n{body}\n)")
    return ("WITH " + ",\n".join(cte_parts) + "\n") if cte_parts else ""


def build_flw_aggregation_query(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> str:
    """
    Build SQL query to aggregate raw visits to FLW level.
    """
    pipeline_id = config.pipeline_id
    select_parts = [
        "username",
        "COUNT(*) as total_visits",
        "COUNT(*) FILTER (WHERE status = 'approved') as approved_visits",
        "COUNT(*) FILTER (WHERE status = 'pending') as pending_visits",
        "COUNT(*) FILTER (WHERE status = 'rejected') as rejected_visits",
        "COUNT(*) FILTER (WHERE flagged = true) as flagged_visits",
        # Use _base_ prefix to avoid conflicts with custom config fields of the same name
        "MIN(visit_date) as _base_first_visit_date",
        "MAX(visit_date) as _base_last_visit_date",
        "COUNT(DISTINCT visit_date) as _base_days_active",
    ]

    # If joins are configured, both the outer FROM and the correlated inner
    # subqueries must read from the same join-extended source — otherwise inner
    # aggregations (mode_share, dup_share, pre_aggregate_by) would not see the
    # `joined.<alias>.<field>` paths. Resolve once and pass through.
    visit_source = _visit_source_sql(config, opportunity_id)

    # Add custom fields from config. first/last go through _aggregation_to_sql which
    # uses ARRAY_AGG ORDER BY visit_date+visit_id — same shape entity stage uses.
    for field in config.fields:
        # Two-pass aggregation gets its own dedicated builder — it produces a
        # correlated subquery scoped to the outer (opportunity_id, username).
        # Single-pass field handling continues below for the typical case.
        if field.pre_aggregate_by:
            pre_agg_sql = _pre_aggregated_field_sql(field, inner_source=visit_source, pipeline_id=pipeline_id)
            select_parts.append(f"{pre_agg_sql} as {field.name}")
            continue

        paths = field.paths if field.paths else [field.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(field, value_expr)

        if field.aggregation == "list":
            agg_expr = f"ARRAY_AGG({transformed_expr}) FILTER (WHERE {transformed_expr} IS NOT NULL)"
            select_parts.append(f"{agg_expr} as {field.name}")
        else:
            agg_expr = _aggregation_to_sql(
                field.aggregation,
                transformed_expr,
                field.name,
                filter_path=field.filter_path,
                filter_paths=field.filter_paths,
                filter_value=field.filter_value,
                filter_op=field.filter_op,
                inner_source=visit_source,
                pipeline_id=pipeline_id,
            )
            select_parts.append(f"{agg_expr} as {field.name}")

    # Add histogram fields
    for hist in config.histograms:
        hist_fields = _build_histogram_fields(hist, opportunity_id)
        for field_name, field_sql in hist_fields:
            select_parts.append(f"{field_sql} as {field_name}")

    select_clause = ",\n    ".join(select_parts)
    # Period scoping (ace#764): append the run's half-open visit-date window
    # when set. Default (no window) leaves the WHERE clause byte-identical.
    where_clause = _pipeline_scope_where(opportunity_id, pipeline_id) + _date_window_where(config)

    with_clause = _build_cte_prologue(config, opportunity_id, include_owners=True)

    # Note: opportunity_id must appear in GROUP BY even though the WHERE clause
    # restricts it to a single value. The `first`/`last` aggregations use a
    # correlated subquery that references labs_raw_visit_cache.opportunity_id
    # from the outer query, and Postgres requires every correlated column to
    # be either grouped or aggregated — it doesn't infer constancy from the
    # WHERE filter. Grouping by opportunity_id is free here (it's constant
    # within the filter) but makes the subquery legal.
    query = f"""
        {with_clause}SELECT
            {select_clause}
        FROM {visit_source} AS labs_raw_visit_cache
        WHERE {where_clause}
        GROUP BY username, opportunity_id
        ORDER BY username
    """

    return query


def _resolve_linking_field_outer_expr(config: AnalysisPipelineConfig) -> str:
    """Build the SQL expression for the linking_field, used as the GROUP BY column.

    Resolution order:
    1. If linking_field is the name of a base column on labs_raw_visit_cache, use
       that column directly.
    2. Otherwise, look up linking_field as the name of a FieldComputation in
       config.fields and build a coalesced JSONB path expression from it.
    3. If neither matches, raise.

    The expression is unqualified — bare column references (`form_json`, `username`)
    resolve to the implicit FROM table at SQL evaluation time. This is the same
    convention `build_flw_aggregation_query` uses.
    """
    name = config.linking_field
    if name in RAW_VISIT_BASE_COLUMNS:
        return name

    # Look for a FieldComputation named the same as linking_field
    field_comp = config.get_field(name)
    if field_comp is None:
        raise ValueError(
            f"linking_field {name!r} is not a base column on labs_raw_visit_cache and "
            f"no FieldComputation with that name was found in config.fields. "
            f"Either use a base column name ({sorted(RAW_VISIT_BASE_COLUMNS)}) or declare "
            f"the linking field as a FieldComputation."
        )

    paths = field_comp.paths if field_comp.paths else [field_comp.path]
    if not paths or not any(paths):
        raise ValueError(
            f"linking_field FieldComputation {name!r} has no path or paths set; " f"cannot use as GROUP BY column."
        )
    return _paths_to_coalesce_sql(paths)


def build_entity_aggregation_query(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> str:
    """
    Build SQL query to aggregate raw visits to entity level.

    Mirrors `build_flw_aggregation_query` but groups by `config.linking_field`
    instead of username. Standard counters are total_visits + first/last_visit_date;
    the FLW status counters (approved/pending/rejected/flagged) are dropped because
    an entity is not approved-vs-rejected — its visits are. Templates that need
    those at entity level declare them as FieldComputations.

    Two pieces survive from the FLW shape:
    - A representative `username` column (first(username) per entity) — useful for
      "all entities served by this FLW" queries.
    - An `entity_name` column (first(entity_name) per entity) — denormalized from
      the base raw-visit column.
    """
    if not config.linking_field:
        raise ValueError("config.linking_field must be set for entity-stage aggregation")

    pipeline_id = config.pipeline_id
    group_expr = _resolve_linking_field_outer_expr(config)

    # entity_id is the same expression as the GROUP BY — the row key.
    # `first` and `last` use ARRAY_AGG ORDER BY visit_date+visit_id internally,
    # so they work over any group expression — no need to special-case the
    # linking_field shape. pipeline_id is threaded through for forward-compat
    # with any future agg type that adds a correlated subquery.
    rep_username = _aggregation_to_sql("first", "username", "username", pipeline_id=pipeline_id)
    rep_entity_name = _aggregation_to_sql("first", "entity_name", "entity_name", pipeline_id=pipeline_id)

    select_parts = [
        f"({group_expr}) as entity_id",
        f"{rep_username} as username",
        f"{rep_entity_name} as entity_name",
        "COUNT(*) as total_visits",
        # Use _base_ prefix to avoid conflicts with custom config fields of the same name
        "MIN(visit_date) as _base_first_visit_date",
        "MAX(visit_date) as _base_last_visit_date",
    ]

    # Add custom fields from config. All aggregations (including first/last) go through
    # _aggregation_to_sql; first/last use ARRAY_AGG ORDER BY visit_date, visit_id so the
    # group column doesn't matter — works for any GROUP BY expression.
    #
    # Note: pre_aggregate_by and the mode_share aggregation aren't supported at entity
    # stage yet — both rely on a correlated subquery scoped to (opportunity_id, username)
    # and would need parameterization to work with linking-field grouping.
    for field in config.fields:
        if field.pre_aggregate_by:
            raise ValueError(
                f"pre_aggregate_by isn't supported at entity stage (field {field.name!r}). "
                "Track at the FLW-stage two-pass primitive — extend if needed."
            )

        paths = field.paths if field.paths else [field.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(field, value_expr)

        if field.aggregation == "list":
            agg_expr = f"ARRAY_AGG({transformed_expr}) FILTER (WHERE {transformed_expr} IS NOT NULL)"
            select_parts.append(f"{agg_expr} as {field.name}")
        else:
            agg_expr = _aggregation_to_sql(
                field.aggregation,
                transformed_expr,
                field.name,
                filter_path=field.filter_path,
                filter_paths=field.filter_paths,
                filter_value=field.filter_value,
                filter_op=field.filter_op,
                pipeline_id=pipeline_id,
            )
            select_parts.append(f"{agg_expr} as {field.name}")

    # Add histogram fields (same code as FLW — doesn't reference username)
    for hist in config.histograms:
        hist_fields = _build_histogram_fields(hist, opportunity_id)
        for field_name, field_sql in hist_fields:
            select_parts.append(f"{field_sql} as {field_name}")

    select_clause = ",\n    ".join(select_parts)
    where_clause = _pipeline_scope_where(opportunity_id, pipeline_id)

    visit_source = _visit_source_sql(config, opportunity_id)
    # Entity-stage doesn't currently use pre_aggregate_by/attribute_to, so the
    # owner-attribution CTEs aren't needed here (include_owners=False). The
    # joined-visits CTE is still emitted when joins are configured.
    with_clause = _build_cte_prologue(config, opportunity_id, include_owners=False)

    # GROUP BY the linking-field expression and opportunity_id (same reasoning as FLW —
    # opportunity_id is used by correlated subqueries and must be grouped or aggregated).
    query = f"""
        {with_clause}SELECT
            {select_clause}
        FROM {visit_source} AS labs_raw_visit_cache
        WHERE {where_clause}
        GROUP BY ({group_expr}), opportunity_id
        ORDER BY entity_id
    """

    return query


def execute_entity_aggregation(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> list[dict]:
    """Execute entity aggregation query and return results as list of dicts."""
    query = build_entity_aggregation_query(config, opportunity_id)

    logger.info(f"[SQL] Executing entity aggregation query for opp {opportunity_id}")
    logger.debug(f"[SQL] Query:\n{query}")

    with connection.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    results = []
    for row in rows:
        row_dict = {col: val for col, val in zip(columns, row)}
        results.append(row_dict)

    logger.info(f"[SQL] Aggregated {len(results)} entities")
    return results


def execute_flw_aggregation(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> list[dict]:
    """Execute FLW aggregation query and return results as list of dicts."""
    query = build_flw_aggregation_query(config, opportunity_id)

    logger.info(f"[SQL] Executing FLW aggregation query for opp {opportunity_id}")
    logger.debug(f"[SQL] Query:\n{query}")

    with connection.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    results = []
    for row in rows:
        row_dict = {}
        for col, val in zip(columns, row):
            # Convert arrays to Python lists
            if isinstance(val, list):
                row_dict[col] = val
            else:
                row_dict[col] = val
        results.append(row_dict)

    logger.info(f"[SQL] Aggregated {len(results)} FLWs")
    return results


# -----------------------------------------------------------------------------
# Visit-level extraction (no aggregation)
# -----------------------------------------------------------------------------


def build_visit_extraction_query(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> str:
    """
    Build SQL query to extract computed fields for each visit (no aggregation).

    Returns one row per visit with base fields + computed fields from config.
    """
    # Base visit fields that map to VisitRow
    select_parts = [
        "visit_id",
        "username",
        "visit_date",
        "status",
        "flagged",
        "location",
        "deliver_unit",
        "deliver_unit_id",
        "entity_id",
        "entity_name",
    ]

    # Check if any field needs full visit context (form_json, images)
    needs_full_context = False
    for field in config.fields:
        if field.extractor and callable(field.extractor):
            needs_full_context = True
            break
        if field.transform and callable(field.transform):
            import inspect

            sig = inspect.signature(field.transform)
            params = list(sig.parameters.keys())
            if "visit_data" in params or len(params) == 0:
                needs_full_context = True
                break

    # Include form_json and images if needed
    if needs_full_context:
        select_parts.extend(["form_json", "images"])

    # Track computed field names for JSON building
    computed_field_names = []

    # Add computed fields from config (no aggregation, just extraction + transform)
    for field in config.fields:
        # Handle extractor fields — need post-processing with full visit context
        if field.extractor and callable(field.extractor):
            select_parts.append(f"NULL as {field.name}")
            computed_field_names.append(field.name)
            continue

        # Skip fields that will be computed from full visit context (special markers like __images__)
        if field.transform and callable(field.transform):
            import inspect

            sig = inspect.signature(field.transform)
            params = list(sig.parameters.keys())
            if "visit_data" in params or len(params) == 0:
                # This field will be computed in post-processing with full visit context
                # Don't try to extract from form_json, just add a NULL placeholder
                select_parts.append(f"NULL as {field.name}")
                computed_field_names.append(field.name)
                continue

        paths = field.paths if field.paths else [field.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(field, value_expr)
        select_parts.append(f"{transformed_expr} as {field.name}")
        computed_field_names.append(field.name)

    select_clause = ",\n    ".join(select_parts)

    # Build WHERE clause with filters. Pipeline-id scope is required so we
    # don't read another pipeline's rows for the same opp (#116).
    where_clauses = [_pipeline_scope_where(opportunity_id, config.pipeline_id)]

    # Add entity_id filter if present
    if "entity_id" in config.filters:
        entity_id = config.filters["entity_id"]
        where_clauses.append(f"entity_id = '{entity_id}'")

    # Add status filter if present
    if "status" in config.filters:
        statuses = config.filters["status"]
        if not isinstance(statuses, list):
            statuses = [statuses]
        status_list = ", ".join([f"'{s}'" for s in statuses])
        where_clauses.append(f"status IN ({status_list})")

    # Add flagged filter if present
    if "flagged" in config.filters:
        flagged = config.filters["flagged"]
        where_clauses.append(f"flagged = {flagged}")

    # Add date range filters if present
    if "date_from" in config.filters:
        date_from = config.filters["date_from"]
        where_clauses.append(f"visit_date >= '{date_from}'")

    if "date_to" in config.filters:
        date_to = config.filters["date_to"]
        where_clauses.append(f"visit_date <= '{date_to}'")

    where_clause = " AND ".join(where_clauses)

    # If joins are configured, swap in the join-extended source for both the
    # plain extraction path and the window-wrapped path below. Visit-level
    # extraction has no per-row correlated subqueries, so the joined-visits
    # CTE here is mostly for symmetry with the FLW builder; the perf benefit
    # is small but the structure stays uniform.
    visit_source = _visit_source_sql(config, opportunity_id)
    with_clause = _build_cte_prologue(config, opportunity_id, include_owners=False)

    # If no window fields, emit the simple flat extraction query.
    if not config.window_fields:
        query = f"""
            {with_clause}SELECT
                {select_clause}
            FROM {visit_source} AS labs_raw_visit_cache
            WHERE {where_clause}
            ORDER BY visit_id
        """
        return query, computed_field_names

    # Window-field path: wrap the extraction in a subquery so window functions
    # can reference both base columns and extracted/computed columns by name.
    # Each WindowFieldComputation appends a SELECT-list expression in the outer
    # query, plus its column name to the computed_field_names tally.
    window_select_parts: list[str] = []
    for wf in config.window_fields:
        window_select_parts.append(_window_field_to_sql(wf))
        computed_field_names.append(wf.name)

    window_select_clause = ",\n        ".join(window_select_parts)

    # Post-extraction filters apply AFTER extraction and BEFORE window
    # functions, so e.g. a `latitude IS NOT NULL` filter excludes GPS-less
    # rows from the LAG window's view. Translate each filter into a SQL
    # predicate on the extracted column.
    extracted_filter_clause = ""
    if config.extracted_filters:
        predicates = []
        for f in config.extracted_filters:
            field_name = f.get("field")
            op = f.get("op", "is_not_null")
            if not field_name:
                continue
            if op == "is_not_null":
                predicates.append(f"{field_name} IS NOT NULL")
            elif op == "is_null":
                predicates.append(f"{field_name} IS NULL")
            else:
                raise ValueError(f"Unknown extracted_filter op {op!r} for field {field_name!r}")
        if predicates:
            extracted_filter_clause = "WHERE " + " AND ".join(predicates)

    query = f"""
        {with_clause}SELECT
            base.*,
            {window_select_clause}
        FROM (
            SELECT * FROM (
                SELECT
                    {select_clause}
                FROM {visit_source} AS labs_raw_visit_cache
                WHERE {where_clause}
            ) extracted
            {extracted_filter_clause}
        ) base
        ORDER BY visit_id
    """
    return query, computed_field_names


def _window_field_to_sql(wf: "WindowFieldComputation") -> str:  # noqa: F821
    """Translate a WindowFieldComputation into a SELECT-list expression.

    The expression references columns from the wrapping subquery (`base.<col>`)
    and uses an inline `OVER (PARTITION BY ... ORDER BY ...)` window. Each
    operation has its own SQL pattern.

    Inline windows are simpler than a top-level `WINDOW w AS (...)` clause when
    each window field has its own partitioning; they don't require coordinating
    a unique window name across fields.
    """
    if wf.operation == "lag_haversine":
        window_spec = f"PARTITION BY base.{wf.partition_by} ORDER BY base.{wf.order_by}"
        return (
            f"haversine_meters("
            f"LAG(base.{wf.lat_field}::float) OVER ({window_spec}), "
            f"LAG(base.{wf.lon_field}::float) OVER ({window_spec}), "
            f"base.{wf.lat_field}::float, "
            f"base.{wf.lon_field}::float"
            f") AS {wf.name}"
        )
    raise ValueError(f"Unknown window operation {wf.operation!r} in field {wf.name!r}")


def execute_visit_extraction(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> tuple[list[dict], list[str]]:
    """
    Execute visit extraction query and return results.

    Returns:
        Tuple of (visit_dicts, computed_field_names):
        - visit_dicts: List of dicts with base fields + computed fields
        - computed_field_names: List of field names that are computed (for VisitRow.computed)
    """
    query, computed_field_names = build_visit_extraction_query(config, opportunity_id)

    logger.info(f"[SQL] Executing visit extraction query for opp {opportunity_id}")
    logger.debug(f"[SQL] Query:\n{query}")

    with connection.cursor() as cursor:
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()

    results = []
    for row in rows:
        row_dict = {}
        for col, val in zip(columns, row):
            row_dict[col] = val
        results.append(row_dict)

    logger.info(f"[SQL] Extracted {len(results)} visits with {len(computed_field_names)} computed fields")
    return results, computed_field_names


# -----------------------------------------------------------------------------
# SQL Preview (for debugging/testing)
# -----------------------------------------------------------------------------


def generate_sql_preview(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> dict:
    """
    Generate SQL query strings without executing them.

    This is useful for debugging and testing queries in external tools like psql.

    Args:
        config: Pipeline configuration
        opportunity_id: Opportunity ID to use in WHERE clause

    Returns:
        Dictionary containing:
        - visit_extraction_sql: SQL for extracting visit-level data
        - flw_aggregation_sql: SQL for aggregating to FLW level (if terminal_stage is AGGREGATED)
        - field_expressions: Dict mapping field names to their SQL extraction expressions
        - histogram_expressions: Dict mapping histogram names to their bin SQL expressions
        - terminal_stage: Which query represents the final output
    """
    from connect_labs.labs.analysis.config import CacheStage

    result = {
        "terminal_stage": config.terminal_stage.value,
        "field_expressions": {},
        "histogram_expressions": {},
    }

    # Generate field extraction expressions
    for field in config.fields:
        paths = field.paths if field.paths else [field.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(field, value_expr)
        result["field_expressions"][field.name] = {
            "paths": paths,
            "extraction_sql": value_expr,
            "transformed_sql": transformed_expr,
            "aggregation": field.aggregation,
        }

    # Generate histogram expressions
    for hist in config.histograms:
        paths = hist.paths if hist.paths else [hist.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(hist, value_expr)

        bin_expressions = {}
        bin_width = (hist.upper_bound - hist.lower_bound) / hist.num_bins

        for i in range(hist.num_bins):
            bin_lower = hist.lower_bound + (i * bin_width)
            bin_upper = bin_lower + bin_width
            lower_str = str(bin_lower).replace(".", "_")
            upper_str = str(bin_upper).replace(".", "_")
            bin_name = f"{hist.bin_name_prefix}_{lower_str}_{upper_str}_visits"

            if i == 0 and hist.include_out_of_range:
                bin_sql = f"COUNT(*) FILTER (WHERE {transformed_expr} < {bin_upper})"
            elif i == hist.num_bins - 1 and hist.include_out_of_range:
                bin_sql = f"COUNT(*) FILTER (WHERE {transformed_expr} >= {bin_lower})"
            elif i == hist.num_bins - 1:
                bin_sql = (
                    f"COUNT(*) FILTER (WHERE {transformed_expr} >= {bin_lower} AND {transformed_expr} <= {bin_upper})"
                )
            else:
                bin_sql = (
                    f"COUNT(*) FILTER (WHERE {transformed_expr} >= {bin_lower} AND {transformed_expr} < {bin_upper})"
                )

            bin_expressions[bin_name] = bin_sql

        result["histogram_expressions"][hist.name] = {
            "paths": paths,
            "extraction_sql": value_expr,
            "transformed_sql": transformed_expr,
            "bins": bin_expressions,
        }

    # Generate visit extraction query
    visit_query, computed_fields = build_visit_extraction_query(config, opportunity_id)
    result["visit_extraction_sql"] = _format_sql(visit_query)
    result["computed_fields"] = computed_fields

    # Generate FLW aggregation query if applicable
    if config.terminal_stage == CacheStage.AGGREGATED:
        flw_query = build_flw_aggregation_query(config, opportunity_id)
        result["flw_aggregation_sql"] = _format_sql(flw_query)
    else:
        result["flw_aggregation_sql"] = None

    return result


def _format_sql(sql: str) -> str:
    """Format SQL for readability."""
    # Remove excess whitespace but keep structure
    lines = sql.strip().split("\n")
    formatted_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            formatted_lines.append(stripped)
    return "\n".join(formatted_lines)
