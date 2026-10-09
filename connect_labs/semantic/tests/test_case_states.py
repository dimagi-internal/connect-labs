"""Case states: the `case_state:` meta, its validation, and how a case is worded from it."""

import copy
import datetime as dt
from pathlib import Path

import pytest
import yaml

from connect_labs.semantic import case_states as cs
from connect_labs.semantic import compiler
from connect_labs.semantic.layer1 import visit_columns_sql
from connect_labs.semantic.model import resolve_model

REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "kmc"


@pytest.fixture
def kmc():
    return (
        yaml.safe_load((REGISTRY / "properties.yml").read_text()),
        yaml.safe_load((REGISTRY / "indicators.yml").read_text()),
    )


def _problems(props, inds):
    return compiler.validate(props, inds)


def test_the_kmc_registry_declares_four_case_states_in_order(kmc):
    props, inds = kmc
    assert _problems(props, inds) == []
    cat = cs.catalog(props)
    assert [s["name"] for s in cat] == [
        "case_state_danger_unreferred",
        "case_state_weight_check",
        "case_state_faltering",
        "case_state_thriving",
    ]
    for s in cat:
        assert s["tone"] in cs.TONES and s["means"] and s["label"]
        assert set(s["coach"]) == {"approach", "next_steps", "limits"}
        assert s["picture"]["type"] in cs.PICTURE_TYPES


def _state_prop(props, name):
    return next(p for p in props["properties"] if p["name"] == name)


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p.update(type="int"), "a case state is a bool property"),
        (lambda p: p["case_state"].update(tone="happy"), "tone: must be one of"),
        (lambda p: p["case_state"].update(colour="red"), "not a case_state key"),
        (lambda p: p["case_state"].update(evidence=["no_such_column"]), "'no_such_column' is not a column"),
        (lambda p: p["case_state"].update(facts="Rose {nope} g"), "{nope} is not a column"),
        (lambda p: p["case_state"].update(facts="Rose {last_weight_g|cubits} g"), "|cubits is not a filter"),
        (lambda p: p["case_state"]["picture"].update(type="pie"), "picture: must be a mapping whose type"),
        (lambda p: p["case_state"]["picture"].update(series="height"), "is not one of this registry's case_series"),
        (lambda p: p["case_state"]["coach"].pop("limits"), "coach.limits: must be text"),
        (lambda p: p["case_state"].update(priority=10), "priority: 10 is also case_state_danger_unreferred's"),
    ],
)
def test_a_malformed_case_state_is_refused(kmc, mutate, expected):
    props, inds = copy.deepcopy(kmc[0]), kmc[1]
    mutate(_state_prop(props, "case_state_thriving"))
    problems = _problems(props, inds)
    assert any(expected in p for p in problems), problems


def test_a_labels_visit_column_compiles_to_the_matching_labels_or_null():
    model = resolve_model(
        {
            "entity": {"name": "baby", "key": "k"},
            "visit_columns": [
                {
                    "name": "signs",
                    "labels": [
                        {"column": "ds_fever", "word": "ok", "label": "fever"},
                        {"column": "ds_pus", "word": "yes", "label": "pus in the eyes, skin or belly button"},
                    ],
                }
            ],
        }
    )
    sql = visit_columns_sql(model.visit_columns)
    assert "NULLIF(CONCAT_WS(', ', CASE WHEN (x.ds_fever)::text ~* '\\yok\\y' THEN 'fever' END" in sql
    assert "AS signs" in sql


def test_a_labels_label_cannot_carry_sql():
    props = {
        "entity": {"name": "baby", "key": "k"},
        "visit_columns": [{"name": "s", "labels": [{"column": "c", "word": "yes", "label": "x'; DROP TABLE t; --"}]}],
    }
    assert any("label" in p for p in compiler.model_problems(props))


def test_an_optional_visit_column_is_null_until_the_pipeline_carries_its_fields():
    model = resolve_model(
        {
            "entity": {"name": "baby", "key": "k"},
            "visit_columns": [
                {"name": "referred_no", "optional": True, "word_match": {"column": "referral_answer", "word": "no"}},
                {"name": "alive_no", "word_match": {"column": "alive", "word": "no"}},
            ],
        }
    )
    without = visit_columns_sql(model.visit_columns, available=frozenset({"alive", "visit_date"}))
    assert "NULL::boolean AS referred_no" in without
    assert "(x.alive ~* '\\yno\\y') AS alive_no" in without
    with_it = visit_columns_sql(model.visit_columns, available=frozenset({"alive", "referral_answer"}))
    assert "(x.referral_answer ~* '\\yno\\y') AS referred_no" in with_it


def test_facts_are_filled_from_the_case_with_filters_and_choices(kmc):
    props, _ = kmc
    check = next(s for s in cs.catalog(props) if s["name"] == "case_state_weight_check")
    case = {
        "any_bad_step": True,
        "step_change_g": 495.25,
        "step_days": 1,
        "step_from_date": "2026-05-17",
        "step_to_date": dt.date(2026, 5, 18),
        "step_rate": 297.9,
    }
    assert cs.facts(check, case) == (
        "Weight changed by +495 g in 1 day between 17 May and 18 May, about 298 g/kg/day; the programme counts "
        "a change outside -20 to 45 g/kg/day as impossible."
    )
    case = {"any_bad_step": False, "implausible_weight_date": "2026-09-08", "implausible_weight_g": 295}
    assert cs.facts(check, case) == "Weight was recorded as 295 g on 8 Sep 2026; a baby in KMC weighs 800 to 5,000 g."


def test_a_missing_value_reads_not_recorded():
    assert cs.fill("Weight {w|grams} g on {d|day}", {"w": None}) == "Weight not recorded g on not recorded"
    assert cs.format_value(16.0, []) == "16" and cs.format_value(4.75, []) == "4.8"


def test_about_and_visit_lines(kmc):
    props, _ = kmc
    row = {"birth_weight_g": 1250, "reg_date": "2026-05-17", "num_visits": 5, "last_visit": "2026-06-08"}
    assert cs.about(props, row) == "Birth weight 1,250 g; registered 17 May 2026; 5 visits, the last on 8 Jun 2026."
    assert cs.about(props, {**row, "birth_weight_g": None}).startswith("Birth weight not recorded; registered")
    series = cs.case_series(props)
    visit = {
        "visit_date": "2026-05-25",
        "weight": 1635.0,
        "skin_to_skin": 20.0,
        "danger_signs": None,
        "referred": "no",
    }
    assert cs.visit_line(series, visit) == (
        "- 25 May 2026: weight 1,635 g; skin-to-skin 20 h in the last 24 h; danger signs: none; referred: no"
    )


def test_case_fields_carry_every_state_and_what_presents_it(kmc):
    props, _ = kmc
    fields = cs.case_fields(props)
    for name in ("case_state_thriving", "last_weigh_date", "check_from_date", "unreferred_danger_signs", "reg_date"):
        assert fields.get(name) == name
    assert set(fields) <= compiler.case_row_columns(props)


def test_the_case_state_of_a_row_is_its_most_urgent():
    cat = [{"name": "a", "priority": 1}, {"name": "b", "priority": 2}]
    assert cs.case_state({"a": False, "b": True}, cat)["name"] == "b"
    assert cs.case_state({"a": True, "b": True}, cat)["name"] == "a"
    assert cs.case_state({"a": True, "b": True}, cat, "b")["name"] == "b"
    assert cs.case_state({"a": False, "b": False}, cat) is None
