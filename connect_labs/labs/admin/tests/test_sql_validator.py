import pytest

from connect_labs.labs.admin.sql_validator import build_safe_query, validate_where_clause

FILTERS = [
    "form_json->>'status' = 'complete'",
    "entity_id = 'abc123'",
    "form_json->'form'->>'name' LIKE '%test%'",
    "visit_date > '2026-01-01'::date AND status <> 'rejected'",
    "lower(username) = 'a'",
    "form_json ? 'meta'",
    "deliver_unit IS NOT NULL",
]

ESCAPES = [
    # closes the opportunity filter it is wrapped in
    "username = 'a') OR (1=1",
    # reads another table
    "username IN (SELECT username FROM users_user)",
    "username = 'a' OR (SELECT count(*) FROM mcp_mcpaccesstoken) > 0",
    # a function sqlglot does not know
    "username = 'a' AND current_setting('is_superuser') = 'on'",
    "username = 'a' AND pg_sleep(5) IS NULL",
    # a column outside the visit row
    "username = 'a' AND opportunity_id = 5",
    "username = 'a' AND labs_raw_visit_cache.opportunity_id = 5",
]


@pytest.mark.parametrize("clause", FILTERS)
def test_ordinary_filters_pass(clause):
    assert validate_where_clause(clause) == (True, "")
    query, params = build_safe_query(7, clause)
    assert "WHERE opportunity_id = %s AND (" in query and params[0] == 7


@pytest.mark.parametrize("clause", ESCAPES)
def test_clauses_that_reach_past_one_opportunitys_visits_are_refused(clause):
    ok, _ = validate_where_clause(clause)
    assert ok is False
