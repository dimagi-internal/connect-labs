"""A page's declared actions: the same preview -> confirm -> execute as a run's,
within the page's scope instead (a page has no run; actions.PageScope).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory

from connect_labs.workflow import action_views, actions
from connect_labs.workflow.actions import ActionError, PageScope, commit, preview
from connect_labs.workflow.models import WorkflowActionExecution

TASK = {"key": "follow_up", "type": "create_task", "label": "Follow up"}
COACH = {
    "key": "coach",
    "type": "start_ocs_outreach",
    "label": "Coach",
    "defaults": {"prompt": "Talk with them about their week.", "bot": "bot-1"},
}

ORG_DATA = {
    "organizations": [{"id": 3, "slug": "acme", "name": "Acme"}],
    "programs": [{"id": 25, "name": "Nutrition", "organization": "acme"}],
    "opportunities": [
        {"id": 10, "name": "North", "program": 25, "organization": "acme"},
        {"id": 11, "name": "South", "program": 25, "organization": "acme"},
        {"id": 12, "name": "Elsewhere", "program": 99, "organization": "other"},
    ],
}


def _page(*entries, kind="page", opportunity_ids=None):
    data = {"name": "Program home", "config": {"actions": list(entries)}}
    if kind:
        data["kind"] = kind
    return SimpleNamespace(
        id=8, data=data, template_type=None, opportunity_ids=opportunity_ids or [], opportunity_id=None, name="Home"
    )


SUBJECT = PageScope(definition_id=8, scope_type="program", scope_key="25", opportunity_ids=(10, 11), program_id=25)


def _wda():
    wda = MagicMock()
    wda.get_workers.side_effect = lambda oid: [{"username": f"a{oid}", "name": f"Asha {oid}"}]
    return wda


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()


@pytest.fixture
def real_opps(monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)


# --- the framework, on a page ---------------------------------------------------------


def test_a_page_action_previews_and_commits_within_its_scope(user, real_opps):
    page = _page(TASK)
    args = {"workers": [{"key": "10::a10"}, {"key": "11::a11"}]}
    shown = preview(user, wda=_wda(), run=SUBJECT, definition=page, key="follow_up", arguments=args)
    assert shown["needs"] == [] and shown["confirm"]
    assert [w["opportunity_id"] for w in shown["workers"]] == [10, 11]
    with patch("connect_labs.workflow.tasks.execute_workflow_action"):
        execution = commit(
            user,
            wda=_wda(),
            run=SUBJECT,
            definition=page,
            key="follow_up",
            arguments=shown["arguments"],
            confirm=shown["confirm"],
            via="page",
        )
    execution.refresh_from_db()
    assert execution.run_id is None
    assert execution.page_scope == "program:25"
    assert execution.definition_id == 8 and execution.program_id == 25
    assert execution.as_dict()["page_scope"] == "program:25"


def test_a_worker_outside_the_pages_opportunities_is_refused(user, real_opps):
    with pytest.raises(ActionError) as e:
        preview(
            user, wda=_wda(), run=SUBJECT, definition=_page(TASK), key="follow_up",
            arguments={"workers": [{"key": "12::a12"}]},
        )  # fmt: skip
    assert e.value.code == "invalid"


def test_a_pages_token_is_bound_to_its_scope(user, real_opps):
    page = _page(TASK)
    args = {"workers": [{"key": "10::a10"}]}
    shown = preview(user, wda=_wda(), run=SUBJECT, definition=page, key="follow_up", arguments=args)
    elsewhere = PageScope(definition_id=8, scope_type="program", scope_key="26", opportunity_ids=(10,), program_id=26)
    run = SimpleNamespace(id=70, opportunity_id=10, program_id=None, period_end=None)
    for subject in (elsewhere, run):
        with pytest.raises(ActionError) as e:
            commit(
                user, wda=_wda(), run=subject, definition=page, key="follow_up",
                arguments=shown["arguments"], confirm=shown["confirm"], via="page",
            )  # fmt: skip
        assert e.value.code == "confirm_mismatch"


@pytest.mark.parametrize(
    "arguments",
    [
        {"workers": [{"key": "10::a10"}], "include_image": True},
        {"workers": [{"key": "10::a10", "picture": {"type": "topic_bars"}}]},
        {"workers": [{"key": "10::a10", "case": {"id": "c1"}}]},
    ],
)
def test_what_only_a_runs_grading_gives_is_refused_on_a_page(user, real_opps, arguments, monkeypatch):
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])
    with pytest.raises(ActionError) as e:
        preview(user, wda=_wda(), run=SUBJECT, definition=_page(COACH), key="coach", arguments=arguments)
    assert e.value.code == "needs_a_run"


def test_a_coaching_action_on_a_page_sends_the_actions_own_prompt(user, real_opps, monkeypatch):
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])
    shown = preview(
        user, wda=_wda(), run=SUBJECT, definition=_page(COACH), key="coach",
        arguments={"workers": [{"key": "10::a10"}]},
    )  # fmt: skip
    assert shown["confirm"]
    assert shown["workers"][0]["prompt"] == "Talk with them about their week."


def test_a_pages_tasks_are_attached_to_no_run(user):
    execution = WorkflowActionExecution.objects.create(
        user=user, via="page", definition_id=8, run_id=None, page_scope="program:25", action_key="follow_up",
        action_type="create_task", arguments={"workers": [{"key": "10::a10"}], "title": "Follow up"},
    )  # fmt: skip
    made = []

    def factory(**kwargs):
        tda = MagicMock()
        tda.create_task.side_effect = lambda **kw: made.append(kw) or MagicMock(id=100)
        return tda

    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.synthetic.access.labs_only_scope_denied_reason", return_value=None),
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    assert execution.status == "completed"
    assert [(m["username"], m["opportunity_id"], m["workflow_run_id"]) for m in made] == [("a10", 10, None)]


# --- the door -------------------------------------------------------------------------


def _post(view, user, body, context, **kwargs):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = user
    request.session = {"labs_oauth": {"access_token": "t", "organization_data": ORG_DATA}}
    request.labs_context = context
    response = view(request, **kwargs)
    return response.status_code, json.loads(response.content)


@pytest.fixture
def loaded():
    wda = MagicMock()
    wda.get_definition.return_value = _page(TASK)
    with patch.object(action_views, "WorkflowDataAccess", return_value=wda):
        yield wda


def test_the_page_door_acts_within_the_scope_it_is_read_in(user, loaded):
    with patch.object(action_views, "preview", return_value={"summary": "x", "needs": []}) as p:
        status, _ = _post(
            action_views.page_action_preview_api, user, {"arguments": {"workers": []}}, {"program_id": 25},
            definition_id=8, key="follow_up",
        )  # fmt: skip
    assert status == 200
    subject = p.call_args.kwargs["run"]
    assert isinstance(subject, PageScope)
    assert subject.label == "program:25"
    # The program's opportunities the viewer can see -- not 12, another program's.
    assert subject.opportunity_ids == (10, 11)


def test_the_page_door_refuses_a_workflow_with_runs(user, loaded):
    loaded.get_definition.return_value = _page(TASK, kind=None)
    with patch.object(action_views, "preview") as p:
        status, body = _post(
            action_views.page_action_preview_api, user, {"arguments": {}}, {"program_id": 25},
            definition_id=8, key="follow_up",
        )  # fmt: skip
    assert status == 400 and body["code"] == "not_a_page"
    p.assert_not_called()


def test_the_page_door_needs_a_scope(user, loaded):
    status, _ = _post(
        action_views.page_action_run_api, user, {"arguments": {}, "confirm": "t"}, {}, definition_id=8, key="k"
    )
    assert status == 400


def test_running_a_pages_action_is_recorded_as_the_page(user, loaded):
    execution = WorkflowActionExecution(
        pk=6, user=user, via="page", definition_id=8, run_id=None, page_scope="program:25", action_key="follow_up",
        action_type="create_task", arguments={"workers": []},
    )  # fmt: skip
    with patch.object(action_views, "commit", return_value=execution) as c:
        status, body = _post(
            action_views.page_action_run_api, user, {"arguments": {"workers": []}, "confirm": "tok"},
            {"program_id": 25}, definition_id=8, key="follow_up",
        )  # fmt: skip
    assert status == 202 and body["execution"]["page_scope"] == "program:25"
    assert c.call_args.kwargs["confirm"] == "tok" and c.call_args.kwargs["via"] == "page"


# --- the page is told where its actions are -------------------------------------------


@pytest.mark.django_db
def test_a_page_with_actions_gets_the_hosts_dialog_and_not_in_present_mode():
    from connect_labs.workflow.tests.test_page_mode import _request, _run_context

    data = {"name": "Home", "kind": "page", "config": {"actions": [TASK]}}
    context, _ = _run_context(data, page_mode=True)
    endpoints = context["workflow_data"]["apiEndpoints"]
    assert [a["key"] for a in context["workflow_data"]["actions"]] == ["follow_up"]
    assert endpoints["actionBase"] == "/labs/workflow/api/47/page-actions/"
    assert endpoints["actionScope"] == "?program_id=25"
    present, _ = _run_context(data, request=_request("/labs/workflow/47/run/?program_id=25&present=1"), page_mode=True)
    assert "actionBase" not in present["workflow_data"]["apiEndpoints"]
