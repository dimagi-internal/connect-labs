"""start_ai_session: the one way a task gets an OCS conversation."""

from unittest.mock import MagicMock, patch

from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT, start_ai_session


def _user():
    user = MagicMock(username="manager")
    user.get_display_name.return_value = "Manager"
    return user


def _task(opportunity_id=10):
    task = MagicMock(id=5, opportunity_id=opportunity_id, task_username="asha", flw_name="Asha", data={})
    return task


def test_a_real_bot_is_triggered_and_its_session_linked_to_the_task():
    task, tda = _task(), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session": {"id": 881}}
    with patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None):
        out = start_ai_session(_user(), tda, task, ocs=client, identifier="asha", experiment="bot-1", prompt_text="Hi")

    assert out == {"session_id": "881", "status": "completed", "message": "AI conversation initiated."}
    sent = client.trigger_bot.call_args.kwargs
    assert sent["experiment_id"] == "bot-1"
    assert sent["session_data"]["task_id"] == "5"
    task.add_ai_session.assert_called_once()
    assert task.add_ai_session.call_args.kwargs["session_id"] == "881"
    tda.save_task.assert_called_once_with(task)
    client.close.assert_not_called()  # the caller's client; the caller closes it


def test_with_no_client_one_is_made_from_the_users_stored_token_and_closed():
    task, tda, user = _task(), MagicMock(), _user()
    client = MagicMock()
    client.trigger_bot.return_value = {"session_id": "9"}
    with (
        patch("connect_labs.tasks.ai_sessions.OCSDataAccess", return_value=client) as made,
        patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None),
    ):
        out = start_ai_session(user, tda, task, identifier="asha", experiment="bot-1", prompt_text="Hi")
    made.assert_called_once_with(user=user)
    client.close.assert_called_once()
    assert out["session_id"] == "9"


def test_the_synthetic_bot_never_calls_ocs():
    task, tda = _task(), MagicMock()
    with (
        patch("connect_labs.tasks.ai_sessions.OCSDataAccess") as ocs,
        patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None),
        patch("connect_labs.labs.synthetic.manager_flow_views._coaching_conversation", return_value=[{"m": 1}]),
    ):
        out = start_ai_session(_user(), tda, task, identifier="asha", experiment=SYNTHETIC_BOT, prompt_text="Hi")

    ocs.assert_not_called()
    assert out["session_id"] == "synthetic-coaching-session"
    assert task.data["ocs_conversation"] == [{"m": 1}]
    tda.add_ai_session.assert_called_once()
