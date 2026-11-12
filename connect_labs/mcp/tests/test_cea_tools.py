"""cea_model_run: the explorer's engine, for an agent."""

from __future__ import annotations

import pytest

from connect_labs.impact import explore
from connect_labs.labs import canopy
from connect_labs.mcp import token_scopes
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import cea


def test_it_is_the_answer_the_explorer_gives_for_the_same_inputs():
    got = cea.cea_model_run(None, model="chc", overrides={"cost": 2.5})
    want = explore.run("chc", {"cost": 2.5})
    assert got["run"]["headline"]["multiple"] == pytest.approx(want["headline"]["multiple"])
    assert got["run"]["overrides"] == {"cost": 2.5}
    assert any(r["key"] == "full_x" for r in got["run"]["chain"])


def test_it_matches_the_live_sheets_headline_at_the_models_own_inputs():
    assert cea.cea_model_run(None)["run"]["headline"]["multiple"] == pytest.approx(6.095357753901075)


def test_which_components_count_is_an_input():
    both = cea.cea_model_run(None)["run"]["headline"]["multiple"]
    ors_only = cea.cea_model_run(None, overrides={"va_on": 0, "dw_on": 0})["run"]["headline"]["multiple"]
    assert ors_only < both


def test_it_returns_a_link_to_the_page_with_the_inputs_set():
    got = cea.cea_model_run(None, model="mbw", overrides={"cost": 12})
    assert got["explorer_path"].startswith("/labs/cea/models/?")
    assert "model=mbw" in got["explorer_path"] and "i_cost=12.0" in got["explorer_path"]


def test_include_adds_what_is_asked_for_and_nothing_else():
    base = cea.cea_model_run(None)
    assert "tornado" in base and "scenarios" not in base and "places" not in base and "inputs" not in base
    more = cea.cea_model_run(None, include=["scenarios", "places", "inputs"])
    assert "tornado" not in more
    assert len(more["scenarios"]) == 6 and len(more["places"]) == 45 and all("inputs" not in p for p in more["places"])
    assert {i["key"] for i in more["inputs"]} >= {"ors", "cost", "va_on", "funging"}


def test_a_grid_runs_two_inputs_against_each_other():
    got = cea.cea_model_run(None, include=[], grid={"x": "cost", "xs": [1.44, 2.5], "y": "ors", "ys": [0.2, 0.6]})
    assert len(got["grid"]["multiples"]) == 2 and len(got["grid"]["multiples"][0]) == 2


@pytest.mark.parametrize(
    "kwargs, words",
    [
        ({"overrides": {"nope": 1}}, "nope"),
        ({"model": "zzz"}, "Unknown model"),
        ({"include": ["bogus"]}, "bogus"),
        ({"grid": {"x": "cost", "xs": [1], "y": "cost", "ys": [1]}}, "two different"),
    ],
)
def test_bad_requests_are_refused_with_a_reason(kwargs, words):
    with pytest.raises(MCPToolError) as err:
        cea.cea_model_run(None, **kwargs)
    assert words in str(err.value)


def test_it_is_in_the_targeting_read_scope_and_open_to_a_restricted_caller():
    """Arithmetic on vendored public models: nothing in it is a visit, so a no-visit-data token may call it."""
    assert "cea_model_run" in canopy.SCOPE_TOOLS["targeting:read"]
    assert "cea_model_run" in token_scopes.NO_USERVISIT_DATA_TOOLS
