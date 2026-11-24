"""The ``create_audit`` action: an image audit per worker from a saved audit setup."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from connect_labs.audit import setups
from connect_labs.workflow import actions
from connect_labs.workflow.actions import ActionError, preview
from connect_labs.workflow.models import WorkflowActionExecution
from connect_labs.workflow.tests.test_actions import RUN, _definition, _wda, user  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

AUDIT = {"key": "image_audit", "type": "create_audit", "label": "AI image audit"}


@pytest.fixture
def setup():
    return setups.save(
        {
            "name": "KC weight photos",
            "opportunities": [{"id": 10}],
            "criteria": {"audit_type": "last_n_per_flw", "countPerFlw": 25},
            "image_audits": [
                {"image_path": "anthropometric/upload_weight_image", "reviewers": [{"agent_id": "scale_validation"}]}
            ],
        },
        user=None,
    )


@pytest.fixture(autouse=True)
def _real_opps(monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)


def _preview(user, **arguments):  # noqa: F811
    return preview(user, wda=_wda(), run=RUN, definition=_definition(AUDIT), key="image_audit", arguments=arguments)


def test_the_preview_names_the_setup_and_what_each_workers_audit_covers(user, setup):  # noqa: F811
    out = _preview(user, workers=[{"key": "10::a10", "opportunities": [10, 11]}], setup=setup.pk)
    assert out["needs"] == [] and out["confirm"]
    assert out["setup"]["name"] == "KC weight photos"
    line = out["workers"][0]["prompt"]
    assert (
        "KC weight photos" in line and "25 most recent visits per worker" in line and "Opportunities: 10, 11" in line
    )
    assert not out.get("synthetic")


def test_in_test_mode_the_preview_says_nothing_will_be_created(user, setup):  # noqa: F811
    out = _preview(user, workers=[{"key": "10::a10"}], setup=setup.pk, dry_run=True)
    assert out["synthetic"] is True and out["synthetic_note"] == actions.AUDIT_TEST_NOTE
    assert out["confirm"]


def test_an_unknown_setup_cannot_be_confirmed(user):  # noqa: F811
    out = _preview(user, workers=[{"key": "10::a10"}], setup=999)
    assert out["needs"] == ["setup"] and "confirm" not in out


def test_an_opportunity_outside_the_workflow_is_refused(user, setup):  # noqa: F811
    with pytest.raises(ActionError, match="not this workflow's"):
        _preview(user, workers=[{"key": "10::a10", "opportunities": [10, 99]}], setup=setup.pk)


def _execution(user, setup, **args):  # noqa: F811
    return WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        program_id=25,
        action_key="image_audit",
        action_type="create_audit",
        arguments={"workers": [{"key": "10::a10", "opportunities": [10, 11]}], "setup": setup.pk, **args},
    )


def _run(execution, result):
    calls = []

    def apply(kwargs):
        calls.append(kwargs)
        return SimpleNamespace(result=result)

    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess"),
        patch("connect_labs.audit.tasks.run_audit_creation.apply", side_effect=apply),
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    return calls


def test_each_worker_gets_an_audit_from_the_setup_linked_to_the_run(user, setup):  # noqa: F811
    execution = _execution(user, setup, title="KC review #1004")
    calls = _run(execution, {"success": True, "sessions": [{"id": 501, "title": "KC review #1004", "visits": 25}]})
    assert execution.status == "completed"
    assert len(calls) == 1
    kw = calls[0]
    assert kw["criteria"]["selected_flw_user_ids"] == ["a10"] and kw["criteria"]["granularity"] == "per_flw"
    assert kw["opportunities"] == [{"id": 10, "name": ""}, {"id": 11, "name": ""}]
    assert kw["workflow_run_id"] == 70 and kw["username"] == "manager" and kw["access_token"] == "ct"
    rec = execution.results["10::a10"]
    assert rec["sessions"] == [{"id": 501, "title": "KC review #1004", "visits": 25}] and rec["setup"] == setup.pk


def test_a_dry_run_creates_nothing(user, setup):  # noqa: F811
    execution = _execution(user, setup, dry_run=True)
    calls = _run(execution, {"success": True, "sessions": [{"id": 1}]})
    assert calls == []
    assert execution.status == "completed" and execution.results["10::a10"]["dry_run"] is True


def test_a_failed_audit_marks_only_that_worker_failed(user, setup):  # noqa: F811
    execution = _execution(user, setup)
    _run(execution, {"success": False, "error": "no visits"})
    assert execution.results["10::a10"]["status"] != "ok"


def test_a_retry_never_makes_a_second_audit(user, setup):  # noqa: F811
    execution = _execution(user, setup)
    _run(execution, {"success": True, "sessions": [{"id": 501}]})
    first = execution.results["10::a10"]
    # Run again (a redelivered task): the worker already has its audit.
    WorkflowActionExecution.objects.filter(pk=execution.pk).update(status="running")
    calls = _run(execution, {"success": True, "sessions": [{"id": 502}]})
    assert calls == [] and execution.results["10::a10"]["sessions"] == first["sessions"]
