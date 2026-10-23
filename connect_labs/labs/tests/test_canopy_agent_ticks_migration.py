"""Migration 0037 renames each agent's "Real data" tick in every saved row: ``real_data``
is ``sensitive_data``, and back."""

import importlib

MIGRATION = importlib.import_module("connect_labs.labs.migrations.0037_canopy_agent_ticks_sensitive_data")


def test_real_data_becomes_sensitive_data_and_the_rest_of_the_tick_stays():
    ticks = [
        {"slug": "ace", "offered": True, "real_data": False},
        {"slug": "labsdata", "offered": True, "real_data": True},
    ]
    assert MIGRATION._renamed(ticks, "real_data", "sensitive_data") == [
        {"slug": "ace", "offered": True, "sensitive_data": False},
        {"slug": "labsdata", "offered": True, "sensitive_data": True},
    ]


def test_backwards_is_the_inverse():
    ticks = [{"slug": "labsdata", "offered": True, "sensitive_data": True}]
    back = MIGRATION._renamed(ticks, "sensitive_data", "real_data")
    assert back == [{"slug": "labsdata", "offered": True, "real_data": True}]
    assert MIGRATION._renamed(back, "real_data", "sensitive_data") == ticks


def test_a_row_already_renamed_or_empty_is_unchanged():
    ticks = [{"slug": "ace", "offered": True, "sensitive_data": False}]
    assert MIGRATION._renamed(ticks, "real_data", "sensitive_data") == ticks
    assert MIGRATION._renamed([], "real_data", "sensitive_data") == []
    assert MIGRATION._renamed(None, "real_data", "sensitive_data") == []
