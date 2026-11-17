"""The scenario: what someone is working on, read from a link and written back to one."""

import pytest

from connect_labs.effectiveness import explore
from connect_labs.effectiveness.scenario import (
    DIALS,
    EVERYWHERE,
    KNOB_DEFAULT,
    Scenario,
    default_dial,
    dial_defaults,
    number,
)


def test_the_default_scenario_is_the_bare_page():
    sc = Scenario.from_params({})
    assert sc.models == ("chc",) and sc.everywhere and sc.indicator == EVERYWHERE
    assert sc.to_params() == {"models": "chc"}
    assert sc.changes() == {"dials": 0, "assumptions": 0}


def test_a_link_round_trips_and_only_what_differs_is_written():
    link = {
        "models": "chc,kc",
        "indicator": "u5mr",
        "threshold": "80",
        "iso": "NGA,NER",
        "bar": "8",
        "chc_reach": "50",
        "kc_extra": "2.5",
        "i.chc.cost": "3",
        "method": "dhs",
    }
    sc = Scenario.from_params(link)
    assert sc.to_params() == link
    assert Scenario.from_params(sc.to_params()) == sc


def test_a_dial_left_at_its_default_leaves_the_link():
    sc = Scenario.from_params({"models": "chc", "chc_reach": "100", "chc_effect": "100", "chc_extra": "0"})
    assert sc.dials == {} and sc.to_params() == {"models": "chc"}


def test_kangaroo_care_starts_at_the_most_it_can_reach_and_the_others_at_everyone():
    assert default_dial("kc", "reach") == 50.0 and default_dial("chc", "reach") == 100.0
    assert Scenario.from_params({"models": "kc"}).dials_for("kc")["reach"] == 50.0
    assert Scenario.from_params({"models": "kc", "kc_reach": "50"}).to_params() == {"models": "kc"}
    # 100% is a change for kc, and it is kept
    assert Scenario.from_params({"models": "kc", "kc_reach": "100"}).to_params() == {"models": "kc", "kc_reach": "100"}
    assert dial_defaults()["kc"] == {"extra": 0.0, "effect": 100.0, "reach": 50.0}


def test_the_extra_cost_dial_may_go_below_zero_and_the_others_may_not():
    sc = Scenario.from_params({"models": "chc", "chc_extra": "-10", "chc_effect": "-5", "chc_reach": "-1"})
    assert sc.dials == {"chc": {"extra": -10.0}}


def test_a_target_the_risk_switch_and_the_grain_are_in_the_link_only_when_not_the_default():
    sc = Scenario.from_params({"models": "kc", "target": "1000000", "place_risk": "0", "grain": "territory"})
    assert sc.target == 1_000_000 and sc.place_risk is False and sc.grain == "territory"
    assert sc.to_params() == {"models": "kc", "target": "1000000", "place_risk": "0", "grain": "territory"}
    d = Scenario.from_params({"models": "kc", "target": "-4", "place_risk": "1", "grain": "galaxy"})
    assert d.target is None and d.place_risk is True and d.grain == "country" and d.to_params() == {"models": "kc"}


def test_switching_off_place_risk_counts_as_a_change_only_for_a_model_that_has_it():
    assert Scenario.from_params({"models": "kc", "place_risk": "0"}).changes() == {"dials": 0, "assumptions": 1}
    assert Scenario.from_params({"models": "chc", "place_risk": "0"}).changes() == {"dials": 0, "assumptions": 0}


def test_a_territory_grain_asks_the_baseline_for_every_state_not_a_country_folded_into_one_row():
    sc = Scenario.from_params({"grain": "territory", "indicator": "u5mr"})
    assert sc.place_params()["rollup"] == "0" and sc.place_params()["admin_level"] == "1"
    assert "rollup" not in Scenario.from_params({"indicator": "u5mr"}).place_params()


def test_dials_are_the_implementation_pages_dials_spelled_the_same_way():
    from connect_labs.effectiveness.implementation import KNOB_DEFAULT as impl_defaults
    from connect_labs.effectiveness.implementation import what_if_from

    assert impl_defaults is KNOB_DEFAULT and [d["key"] for d in DIALS] == list(KNOB_DEFAULT)
    link = {"chc_reach": "40", "chc_effect": "80", "chc_extra": "1"}
    assert what_if_from(link, 6.0, ["chc"])["m"]["chc"] == Scenario.from_params(link).dials_for("chc")


def test_a_link_that_names_nothing_real_degrades_instead_of_failing():
    sc = Scenario.from_params({"models": "nope,kc,kc", "bar": "-3", "chc_reach": "lots", "threshold": "x"})
    assert sc.models == ("kc",) and sc.bar is None and sc.threshold is None and sc.dials == {}


def test_an_assumption_that_is_not_an_input_says_so_in_words():
    with pytest.raises(explore.ExploreError) as e:
        Scenario.from_params({"i.chc.nonsense": "1"})
    assert "nonsense" in e.value.message


def test_only_the_programmes_in_play_count_as_changes():
    sc = Scenario.from_params({"models": "chc", "chc_reach": "50", "kc_reach": "50", "i.kc.cost": "99"})
    assert sc.changes() == {"dials": 1, "assumptions": 0}


def test_iso_codes_are_upper_cased_and_split_on_commas_or_spaces():
    assert Scenario.from_params({"iso": "nga, ner tcd"}).iso == ("NGA", "NER", "TCD")


def test_the_places_are_handed_to_the_baseline_in_its_own_keys():
    sc = Scenario.from_params({"indicator": "u5mr", "threshold": "80", "iso": "NGA", "method": "dhs"})
    assert sc.place_params() == {"method": "dhs", "indicator": "u5mr", "threshold": "80", "iso": "NGA"}


def test_a_page_starts_from_every_choice_written_out():
    state = Scenario.from_params({"chc_reach": "50"}).to_state(default_bar=6.0)
    assert state["bar"] == 6.0 and state["dials"]["chc"] == {"extra": 0.0, "effect": 100.0, "reach": 50.0}
    assert set(state["dials"]) == {"chc", "kc", "mbw"}


@pytest.mark.parametrize("raw, want", [("3", 3.0), ("x", None), ("-1", None), ("nan", None), ("inf", None)])
def test_number_keeps_finite_non_negative_values(raw, want):
    assert number(raw) == want


def test_the_server_and_the_browser_agree_on_the_link():
    """The same fixture the page's script is tested against (``static/effectiveness/scenario.test.js``)."""
    import json
    from pathlib import Path

    contract = json.loads((Path(__file__).parent / "scenario_contract.json").read_text())
    sc = Scenario.from_params(contract["link"])
    assert sc.to_state(contract["default_bar"]) == contract["state"]
    assert sc.to_params() == contract["link"]
