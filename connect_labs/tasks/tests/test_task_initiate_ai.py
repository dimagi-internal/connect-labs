"""The task page's "Initiate AI Assistant" endpoint.

The platform default here matters because a request that omits `platform` (the modal's own
JS default, or any future caller) has to land on a channel OCS actually recognises -- OCS's
ChannelPlatform enum has no "connect_labs" choice, so sending it 500s on OCS's side before
"no channel for this platform" is even reached. PR #797 accidentally renamed this literal
along with the Python package; this pins it and checks that an OCS failure is no longer
reported as a bare, undiagnosable 500.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from connect_labs.labs.integrations.ocs.api_client import OCSAPIError
from connect_labs.tasks.views import task_initiate_ai


def _task(task_id=5, opportunity_id=1):
    task = MagicMock(id=task_id, opportunity_id=opportunity_id, task_username="asha", flw_name="Asha", data={})
    return task


def _post(body, start_ai_session_result=None, start_ai_session_error=None):
    request = RequestFactory().post("/x/", data=json.dumps(body), content_type="application/json")
    request.user = MagicMock(is_authenticated=True, get_display_name=lambda: "Manager")
    access = MagicMock()
    access.get_task.return_value = _task()
    with (
        patch("connect_labs.tasks.views.TaskDataAccess", return_value=access),
        patch("connect_labs.tasks.views.OCSDataAccess", return_value=MagicMock()),
        patch("connect_labs.tasks.views.start_ai_session") as started,
    ):
        if start_ai_session_error is not None:
            started.side_effect = start_ai_session_error
        else:
            started.return_value = start_ai_session_result or {
                "session_id": "1",
                "status": "completed",
                "message": "AI conversation initiated.",
            }
        response = task_initiate_ai(request, 5)
    return response, started


class TestTheDefaultPlatformIsARealOCSChannel:
    def test_a_request_with_no_platform_defaults_to_commcare_connect(self):
        _, started = _post({"identifier": "asha", "experiment": "bot-1", "prompt_text": "Hi"})

        assert started.call_args.kwargs["platform"] == "commcare_connect"

    def test_an_explicit_platform_is_still_honoured(self):
        _, started = _post({"identifier": "asha", "experiment": "bot-1", "prompt_text": "Hi", "platform": "whatsapp"})

        assert started.call_args.kwargs["platform"] == "whatsapp"


class TestAnOCSFailureIsDiagnosableRatherThanABareFiveHundred:
    def test_ocs_rejecting_the_request_is_a_502_naming_ocs(self):
        response, _ = _post(
            {"identifier": "asha", "experiment": "bot-1", "prompt_text": "Hi"},
            start_ai_session_error=OCSAPIError("Failed to trigger bot: 'connect_labs' is not a valid choice."),
        )

        body = json.loads(response.content)
        assert response.status_code == 502
        assert "connect_labs" in body["error"]
        assert "Open Chat Studio" in body["error"]


class TestStartingParticipantData:
    BASE = {"identifier": "asha", "experiment": "bot-1", "prompt_text": "Hi"}

    def test_it_is_passed_through(self):
        reset = {"chatbot_task_status": "not_started", "chatbot_topics_done": []}
        _, started = _post({**self.BASE, "participant_data": reset})

        assert started.call_args.kwargs["participant_data"] == reset

    def test_absent_means_none(self):
        _, started = _post(self.BASE)

        assert started.call_args.kwargs["participant_data"] is None

    def test_a_non_object_is_refused_before_ocs_is_called(self):
        response, started = _post({**self.BASE, "participant_data": ["not", "an", "object"]})

        assert response.status_code == 400
        started.assert_not_called()

    def test_a_nested_object_is_refused(self):
        response, started = _post({**self.BASE, "participant_data": {"a": {"b": 1}}})

        assert response.status_code == 400
        started.assert_not_called()

    def test_too_many_keys_are_refused(self):
        response, started = _post({**self.BASE, "participant_data": {f"k{i}": "v" for i in range(21)}})

        assert response.status_code == 400
        started.assert_not_called()
