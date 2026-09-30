"""The run page's door to a workflow's actions: preview, run, follow."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from connect_labs.workflow import action_views
from connect_labs.workflow.actions import ActionError
from connect_labs.workflow.models import WorkflowActionExecution

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="manager", password="p")


def _post(view, user, body, **kwargs):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = user
    request.session = {}
    response = view(request, **kwargs)
    return response.status_code, json.loads(response.content)


@pytest.fixture
def loaded():
    wda = MagicMock()
    run = SimpleNamespace(id=70, data={"definition_id": 7})
    wda.get_run.return_value = run
    wda.get_definition.return_value = SimpleNamespace(id=7)
    with patch.object(action_views, "WorkflowDataAccess", return_value=wda):
        yield wda


def test_a_preview_passes_the_request_through_and_returns_what_would_happen(user, loaded):
    with patch.object(action_views, "preview", return_value={"summary": "x", "needs": []}) as preview:
        status, body = _post(
            action_views.action_preview_api,
            user,
            {"arguments": {"workers": [{"key": "10::a"}]}},
            run_id=70,
            key="initiate_ai_coach",
        )
    assert status == 200 and body["summary"] == "x"
    kwargs = preview.call_args.kwargs
    assert kwargs["key"] == "initiate_ai_coach"
    assert kwargs["arguments"] == {"workers": [{"key": "10::a"}]}
    assert kwargs["request"] is not None, "the page's own OCS session is used to list bots"


def test_running_from_the_page_is_recorded_as_the_page(user, loaded):
    execution = WorkflowActionExecution(
        pk=5,
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        action_key="k",
        action_type="create_task",
        arguments={"workers": []},
    )
    with patch.object(action_views, "commit", return_value=execution) as commit:
        status, body = _post(
            action_views.action_run_api, user, {"arguments": {"workers": []}, "confirm": "tok"}, run_id=70, key="k"
        )
    assert status == 202
    assert commit.call_args.kwargs["via"] == "page"
    assert commit.call_args.kwargs["confirm"] == "tok"


def test_a_stale_or_mismatched_confirm_is_a_conflict(user, loaded):
    with patch.object(action_views, "commit", side_effect=ActionError("confirm_mismatch", "preview again")):
        status, body = _post(action_views.action_run_api, user, {"arguments": {}, "confirm": "t"}, run_id=70, key="k")
    assert status == 409 and body["code"] == "confirm_mismatch"


def test_a_run_nobody_can_read_is_not_found(user):
    wda = MagicMock()
    wda.get_run.return_value = None
    with patch.object(action_views, "WorkflowDataAccess", return_value=wda):
        status, _ = _post(action_views.action_preview_api, user, {}, run_id=70, key="k")
    assert status == 404


def test_only_the_person_an_action_ran_as_can_follow_it(user):
    other = get_user_model().objects.create_user(username="other", password="p")
    execution = WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        action_key="k",
        action_type="create_task",
        arguments={"workers": [{"key": "10::a"}]},
    )
    request = RequestFactory().get("/x/")
    request.user = other
    assert action_views.action_execution_api(request, execution_id=execution.pk).status_code == 404
    request.user = user
    body = json.loads(action_views.action_execution_api(request, execution_id=execution.pk).content)
    assert body["execution"]["progress"] == {"total": 1, "done": 0, "ok": 0, "failed": 0}
