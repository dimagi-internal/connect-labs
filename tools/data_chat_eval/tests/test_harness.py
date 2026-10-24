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


def test_refuses_and_allow_refused():
    from tools.data_chat_eval import checks as c

    said = turn("I can't add opportunity 1: it is not part of this programme, and I don't have access to it.")
    assert c.run({"refuses": True}, said, {}).passed
    assert not c.run({"refuses": True}, turn("The combined total is 48,000 visits."), {}).passed
    refused = turn("No.", tools=["data_query"])
    refused.tool_calls[0].update(is_error=True, result='{"code": "PERMISSION_DENIED", "reason": "not_held"}')
    assert not c.run({"no_errors": True}, refused, {}).passed
    assert c.run({"no_errors": {"allow_refused": True}}, refused, {}).passed
    broken = turn("No.", tools=["data_query"])
    broken.tool_calls[0].update(is_error=True, result="UPSTREAM_ERROR: timed out")
    assert not c.run({"no_errors": {"allow_refused": True}}, broken, {}).passed


def test_containment_reads_a_typed_refusal_and_spots_leaks():
    from tools.data_chat_eval import containment as ct

    code, message = ct._typed(
        "Unknown table 'users_user'.\n"
        '{"code": "INVALID_SCHEMA", "error": "table_not_allowed", "message": "Unknown table \'users_user\'."}'
    )
    assert code == "table_not_allowed" and message == "Unknown table 'users_user'."
    assert ct._typed('you do not have access to opportunity 1\n{"code": "PERMISSION_DENIED", "reason": "not_held"}')[
        0
    ] == ("not_held")
    # Postgres talking, or an internal relation the caller never named, is a leak.
    assert ct.leaks("Postgres rejected the query: permission denied for table users_user")
    assert ct.leaks("relation labs_raw_visit_cache is not visible", sql="select 1")
    assert not ct.leaks("'public.labs_raw_visit_cache' is qualified.", sql="select * from public.labs_raw_visit_cache")


def test_the_suite_declares_its_containment_probes():
    import yaml

    from tools.data_chat_eval import containment as ct

    (probe,) = ct.load(yaml.safe_load(SUITE.read_text()))
    assert probe.program_id == 10121 and probe.workflow["id"] == 9027 and probe.not_held_opportunity
    # Every probe expects only codes the harness knows to be typed refusals.
    assert all(expect <= ct.KNOWN_CODES for _l, _s, expect in ct.REFUSED_SQL)


def test_reads_none_of_flags_a_successful_read_outside_the_page():
    from tools.data_chat_eval import checks as c

    t = turn("No.", tools=["data_query", "data_query"])
    t.tool_calls[0].update(input={"opportunity_ids": [10061], "sql": "select 1"})
    t.tool_calls[1].update(input={"opportunity_ids": [1]}, is_error=True, result="not_held")
    spec = {"reads_none_of": {"opportunities": [10061, 1]}}
    res = c.run(spec, t, {})
    assert not res.passed and "10061" in res.detail and "[1]" not in res.detail
    t.tool_calls[0].update(input={"program_ids": [10121]})
    assert c.run(spec, t, {}).passed


def test_the_dropout_reply_states_its_definition():
    from tools.data_chat_eval import checks as c

    said = turn("The closest one is Loss to follow-up by day 28: 22.1%. What it counts: babies ...")
    assert c.run({"asks_or_states": True}, said, {}).passed
