"""OCSDataAccess.trigger_bot sends exactly one of prompt_text / message_text."""

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess


def _client():
    ocs = OCSDataAccess()
    http = MagicMock()
    http.post.return_value = MagicMock(text="{}", json=lambda: {"session_id": "1"})
    ocs._client = http
    return ocs, http


def _send(**kw):
    ocs, http = _client()
    with patch.object(OCSDataAccess, "check_token_valid", return_value=True):
        ocs.trigger_bot(identifier="asha", platform="commcare_connect", experiment_id="bot-1", **kw)
    return http.post.call_args.kwargs["json"]


def test_message_text_is_sent_verbatim_and_no_prompt_text():
    payload = _send(message_text="Hello Asha!", session_data={"coach_briefing": "B"})
    assert payload["message_text"] == "Hello Asha!"
    assert "prompt_text" not in payload
    assert payload["session_data"] == {"coach_briefing": "B"}


def test_prompt_text_is_still_sent_as_before():
    payload = _send(prompt_text="Remind them")
    assert payload["prompt_text"] == "Remind them"
    assert "message_text" not in payload


@pytest.mark.parametrize("kw", [{}, {"prompt_text": "a", "message_text": "b"}])
def test_exactly_one_of_the_two_is_required(kw):
    with pytest.raises(ValueError):
        _send(**kw)
