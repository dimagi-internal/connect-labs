"""A path segment that picks one item out of a JSON list by a key's value.

Connect's user_data export carries each worker's visit limits as a LIST, one item per
payment unit, in no fixed order:

    "claim_limits": [{"max_visits": 1691, "payment_unit": 994, ...},
                     {"max_visits": 104,  "payment_unit": 995, ...}]

`claim_limits.0.max_visits` read null (`->'0'` names a key, not an index), and even a
real index would be wrong for the workers whose list starts with the other unit. The
segment `claim_limits[payment_unit=994]` reads the item whose payment_unit is 994,
the same way in the SQL builder and the Python extractor.
"""

from __future__ import annotations

import json

import pytest

from connect_labs.labs.analysis.backends.sql.query_builder import _jsonb_path_to_sql, _paths_to_coalesce_sql
from connect_labs.labs.analysis.utils import extract_json_path, parse_list_match

LIMITS = [
    {"max_visits": 1691, "payment_unit": 994, "payment_unit_id": "e0ec"},
    {"max_visits": 104, "payment_unit": 995, "payment_unit_id": "dc37"},
]
PATH = "user_data.claim_limits[payment_unit=994].max_visits"


def _record(limits):
    return {"user_data": {"username": "ada", "claim_limits": limits}}


# ── Python extractor ───────────────────────────────────────────────────────────


def test_python_reads_the_matching_item_whatever_the_order():
    assert extract_json_path(_record(LIMITS), PATH) == 1691
    assert extract_json_path(_record(list(reversed(LIMITS))), PATH) == 1691
    assert extract_json_path(_record(LIMITS), "user_data.claim_limits[payment_unit=995].max_visits") == 104


def test_python_matches_a_number_by_its_digits_and_a_string_by_its_text():
    as_text = [{"max_visits": 7, "payment_unit": "994"}]
    assert extract_json_path(_record(as_text), PATH) == 7
    words = [{"max_visits": 3, "kind": "bonus"}]
    assert extract_json_path(_record(words), "user_data.claim_limits[kind=bonus].max_visits") == 3


@pytest.mark.parametrize("limits", [[], [{"max_visits": 9, "payment_unit": 1}], "not a list", None])
def test_python_reads_none_when_nothing_matches(limits):
    assert extract_json_path(_record(limits), PATH) is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "items, path",
    [
        ([{"v": 1, "k": 994.0}], "x.l[k=994].v"),
        ([{"v": 1, "k": 1000}], "x.l[k=1e3].v"),
        ([{"v": 1, "k": True}], "x.l[k=true].v"),
        ([{"v": 1, "k": "0994"}], "x.l[k=994].v"),
        ([{"v": 1, "k": "abc"}], "x.l[k=abc].v"),
    ],
)
def test_python_and_postgres_match_the_same_items(items, path):
    record = {"x": {"l": items}}
    sql_value = _run(_jsonb_path_to_sql(path), record)
    py_value = extract_json_path(record, path)
    assert (sql_value is None) == (py_value is None), (path, sql_value, py_value)


def test_python_plain_paths_are_unchanged():
    data = {"form": {"case": {"@case_id": "x"}}}
    assert extract_json_path(data, "form.case.@case_id") == "x"


# ── what counts as a list-match segment ───────────────────────────────────────


@pytest.mark.parametrize(
    "segment",
    [
        "claim_limits[payment_unit=994'];--]",
        "claim_limits[payment unit=994]",
        "claim_limits[payment_unit=99.4]",
        'claim_limits[payment_unit="994"]',
        "claim_limits[=994]",
        "claim_limits[payment_unit=994]x",
    ],
)
def test_anything_but_the_exact_form_is_an_ordinary_key(segment):
    assert parse_list_match(segment) is None


# ── SQL builder ────────────────────────────────────────────────────────────────


def test_a_plain_path_compiles_byte_for_byte_as_before():
    assert _jsonb_path_to_sql("form.case.update.muac_cm") == "form_json->'form'->'case'->'update'->>'muac_cm'"
    assert _jsonb_path_to_sql("form.it's", "rv.form_json") == "rv.form_json->'form'->>'it''s'"


def test_a_list_match_path_compiles_to_one_jsonpath_query():
    sql = _jsonb_path_to_sql(PATH)
    assert sql == (
        "jsonb_path_query_first(form_json, "
        '\'$."user_data"."claim_limits"[*] ? (@."payment_unit" == 994 || @."payment_unit" == "994")'
        ".\"max_visits\"') #>> '{}'"
    )


def test_a_hostile_segment_stays_an_escaped_plain_key():
    sql = _jsonb_path_to_sql("user_data.claim_limits[payment_unit=994'];--]")
    assert "jsonb_path_query_first" not in sql
    assert sql == "form_json->'user_data'->>'claim_limits[payment_unit=994''];--]'"


def _run(expr, record):
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(f"SELECT {expr} FROM (SELECT %s::jsonb AS form_json) t", [json.dumps(record)])
        return cur.fetchone()[0]


@pytest.mark.django_db
def test_postgres_reads_the_matching_item_whatever_the_order():
    assert _run(_jsonb_path_to_sql(PATH), _record(LIMITS)) == "1691"
    assert _run(_jsonb_path_to_sql(PATH), _record(list(reversed(LIMITS)))) == "1691"
    assert _run(_jsonb_path_to_sql(PATH.replace("994", "995")), _record(LIMITS)) == "104"


@pytest.mark.django_db
def test_postgres_agrees_with_python_on_strings_and_misses():
    as_text = [{"max_visits": 7, "payment_unit": "994"}]
    assert _run(_jsonb_path_to_sql(PATH), _record(as_text)) == "7"
    assert _run(_jsonb_path_to_sql(PATH), _record([])) is None
    assert _run(_jsonb_path_to_sql(PATH), _record("not a list")) is None


@pytest.mark.django_db
def test_a_list_match_path_works_inside_a_coalesce():
    expr = _paths_to_coalesce_sql(["user_data.missing", PATH])
    assert _run(expr, _record(LIMITS)) == "1691"
