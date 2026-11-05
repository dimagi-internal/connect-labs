"""Reading a run's cases by CASE STATE: ``case_briefing.cases_view`` (the
``workflow_run_cases`` answer) and the ``case-states`` endpoint behind the case panel's
"Coach about this baby"."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
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


def _read_as(username, rows_mock, case_id="cost-1"):
    from connect_labs.workflow import views

    request = RequestFactory().get(
        "/labs/workflow/api/5618/case-states/", {"rows_opportunity_id": "10042", "case_id": case_id}
    )
    request.user = MagicMock(is_authenticated=True, username=username)
    request.session = {"labs_oauth": {"access_token": "t"}}
    wda = MagicMock()
    wda.get_definition.return_value = SimpleNamespace(opportunity_ids=[10042], data={}, template_type=None)
    with (
        patch.object(views, "WorkflowDataAccess", return_value=wda),
        patch("connect_labs.workflow.snapshot_builders.case_context", return_value={"props_doc": PROPS}),
        patch("connect_labs.workflow.snapshot_builders.case_rows", rows_mock),
    ):
        response = views.case_states_api(request, 5618)
    return json.loads(response.content), response.status_code


@pytest.fixture
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def test_a_repeat_read_does_not_evaluate_the_opportunity_again(clean_cache):
    """#2876: the case panel asks each time it remounts, and one answer costs a
    whole-opportunity evaluation, so the expensive call itself is what is counted."""
    rows = MagicMock(return_value=[_danger_row()])
    first, _ = _read_as("viewer", rows)
    second, _ = _read_as("viewer", rows)
    assert rows.call_count == 1
    assert second == first


def test_another_viewer_gets_their_own_read(clean_cache):
    """The read resolves pipelines as the person, so one answer never serves another."""
    rows = MagicMock(return_value=[_danger_row()])
    _read_as("viewer", rows)
    _read_as("someone-else", rows)
    assert rows.call_count == 2


def test_a_failed_read_is_not_kept_and_leaves_no_claim(clean_cache):
    rows = MagicMock(side_effect=[RuntimeError("statement timeout"), [_danger_row()]])
    _, status = _read_as("viewer", rows)
    assert status == 500
    out, status = _read_as("viewer", rows)
    assert (status, out["case_state"]) == (200, DANGER)


def test_an_overlapping_read_waits_for_the_one_running(clean_cache):
    from connect_labs.workflow import views

    computed = MagicMock(return_value={"case_state": "late"})
    cache.add("k:fill", 1, 60)

    def fill_finishes(_seconds):
        cache.set("k", {"case_state": "theirs"}, 60)

    with patch.object(views.time, "sleep", side_effect=fill_finishes):
        assert views._case_states_once("k", computed) == {"case_state": "theirs"}
    computed.assert_not_called()


def test_a_read_stops_waiting_rather_than_starting_a_second_evaluation(clean_cache):
    from connect_labs.workflow import views

    computed = MagicMock()
    cache.add("k:fill", 1, 60)
    with (
        patch.object(views, "CASE_STATES_WAIT_SECONDS", 0),
        patch.object(views.time, "sleep"),
    ):
        assert views._case_states_once("k", computed) == {"case_state": None, "reason": "busy"}
    computed.assert_not_called()


def test_a_fill_that_finished_just_before_the_claim_is_not_repeated(clean_cache):
    from connect_labs.workflow import views

    computed = MagicMock()
    with patch.object(views.cache, "get", side_effect=[None, {"case_state": "theirs"}]):
        assert views._case_states_once("k", computed) == {"case_state": "theirs"}
    computed.assert_not_called()


def test_a_fill_past_its_lease_leaves_the_newer_claim_alone(clean_cache):
    from connect_labs.workflow import views

    def outlives_its_lease():
        cache.set("k:fill", "a newer reader's claim", 60)
        raise RuntimeError("slow and then failed")

    # The wait's deadline, the claim, then the lease check long after the claim ran out.
    clock = [0.0, 0.0, views.CASE_STATES_FILL_SECONDS + 1.0]
    with patch.object(views.time, "monotonic", side_effect=clock), pytest.raises(RuntimeError):
        views._case_states_once("k", outlives_its_lease)
    assert cache.get("k:fill") == "a newer reader's claim"


def test_a_failed_fill_within_its_lease_releases_the_claim(clean_cache):
    from connect_labs.workflow import views

    with pytest.raises(RuntimeError):
        views._case_states_once("k", MagicMock(side_effect=RuntimeError("failed")))
    assert cache.get("k:fill") is None


def test_a_cache_that_is_down_still_answers(clean_cache):
    """`IGNORE_EXCEPTIONS` makes a dead Redis refuse every claim without raising, and a
    reader must not sit out the wait for a fill that cannot exist."""
    from connect_labs.workflow import views

    with (
        patch.object(views.cache, "get", return_value=None),
        patch.object(views.cache, "add", return_value=False),
        patch.object(views.time, "sleep") as sleep,
    ):
        assert views._case_states_once("k", lambda: {"case_state": "x"}) == {"case_state": "x"}
    sleep.assert_not_called()
