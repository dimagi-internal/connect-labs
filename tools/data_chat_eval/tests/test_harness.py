"""Unit tests for the harness's pure parts (no browser, no network).

Not collected by CI (it runs ``pytest connect_labs/`` only): run them with
``pytest -p no:django tools/data_chat_eval/tests``.
"""

from pathlib import Path

import pytest

from tools.data_chat_eval import checks, report, suite
from tools.data_chat_eval.driver import TurnCapture, _panel_reply

SUITE = Path(__file__).resolve().parents[1] / "scenarios" / "kmc.yaml"


def turn(reply="", cards=(), tools=(), finished=True, panel_errors=()):
    t = TurnCapture(say="q", reply=reply, cards=list(cards), finished=finished, panel_errors=list(panel_errors))
    t.tool_calls = [{"short": n, "name": n, "is_error": False, "result": ""} for n in tools]
    return t


def test_numbers_skip_dates_and_read_thousands():
    assert checks.numbers_in("46,987 visits on 2026-10-15 at 12:30, 74.9%") == [46987.0, 74.9]


def test_number_check_against_truth_reference():
    truth = {"total": {"n": 46987}}
    ok = checks.run({"number": {"value": "total.n"}}, turn("There are 46,987 visits."), truth)
    assert ok.passed and ok.gate
    bad = checks.run({"number": {"value": "total.n"}}, turn("About 47,000 visits."), truth)
    assert not bad.passed and "46,987" in bad.detail


def test_number_check_scales_a_fraction_to_a_percent():
    truth = {"share": {"ke": 0.0427}}
    spec = {"number": {"value": "share.ke", "scale": 100, "tolerance": 0.1}}
    assert checks.run(spec, turn("KE GHI is 4.3% of approved visits"), truth).passed


def test_text_matches_a_date_as_people_write_it():
    t = turn("It was done on 24 July 2026.")
    assert checks.run({"text": {"any_of": ["v.d"]}}, t, {"v": {"d": "2026-07-24"}}).passed


def test_number_found_in_a_collapsed_cards_cells():
    t = turn("See the card.", cards=[{"text": "Sep 1, 20263,3495,894", "cells": ["Sep 1, 2026", "3,349"]}])
    assert checks.run({"number": 3349}, t, {}).passed


def test_number_found_in_a_card_counts():
    t = turn("See the card.", cards=[{"text": "Visits 35,172", "headers": ["Visits"]}])
    assert checks.run({"number": 35172}, t, {}).passed


def test_gate_and_route_overrides():
    res = checks.run({"text": {"any_of": ["pending"], "gate": False, "route": "here"}}, turn("nope"), {})
    assert not res.passed and not res.gate and res.route == "here"


def test_card_check_flags_raw_headers_and_missing_chart():
    raw = turn(cards=[{"headers": ["visit_count", "Status"], "svg": 1}])
    assert "raw column names" in checks.run({"card": {"min": 1}}, raw, {}).detail
    labelled = turn(cards=[{"headers": ["Distinct babies (approved visits)", "Visits"]}])
    assert checks.run({"card": {"min": 1}}, labelled, {}).passed
    flat = turn(cards=[{"headers": ["Visits"], "svg": 0, "canvas": 0}])
    assert not checks.run({"card": {"chart": True}}, flat, {}).passed
    tabbed = turn(cards=[{"headers": ["Month"], "chart_on_click": True, "active_tab": "Table"}])
    assert "opens on its Table tab" in checks.run({"card": {"chart": True}}, tabbed, {}).detail
    assert not checks.run({"card": {"min": 1}}, turn(), {}).passed


def test_no_rows_repeated_only_matters_with_a_card():
    table = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    assert checks.run({"no_rows_repeated": True}, turn(table), {}).passed
    assert not checks.run({"no_rows_repeated": True}, turn(table, cards=[{"text": ""}]), {}).passed


def test_tool_check_reads_canopys_record():
    assert checks.run({"tool": {"any_of": ["data_query"]}}, turn(tools=["ToolSearch", "data_query"]), {}).passed
    assert not checks.run({"tool": {"any_of": ["data_query"]}}, turn(tools=["workflow_run_indicators"]), {}).passed


def test_no_errors_fails_an_unfinished_turn():
    assert not checks.run({"no_errors": True}, turn(finished=False), {}).passed


def test_render_fills_placeholders_from_truth():
    assert suite.render("Open visit {visit.visit_id}", {"visit": {"visit_id": "85"}}) == "Open visit 85"
    assert suite.render("{nope.x}", {}) == "{nope.x}"


def test_suite_loads_and_every_check_kind_is_known():
    scenarios = suite.load(SUITE)
    assert {s.page.surface for s in scenarios} == {"workflow_run", "data_page"}
    for s in scenarios:
        for t in s.turns:
            for c in t.checks:
                kinds = [k for k in c if k != "gate"]
                assert len(kinds) == 1 and kinds[0] in checks.KINDS, (s.id, c)


def test_duplicate_ids_are_refused(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "pages: {p: {data: {opportunities: [1]}}}\n"
        "scenarios:\n- {id: a, page: p, turns: [{say: x}]}\n- {id: a, page: p, turns: [{say: y}]}\n"
    )
    with pytest.raises(suite.SuiteError):
        suite.load(p)


def test_findings_route_blockers_first():
    entries = [
        {
            "id": "a",
            "checks": [{"turn": 1, "name": "n", "passed": False, "gate": True, "detail": "d", "route": "r"}],
            "turns": [{"reply": "x", "screenshots": {"panel": "turn1-panel.png"}}],
        },
        {
            "id": "b",
            "blocked": "a sensitive conversation needs a canopy account with a vault key",
            "checks": [],
            "turns": [],
        },
    ]
    found = report.findings(entries)
    assert [f["severity"] for f in found] == ["blocker", "fail"]
    assert "vault key" in found[0]["route"]
    assert found[1]["evidence"] == "a/turn1-panel.png"


def test_panel_reply_fallback():
    panel = "ACE\nNew chat\nq\n\nThe answer is 5.\n\nattach\nsend"
    assert _panel_reply(panel, "q") == "The answer is 5."


def test_a_space_whose_chat_is_blocked_reports_blocked_with_one_blocker():
    entry = {
        "id": "s",
        "surface": "data_space",
        "blocked_agent": "a sensitive conversation needs a canopy account with a vault key",
        "checks": [
            {"turn": 1, "name": "create", "passed": True, "gate": True, "detail": "", "route": ""},
            {"turn": 6, "name": "agent", "passed": False, "gate": False, "detail": "BLOCKED", "route": "r"},
        ],
        "turns": [],
    }
    assert report.status(entry) == "BLOCKED"
    assert [f["severity"] for f in report.findings([entry])] == ["blocker", "advisory"]
