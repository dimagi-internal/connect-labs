"""Labs, not each agent, says what a person can do with a coaching preview."""

from connect_labs.mcp.tools.workflow_run import _preview_choices


def _out(**over):
    out = {
        "type": "start_ocs_outreach",
        "needs": [],
        "workers": [{"key": "10092::cr_g02", "name": "Ibrahim Lawal"}],
        "arguments": {"workers": [{"key": "10092::cr_g02"}]},
        "qa_redirect": True,
    }
    out.update(over)
    return out


def _labels(choices):
    return [c["label"] for c in choices]


def test_one_worker_gets_send_qa_and_not_yet():
    assert _labels(_preview_choices(_out())) == ["Send to Ibrahim Lawal", "Send to me (QA test)", "Not yet"]


def test_qa_is_offered_only_to_whom_labs_allows_it():
    assert _labels(_preview_choices(_out(qa_redirect=False))) == ["Send to Ibrahim Lawal", "Not yet"]


def test_a_synthetic_send_says_no_message_goes():
    send = _preview_choices(_out(synthetic=True))[0]
    assert "no message is sent" in send["do"]


def test_the_qa_choice_says_how_to_redirect():
    qa = _preview_choices(_out())[1]
    assert "deliver_to" in qa["do"] and "PersonalID username" in qa["do"]


def test_no_choices_while_needs_are_open_for_several_workers_or_another_type():
    assert _preview_choices(_out(needs=["bot"])) is None
    assert _preview_choices(_out(workers=[{"key": "a"}, {"key": "b"}])) is None
    assert _preview_choices(_out(type="create_task")) is None


def test_a_qa_preview_is_already_chosen():
    assert _preview_choices(_out(arguments={"deliver_to": "jon.1"})) is None
