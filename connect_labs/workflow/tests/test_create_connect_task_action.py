"""The ``create_connect_task`` action: a relearn (or OCS) task assigned in Connect itself."""

import datetime as dt
from unittest.mock import patch

import httpx
import pytest

from connect_labs.workflow import actions, connect_tasks
from connect_labs.workflow.actions import ActionError, commit, preview
from connect_labs.workflow.models import WorkflowActionExecution
from connect_labs.workflow.tests.test_actions import RUN, _definition, _wda, user  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db

DOC = "2f1c6a52-4c9e-4bfb-9a43-0a3f2d1e5b77"
GPS = "8b7e0c1d-1111-4222-8333-944455566677"
RELEARN = {
    "key": "relearn_doc",
    "type": "create_connect_task",
    "label": "Relearn: Document Photo",
    "defaults": {"task_type_id": DOC, "due_in_days": 2},
}
TYPES = [
    {"task_type_id": DOC, "name": "Document Photo Relearn", "mode": "relearn", "is_active": True, "archived": None},
    {"task_type_id": GPS, "name": "GPS Calibration Relearn", "mode": "relearn", "is_active": False, "archived": None},
]


@pytest.fixture(autouse=True)
def _real_opps(monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)


@pytest.fixture
def connect(monkeypatch):
    """Connect as the person: a token, and the opportunity's task types."""
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda who: "ct")
    monkeypatch.setattr(connect_tasks, "list_task_types", lambda token, opp: TYPES)


def _preview(user, action=RELEARN, **arguments):  # noqa: F811
    return preview(user, wda=_wda(), run=RUN, definition=_definition(action), key=action["key"], arguments=arguments)


def test_the_preview_names_the_task_type_and_due_date(user, connect):  # noqa: F811
    out = _preview(user, workers=[{"key": "10::a10"}])
    due = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    assert out["needs"] == [] and out["confirm"]
    assert out["connect_task_type"] == {"task_type_id": DOC, "name": "Document Photo Relearn", "mode": "relearn"}
    assert out["due_date"] == due and out["arguments"]["due_date"] == due
    assert out["workers"][0]["prompt"] == f"Document Photo Relearn, assigned in Connect, due {due}."


BY_NAME = {**RELEARN, "defaults": {"task_type_name": " document photo relearn ", "due_in_days": 2}}


def test_a_type_named_by_its_connect_name_is_looked_up_and_bound(user, connect):  # noqa: F811
    out = _preview(user, action=BY_NAME, workers=[{"key": "10::a10"}])
    assert out["needs"] == [] and out["confirm"]
    assert out["arguments"]["task_type_id"] == DOC
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        execution = commit(
            user,
            wda=_wda(),
            run=RUN,
            definition=_definition(BY_NAME),
            key="relearn_doc",
            arguments=out["arguments"],
            confirm=out["confirm"],
            via="page",
        )
    assert execution.arguments["task_type_id"] == DOC


def test_an_inactive_type_is_not_found_by_name(user, connect):  # noqa: F811
    out = _preview(user, action=BY_NAME, workers=[{"key": "10::a10"}], task_type_name="GPS Calibration Relearn")
    assert out["needs"] == ["task_type"] and "confirm" not in out


def test_a_name_two_active_types_share_finds_nothing(user, monkeypatch):  # noqa: F811
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda who: "ct")
    twins = [dict(TYPES[0]), {**TYPES[0], "task_type_id": GPS}]
    monkeypatch.setattr(connect_tasks, "list_task_types", lambda token, opp: twins)
    out = _preview(user, action=BY_NAME, workers=[{"key": "10::a10"}])
    assert out["needs"] == ["task_type"] and "confirm" not in out


def test_workers_whose_opportunities_have_different_types_are_refused(user, monkeypatch):  # noqa: F811
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda who: "ct")
    per_opp = {10: [TYPES[0]], 11: [{**TYPES[0], "task_type_id": GPS, "is_active": True}]}
    monkeypatch.setattr(connect_tasks, "list_task_types", lambda token, opp: per_opp[opp])
    out = _preview(user, action=BY_NAME, workers=[{"key": "10::a10"}, {"key": "11::a11"}])
    assert out["needs"] == ["task_type"] and "one opportunity at a time" in out["task_type_error"]


def test_without_a_declared_due_it_is_a_week(user, connect):  # noqa: F811
    action = {**RELEARN, "defaults": {"task_type_id": DOC}}
    out = _preview(user, action=action, workers=[{"key": "10::a10"}])
    assert out["due_date"] == (dt.date.today() + dt.timedelta(days=7)).isoformat()


def test_an_inactive_or_unknown_task_type_cannot_be_confirmed(user, connect):  # noqa: F811
    for task_type_id in (GPS, "00000000-0000-4000-8000-000000000000"):
        out = _preview(user, workers=[{"key": "10::a10"}], task_type_id=task_type_id)
        assert out["needs"] == ["task_type"] and "confirm" not in out
        assert out["unknown_task_type"] == task_type_id


def test_connect_unreachable_at_preview_cannot_be_confirmed(user, monkeypatch):  # noqa: F811
    monkeypatch.setattr("connect_labs.labs.connect_tokens.get_valid_access_token", lambda who: "ct")

    def down(token, opp):
        raise connect_tasks.ConnectTaskError("Connect could not be reached to read the task types.")

    monkeypatch.setattr(connect_tasks, "list_task_types", down)
    out = _preview(user, workers=[{"key": "10::a10"}])
    assert out["needs"] == ["task_type"] and "confirm" not in out
    assert out["task_type_error"] == "Connect could not be reached to read the task types."


def test_a_task_type_is_required(user, connect):  # noqa: F811
    action = {**RELEARN, "defaults": {}}
    with pytest.raises(ActionError, match="task_type_id"):
        _preview(user, action=action, workers=[{"key": "10::a10"}])


def test_a_past_due_date_is_refused(user, connect):  # noqa: F811
    with pytest.raises(ActionError, match="past"):
        _preview(user, workers=[{"key": "10::a10"}], due_date="2020-01-01")


def test_the_preview_confirms_into_a_queued_execution(user, connect):  # noqa: F811
    out = _preview(user, workers=[{"key": "10::a10"}])
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        execution = commit(
            user,
            wda=_wda(),
            run=RUN,
            definition=_definition(RELEARN),
            key="relearn_doc",
            arguments=out["arguments"],
            confirm=out["confirm"],
            via="page",
        )
    assert execution.action_type == "create_connect_task"
    assert execution.arguments["task_type_id"] == DOC and execution.arguments["due_date"] == out["due_date"]


def test_the_declaration_is_valid():
    assert actions.declaration_problems([RELEARN, {**BY_NAME, "key": "relearn_by_name"}]) == []


def _execution(user, **args):  # noqa: F811
    return WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        program_id=25,
        action_key="relearn_doc",
        action_type="create_connect_task",
        arguments={"workers": [{"key": "10::a10"}], "task_type_id": DOC, "due_date": "2030-01-02", **args},
    )


def _run(execution, assign):
    calls = []

    def fake(token, opp, **kw):
        calls.append({"token": token, "opp": opp, **kw})
        return assign(**kw)

    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess"),
        patch.object(connect_tasks, "assign_task", side_effect=fake),
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    return calls


def test_each_worker_gets_the_task_in_connect(user):  # noqa: F811
    execution = _execution(user)
    calls = _run(execution, lambda **kw: {"assigned_task_id": "t-1", "username": kw["username"]})
    assert execution.status == "completed"
    assert calls == [{"token": "ct", "opp": 10, "username": "a10", "task_type_id": DOC, "due_date": "2030-01-02"}]
    assert execution.results["10::a10"]["connect_task_id"] == "t-1"


def test_already_assigned_counts_as_done(user):  # noqa: F811
    execution = _execution(user)

    def conflict(**kw):
        raise connect_tasks.ConnectTaskError("already assigned", 409)

    _run(execution, conflict)
    assert execution.status == "completed" and execution.results["10::a10"]["already_assigned"] is True


def test_a_refusal_fails_that_worker_with_connects_reason(user):  # noqa: F811
    execution = _execution(user)

    def refused(**kw):
        raise connect_tasks.ConnectTaskError("Task creation failed: could not update CommCare HQ.", 502)

    _run(execution, refused)
    rec = execution.results["10::a10"]
    assert execution.status == "completed_with_errors"
    assert rec["status"] == "failed" and rec["error"] == "Task creation failed: could not update CommCare HQ."


def test_a_retry_never_assigns_twice(user):  # noqa: F811
    execution = _execution(user)
    _run(execution, lambda **kw: {"assigned_task_id": "t-1"})
    WorkflowActionExecution.objects.filter(pk=execution.pk).update(status="running")
    calls = _run(execution, lambda **kw: {"assigned_task_id": "t-2"})
    assert calls == [] and execution.results["10::a10"]["connect_task_id"] == "t-1"


# ---------------------------------------------------------------------------
# The HTTP client, against Connect's contract (data_export/views.py AssignedTaskCreateView)
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _assign(status, body):
    with patch.object(httpx, "post", return_value=_Resp(status, body)) as post:
        try:
            return connect_tasks.assign_task("tok", 765, username="u1", task_type_id=DOC, due_date="2030-01-02"), post
        except connect_tasks.ConnectTaskError as e:
            return e, post


def test_assign_posts_connects_fields_with_the_persons_token(settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example/"
    out, post = _assign(201, {"assigned_task_id": "t-9"})
    assert out == {"assigned_task_id": "t-9"}
    url = post.call_args.args[0]
    assert url == "https://connect.example/export/opportunity/765/assigned_tasks/create/"
    assert post.call_args.kwargs["json"] == {"task_type_id": DOC, "username": "u1", "due_date": "2030-01-02"}
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


@pytest.mark.parametrize(
    "status,body,says",
    [
        (403, {"detail": "You do not have permission"}, "Sign out of Labs and sign in again"),
        (404, {"detail": "Not found."}, "program manager"),
        (409, {"error": "This task type is already assigned to this worker."}, "already assigned"),
        (400, {"username": ["No active worker with this username exists on this opportunity."]}, "No active worker"),
        (502, {"error": "Task creation failed: could not update CommCare HQ. Please try again."}, "CommCare HQ"),
    ],
)
def test_assign_turns_each_refusal_into_a_plain_reason(status, body, says):
    err, _ = _assign(status, body)
    assert isinstance(err, connect_tasks.ConnectTaskError) and err.status_code == status
    assert says in err.public_message


def test_task_types_follow_every_page(settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example"
    pages = [
        _Resp(200, {"next": "https://connect.example/export/opportunity/765/task_types/?last_id=1", "results": [1]}),
        _Resp(200, {"next": None, "results": [2]}),
    ]
    with patch.object(httpx, "get", side_effect=pages) as get:
        assert connect_tasks.list_task_types("tok", 765) == [1, 2]
    assert get.call_args_list[0].kwargs["headers"]["Accept"] == "application/json;version=2.0"
