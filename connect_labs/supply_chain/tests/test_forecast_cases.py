"""A dispensing rule's `cases` block, and visits that remember the child they were about.

THIS REPOSITORY IS PUBLIC. Every username, id and answer here is invented.
"""

import pytest

from connect_labs.supply_chain.models import WorkerVisit
from connect_labs.supply_chain.stock.services.dispensing import case_answers, form_matches, validate_cases
from connect_labs.supply_chain.tests.test_visit_reader import (  # noqa: F401 -- fixtures
    RUTF_LINES,
    RUTF_PATH,
    _rule,
    da,
    read,
    rutf,
    store,
    visit,
)

pytestmark = pytest.mark.django_db

ENROL = "form.screening_outcome.rutf_enrollment"
OUTCOME = "form.case_state.outcome_value"
CASES = {
    "enrol": {"forms": ["Screening"], "path": ENROL, "equals": "yes"},
    "outcome": {
        "path": OUTCOME,
        "open": ["enrolled"],
        "exit": ["recovered", "lost_for_follow_up"],
        "complete": ["recovered"],
    },
    "lost_after_days": 21,
}


def test_validate_cases_keeps_a_good_block():
    clean = validate_cases(CASES)
    assert clean["lost_after_days"] == 21
    assert clean["enrol"] == {"forms": ["Screening"], "path": ENROL, "equals": "yes"}
    assert clean["outcome"]["complete"] == ["recovered"]


def test_an_empty_block_is_no_rule():
    assert validate_cases({}) == {}
    assert validate_cases(None) == {}


def test_validate_cases_names_a_path_that_is_not_a_form_path():
    with pytest.raises(ValueError, match="form_json path"):
        validate_cases({**CASES, "enrol": {**CASES["enrol"], "path": "screening.x"}})


def test_complete_must_be_exits():
    with pytest.raises(ValueError, match="complete"):
        validate_cases({**CASES, "outcome": {**CASES["outcome"], "complete": ["enrolled"]}})


def test_an_outcome_cannot_be_both_open_and_an_exit():
    with pytest.raises(ValueError, match="both"):
        validate_cases({**CASES, "outcome": {**CASES["outcome"], "open": ["enrolled", "recovered"]}})


def test_lost_after_days_defaults_and_is_bounded():
    without = {key: value for key, value in CASES.items() if key != "lost_after_days"}
    assert validate_cases(without)["lost_after_days"] == 21
    with pytest.raises(ValueError, match="lost_after_days"):
        validate_cases({**CASES, "lost_after_days": 0})


def test_forms_match_by_name_or_xmlns_whatever_the_trailing_space():
    screening = {"form": {"@name": "Screening ", "@xmlns": "http://example.org/screening"}}
    assert form_matches(["Screening"], screening)
    assert form_matches(["http://example.org/screening"], screening)
    assert not form_matches(["Visit Form"], screening)
    assert form_matches([], screening)  # no filter: every form


def test_case_answers_read_the_enrolment_only_on_its_own_form():
    rule = validate_cases(CASES)
    screening = {"form": {"@name": "Screening", "screening_outcome": {"rutf_enrollment": "yes"}}}
    follow_up = {"form": {"@name": "Visit Form", "screening_outcome": {"rutf_enrollment": "yes"}}}
    assert case_answers(rule, screening) == {ENROL: "yes"}
    assert case_answers(rule, follow_up) == {}


def test_case_answers_read_the_outcome_on_every_form():
    rule = validate_cases(CASES)
    follow_up = {"form": {"@name": "Visit Form", "case_state": {"outcome_value": "recovered"}}}
    assert case_answers(rule, follow_up) == {OUTCOME: "recovered"}


def test_the_reader_remembers_the_child_the_form_and_the_case_answers(da, rutf, store):  # noqa: F811
    _rule(rutf, store, RUTF_LINES, cases=validate_cases(CASES))
    screening = visit(
        9001,
        name="Screening ",
        entity_id="child-1",
        answers={ENROL: "yes", RUTF_PATH: "5"},
    )
    screening["form_json"]["form"]["@xmlns"] = "http://example.org/screening"

    read(da, [screening])

    seen = WorkerVisit.objects.get(visit_id="9001")
    assert (seen.entity_id, seen.form_xmlns) == ("child-1", "http://example.org/screening")
    assert seen.answers == {ENROL: "yes", RUTF_PATH: "5"}


def test_a_re_read_backfills_the_child_and_case_answers_on_visits_read_before(da, rutf, store):  # noqa: F811
    rule = _rule(rutf, store, RUTF_LINES)
    follow_up = visit(9001, entity_id="child-1", answers={OUTCOME: "enrolled", RUTF_PATH: "14"})
    read(da, [{key: value for key, value in follow_up.items() if key != "entity_id"}])
    assert WorkerVisit.objects.get(visit_id="9001").entity_id == ""

    rule.cases = validate_cases(CASES)
    rule.save()
    read(da, [follow_up])

    seen = WorkerVisit.objects.get(visit_id="9001")
    assert (seen.entity_id, seen.answers[OUTCOME]) == ("child-1", "enrolled")
