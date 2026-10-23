"""Migration 0034 renames canopy's runner flag in every saved row: ``zdr`` is ``sensitive_data``."""

import importlib

MIGRATION = importlib.import_module("connect_labs.labs.migrations.0034_canopy_runner_requirement_sensitive_data")


def test_zdr_becomes_sensitive_data_and_other_flags_keep_their_place():
    assert MIGRATION._swap(["other", "zdr"], "zdr", "sensitive_data") == ["other", "sensitive_data"]


def test_a_row_naming_both_keeps_one():
    assert MIGRATION._swap(["zdr", "sensitive_data"], "zdr", "sensitive_data") == ["sensitive_data"]


def test_a_row_with_no_requirements_stays_empty():
    assert MIGRATION._swap([], "zdr", "sensitive_data") == []
    assert MIGRATION._swap(None, "zdr", "sensitive_data") == []
