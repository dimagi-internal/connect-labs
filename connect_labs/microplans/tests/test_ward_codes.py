"""Tests for the precomputed nationwide ward-code lookup (core.ward_codes)."""

from __future__ import annotations

import json

from connect_labs.microplans.core import ward_codes


def _set_index(monkeypatch, rows):
    index = {
        (ward_codes._normalize(r["state"]), ward_codes._normalize(r["lga"]), ward_codes._normalize(r["ward"])): r[
            "code"
        ]
        for r in rows
    }
    monkeypatch.setattr(ward_codes, "_index", index)


def test_exact_match_hit(monkeypatch):
    _set_index(monkeypatch, [{"state": "Kano", "lga": "Tofa", "ward": "Doka", "code": "DOKTO"}])
    assert ward_codes.lookup_ward_code("Kano", "Tofa", "Doka") == "DOKTO"


def test_case_and_whitespace_insensitive(monkeypatch):
    _set_index(monkeypatch, [{"state": "Kano", "lga": "Tofa", "ward": "Doka", "code": "DOKTO"}])
    assert ward_codes.lookup_ward_code("  kano ", " TOFA", "doka ") == "DOKTO"


def test_trailing_parenthetical_annotation_stripped(monkeypatch):
    # Real-world case: a local annotation ("non-RCT") on the recorded ward
    # name that GRID3's canonical spelling doesn't carry.
    _set_index(monkeypatch, [{"state": "Kano", "lga": "Madobi", "ward": "Galinja", "code": "GALI"}])
    assert ward_codes.lookup_ward_code("Kano", "Madobi", "Galinja (non-RCT)") == "GALI"


def test_miss_returns_none(monkeypatch):
    _set_index(monkeypatch, [{"state": "Kano", "lga": "Tofa", "ward": "Doka", "code": "DOKTO"}])
    assert ward_codes.lookup_ward_code("Kano", "Tofa", "Nonexistent Ward") is None
    # Same ward name, wrong LGA — must not match (ward name alone isn't a
    # reliable identity; "Doka" alone names 5 different real wards nationally).
    assert ward_codes.lookup_ward_code("Kano", "Some Other LGA", "Doka") is None


def test_empty_or_missing_ward_short_circuits():
    # Must return None without touching the index at all — no state/lga
    # passed here should ever risk an accidental match.
    assert ward_codes.lookup_ward_code("Kano", "Tofa", "") is None
    assert ward_codes.lookup_ward_code("Kano", "Tofa", None) is None


def test_real_table_loads_and_has_no_duplicate_codes():
    # Exercises the ACTUAL packaged data/ward_codes.json (not mocked) — a
    # regression guard against the table ever being regenerated with a bug
    # that reintroduces collisions.
    with open(ward_codes._TABLE_PATH) as f:
        rows = json.load(f)
    assert len(rows) > 5000  # sanity: the real GRID3 table, not an empty stub
    codes = [r["code"] for r in rows]
    assert len(codes) == len(set(codes))


def test_real_table_known_entries():
    # Confirmed by hand against the actual production bug this table exists
    # to prevent (see grouping.py's _disambiguated_ward_prefixes docstring).
    assert ward_codes.lookup_ward_code("Kano", "Rimin Gado", "Doka Dawa") == "DOKRI"
    assert ward_codes.lookup_ward_code("Kano", "Madobi", "Galinja") is not None
    assert ward_codes.lookup_ward_code("Kano", "Nonexistent LGA", "Doka") is None
