"""Workflow actions: declared once, previewed, confirmed, run in the background."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from connect_labs.workflow import actions
from connect_labs.workflow.actions import ActionError, commit, declared_actions, preview
from connect_labs.workflow.models import WorkflowActionExecution

COACH = {
    "key": "initiate_ai_coach",
    "type": "start_ocs_outreach",
    "label": "Initiate AI coach",
    "defaults": {"prompt": "Talk with them about their week.", "bot": "bot-1"},
}
TASK = {"key": "follow_up", "type": "create_task", "label": "Follow up"}


def _definition(*entries):
    return SimpleNamespace(
        id=7, data={"config": {"actions": list(entries)}}, template_type=None, opportunity_ids=[10, 11], name="Report"
    )


RUN = SimpleNamespace(id=70, opportunity_id=None, program_id=25)


def _wda():
    wda = MagicMock()
    wda.get_workers.side_effect = lambda oid: [
        {"username": f"a{oid}", "name": f"Asha {oid}"},
        {"username": f"b{oid}", "name": f"Binta {oid}"},
    ]
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


@pytest.fixture
def bots(monkeypatch):
    monkeypatch.setattr(
        actions,
        "_ocs_bots",
        lambda user, request: [{"id": "bot-1", "name": "Coach"}, {"id": "bot-2", "name": "Other"}],
    )


# ---------------------------------------------------------------------------
# Declaring
# ---------------------------------------------------------------------------


def test_a_workflow_offers_only_actions_of_a_known_type_once_each():
    offered = declared_actions(
        {
            "config": {
                "actions": [
                    COACH,
                    {"key": "initiate_ai_coach", "type": "create_task"},  # duplicate key
                    {"key": "create_audit", "label": "Create audit"},  # a render-code catalog entry, no type
                    TASK,
                ]
            }
        }
    )
    assert [(a["key"], a["type"], a["label"]) for a in offered] == [
        ("initiate_ai_coach", "start_ocs_outreach", "Initiate AI coach"),
        ("follow_up", "create_task", "Follow up"),
    ]
    assert offered[0]["parameters"]["required"] == ["workers"]


def test_no_workflow_offers_actions_unless_it_declares_them():
    assert (
        declared_actions({"config": {"templateType": "indicator_programme_report"}}, "indicator_programme_report")
        == []
    )


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_a_preview_names_each_worker_and_the_bot_and_carries_a_token(user, real_opps, bots):
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(COACH),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10", "prompt": "Weighing is red."}, {"key": "11::b11"}]},
    )
    assert out["needs"] == []
    assert out["bot"] == {"id": "bot-1", "name": "Coach"}
    assert [(w["name"], w["prompt"]) for w in out["workers"]] == [
        ("Asha 10", "Weighing is red."),
        ("Binta 11", "Talk with them about their week."),
    ]
    assert out["summary"] == "Initiate AI coach for 2 workers"
    assert out["confirm"]


def test_a_preview_without_a_bot_asks_for_one_and_cannot_be_confirmed(user, real_opps, bots):
    coach = {**COACH, "defaults": {"prompt": "hi"}}
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(coach),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10"}]},
    )
    assert out["needs"] == ["bot"]
    assert [b["id"] for b in out["bot_choices"]] == ["bot-1", "bot-2"]
    assert "confirm" not in out


def test_a_preview_for_someone_without_ocs_says_to_connect(user, real_opps, monkeypatch):
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: None)
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(COACH),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10"}]},
    )
    assert out["needs"] == ["connect_ocs"]
    assert out["connect_url"] == "/labs/ocs/initiate/"
    assert "confirm" not in out


def test_on_synthetic_opportunities_no_ocs_is_needed(user, monkeypatch):
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: object())
    monkeypatch.setattr(actions, "_ocs_bots", MagicMock(side_effect=AssertionError("OCS must not be asked")))
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(COACH),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10"}]},
    )
    assert out["synthetic"] is True
    assert out["arguments"]["bot"] == "synthetic-muac-coaching"
    assert out["confirm"]


def test_an_action_the_workflow_does_not_offer_is_refused(user):
    with pytest.raises(ActionError) as e:
        preview(user, wda=_wda(), run=RUN, definition=_definition(TASK), key="initiate_ai_coach", arguments={})
    assert e.value.code == "not_offered"


def test_a_worker_not_on_the_run_is_refused(user):
    with pytest.raises(ActionError, match="not workers on this run"):
        preview(
            user,
            wda=_wda(),
            run=RUN,
            definition=_definition(TASK),
            key="follow_up",
            arguments={"workers": [{"key": "10::zed"}]},
        )


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def _previewed(user, definition, key, arguments):
    return preview(user, wda=_wda(), run=RUN, definition=definition, key=key, arguments=arguments)


def _commit(user, definition, key, arguments, confirm):
    return commit(
        user, wda=_wda(), run=RUN, definition=definition, key=key, arguments=arguments, confirm=confirm, via="mcp"
    )


def test_a_confirmed_preview_is_recorded_and_queued_once(user, real_opps, bots, django_capture_on_commit_callbacks):
    definition = _definition(COACH)
    p = _previewed(user, definition, "initiate_ai_coach", {"workers": [{"key": "10::a10"}]})
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay") as delay:
        with django_capture_on_commit_callbacks(execute=True):
            execution = _commit(user, definition, "initiate_ai_coach", p["arguments"], p["confirm"])
    delay.assert_called_once_with(execution.pk)
    assert (execution.action_key, execution.action_type, execution.via) == (
        "initiate_ai_coach",
        "start_ocs_outreach",
        "mcp",
    )
    assert execution.arguments["bot"] == "bot-1"
    with pytest.raises(ActionError) as again:
        _commit(user, definition, "initiate_ai_coach", p["arguments"], p["confirm"])
    assert again.value.code == "confirm_used"
    assert WorkflowActionExecution.objects.count() == 1


def test_arguments_changed_after_the_preview_are_refused(user, real_opps, bots):
    definition = _definition(COACH)
    p = _previewed(user, definition, "initiate_ai_coach", {"workers": [{"key": "10::a10"}]})
    widened = {**p["arguments"], "workers": [{"key": "10::a10"}, {"key": "10::b10"}]}
    with pytest.raises(ActionError) as e:
        _commit(user, definition, "initiate_ai_coach", widened, p["confirm"])
    assert e.value.code == "confirm_mismatch"
    assert not WorkflowActionExecution.objects.exists()


def test_a_preview_cannot_be_confirmed_by_someone_else(user, real_opps, bots):
    definition = _definition(COACH)
    p = _previewed(user, definition, "initiate_ai_coach", {"workers": [{"key": "10::a10"}]})
    other = get_user_model().objects.create_user(username="other", password="p")
    with pytest.raises(ActionError) as e:
        _commit(other, definition, "initiate_ai_coach", p["arguments"], p["confirm"])
    assert e.value.code == "confirm_mismatch"


def test_acting_without_a_preview_is_refused(user, real_opps, bots):
    with pytest.raises(ActionError) as e:
        _commit(user, _definition(COACH), "initiate_ai_coach", {"workers": [{"key": "10::a10"}]}, "made-up")
    assert e.value.code == "confirm_invalid"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execution(user, action_type="start_ocs_outreach", keys=("10::a10", "11::b11"), **kw):
    args = {
        "workers": [{"key": k} for k in keys],
        "prompt": "Talk with them.",
        "bot": "bot-1",
        "priority": "medium",
        "title": "Initiate AI coach",
    }
    return WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        program_id=25,
        action_key="initiate_ai_coach",
        action_type=action_type,
        arguments=args,
        **kw,
    )


def _fake_tasks():
    made, scopes = [], []

    def factory(**kwargs):
        scopes.append(kwargs.get("opportunity_id"))
        tda = MagicMock()

        def create_task(**kw):
            made.append(kw)
            return MagicMock(id=100 + len(made) - 1)

        tda.create_task.side_effect = create_task
        tda.get_task.side_effect = lambda task_id: MagicMock(id=task_id)
        return tda

    return factory, made, scopes


def test_each_worker_gets_a_task_in_their_own_opportunity_and_a_conversation(user):
    execution = _execution(user)
    factory, made, scopes = _fake_tasks()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess") as ocs,
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s1"}) as start,
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    assert execution.status == "completed"
    assert scopes == [10, 11]
    assert [(m["username"], m["opportunity_id"], m["workflow_run_id"]) for m in made] == [
        ("a10", 10, 70),
        ("b11", 11, 70),
    ]
    assert [c.kwargs["identifier"] for c in start.call_args_list] == ["a10", "b11"]
    # Run as the person it was confirmed by, from their stored OCS token.
    ocs.assert_called_once_with(user=user)
    assert execution.results["10::a10"]["session_id"] == "s1"


def test_a_worker_that_fails_is_recorded_and_the_rest_carry_on(user):
    execution = _execution(user)
    factory, made, _ = _fake_tasks()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch(
            "connect_labs.tasks.ai_sessions.start_ai_session",
            side_effect=[RuntimeError("ocs down"), {"session_id": "s2"}],
        ),
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    assert execution.status == "completed_with_errors"
    assert execution.results["10::a10"] == {**execution.results["10::a10"], "status": "failed", "task_id": 100}
    assert execution.results["11::b11"]["status"] == "ok"


def test_a_redelivered_run_resumes_without_repeating_a_worker(user):
    execution = _execution(user, results={"10::a10": {"status": "ok", "task_id": 55}}, status="running")
    factory, made, _ = _fake_tasks()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s"}),
    ):
        actions.execute(execution.pk)
    assert [m["username"] for m in made] == ["b11"]


def test_without_a_connect_login_the_run_fails_and_says_why(user):
    from connect_labs.labs.connect_tokens import ConnectTokenError

    execution = _execution(user, action_type="create_task")
    with patch("connect_labs.labs.connect_tokens.get_valid_access_token", side_effect=ConnectTokenError("none")):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    assert execution.status == "failed"
    assert "Connect login" in execution.error


def test_synthetic_outreach_never_opens_ocs(user):
    execution = _execution(user)
    execution.arguments = {**execution.arguments, "bot": "synthetic-muac-coaching"}
    execution.save()
    factory, _, _ = _fake_tasks()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess") as ocs,
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "synthetic"}) as start,
    ):
        actions.execute(execution.pk)
    ocs.assert_not_called()
    assert all(c.kwargs["ocs"] is None for c in start.call_args_list)


# ---------------------------------------------------------------------------
# QA redirect (deliver_to) and coached indicators
# ---------------------------------------------------------------------------


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(username="qa", password="p", email="qa@dimagi.com")


def _preview_coach(who, arguments):
    return preview(
        who, wda=_wda(), run=RUN, definition=_definition(COACH), key="initiate_ai_coach", arguments=arguments
    )


def test_a_qa_redirect_preview_says_plainly_where_the_conversation_goes(staff, real_opps, bots):
    out = _preview_coach(staff, {"workers": [{"key": "10::a10"}], "deliver_to": "qa_connect"})
    assert out["deliver_to"] == "qa_connect"
    assert out["workers"][0]["sending_to"] == "sending to: qa_connect (QA, on behalf of a10)"
    assert "QA" in out["summary"] and "qa_connect" in out["summary"]
    assert out["arguments"]["deliver_to"] == "qa_connect"
    assert out["confirm"]


def test_the_confirm_token_is_bound_to_the_qa_recipient(staff, real_opps, bots):
    definition = _definition(COACH)
    p = _previewed(staff, definition, "initiate_ai_coach", {"workers": [{"key": "10::a10"}], "deliver_to": "qa_a"})
    with pytest.raises(ActionError) as e:
        _commit(staff, definition, "initiate_ai_coach", {**p["arguments"], "deliver_to": "qa_b"}, p["confirm"])
    assert e.value.code == "confirm_mismatch"


def test_a_qa_redirect_is_refused_for_anyone_not_dimagi_staff(user, real_opps, bots):
    user.email = "someone@partner.org"
    user.save()
    with pytest.raises(ActionError) as e:
        _preview_coach(user, {"workers": [{"key": "10::a10"}], "deliver_to": "qa_connect"})
    assert e.value.code == "forbidden"


def test_a_qa_redirect_is_refused_for_several_workers(staff, real_opps, bots):
    with pytest.raises(ActionError, match="one worker at a time"):
        _preview_coach(staff, {"workers": [{"key": "10::a10"}, {"key": "11::b11"}], "deliver_to": "qa_connect"})


def test_a_qa_redirect_on_synthetic_opportunities_uses_a_real_bot(staff, monkeypatch, bots):
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: object())
    out = _preview_coach(staff, {"workers": [{"key": "10::a10"}], "deliver_to": "qa_connect"})
    assert "synthetic" not in out
    assert out["bot"] == {"id": "bot-1", "name": "Coach"}
    assert out["arguments"]["bot"] == "bot-1"


def test_a_declaration_cannot_default_a_qa_redirect():
    problems = actions.declaration_problems([{**COACH, "defaults": {**COACH["defaults"], "deliver_to": "qa"}}])
    assert any("deliver_to" in p for p in problems)


def test_a_qa_redirect_execution_sends_to_the_recipient_for_the_worker(staff):
    execution = _execution(staff, keys=("10::a10",))
    execution.arguments = {**execution.arguments, "deliver_to": "qa_connect"}
    execution.save()
    factory, made, _ = _fake_tasks()
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s1"}) as start,
    ):
        actions.execute(execution.pk)
    assert made[0]["username"] == "a10"  # the task is the worker's
    sent = start.call_args.kwargs
    assert (sent["identifier"], sent["on_behalf_of"]) == ("qa_connect", "a10")


def test_coached_indicators_are_recorded_on_a_new_task(user):
    execution = _execution(user, keys=("10::a10", "11::b11"))
    execution.arguments = {
        **execution.arguments,
        "workers": [{"key": "10::a10", "indicators": ["SF_P1", "SF_P3"]}, {"key": "11::b11"}],
    }
    execution.save()
    tdas = []

    def factory(**kwargs):
        tda = MagicMock()
        tda.create_task.side_effect = lambda **kw: SimpleNamespace(id=100 + len(tdas), data={"title": "t"})
        tdas.append(tda)
        return tda

    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", side_effect=factory),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s"}),
    ):
        actions.execute(execution.pk)
    saved = tdas[0].save_task.call_args.args[0]
    assert saved.data == {"title": "t", "coaching_indicators": ["SF_P1", "SF_P3"]}
    tdas[1].save_task.assert_not_called()  # no indicators given, nothing to record


# ---------------------------------------------------------------------------
# Checking a declaration when it is saved
# ---------------------------------------------------------------------------


def test_a_valid_declaration_has_no_problems():
    assert actions.declaration_problems([COACH, TASK]) == []
    assert actions.declaration_problems(None) == []


def test_a_declaration_is_checked_against_the_known_types_and_their_schemas():
    problems = actions.declaration_problems(
        [
            {"key": "coach", "type": "start_ocs_outreach", "defaults": {"priority": "urgent"}},
            {"key": "coach", "type": "create_task"},
            {"key": "x", "type": "send_sms"},
            {"key": "has space", "type": "create_task"},
            {"key": "w", "type": "create_task", "defaults": {"workers": [{"key": "10::a"}]}},
        ]
    )
    assert any("defaults" in p and "urgent" in p for p in problems)
    assert any("used twice" in p for p in problems)
    assert any("'send_sms' is not an action type" in p for p in problems)
    assert any("must be a slug" in p for p in problems)
    assert any("cannot name workers" in p for p in problems)


def test_every_templates_declared_actions_are_valid():
    from connect_labs.workflow.templates import TEMPLATES

    for key, template in TEMPLATES.items():
        declared = ((template.get("definition") or {}).get("config") or {}).get("actions")
        assert actions.declaration_problems(declared) == [], key


def test_every_action_type_can_execute():
    for kind, action_type in actions.ACTION_TYPES.items():
        assert callable(action_type.execute), kind
        assert action_type.parameters["required"] == ["workers"], kind
