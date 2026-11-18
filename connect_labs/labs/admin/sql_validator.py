"""
SQL validation for safe execution of user-provided WHERE clauses.

Ensures user SQL is read-only and only operates on allowed columns:
- form_json: Full form data (JSONB)
- entity_id: Entity identifier
- username: User who submitted the visit
- visit_date: Date of the visit
- status: Visit validation status
- deliver_unit: Delivery unit ID

Additional Safety:
- All queries execute within a READ ONLY transaction (enforced by PostgreSQL)
- Query structure is fixed (SELECT only with specific columns)
- Results are limited to prevent data dumps
- Dangerous SQL keywords are blocked
"""

import logging
import re

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

#: The visit-cache columns a WHERE clause may filter on.
ALLOWED_COLUMNS = ["form_json", "entity_id", "username", "visit_date", "status", "deliver_unit"]

# Dangerous SQL keywords that should not appear in WHERE clauses
BLACKLISTED_KEYWORDS = [
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "REPLACE",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "REVOKE",
    "UNION",
    "IMPORT",
    "LOAD",
    "COPY",
    "MERGE",
    "CALL",
    "DO",
    "HANDLER",
    "PREPARE",
    "DEALLOCATE",
]


def validate_where_clause(clause: str) -> tuple[bool, str]:
    """
    Validate a user-provided SQL WHERE clause for safety.

    Args:
        clause: The WHERE clause string (without the WHERE keyword)

    Returns:
        Tuple of (is_valid, error_message)
        - is_valid: True if the clause is safe to execute
        - error_message: Empty string if valid, otherwise describes the issue

    Examples:
        >>> validate_where_clause("form_json->>'status' = 'complete'")
        (True, "")

        >>> validate_where_clause("entity_id = 'abc123'")
        (True, "")

        >>> validate_where_clause("form_json->'form'->>'name' LIKE '%test%'")
        (True, "")

        >>> validate_where_clause("DROP TABLE users")
        (False, "Clause contains blacklisted keyword: DROP")

        >>> validate_where_clause("username = 'admin' OR 1=1; DROP TABLE users")
        (False, "Clause contains blacklisted keyword: DROP")
    """
    if not clause or not clause.strip():
        return False, "WHERE clause cannot be empty"

    clause_upper = clause.upper()

    # Check for blacklisted keywords
    for keyword in BLACKLISTED_KEYWORDS:
        # Use word boundaries to avoid false positives (e.g., "UPDATE" in "updated_at")
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, clause_upper):
            return False, f"Clause contains blacklisted keyword: {keyword}"

    # Check for semicolons (could indicate multiple statements)
    if ";" in clause:
        return False, "Semicolons are not allowed in WHERE clauses"

    # Check for comments (could be used to bypass validation)
    if "--" in clause or "/*" in clause or "*/" in clause:
        return False, "SQL comments are not allowed"

    # Verify it references allowed columns
    # These are the columns available in the query and safe to filter on
    allowed_columns = ALLOWED_COLUMNS
    clause_lower = clause.lower()
    has_allowed_column = any(col in clause_lower for col in allowed_columns)

    if not has_allowed_column:
        return False, f"WHERE clause must reference one of the allowed columns: {', '.join(allowed_columns)}"

    # Basic length check
    if len(clause) > 1000:
        return False, "WHERE clause is too long (max 1000 characters)"

    # Parse it. A keyword list cannot see structure: "1=1) OR (1=1" closed the
    # opportunity filter around it, and a subquery read any table in labs' database.
    try:
        _parse_where_clause(clause, allowed_columns)
    except ValueError as e:
        return False, str(e)

    logger.info(f"Validated WHERE clause: {clause[:100]}...")
    return True, ""


#: Node types a filter on one visit row never needs: anything that reads another
#: table, and any function sqlglot does not know (pg_sleep, current_setting, dblink...).
_FORBIDDEN_NODES = (exp.Query, exp.Subquery, exp.Anonymous, exp.Command, exp.Block)


def _parse_where_clause(clause: str, allowed_columns: list[str]) -> exp.Expression:
    """Parse ``clause`` as ONE Postgres boolean expression over ``allowed_columns``.

    Raises ValueError saying why it is not one.
    """
    try:
        tree = sqlglot.parse_one(clause, dialect="postgres")
    except sqlglot.errors.ParseError as e:
        raise ValueError("WHERE clause is not a single valid expression") from e
    if tree is None or isinstance(tree, (exp.Query, exp.Block, exp.Command)):
        raise ValueError("WHERE clause is not a single valid expression")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise ValueError("Subqueries and unknown functions are not allowed in WHERE clauses")
        if isinstance(node, exp.Column) and (node.table or node.name.lower() not in allowed_columns):
            raise ValueError(f"Column {node.sql()!r} is not one of: {', '.join(allowed_columns)}")
    return tree


def _canonical(where_clause: str) -> str:
    """The clause as sqlglot renders what it parsed, so the SQL that runs is the SQL
    that was checked (not text Postgres might read differently, e.g. dollar quoting).
    """
    return _parse_where_clause(where_clause, ALLOWED_COLUMNS).sql(dialect="postgres")


def build_safe_query(opportunity_id: int, where_clause: str, limit: int = 1000) -> tuple[str, list]:
    """
    Build a safe SQL query with the user's WHERE clause.

    Args:
        opportunity_id: The opportunity ID to filter by
        where_clause: The validated user WHERE clause
        limit: Maximum number of results to return

    Returns:
        Tuple of (query_string, params) ready for cursor.execute()

    The query will always:
    - Be read-only (SELECT only)
    - Filter by opportunity_id
    - Limit results to prevent massive downloads
    - Use parameterized queries for opportunity_id
    """
    # Note: We can't parameterize the WHERE clause because it contains operators and column names
    # But we've validated it doesn't contain dangerous keywords
    query = f"""
        SELECT
            visit_id,
            username,
            visit_date,
            status,
            deliver_unit,
            entity_id,
            form_json
        FROM labs_raw_visit_cache
        WHERE opportunity_id = %s AND ({_canonical(where_clause)})
        ORDER BY visit_id DESC
        LIMIT {limit}
    """

    params = [opportunity_id]
    return query, params
