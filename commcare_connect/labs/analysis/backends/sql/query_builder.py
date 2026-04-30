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

from commcare_connect.labs.analysis.config import (
    RAW_VISIT_BASE_COLUMNS,
    AnalysisPipelineConfig,
    FieldComputation,
    HistogramComputation,
)

logger = logging.getLogger(__name__)


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
        # Mirrors the first/last subquery pattern; like those, the per-field
        # FILTER (path/value) clause isn't supported on this aggregation —
        # early return below.
        return f"""(
            SELECT MAX(c)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
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
        return f"""(
            SELECT COALESCE(SUM(c) FILTER (WHERE c > 1), 0)::float / NULLIF(SUM(c), 0)
            FROM (
                SELECT COUNT(*) AS c
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
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

    # Apply per-field FILTER clause if both filter_path and filter_value are provided.
    # filter_op switches the comparison shape: "eq" (default) is the prior
    # behaviour; "contains_word" treats the value as a whitespace-tokenized list
    # and matches when filter_value is one of the tokens.
    if filter_path and filter_value:
        filter_sql = _jsonb_path_to_sql(filter_path)
        if filter_op == "eq":
            predicate = f"{filter_sql} = '{filter_value}'"
        elif filter_op == "contains_word":
            # Postgres: split on whitespace, test array membership.
            # COALESCE keeps the predicate well-defined when the path is NULL.
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


def _pre_aggregated_field_sql(field: FieldComputation) -> str:
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
    paths = field.paths if field.paths else [field.path]
    value_expr = _paths_to_coalesce_sql(paths)
    transformed_expr = _transform_to_sql(field, value_expr)
    pre_path_sql = _jsonb_path_to_sql(field.pre_aggregate_by)
    inner_collapse = _inner_agg_expr(field.pre_aggregation, transformed_expr)

    inner_subquery = f"""SELECT {pre_path_sql} AS pre_group, {inner_collapse} AS v
            FROM labs_raw_visit_cache sub
            WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
              AND sub.username = labs_raw_visit_cache.username
              AND {pre_path_sql} IS NOT NULL
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


def build_flw_aggregation_query(
    config: AnalysisPipelineConfig,
    opportunity_id: int,
) -> str:
    """
    Build SQL query to aggregate raw visits to FLW level.
    """
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
    ]

    # Add custom fields from config. first/last go through _aggregation_to_sql which
    # uses ARRAY_AGG ORDER BY visit_date+visit_id — same shape entity stage uses.
    for field in config.fields:
        # Two-pass aggregation gets its own dedicated builder — it produces a
        # correlated subquery scoped to the outer (opportunity_id, username).
        # Single-pass field handling continues below for the typical case.
        if field.pre_aggregate_by:
            select_parts.append(f"{_pre_aggregated_field_sql(field)} as {field.name}")
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
                filter_value=field.filter_value,
                filter_op=field.filter_op,
            )
            select_parts.append(f"{agg_expr} as {field.name}")

    # Add histogram fields
    for hist in config.histograms:
        hist_fields = _build_histogram_fields(hist, opportunity_id)
        for field_name, field_sql in hist_fields:
            select_parts.append(f"{field_sql} as {field_name}")

    select_clause = ",\n    ".join(select_parts)

    # Note: opportunity_id must appear in GROUP BY even though the WHERE clause
    # restricts it to a single value. The `first`/`last` aggregations use a
    # correlated subquery that references labs_raw_visit_cache.opportunity_id
    # from the outer query, and Postgres requires every correlated column to
    # be either grouped or aggregated — it doesn't infer constancy from the
    # WHERE filter. Grouping by opportunity_id is free here (it's constant
    # within the filter) but makes the subquery legal.
    query = f"""
        SELECT
            {select_clause}
        FROM labs_raw_visit_cache
        WHERE opportunity_id = {opportunity_id}
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

    group_expr = _resolve_linking_field_outer_expr(config)

    # entity_id is the same expression as the GROUP BY — the row key.
    # `first` and `last` use ARRAY_AGG ORDER BY visit_date+visit_id internally, so they
    # work over any group expression — no need to special-case the linking_field shape.
    rep_username = _aggregation_to_sql("first", "username", "username")
    rep_entity_name = _aggregation_to_sql("first", "entity_name", "entity_name")

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
                filter_value=field.filter_value,
                filter_op=field.filter_op,
            )
            select_parts.append(f"{agg_expr} as {field.name}")

    # Add histogram fields (same code as FLW — doesn't reference username)
    for hist in config.histograms:
        hist_fields = _build_histogram_fields(hist, opportunity_id)
        for field_name, field_sql in hist_fields:
            select_parts.append(f"{field_sql} as {field_name}")

    select_clause = ",\n    ".join(select_parts)

    # GROUP BY the linking-field expression and opportunity_id (same reasoning as FLW —
    # opportunity_id is used by correlated subqueries and must be grouped or aggregated).
    query = f"""
        SELECT
            {select_clause}
        FROM labs_raw_visit_cache
        WHERE opportunity_id = {opportunity_id}
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

    # Build WHERE clause with filters
    where_clauses = [f"opportunity_id = {opportunity_id}"]

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

    # If no window fields, emit the simple flat extraction query.
    if not config.window_fields:
        query = f"""
            SELECT
                {select_clause}
            FROM labs_raw_visit_cache
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

    query = f"""
        SELECT
            base.*,
            {window_select_clause}
        FROM (
            SELECT
                {select_clause}
            FROM labs_raw_visit_cache
            WHERE {where_clause}
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
    from commcare_connect.labs.analysis.config import CacheStage

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
