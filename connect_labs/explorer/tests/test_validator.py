"""The explorer's SQL rules: Scout's checks plus the relation allow-list."""

import pytest

from connect_labs.explorer.sql_validator import SQLValidationError
from connect_labs.explorer.validator import HARD_MAX_ROWS, validator


def _ok(sql, max_rows=500):
    check = validator(max_rows)
    return check.inject_limit(check.validate(sql)).sql(dialect="postgres")


def _refused(sql):
    with pytest.raises(SQLValidationError) as e:
        validator().validate(sql)
    return e.value


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM visits",
        "SELECT llo, COUNT(DISTINCT entity_id) FROM visits GROUP BY llo",
        "WITH b AS (SELECT form_json #>> '{form,place}' AS place FROM visits) "
        "SELECT place, COUNT(*) FROM b GROUP BY 1",
        "SELECT v.visit_id, item ->> 'w' FROM visits v "
        "CROSS JOIN LATERAL jsonb_array_elements(v.form_json -> 'r') AS item",
        "SELECT date_trunc('month', visit_date) m, COUNT(*) FROM visits WHERE username LIKE '%a%' GROUP BY 1",
        "SELECT a FROM (SELECT 1 AS a FROM visits) s UNION ALL SELECT 2",
        'SELECT * FROM "visits"',
    ],
)
def test_allowed(sql):
    assert "LIMIT" in _ok(sql).upper()


@pytest.mark.parametrize(
    "sql,error_type",
    [
        ("SELECT * FROM labs_raw_visit_cache", "table_not_allowed"),
        ("SELECT * FROM public.labs_raw_visit_cache", "schema_not_allowed"),
        ("SELECT * FROM public.visits", "schema_not_allowed"),
        ("SELECT * FROM users_user", "table_not_allowed"),
        ("SELECT * FROM visits v JOIN auth_group g ON true", "table_not_allowed"),
        ("SELECT * FROM visits WHERE visit_id IN (SELECT token FROM labs_token)", "table_not_allowed"),
    ],
)
def test_refuses_any_other_relation(sql, error_type):
    assert _refused(sql).error_type == error_type


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pg_class",
        "SELECT * FROM information_schema.tables",
        "SELECT pg_sleep(10) FROM visits",
        "SELECT pg_read_file('/etc/passwd')",
        "DELETE FROM visits",
        "INSERT INTO visits VALUES (1)",
        "SELECT 1 FROM visits; SELECT 2 FROM visits",
        "WITH d AS (DELETE FROM visits RETURNING *) SELECT * FROM d",
        "SELECT set_config('search_path', 'public', true)",
        "SELECT * INTO copy_of_visits FROM visits",
    ],
)
def test_refuses_scout_categories(sql):
    _refused(sql)


def test_a_cte_named_like_a_real_table_cannot_reach_it():
    # The CTE shadows the name, so the reference is to the CTE -- whose own body
    # must still pass the allow-list.
    _refused("WITH x AS (SELECT * FROM labs_raw_visit_cache) SELECT * FROM x")
    assert _ok("WITH labs_raw_visit_cache AS (SELECT * FROM visits) SELECT * FROM labs_raw_visit_cache")


def test_limit_injected_and_capped():
    assert "LIMIT 25" in _ok("SELECT * FROM visits", max_rows=25)
    assert "LIMIT 25" in _ok("SELECT * FROM visits LIMIT 1000", max_rows=25)
    assert "LIMIT 10" in _ok("SELECT * FROM visits LIMIT 10", max_rows=25)
    assert f"LIMIT {HARD_MAX_ROWS}" in _ok("SELECT * FROM visits", max_rows=10**9)
