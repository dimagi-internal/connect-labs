"""What a run shows an agent: the opt-in, worker keys, and the grading readers."""

import pytest

from connect_labs.workflow.agent_sharing import (
    GradingError,
    graded_payload,
    indicator_catalog,
    scope_rows,
    select_workers,
    shares_with_agent,
    split_worker_key,
)
from connect_labs.workflow.templates import TEMPLATES


def test_sharing_is_off_unless_a_workflow_turns_it_on():
    opted = sorted(k for k, t in TEMPLATES.items() if ((t.get("definition") or {}).get("config") or {}).get("agent"))
    assert opted == []
    assert (
        shares_with_agent({"config": {"templateType": "indicator_programme_report"}}, "indicator_programme_report")
        is False
    )
    assert shares_with_agent({"config": {"agent": {"share": True}}}) is True


def test_a_template_default_is_inherited_and_an_instance_can_opt_out(monkeypatch):
    monkeypatch.setitem(TEMPLATES, "agent_demo", {"definition": {"config": {"agent": {"share": True}}}})
    assert shares_with_agent({"config": {}}, "agent_demo") is True
    assert shares_with_agent({"config": {"agent": {"share": False}}}, "agent_demo") is False


def test_worker_keys_split_on_the_first_separator():
    assert split_worker_key("10::a::b") == (10, "a::b")
    with pytest.raises(ValueError):
        split_worker_key("asha")
    with pytest.raises(ValueError):
        split_worker_key("x::asha")


# ---------------------------------------------------------------------------
# Reading the grading
# ---------------------------------------------------------------------------

PAYLOAD = {
    "cMeasures": [
        {"indicator": "wt", "label": "Weighed", "direction": "higher", "bands": [80, 60], "unit": "%", "target": 80},
        {"indicator": "fu", "label": "Followed up", "direction": "higher", "bands": [90, 70], "unit": "%"},
    ],
    "programInd": {"wt": {"band": "yellow", "value": 0.7, "n": 300}},
    "byLLO": [{"llo": "Org A", "ind": {"wt": {"band": "red", "value": 0.5, "n": 40}}, "rows": [], "opps": [{}]}],
    "byOpp": [],
    "byFLW": [
        {
            "key": "10::asha",
            "opp": 10,
            "username": "asha",
            "llo": "Org A",
            "n": 30,
            "reds": 2,
            "yellows": 0,
            "rows": [1, 2, 3],
            "ind": {"wt": {"band": "red", "value": 0.4, "n": 30}, "fu": {"band": "red", "value": 0.5, "n": 30}},
        },
        {
            "key": "10::binta",
            "opp": 10,
            "username": "binta",
            "llo": "Org A",
            "n": 25,
            "reds": 1,
            "yellows": 0,
            "rows": [],
            "ind": {"wt": {"band": "green", "value": 0.9, "n": 25}, "fu": {"band": "red", "value": 0.6, "n": 25}},
        },
        {
            "key": "11::chidi",
            "opp": 11,
            "username": "chidi",
            "llo": "Org B",
            "n": 12,
            "reds": 0,
            "yellows": 0,
            "rows": [],
            "ind": {"wt": {"band": "insufficient", "value": None, "n": 12}},
        },
    ],
}


def test_the_graded_payload_is_found_wherever_the_spec_stored_it():
    wrapped = {"state": {"report": PAYLOAD}, "pipelines": {}, "workers": []}
    assert graded_payload(wrapped) is PAYLOAD
    assert graded_payload(PAYLOAD) is PAYLOAD
    assert graded_payload({"state": {"worker_states": {}}}) is None


def test_everyone_with_a_red_metric_is_every_worker_with_any_red_cell():
    got = select_workers(PAYLOAD, band="red")
    assert [w["key"] for w in got["workers"]] == ["10::asha", "10::binta"]
    assert got["workers"][0]["matched"] == ["fu", "wt"]
    assert got["workers"][1]["matched"] == ["fu"]
    # A cell with no judgement ("insufficient") is not red.
    assert "11::chidi" not in {w["key"] for w in got["workers"]}


def test_red_on_one_indicator_narrows_the_match_and_the_cells():
    got = select_workers(PAYLOAD, band="red", indicators=["wt"])
    assert [w["key"] for w in got["workers"]] == ["10::asha"]
    assert set(got["workers"][0]["indicators"]) == {"wt"}


def test_worker_rows_carry_no_case_indexes():
    got = select_workers(PAYLOAD)
    assert all("rows" not in w for w in got["workers"])
    assert got["total"] == 3


def test_an_unknown_band_is_refused():
    with pytest.raises(GradingError):
        select_workers(PAYLOAD, band="crimson")


def test_organisation_rows_drop_their_nested_opportunities():
    rows = scope_rows(PAYLOAD, "organisation")
    assert rows == [{"llo": "Org A", "indicators": {"wt": {"band": "red", "value": 0.5, "n": 40}}}]
    assert scope_rows(PAYLOAD, "programme")[0]["indicators"]["wt"]["band"] == "yellow"


def test_the_catalog_describes_the_thresholds_the_run_was_graded_with():
    catalog = indicator_catalog(PAYLOAD)
    assert catalog[0]["id"] == "wt"
    assert catalog[0]["bands"] == [80, 60]
    assert catalog[0]["target"] == 80
