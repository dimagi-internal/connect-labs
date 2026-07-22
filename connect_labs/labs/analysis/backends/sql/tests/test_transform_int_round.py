"""Regression tests for pipeline int/round transforms (issue #958).

`transform: "int"` used to emit a ::FLOAT cast: the registry lambda
`int(float(x))` contains the substring `float(x)`, so the source-pattern
matcher classified it as `simple_float` before reaching the integer branch —
so a jittered-float ordinal was never integerized. These tests pin the fix
and the new `round`/`round_int` transforms, plus fail-loud on unknown names.
"""

import pytest

from connect_labs.labs.analysis.backends.sql.query_builder import _transform_to_sql


def _config(transform: str):
    """Resolve a one-field schema through the real transform registry."""
    from connect_labs.workflow.data_access import PipelineDataAccess

    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    schema = {
        "data_source": {"type": "connect_csv"},
        "grouping_key": "username",
        "terminal_stage": "visit_level",
        "fields": [
            {"name": "followup_number", "path": "form.followup_number", "aggregation": "first", "transform": transform}
        ],
    }
    return access._schema_to_config(schema, definition_id=99001)


def _field_sql(transform: str) -> str:
    config = _config(transform)
    field = config.fields[0]
    return _transform_to_sql(field, "col")


def test_int_transform_casts_to_integer_not_float():
    """`int` must integerize a decimal string — the #958 bug emitted ::FLOAT."""
    sql = _field_sql("int")
    assert "::INTEGER" in sql
    assert "TRUNC(" in sql
    assert "::FLOAT" not in sql


def test_round_transform_rounds_to_integer():
    sql = _field_sql("round")
    assert "ROUND(" in sql
    assert "::INTEGER" in sql
    assert "::FLOAT" not in sql


def test_round_int_alias_matches_round():
    assert _field_sql("round_int") == _field_sql("round")


def test_float_transform_still_casts_to_float():
    """Guard: the fix must not regress the plain `float` transform."""
    sql = _field_sql("float")
    assert "::FLOAT" in sql
    assert "::INTEGER" not in sql


def test_decimal_regex_accepts_jittered_ordinal():
    """int/round SQL must accept a decimal like 2.897 (not the integer-only regex)."""
    for t in ("int", "round"):
        sql = _field_sql(t)
        assert r"'^-?[0-9]*\.?[0-9]+$'" in sql, t


def test_unknown_transform_fails_loud():
    """A mis-named transform must raise, not silently pass values through (#958)."""
    with pytest.raises(ValueError, match="Unknown pipeline transform"):
        _config("integerr")


def test_date_transform_is_a_registered_noop():
    """`date` is a legitimate no-op transform (value None) — must not raise."""
    config = _config("date")
    assert config.fields[0].transform is None
