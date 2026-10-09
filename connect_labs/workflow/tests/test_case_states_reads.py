"""Reading a run's cases by CASE STATE: ``case_briefing.cases_view`` (the
``workflow_run_cases`` answer) and the ``case-states`` endpoint behind the case panel's
"Coach about this baby"."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from connect_labs.workflow import case_briefing as cb
from connect_labs.workflow.tests.test_case_coaching_action import CATALOG, DANGER, PROPS, THRIVING, _danger_row


def _thriving(entity_id, username="a10", last="2026-06-08"):
    return {
        **_danger_row(entity_id=entity_id, username=username, case_name=f"Baby {entity_id}"),
        DANGER: False,
        THRIVING: True,
        "first_weigh_date": "2026-05-18",
        "last_weigh_date": last,
        "first_weight_g": 1350,
        "last_weight_g": 2285,
        "avg_rate_gap3": 24.6,
    }


def test_cases_view_groups_by_worker_most_urgent_first():
    cases = [
        _thriving("t1"),
        _thriving("t2", last="2026-06-01"),
        _danger_row(entity_id="d1"),
        _thriving("t3", username="b10"),
        {**_danger_row(entity_id="none"), DANGER: False},
    ]
    view = cb.cases_view(cases, CATALOG, names={"10::a10": "Asha"}, label_field="case_name", per_worker=2)
    assert view["counts"] == {
        "case_state_danger_unreferred": 1,
        "case_state_weight_check": 0,
        "case_state_faltering": 0,
        "case_state_thriving": 3,
    }
    asha, binta = view["workers"]
    assert (asha["key"], asha["name"], asha["counts"]) == ("10::a10", "Asha", {DANGER: 1, THRIVING: 2})
    # most urgent first, then the most recent evidence; capped per worker
    assert [c["entity_id"] for c in asha["cases"]] == ["d1", "t1"]
    assert asha["cases"][0]["facts"].startswith("On 5 Jun 2026 the visit recorded fast breathing")
    assert asha["cases"][0]["evidence"]["unreferred_danger_visits"] == 1
    assert (binta["key"], binta["name"]) == ("10::b10", "b10")
    assert {s["name"] for s in view["case_states"]} == {s["name"] for s in CATALOG}
    assert set(view["case_states"][0]["coach"]) == {"approach", "next_steps", "limits"}


def test_cases_view_filters_by_case_state_and_worker():
    cases = [_thriving("t1"), _danger_row(entity_id="d1"), _thriving("t3", username="b10")]
    view = cb.cases_view(cases, CATALOG, case_state=THRIVING, worker_keys=["10::b10"])
    assert [w["key"] for w in view["workers"]] == ["10::b10"]
    assert view["counts"] == {THRIVING: 1}
    assert [s["name"] for s in view["case_states"]] == [THRIVING]


def _call(rows, opps=(10042,), **params):
    from connect_labs.workflow import views

    request = RequestFactory().get("/labs/workflow/api/5618/case-states/", params)
    request.user = MagicMock(is_authenticated=True)
    request.session = {"labs_oauth": {"access_token": "t"}}
    wda = MagicMock()
    wda.get_definition.return_value = SimpleNamespace(opportunity_ids=list(opps), data={}, template_type=None)
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch("connect_labs.workflow.snapshot_builders.case_context", return_value={"props_doc": PROPS}),
        patch("connect_labs.workflow.snapshot_builders.case_rows", return_value=rows),
    ):
        response = views.case_states_api(request, 5618)
    return json.loads(response.content), response.status_code


def test_a_case_in_a_case_state_says_which():
    out, status = _call([_danger_row()], rows_opportunity_id="10042", case_id="baby-1")
    assert status == 200
    assert (out["case_state"], out["label"], out["username"]) == (DANGER, "Danger sign recorded, no referral", "a10")
    assert out["facts"].startswith("On 5 Jun 2026")


def test_a_case_in_no_case_state_offers_nothing():
    out, _ = _call([{**_danger_row(), DANGER: False}], rows_opportunity_id="10042", case_id="baby-1")
    assert out["case_state"] is None


def test_only_the_workflows_own_opportunities():
    _, status = _call([_danger_row()], rows_opportunity_id="999", case_id="baby-1")
    assert status == 403
