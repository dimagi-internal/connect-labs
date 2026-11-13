"""cea_model_run: the explorer's engine, for an agent."""

from __future__ import annotations

import pytest

from connect_labs.effectiveness import explore
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


def _row(i, **kw):
    base = {
        "id": i,
        "name": f"Opp {i}",
        "org_slug": "partner-a",
        "program_id": 7,
        "service_name": "Child Health Campaign",
        "country": "NG",
        "country_name": "Nigeria",
        "active": True,
        "status": "modelled",
        "status_text": "Modelled",
        "rank": i,
        "total_usd": 1000.0 * i,
        "own_usd": 1000.0 * i,
        "reached": 500 * i,
        "reach_basis": "distinct_people",
        "cost_per_person": 2.0,
        "model_cost_per_person": 1.84,
        "model_x": 20.0,
        "x": 30.0 / i,
        "clears_bar": True,
        "deaths_averted": 2.0 * i,
        "cost_per_death": 500.0,
        "baseline_level": "state or province",
        "areas_n": 1,
        "model": "chc",
    }
    base.update(kw)
    return base


class TestOpportunities:
    @pytest.fixture
    def ranking(self, monkeypatch):
        from connect_labs.effectiveness import implementation as cea

        rows = [
            _row(1),
            _row(2),
            _row(3, program_id=8, country="UG", country_name="Uganda", model="mbw"),
            _row(4, status="no_model", status_text="No model", rank=None, model=None),
        ]
        data = {"rows": rows, "bar_x": 6, "generated": "2026-11-10", "source": "src", "totals": {}, "programmes": []}
        monkeypatch.setattr(cea, "ranking", lambda *a, **k: data)
        monkeypatch.setattr(cea, "programmes", lambda rs: [{"program_id": r["program_id"]} for r in rs])
        return rows

    def test_it_ranks_filters_and_rolls_up_exactly_the_selection(self, ranking):
        got = cea.cea_opportunities(None, program_id=7)
        assert [r["id"] for r in got["rows"]] == [1, 2] and got["count"] == 2
        assert got["rollup"]["spend"] == pytest.approx(1000 + 2000)
        assert got["rollup"]["people"] == 500 + 1000
        assert got["rollup"]["portfolio_x"] == pytest.approx((30 * 1000 + 15 * 2000) / 3000)

    def test_country_matches_a_name_or_an_alpha_2_and_model_filters(self, ranking):
        assert [r["id"] for r in cea.cea_opportunities(None, country="uganda")["rows"]] == [3]
        assert [r["id"] for r in cea.cea_opportunities(None, country="NG")["rows"]] == [1, 2]
        assert [r["id"] for r in cea.cea_opportunities(None, model="mbw")["rows"]] == [3]

    def test_unmodelled_rows_are_left_out_unless_asked_for_by_id(self, ranking):
        assert 4 not in [r["id"] for r in cea.cea_opportunities(None)["rows"]]
        assert [r["id"] for r in cea.cea_opportunities(None, opportunity_ids=[4])["rows"]] == [4]

    def test_limit_caps_the_rows_but_not_the_rollup(self, ranking):
        got = cea.cea_opportunities(None, limit=1)
        assert got["shown"] == 1 and got["count"] == 3 and got["rollup"]["opportunities"] == 3

    def test_detail_needs_one_opportunity_and_returns_the_explorer_links(self, ranking, monkeypatch):
        from connect_labs.effectiveness import implementation as pulse_cea

        monkeypatch.setattr(
            pulse_cea,
            "opportunity_detail",
            lambda i: {
                "placement": {"areas": 1},
                "result": {"areas": [{"name": "Kaduna"}], "other_inputs": []},
                "explore": [{"path": "/labs/cea/models/?model=chc"}],
            },
        )
        got = cea.cea_opportunities(None, opportunity_ids=[1], detail=True)
        assert got["detail"]["explore"][0]["path"].startswith("/labs/cea/models/")
        with pytest.raises(MCPToolError):
            cea.cea_opportunities(None, program_id=7, detail=True)

    def test_an_unknown_opportunity_is_not_found(self, ranking):
        with pytest.raises(MCPToolError) as err:
            cea.cea_opportunities(None, opportunity_ids=[999])
        assert "999" in str(err.value)

    def test_it_is_not_open_to_a_no_visit_data_token(self):
        """It carries what each opportunity cost and how many people it reached."""
        assert "cea_opportunities" not in token_scopes.NO_USERVISIT_DATA_TOOLS
        assert "cea_model_run" in token_scopes.NO_USERVISIT_DATA_TOOLS
