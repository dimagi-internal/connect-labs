"""The coaching bot's own status per worker, read from OCS participant data.

Two statuses travel together in this feature and they are easy to conflate: the REVIEWER's
verdict (`task.data.review`, set in Labs) and the BOT's status (`chatbot_task_status`, set by
the bot in OCS). This endpoint is only the second one.

Reading participant data rather than session state is the load-bearing choice. `update-user-data`
writes to participant data, and OCS keys it by (participant, experiment) — so it is both the
original and the only place that answers "what does THIS bot think". Session state is a copy a
Python node has to maintain by hand, and a bot that forgets leaves it stale.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.integrations.ocs.api_client import OCSAPIError
from connect_labs.workflow.views import CHATBOT_STATUS_ORDER, chatbot_status_api

EXP = "56a2cf10-1378-4967-9ea0-49ca64ce6ab9"


def _participant(identifier, status, chatbot_id=EXP, extra_entries=()):
    data = []
    if status is not None:
        data.append(
            {
                "chatbot": "[Test] KMC Audit bot",
                "chatbot_id": chatbot_id,
                "data": {"chatbot_task_status": status},
                "connect_channel_id": "c1",
            }
        )
    data.extend(extra_entries)
    return {
        "id": "p1",
        "identifier": identifier,
        "name": "",
        "platform": "commcare_connect",
        "remote_id": "",
        "data": data,
    }


def _call(participants=None, token_valid=True, raises=None, experiment=EXP):
    request = RequestFactory().get("/x/", {"experiment": experiment} if experiment else {})
    request.user = MagicMock(is_authenticated=True)
    client = MagicMock()
    client.check_token_valid.return_value = token_valid
    if raises is not None:
        client.list_participants.side_effect = raises
    else:
        client.list_participants.return_value = participants or []
    with patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess", return_value=client):
        response = chatbot_status_api(request)
    return response, json.loads(response.content), client


class TestItReadsTheBotsStatus:
    def test_a_workers_status_comes_back(self):
        response, body, _ = _call([_participant("flw_a", "in_progress")])

        assert response.status_code == 200
        assert body["statuses"] == {"flw_a": "in_progress"}

    @pytest.mark.parametrize("status", CHATBOT_STATUS_ORDER)
    def test_every_declared_status_survives_the_round_trip(self, status):
        """Parametrised off the vocabulary, so adding a state the bot can report without
        teaching this endpoint about it fails here rather than showing a blank column."""
        _, body, _ = _call([_participant("flw_a", status)])

        assert body["statuses"]["flw_a"] == status

    def test_it_asks_OCS_for_the_right_chatbot(self):
        """Participant data is keyed by (participant, experiment). Asking for the wrong
        experiment returns another bot's opinion, which would be silently plausible."""
        _, _, client = _call([_participant("flw_a", "initiated")])

        client.list_participants.assert_called_once_with(EXP)

    def test_identifiers_are_lowercased(self):
        """The dashboard rows are not lowercase and open_tasks_api lowercases its keys;
        diverging here would make every mixed-case worker look like it has no status."""
        _, body, _ = _call([_participant("FLW_Mixed", "completed")])

        assert "flw_mixed" in body["statuses"]

    def test_several_workers_are_kept_apart(self):
        _, body, _ = _call([_participant("flw_a", "initiated"), _participant("flw_b", "completed")])

        assert body["statuses"] == {"flw_a": "initiated", "flw_b": "completed"}


class TestItDoesNotInventAStatus:
    def test_a_participant_with_no_status_is_omitted(self):
        """Absent is not the same as not_started: the column must be able to show nothing."""
        _, body, _ = _call([_participant("flw_a", None)])

        assert body["statuses"] == {}

    def test_an_empty_status_string_is_omitted(self):
        _, body, _ = _call([_participant("flw_a", "   ")])

        assert body["statuses"] == {}

    def test_another_chatbots_entry_is_ignored(self):
        """A worker also talks to the FLW/mother bot. That bot's status must never be shown
        as this one's — they are separate records and mean different things."""
        other = {
            "chatbot": "FLW mothers bot",
            "chatbot_id": "a4ce4094-other",
            "data": {"chatbot_task_status": "completed"},
        }
        _, body, _ = _call([_participant("flw_a", None, extra_entries=(other,))])

        assert body["statuses"] == {}

    def test_a_participant_with_no_identifier_is_skipped(self):
        _, body, _ = _call([_participant("", "in_progress")])

        assert body["statuses"] == {}


class TestConflictingEntriesMoveForwardOnly:
    def test_the_furthest_state_wins(self):
        """Two entries for one worker should not happen. If they do, showing the earlier
        state would make a conversation appear to move backwards, which is the worse error."""
        stale = {"chatbot": "b", "chatbot_id": EXP, "data": {"chatbot_task_status": "initiated"}}
        _, body, _ = _call([_participant("flw_a", "completed", extra_entries=(stale,))])

        assert body["statuses"]["flw_a"] == "completed"

    def test_an_unknown_value_never_displaces_a_known_one(self):
        junk = {"chatbot": "b", "chatbot_id": EXP, "data": {"chatbot_task_status": "banana"}}
        _, body, _ = _call([_participant("flw_a", "in_progress", extra_entries=(junk,))])

        assert body["statuses"]["flw_a"] == "in_progress"


class TestItFailsVisiblyRatherThanQuietly:
    def test_no_OCS_auth_says_so_and_returns_no_data(self):
        """Showing a blank column and an unauthorised read look identical to a user. The
        dashboard needs to be able to tell them apart to prompt a sign-in."""
        response, body, _ = _call(token_valid=False)

        assert response.status_code == 200
        assert body["ocs_auth_required"] is True
        assert body["statuses"] == {}
        assert body["login_url"]

    def test_an_OCS_failure_is_a_502_with_no_statuses(self):
        response, body, _ = _call(raises=OCSAPIError("upstream said no"))

        assert response.status_code == 502
        assert body["statuses"] == {}

    def test_a_missing_experiment_is_rejected(self):
        """Without it the endpoint would have to guess which bot, and guessing wrong returns
        a different bot's status."""
        response, _, _ = _call(experiment=None)

        assert response.status_code == 400
