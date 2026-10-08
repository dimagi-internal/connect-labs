# Harvested from dimagi-rad/scout @ d5ca8aa6, mcp_server/services/sql_validator.py -- unchanged
# apart from black's formatting.
# Keep it a copy: labs-specific rules live in connect_labs/explorer/validator.py, so a
# Scout fix can be diffed in. Upstream tests: dimagi-rad/scout tests/test_sql_validator.py.
"""
SQL validation for the Scout MCP server.

This module provides the SQL validation layer that ensures all queries
executed through the MCP server are safe and comply with project rules.

Security features:
- Only SELECT statements allowed (including UNION/INTERSECT/EXCEPT)
- Single statement enforcement
- Explicit analytics function allowlist (no custom or extension functions)
- Schema/table allowlist enforcement
- Automatic LIMIT injection and capping
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.tokens import TokenType

logger = logging.getLogger(__name__)


# Dangerous PostgreSQL functions that could be used for data exfiltration or system access
DANGEROUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        # File system access
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_ls_archive_statusdir",
        "pg_ls_tmpdir",
        # Large object manipulation
        "lo_import",
        "lo_export",
        "lo_create",
        "lo_open",
        "lo_write",
        "lo_read",
        "lo_unlink",
        # Remote database access
        "dblink",
        "dblink_connect",
        "dblink_connect_u",
        "dblink_disconnect",
        "dblink_exec",
        "dblink_open",
        "dblink_fetch",
        "dblink_close",
        "dblink_get_connections",
        "dblink_send_query",
        "dblink_is_busy",
        "dblink_get_result",
        "dblink_cancel_query",
        "dblink_error_message",
        # Copy commands via functions
        "pg_copy_from",
        "pg_copy_to",
        # Extension management
        "pg_extension_config_dump",
        # Advisory locks (potential for DoS)
        "pg_advisory_lock",
        "pg_advisory_lock_shared",
        "pg_try_advisory_lock",
        "pg_try_advisory_lock_shared",
        # System information that could aid attacks
        "pg_reload_conf",
        "pg_rotate_logfile",
        "pg_terminate_backend",
        "pg_cancel_backend",
        # Denial of service / session tampering
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "set_config",
        # Command execution
        "query_to_xml",
        "query_to_xmlschema",
        "query_to_xml_and_xmlschema",
        "cursor_to_xml",
        "cursor_to_xmlschema",
        "table_to_xml",
        "table_to_xmlschema",
        "table_to_xml_and_xmlschema",
        "schema_to_xml",
        "schema_to_xmlschema",
        "schema_to_xml_and_xmlschema",
        "database_to_xml",
        "database_to_xmlschema",
        "database_to_xml_and_xmlschema",
    }
)

# Names are SQLGlot's normalized names for built-ins, or PostgreSQL names for
# Anonymous nodes. Unknown/extension functions may execute SQL hidden in strings;
# adding one here requires reviewing its behavior, not just its return type.
ALLOWED_ANALYTICS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "array_contains_all",
        "array_contained_by",
        "jsonb_extract",
        "jsonb_extract_scalar",
        "j_s_o_n_b_contains_top_key",
        "j_s_o_n_b_contains_any_top_keys",
        "j_s_o_n_b_contains_all_top_keys",
        "j_s_o_n_b_path_exists",
        "match_against",
        "array_overlaps",
        "j_s_o_n_b_delete_at_path",
        "localtime",
        "regexp_i_like",
        "logical_and",
        "logical_or",
        "j_s_o_n_array_agg",
        "j_s_o_n_object_agg",
        "j_s_o_n_b_object_agg",
        "unix_to_time",
        "variance_pop",
        "pad",
        "abs",
        "age",
        "and",
        "or",
        "explode",
        "array",
        "array_agg",
        "array_length",
        "array_size",
        "array_to_string",
        "avg",
        "bool_and",
        "bool_or",
        "btrim",
        "cardinality",
        "case",
        "cast",
        "ceil",
        "ceiling",
        "char_length",
        "character_length",
        "coalesce",
        "concat",
        "concat_ws",
        "corr",
        "count",
        "covar_pop",
        "covar_samp",
        "cume_dist",
        "current_date",
        "current_time",
        "current_timestamp",
        "date_part",
        "date_trunc",
        "dense_rank",
        "exists",
        "exp",
        "exploding_generate_series",
        "extract",
        "first_value",
        "floor",
        "generate_series",
        "greatest",
        "group_concat",
        "if",
        "json_agg",
        "json_array_elements",
        "json_array_elements_text",
        "json_array_length",
        "json_build_array",
        "json_build_object",
        "json_extract",
        "json_extract_scalar",
        "json_object_agg",
        "jsonb_agg",
        "jsonb_array_elements",
        "jsonb_array_elements_text",
        "jsonb_array_length",
        "jsonb_build_array",
        "jsonb_build_object",
        "jsonb_extract_path",
        "jsonb_extract_path_text",
        "jsonb_object_agg",
        "jsonb_typeof",
        "lag",
        "last_value",
        "lead",
        "least",
        "left",
        "length",
        "ln",
        "log",
        "lower",
        "lpad",
        "ltrim",
        "max",
        "md5",
        "min",
        "mod",
        "now",
        "nth_value",
        "ntile",
        "nullif",
        "percent_rank",
        "percentile_cont",
        "percentile_disc",
        "position",
        "pow",
        "power",
        "rank",
        "regexp_extract",
        "regexp_like",
        "regexp_replace",
        "regexp_split_to_array",
        "regexp_split_to_table",
        "replace",
        "right",
        "round",
        "row_number",
        "rpad",
        "rtrim",
        "sign",
        "split_part",
        "sqrt",
        "str_position",
        "str_to_date",
        "str_to_time",
        "stddev",
        "stddev_pop",
        "stddev_samp",
        "string_agg",
        "substring",
        "sum",
        "time_to_str",
        "to_char",
        "to_date",
        "to_json",
        "to_jsonb",
        "to_number",
        "to_timestamp",
        "pg_input_is_valid",
        "pg_typeof",
        "translate",
        "trim",
        "trunc",
        "unnest",
        "upper",
        "var_pop",
        "var_samp",
        "variance",
        "width_bucket",
    }
)

# Parser-only names are not PostgreSQL function names and must not authorize a UDF.
PARSER_ONLY_FUNCTIONS: frozenset[str] = frozenset(
    {
        "array_contains_all",
        "array_contained_by",
        "jsonb_extract",
        "jsonb_extract_scalar",
        "j_s_o_n_b_contains_top_key",
        "j_s_o_n_b_contains_any_top_keys",
        "j_s_o_n_b_contains_all_top_keys",
        "j_s_o_n_b_path_exists",
        "match_against",
        "array_overlaps",
        "j_s_o_n_b_delete_at_path",
        "localtime",
        "regexp_i_like",
        "logical_and",
        "logical_or",
        "j_s_o_n_array_agg",
        "j_s_o_n_object_agg",
        "j_s_o_n_b_object_agg",
        "unix_to_time",
        "variance_pop",
        "pad",
        "and",
        "array",
        "case",
        "cast",
        "current_date",
        "current_time",
        "current_timestamp",
        "explode",
        "exploding_generate_series",
        "exists",
        "extract",
        "str_to_date",
        "str_to_time",
        "group_concat",
        "if",
        "json_extract",
        "json_extract_scalar",
        "or",
        "time_to_str",
    }
)

# PostgreSQL parses these as syntax/operators, not search_path function calls.
SQL_SPECIAL_FORMS: frozenset[str] = frozenset(
    {
        "array_contains_all",
        "array_contained_by",
        "jsonb_extract",
        "jsonb_extract_scalar",
        "j_s_o_n_b_contains_top_key",
        "j_s_o_n_b_contains_any_top_keys",
        "j_s_o_n_b_contains_all_top_keys",
        "j_s_o_n_b_path_exists",
        "match_against",
        "array_overlaps",
        "j_s_o_n_b_delete_at_path",
        "localtime",
        "regexp_i_like",
        "regexp_like",
        "and",
        "array",
        "case",
        "cast",
        "coalesce",
        "current_date",
        "current_time",
        "current_timestamp",
        "exists",
        "extract",
        "greatest",
        "if",
        "json_extract",
        "json_extract_scalar",
        "least",
        "nullif",
        "or",
        "str_position",
        "substring",
        "trim",
    }
)

# Casts invoke type I/O functions, so unknown and OID-alias types are not a
# safe substitute for the function allowlist. Arrays must have safe element types.
ALLOWED_CAST_TYPES = frozenset(
    {
        exp.DType.ARRAY,
        exp.DType.BIGINT,
        exp.DType.BIT,
        exp.DType.BOOLEAN,
        exp.DType.CHAR,
        exp.DType.DATE,
        exp.DType.DECIMAL,
        exp.DType.DOUBLE,
        exp.DType.FLOAT,
        exp.DType.INET,
        exp.DType.INT,
        exp.DType.INTERVAL,
        exp.DType.JSON,
        exp.DType.JSONB,
        exp.DType.SMALLINT,
        exp.DType.TEXT,
        exp.DType.TIME,
        exp.DType.TIMETZ,
        exp.DType.TIMESTAMP,
        exp.DType.TIMESTAMPTZ,
        exp.DType.UUID,
        exp.DType.VARBINARY,
        exp.DType.VARCHAR,
    }
)

# Statement types that are not allowed (only SELECT is permitted)
FORBIDDEN_STATEMENT_TYPES: frozenset[type] = frozenset(
    {
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Alter,
        exp.TruncateTable,
        exp.Create,
        exp.Grant,
        exp.Revoke,
        exp.Merge,
        exp.Set,
        exp.Command,
    }
)

# Prefix reserved by PostgreSQL for system objects, and the reason unqualified
# catalog reads have to be rejected by name rather than by schema.
#
# An unqualified relation resolves via the implicit ``pg_catalog`` schema, and
# many of those views (``pg_class``, ``pg_statio_all_tables``,
# ``pg_stat_user_indexes``, ...) keep the default PUBLIC SELECT grant with no
# ``has_table_privilege`` filter, so they are readable regardless of SET ROLE and
# leak cross-tenant metadata: tenant schema names derive from customer
# identifiers and ``pg_class.reltuples`` exposes row counts. The AST carries no
# schema for those references, so ``_validate_table_access`` — which only checks
# a reference that names a schema — never sees them.
#
# Matching the whole ``pg_`` namespace rather than an enumerated list is
# deliberate: an exact-match set silently missed siblings from the very same view
# family (it held ``pg_stat_all_tables`` but not ``pg_statio_all_tables``).
#
# PostgreSQL reserves ``pg_`` for schema names but not for table names, so a
# tenant table could in principle be called ``pg_notes``. The check therefore
# applies only to an UNqualified reference — the form that resolves through
# ``pg_catalog`` and the only form the schema allowlist cannot see. Qualified
# ``ws_x.pg_notes`` is left to the allowlist, which permits the tenant's own
# schema and rejects ``pg_catalog``.
SYSTEM_CATALOG_PREFIX = "pg_"


def _argument_count(expression: exp.Expression) -> int:
    # Count operand expressions, not dialect flags such as trim direction.
    return sum(1 for _ in expression.iter_expressions())


class _PreservingPostgres(Postgres):
    """Retain call arity before SQLGlot normalizes away unsupported arguments."""

    # This private parser hook is pinned to SQLGlot 30.18.0; the preservation
    # sweeps must pass before a dependency upgrade changes its contract.
    class Parser(Postgres.Parser):
        # PostgreSQL units are text expressions, not SQLGlot date-part keywords.
        FUNCTIONS = {
            **Postgres.Parser.FUNCTIONS,
            "DATE_TRUNC": lambda args: exp.Anonymous(this="date_trunc", expressions=args),
            "DATE_PART": lambda args: exp.Anonymous(this="date_part", expressions=args),
        }
        FUNCTION_PARSERS = {
            name: parser for name, parser in Postgres.Parser.FUNCTION_PARSERS.items() if name != "DATE_PART"
        }

        def _parse_function_call(self, *args, **kwargs):
            source_arity = None
            source_name = self._curr.text if self._curr else ""
            if self._next and self._next.token_type == TokenType.L_PAREN:
                depth = 0
                source_arity = 0
                for token in self._tokens[self._index + 1 :]:
                    kind = token.token_type
                    if kind in (TokenType.L_PAREN, TokenType.L_BRACKET):
                        depth += 1
                        if depth > 1 and source_arity == 0:
                            source_arity = 1
                    elif kind in (TokenType.R_PAREN, TokenType.R_BRACKET):
                        depth -= 1
                        if depth == 0:
                            break
                    elif depth == 1:
                        # Aggregate ORDER BY and EXISTS SELECT lists are not args.
                        if kind in (TokenType.ORDER_BY, TokenType.SELECT):
                            break
                        if kind == TokenType.COMMA:
                            source_arity += 1
                        elif source_arity == 0:
                            source_arity = 1
            name = source_name.lower()
            if source_arity is not None:
                if name == "extract" and source_arity > 1:
                    raise SQLValidationError(
                        "Use EXTRACT(field FROM timestamp) or date_part(unit, timestamp).",
                        error_type="function_not_allowed",
                    )
                max_arity = {"date_trunc": 3, "date_part": 2}.get(name)
                if max_arity is not None and source_arity > max_arity:
                    raise SQLValidationError(
                        f"Function '{source_name}' has unsupported extra arguments.",
                        error_type="function_not_allowed",
                    )
            result = super()._parse_function_call(*args, **kwargs)
            function = result
            while isinstance(function, exp.Window | exp.Filter | exp.WithinGroup):
                function = function.this
            if function is not None and source_arity is not None:
                function.meta["scout_source_arity"] = source_arity
                function.meta["scout_source_name"] = source_name
            return result


class SQLValidationError(Exception):
    """
    Raised when SQL validation fails.

    Attributes:
        message: Human-readable error message
        sql: The SQL query that failed validation
        error_type: Category of validation error
    """

    def __init__(
        self,
        message: str,
        sql: str | None = None,
        error_type: str = "validation_error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.sql = sql
        self.error_type = error_type

    def __str__(self) -> str:
        return self.message


@dataclass
class SQLValidator:
    """
    Validates SQL queries for safety and compliance with project rules.

    This validator ensures that:
    1. Only SELECT statements are executed
    2. Only a single statement is present
    3. No dangerous functions are called
    4. Only allowed schemas are accessed

    Attributes:
        schema: The primary database schema to enforce (e.g., "public")
        allowed_schemas: Additional schemas the user can access (e.g., materialized data)
        max_limit: Maximum number of rows to return
        dialect: SQL dialect for parsing (default: postgres)
    """

    schema: str = "public"
    allowed_schemas: list[str] = field(default_factory=list)
    max_limit: int = 500
    dialect: str = "postgres"

    def validate(self, sql: str) -> exp.Expression:
        """
        Validate a SQL query and return the parsed AST.

        Args:
            sql: The SQL query string to validate

        Returns:
            The parsed SQL expression (AST)

        Raises:
            SQLValidationError: If the query fails any validation check
        """
        try:
            statements = sqlglot.parse(
                sql, dialect=_PreservingPostgres if self.dialect == "postgres" else self.dialect
            )
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
            raise SQLValidationError(
                f"SQL parse error: {e}",
                sql=sql,
                error_type="parse_error",
            ) from e

        if not statements:
            raise SQLValidationError(
                "Empty SQL statement",
                sql=sql,
                error_type="empty_statement",
            )

        # Filter out None values (can occur with trailing semicolons)
        valid_statements = [s for s in statements if s is not None]

        if len(valid_statements) == 0:
            raise SQLValidationError(
                "Empty SQL statement",
                sql=sql,
                error_type="empty_statement",
            )

        if len(valid_statements) > 1:
            raise SQLValidationError(
                "Multiple SQL statements are not allowed. Please submit one query at a time.",
                sql=sql,
                error_type="multiple_statements",
            )

        statement = valid_statements[0]

        self._validate_statement_type(statement, sql)
        self._validate_no_dangerous_functions(statement, sql)
        self._validate_cast_types(statement, sql)
        self._validate_argument_preservation(statement, sql)
        # Rejects unqualified system-catalog reads (cross-tenant disclosure) that
        # the schema allowlist below cannot see.
        self._validate_no_system_catalogs(statement, sql)
        self._validate_table_access(statement, sql)

        # Pin ordinary built-ins to pg_catalog so tenant overloads cannot affect
        # function resolution. Special forms use PostgreSQL's dedicated syntax.
        for func in reversed(list(statement.find_all(exp.Func))):
            func_name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
            if isinstance(func, exp.CurrentTime) and func.this is None:
                func.replace(exp.Var(this="CURRENT_TIME"))
                continue
            # SQLGlot's timestamp emitter drops the optional precision.
            if isinstance(func, exp.CurrentTimestamp) and func.this is not None:
                func.replace(exp.Anonymous(this="CURRENT_TIMESTAMP", expressions=[func.this.copy()]))
                continue
            # SQLGlot's regex operator emitter drops regexp_like's third argument.
            if isinstance(func, exp.RegexpLike) and func.args.get("flag") is not None:
                call = exp.Anonymous(
                    this="regexp_like",
                    expressions=[
                        func.this.copy(),
                        func.expression.copy(),
                        func.args["flag"].copy(),
                    ],
                )
                func.replace(exp.Dot(this=exp.to_identifier("pg_catalog"), expression=call))
                continue
            if not isinstance(func, exp.Anonymous) and func_name in SQL_SPECIAL_FORMS:
                continue
            if isinstance(func.parent, exp.Dot) and func.parent.expression is func:
                continue
            func.replace(exp.Dot(this=exp.to_identifier("pg_catalog"), expression=func.copy()))

        return statement

    def _validate_statement_type(self, statement: exp.Expression, sql: str) -> None:
        """Ensure only SELECT statements are allowed.

        Beyond the top-level expression type, this also rejects:

        - ``SELECT ... INTO`` (a table-creating SELECT — the ``into`` arg on a
          ``Select`` node), and
        - data-modifying CTEs (``WITH x AS (INSERT/UPDATE/DELETE ...) SELECT ...``)
          which parse as a top-level ``Select`` but embed DML in a subtree.

        Both pass a naive top-level ``isinstance`` check, so we scan the AST.
        """
        for node_type, message in (
            (exp.Placeholder, "SQL parameters are not supported. Use literal values in the query."),
            (
                exp.Parameter,
                "SQL parameters and unary @ are not supported. Use abs() for absolute values.",
            ),
            (exp.Fetch, "FETCH syntax is not supported. Use LIMIT to bound query results."),
            (exp.Lock, "Row locking is not permitted in read-only queries."),
            (exp.Operator, "Explicit OPERATOR syntax is not supported. Use built-in operators."),
        ):
            if statement.find(node_type) is not None:
                raise SQLValidationError(message, sql=sql, error_type="forbidden_statement")

        if (
            isinstance(statement, exp.Alias)
            and isinstance(statement.this, exp.Column)
            and statement.this.name.upper() == "TABLE"
        ):
            raise SQLValidationError(
                "TABLE syntax is not supported. Use SELECT * FROM the table instead.",
                sql=sql,
                error_type="forbidden_statement",
            )

        # SELECT ... INTO creates a new relation; reject regardless of nesting.
        if statement.args.get("into") is not None or statement.find(exp.Into) is not None:
            raise SQLValidationError(
                "SELECT ... INTO is not allowed. Only read-only SELECT queries are permitted.",
                sql=sql,
                error_type="forbidden_statement",
            )

        # Data-modifying CTEs / subqueries: any embedded INSERT/UPDATE/DELETE/
        # MERGE/etc. is forbidden even if the outer statement is a SELECT.
        forbidden_tuple = tuple(FORBIDDEN_STATEMENT_TYPES)
        for node in statement.find_all(*forbidden_tuple):
            raise SQLValidationError(
                f"{type(node).__name__.upper()} statements are not allowed "
                "(including inside CTEs and subqueries). Only SELECT queries are permitted.",
                sql=sql,
                error_type="forbidden_statement",
            )

        if not isinstance(statement.unnest(), exp.Select):
            # Also allow UNION, INTERSECT, EXCEPT which wrap SELECT statements
            if isinstance(statement.unnest(), exp.Union | exp.Intersect | exp.Except):
                return

            # If not a SELECT and not explicitly forbidden, still reject
            raise SQLValidationError(
                f"Statement type '{type(statement).__name__}' is not allowed. " "Only SELECT queries are permitted.",
                sql=sql,
                error_type="forbidden_statement",
            )

    def _validate_argument_preservation(self, statement: exp.Expression, sql: str) -> None:
        for node in statement.walk():
            source_arity = node.meta.get("scout_source_arity", 0)
            if not source_arity:
                continue
            expected = max(source_arity, _argument_count(node))
            name = node.meta.get("scout_source_name", node.key)
            rendered_node = node
            if isinstance(node, exp.CurrentTimestamp) and node.this is not None:
                rendered_node = exp.Anonymous(this="CURRENT_TIMESTAMP", expressions=[node.this.copy()])
            elif isinstance(node, exp.RegexpLike) and node.args.get("flag") is not None:
                rendered_node = exp.Anonymous(
                    this="regexp_like",
                    expressions=[
                        node.this.copy(),
                        node.expression.copy(),
                        node.args["flag"].copy(),
                    ],
                )
            try:
                reparsed = sqlglot.parse_one(
                    rendered_node.sql(dialect=self.dialect),
                    dialect=_PreservingPostgres if self.dialect == "postgres" else self.dialect,
                )
                retained = max(reparsed.meta.get("scout_source_arity", 0), _argument_count(reparsed))
            except (sqlglot.errors.ParseError, TypeError, ValueError) as error:
                raise SQLValidationError(
                    f"Function '{name}' arguments cannot be represented in supported PostgreSQL syntax.",
                    sql=sql,
                    error_type="function_not_allowed",
                ) from error
            if retained < expected:
                raise SQLValidationError(
                    f"Function '{name}' has unsupported arguments that would be discarded.",
                    sql=sql,
                    error_type="function_not_allowed",
                )

    def _validate_cast_types(self, statement: exp.Expression, sql: str) -> None:
        for target in statement.find_all(exp.DataType):
            if isinstance(target, exp.ObjectIdentifier) or target.this not in ALLOWED_CAST_TYPES:
                raise SQLValidationError(
                    f"Type '{target.sql(dialect='postgres')}' is not supported. "
                    "Use core PostgreSQL text, numeric, boolean, "
                    "date/time, JSON, UUID, binary or array types; custom and OID-alias types "
                    "are not permitted.",
                    sql=sql,
                    error_type="cast_type_not_allowed",
                )

    def _validate_no_system_catalogs(self, statement: exp.Expression, sql: str) -> None:
        """Reject references to PostgreSQL system catalogs.

        Unqualified catalog relations (``pg_class``, ``pg_statio_all_tables``, ...)
        resolve via the implicit ``pg_catalog`` schema and stay readable regardless
        of ``SET ROLE``, leaking cross-tenant metadata. Schema-qualified
        ``pg_catalog.*`` is already blocked by ``_validate_table_access``; this
        catches the unqualified form, which the AST records with no schema and
        which the schema allowlist therefore never inspects.
        """
        for table in statement.find_all(exp.Table):
            if table.db or table.catalog:
                continue
            if table.name.lower().startswith(SYSTEM_CATALOG_PREFIX):
                raise SQLValidationError(
                    f"Access to system catalog '{table.name}' is not permitted. "
                    "Use the describe_table and list_tables tools to inspect schema.",
                    sql=sql,
                    error_type="system_catalog_not_allowed",
                )

    def _validate_no_dangerous_functions(self, statement: exp.Expression, sql: str) -> None:
        """Check for dangerous function calls in the query."""
        for func in statement.find_all(exp.Func):
            func_name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()
            if func_name in DANGEROUS_FUNCTIONS:
                raise SQLValidationError(
                    f"Function '{func_name}' is not allowed for security reasons.",
                    sql=sql,
                    error_type="dangerous_function",
                )

            if isinstance(func, exp.MatchAgainst) and not isinstance(func.args.get("expressions"), list):
                raise SQLValidationError(
                    "match_against() call syntax is not supported. Use the @@ operator for JSONPath matching.",
                    sql=sql,
                    error_type="function_not_allowed",
                )

            parent = func.parent
            qualified = isinstance(parent, exp.Dot) and parent.expression is func
            builtin_schema = not qualified or (
                isinstance(parent.this, exp.Identifier) and parent.this.name == "pg_catalog"
            )
            allowed_name = func_name in ALLOWED_ANALYTICS_FUNCTIONS and not (
                isinstance(func, exp.Anonymous) and func_name in PARSER_ONLY_FUNCTIONS
            )
            if not allowed_name or not builtin_schema:
                raise SQLValidationError(
                    f"Function '{func_name}' is not allowed. Use supported PostgreSQL "
                    "analytics functions; custom and extension functions are not supported.",
                    sql=sql,
                    error_type="function_not_allowed",
                )
            if func_name == "pg_input_is_valid":
                arguments = func.expressions
                if (
                    len(arguments) != 2
                    or not isinstance(arguments[1], exp.Literal)
                    or not arguments[1].is_string
                    or arguments[1].this != "timestamp with time zone"
                ):
                    raise SQLValidationError(
                        "pg_input_is_valid only supports the literal type 'timestamp with time zone'.",
                        sql=sql,
                        error_type="function_not_allowed",
                    )

    def _validate_table_access(self, statement: exp.Expression, sql: str) -> None:
        """Validate that only allowed schemas are accessed."""
        tables_accessed = self._extract_tables(statement)

        for table_info in tables_accessed:
            if catalog := table_info.get("catalog"):
                raise SQLValidationError(
                    f"Database qualifier '{catalog}' is not supported. "
                    "Use a table name qualified only by the workspace schema or public.",
                    sql=sql,
                    error_type="catalog_not_allowed",
                )
            table_schema = table_info.get("schema")

            # Validate schema if specified in the query
            if table_schema:
                # Build list of allowed schemas
                all_allowed_schemas = {"public", self.schema.lower()}
                all_allowed_schemas.update(s.lower() for s in self.allowed_schemas)

                if table_schema.lower() not in all_allowed_schemas:
                    raise SQLValidationError(
                        f"Access to schema '{table_schema}' is not permitted. "
                        f"Allowed schemas: {', '.join(sorted(all_allowed_schemas))}",
                        sql=sql,
                        error_type="schema_not_allowed",
                    )

    def _extract_tables(self, statement: exp.Expression) -> list[dict[str, str]]:
        """
        Extract all table references from a SQL statement.

        Excludes CTE (Common Table Expression) aliases from the result since
        they are not actual database tables.

        Returns:
            List of dicts with 'table' and optional 'schema' keys
        """
        tables: list[dict[str, str]] = []

        for table in statement.find_all(exp.Table):
            table_name = table.name
            if self._is_cte_reference(table):
                continue
            table_info: dict[str, str] = {"table": table_name}
            if table.db:
                table_info["schema"] = table.db
            if table.catalog:
                table_info["catalog"] = table.catalog
            tables.append(table_info)

        return tables

    @staticmethod
    def _is_cte_reference(table: exp.Table) -> bool:
        # Qualified relations always name physical schemas, never a CTE.
        if table.db or table.catalog or not isinstance(table.this, exp.Identifier):
            return False
        table_name = table.name if table.this.quoted else table.name.lower()
        enclosing_cte = None
        ancestor = table.parent
        while ancestor is not None:
            if isinstance(ancestor, exp.CTE):
                enclosing_cte = ancestor
            with_clause = ancestor.args.get("with_")
            if isinstance(with_clause, exp.With):
                for cte in with_clause.expressions:
                    # Nonrecursive definitions see preceding siblings and outer
                    # scopes; recursive WITH makes all sibling names visible.
                    if cte is enclosing_cte and not with_clause.recursive:
                        break
                    alias = cte.args.get("alias")
                    identifier = alias.this if isinstance(alias, exp.TableAlias) else None
                    if isinstance(identifier, exp.Identifier):
                        name = identifier.name if identifier.quoted else identifier.name.lower()
                        if name == table_name:
                            return True
            ancestor = ancestor.parent
        return False

    def inject_limit(self, statement: exp.Expression) -> exp.Expression:
        """
        Add or cap the LIMIT clause on a SELECT statement.

        If no LIMIT exists, adds one with max_limit.
        If a LIMIT exists but exceeds max_limit, caps it at max_limit.
        If a LIMIT exists but cannot be parsed as a literal (e.g., subquery or expression),
        it is replaced with max_limit to prevent unlimited queries.

        Args:
            statement: The parsed SQL expression

        Returns:
            The modified expression with appropriate LIMIT
        """
        if isinstance(statement, exp.Union | exp.Intersect | exp.Except):
            existing_limit = statement.args.get("limit")
            if existing_limit:
                limit_value = self._get_limit_value(existing_limit)
                if limit_value is None:
                    # Non-literal LIMIT (e.g., subquery, expression) - force cap
                    logger.warning(
                        "Non-literal LIMIT expression detected, forcing cap to %d",
                        self.max_limit,
                    )
                    statement.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
                elif limit_value > self.max_limit:
                    statement.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
            else:
                statement.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
            return statement

        if isinstance(statement, exp.Select | exp.Subquery):
            existing_limit = statement.args.get("limit")
            if existing_limit:
                limit_value = self._get_limit_value(existing_limit)
                if limit_value is None:
                    # Non-literal LIMIT (e.g., subquery, expression) - force cap
                    logger.warning(
                        "Non-literal LIMIT expression detected, forcing cap to %d",
                        self.max_limit,
                    )
                    statement.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
                elif limit_value > self.max_limit:
                    statement.set("limit", exp.Limit(expression=exp.Literal.number(self.max_limit)))
            else:
                statement = statement.limit(self.max_limit)

        return statement

    def _get_limit_value(self, limit_expr: exp.Limit) -> int | None:
        """Extract the numeric value from a LIMIT expression."""
        if limit_expr.expression and isinstance(limit_expr.expression, exp.Literal):
            try:
                return int(limit_expr.expression.this)
            except (ValueError, TypeError):
                return None
        return None

    def limit_value(self, statement: exp.Expression) -> int | None:
        """Return the statement's literal LIMIT, or None if absent or non-literal.

        Call before ``inject_limit``, which rewrites the clause in place.
        """
        limit_expr = statement.args.get("limit")
        if limit_expr is None:
            return None
        return self._get_limit_value(limit_expr)

    def get_tables_accessed(self, statement: exp.Expression) -> list[str]:
        """
        Get a list of table names accessed by the query.

        Args:
            statement: The parsed SQL expression

        Returns:
            List of table names, preserving explicit schema/catalog qualifiers
        """
        return [
            ".".join(t[key] for key in ("catalog", "schema", "table") if t.get(key))
            for t in self._extract_tables(statement)
            if t["table"]
        ]


__all__ = [
    "DANGEROUS_FUNCTIONS",
    "FORBIDDEN_STATEMENT_TYPES",
    "SYSTEM_CATALOG_PREFIX",
    "SQLValidationError",
    "SQLValidator",
]
