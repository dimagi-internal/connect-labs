"""Regression: cancelling an audit-creation task must delete the sessions the
task actually created.

The creation task records created sessions under the "sessions" key as a list
of {"id", ...} dicts (tasks.py). The cancel cleanup used to read a "session_ids"
key that the task never writes, so it deleted nothing and orphaned the session
(surfacing as a stray In-Progress session on the workflow run).
"""
from connect_labs.audit.data_access import _created_session_ids


def test_reads_sessions_key_list_of_dicts():
    info = {"success": True, "sessions": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]}
    assert _created_session_ids(info) == [1, 2]


def test_reads_legacy_session_ids_key():
    assert _created_session_ids({"session_ids": [3]}) == [3]


def test_reads_nested_result_dict():
    assert _created_session_ids({"result": {"sessions": [{"id": 4}]}}) == [4]


def test_empty_or_non_dict():
    assert _created_session_ids({}) == []
    assert _created_session_ids(None) == []


def test_dedupes_across_shapes():
    info = {"sessions": [{"id": 1}], "session_ids": [1]}
    assert _created_session_ids(info) == [1]
