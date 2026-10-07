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


def test_the_default_platform_is_a_real_OCS_channel():
    """OCS's ChannelPlatform enum has no 'connect_labs' choice -- sending it 500s on OCS's
    side (an unhandled ValueError) before it ever gets to "no channel for this platform".
    A caller that omits `platform` (workflow/actions.py's `_execute_ocs_outreach` is the one
    that does) must still land on a channel OCS actually recognises. Caught once already
    (PR #797 renamed this same substring by accident); this pins it against a repeat."""
    task, tda = _task(), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session_id": "1"}
    with patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None):
        start_ai_session(_user(), tda, task, ocs=client, identifier="asha", experiment="bot-1", prompt_text="Hi")

    assert client.trigger_bot.call_args.kwargs["platform"] == "commcare_connect"


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


def test_a_qa_redirect_goes_to_the_staff_member_and_says_so():
    task, tda = _task(), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session": {"id": 77}}
    with patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None):
        start_ai_session(
            _user(),
            tda,
            task,
            ocs=client,
            identifier="qa_staff",
            on_behalf_of="asha",
            experiment="bot-1",
            prompt_text="Hi",
        )

    sent = client.trigger_bot.call_args.kwargs
    assert sent["identifier"] == "qa_staff"
    assert sent["session_data"]["username"] == "asha"  # still the worker's task
    assert sent["session_data"]["qa_recipient"] == "qa_staff"
    assert sent["session_data"]["on_behalf_of"] == "asha"
    assert sent["session_data"]["created_by"] == "manager"
    params = task.add_ai_session.call_args.kwargs["session_params"]
    assert (params["qa_recipient"], params["on_behalf_of"]) == ("qa_staff", "asha")


def test_without_a_redirect_the_session_data_carries_no_qa_fields():
    task, tda = _task(), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session_id": "1"}
    with patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None):
        start_ai_session(_user(), tda, task, ocs=client, identifier="asha", experiment="bot-1", prompt_text="Hi")

    assert "qa_recipient" not in client.trigger_bot.call_args.kwargs["session_data"]


def test_a_qa_redirect_on_a_synthetic_opportunity_really_calls_ocs():
    """The point of the redirect is to QA the actual bot from a synthetic report, so the
    canned-transcript short circuit must not swallow it."""
    task, tda = _task(opportunity_id=10001), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session_id": "42"}
    with (
        patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=object()),
        patch("connect_labs.labs.synthetic.manager_flow_views._coaching_conversation") as canned,
    ):
        out = start_ai_session(
            _user(),
            tda,
            task,
            ocs=client,
            identifier="qa_staff",
            on_behalf_of="asha",
            experiment="bot-1",
            prompt_text="Hi",
        )

    client.trigger_bot.assert_called_once()
    canned.assert_not_called()
    assert out["session_id"] == "42"


def test_a_synthetic_opportunity_without_a_redirect_still_gets_the_canned_conversation():
    task, tda = _task(opportunity_id=10001), MagicMock()
    client = MagicMock()
    with (
        patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=object()),
        patch("connect_labs.labs.synthetic.manager_flow_views._coaching_conversation", return_value=[]),
    ):
        out = start_ai_session(_user(), tda, task, ocs=client, identifier="asha", experiment="bot-1", prompt_text="Hi")

    client.trigger_bot.assert_not_called()
    assert out["session_id"] == "synthetic-coaching-session"


BRIEFING = (
    "BRIEFING (system text — do not show to the worker)\n"
    "Programme: Spark Facilitators\n"
    "Worker: Tiyamike Kalinde\n"
    "Topics, most important first:\n"
    "1. Step 7 on time [SF_P7] — 0 of 3 (0%), band red\n"
    "Follow your conversation steps from the opening."
)


def _trigger(prompt_text, **kw):
    task, tda = _task(), MagicMock()
    client = MagicMock()
    client.trigger_bot.return_value = {"session_id": "1"}
    with patch("connect_labs.labs.synthetic.registry.get_synthetic_opp", return_value=None):
        start_ai_session(
            _user(), tda, task, ocs=client, identifier="asha", experiment="bot-1", prompt_text=prompt_text, **kw
        )
    return client.trigger_bot.call_args.kwargs


def test_a_briefing_opens_with_a_fixed_message_and_rides_in_session_state():
    """OCS's prompt_text never reaches the bot's pipeline: a generic LLM writes the opening
    from it, and the raw briefing reached a worker (OCS session f931d8ea, 2026-10-07)."""
    sent = _trigger(BRIEFING, start_new_session=True)
    assert "prompt_text" not in sent
    assert sent["message_text"] == (
        "Hello Tiyamike! This is a short, friendly check-in about how your work has been going. "
        "Is now a good time to talk for a few minutes?"
    )
    assert sent["session_data"]["coach_briefing"] == BRIEFING
    # The links back to Connect are kept.
    assert sent["session_data"]["task_id"] == "5"
    assert sent["session_data"]["username"] == "asha"
    assert sent["session_data"]["created_by"] == "manager"


def test_a_briefed_worker_known_only_by_a_username_is_greeted_without_a_name():
    sent = _trigger(BRIEFING.replace("Tiyamike Kalinde", "spark_fac_07"))
    assert sent["message_text"].startswith("Hello! This is a short")


def test_a_briefing_on_a_qa_redirect_keeps_the_redirect_fields():
    sent = _trigger(BRIEFING, on_behalf_of="asha")
    assert sent["session_data"]["on_behalf_of"] == "asha"
    assert sent["session_data"]["coach_briefing"] == BRIEFING


def test_any_other_prompt_keeps_the_prompt_text_path():
    sent = _trigger("Ask how the KMC visits went this week.")
    assert sent["prompt_text"] == "Ask how the KMC visits went this week."
    assert "message_text" not in sent
    assert "coach_briefing" not in sent["session_data"]
