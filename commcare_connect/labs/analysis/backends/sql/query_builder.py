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

from commcare_connect.labs.analysis.config import AnalysisPipelineConfig, FieldComputation, HistogramComputation

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
    agg: str, value_expr: str, field_name: str, filter_path: str = "", filter_value: str = ""
) -> str:
    """Convert aggregation type to SQL aggregate function.

    Args:
        agg: Aggregation type (count, sum, avg, first, last, list, count_distinct, etc.)
        value_expr: SQL expression for the value being aggregated
        filter_path: Optional dot-notation path for a FILTER (WHERE ...) clause
        filter_value: Optional value to compare against in the filter clause
    """
    if agg == "count":
        base = f"COUNT({value_expr})"
    elif agg == "sum":
        base = f"SUM({value_expr})"
    elif agg == "avg":
        base = f"AVG({value_expr})"
    elif agg == "first":
        # Use subquery to get value from row with earliest visit_date
        # This properly implements "first" semantics
        # Note: FILTER not supported for subquery-based aggregations
        return f"""(
            SELECT sub.val FROM (
                SELECT {value_expr} as val, visit_date
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                ORDER BY visit_date ASC
                LIMIT 1
            ) sub
        )"""
    elif agg == "count_distinct" or agg == "count_unique":
        base = f"COUNT(DISTINCT {value_expr})"
    elif agg == "last":
        # Use subquery to get value from row with latest visit_date
        # Mirrors "first" but with DESC ordering
        # Note: FILTER not supported for subquery-based aggregations
        return f"""(
            SELECT sub.val FROM (
                SELECT {value_expr} as val, visit_date
                FROM labs_raw_visit_cache sub
                WHERE sub.opportunity_id = labs_raw_visit_cache.opportunity_id
                  AND sub.username = labs_raw_visit_cache.username
                  AND {value_expr} IS NOT NULL
                ORDER BY visit_date DESC
                LIMIT 1
            ) sub
        )"""
    elif agg == "list":
        # Aggregate as array, will be converted to Python list
        # Note: list already has its own FILTER clause, skip per-field filter
        return f"ARRAY_AGG({value_expr}) FILTER (WHERE {value_expr} IS NOT NULL)"
    elif agg == "min":
        base = f"MIN({value_expr})"
    elif agg == "max":
        base = f"MAX({value_expr})"
    else:
        # Fail loudly on unknown aggregations rather than silently substituting
        # MIN(). Prior behaviour made typos produce wrong data without warning.
        raise ValueError(
            f"Unknown aggregation {agg!r} on field {field_name!r}. "
            "Valid: count, sum, avg, min, max, first, last, count_distinct, count_unique, list."
        )

    # Apply per-field FILTER clause if both filter_path and filter_value are provided
    if filter_path and filter_value:
        filter_sql = _jsonb_path_to_sql(filter_path)
        base = f"{base} FILTER (WHERE {filter_sql} = '{filter_value}')"

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

    # Add custom fields from config
    for field in config.fields:
        paths = field.paths if field.paths else [field.path]
        value_expr = _paths_to_coalesce_sql(paths)
        transformed_expr = _transform_to_sql(field, value_expr)

        if field.aggregation == "first":
            # For "first", order by visit_id to match Python iteration order
            # (visits are typically processed in order they come from API, which is by ID)
            first_expr = f"""(
                ARRAY_AGG({transformed_expr} ORDER BY visit_id ASC) FILTER (WHERE {transformed_expr} IS NOT NULL)
            )[1]"""
            select_parts.append(f"{first_expr} as {field.name}")
        elif field.aggregation == "list":
            agg_expr = f"ARRAY_AGG({transformed_expr}) FILTER (WHERE {transformed_expr} IS NOT NULL)"
            select_parts.append(f"{agg_expr} as {field.name}")
        else:
            agg_expr = _aggregation_to_sql(
                field.aggregation,
                transformed_expr,
                field.name,
                filter_path=field.filter_path,
                filter_value=field.filter_value,
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

    query = f"""
        SELECT
            {select_clause}
        FROM labs_raw_visit_cache
        WHERE {where_clause}
        ORDER BY visit_id
    """

    return query, computed_field_names


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
