"""Coaching progress: who says how much work there was, and who says how much is done.

A worker flagged on three indicators gets one coaching task. The chatbot works the topics and
reports which it has finished. The question these two endpoints answer together is whether it
finished all of them — and the reason the answer is trustworthy is that the two halves come
from different places:

* the DENOMINATOR (`coaching_indicators`) is written by the dashboard onto the task at
  creation and never updated. The chatbot has no write path to a Labs task, so it cannot
  change how many topics there were.
* the NUMERATOR (`chatbot_topics_done`) is written by the chatbot into OCS participant data.

So a conversation that closes after one topic of three reports `completed` and one finished
topic, and the caller can see the gap rather than taking "completed" at its word. These tests
exist mostly to keep that asymmetry intact.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.labs.integrations.ocs.api_client import OCSAPIError
from connect_labs.workflow.views import chatbot_status_api, worker_tasks_api

EXP = "56a2cf10-1378-4967-9ea0-49ca64ce6ab9"


# --------------------------------------------------------------------------- the denominator


def _task(task_id=1, username="flw_a", **data):
    task = MagicMock()
    task.id = task_id
    task.data = {"username": username, "status": "investigating", "events": [], **data}
    return task


def _worker_tasks(tasks):
    request = RequestFactory().get("/x/")
    request.user = MagicMock(is_authenticated=True)
    access = MagicMock()
    access.get_tasks.return_value = tasks
    with patch("connect_labs.tasks.data_access.TaskDataAccess", return_value=access):
        response = worker_tasks_api(request)
    return response, json.loads(response.content)


class TestTheTaskCarriesWhatItWasCreatedToCover:
    def test_the_indicator_list_comes_back(self):
        _, body = _worker_tasks([_task(coaching_indicators=["image_missing", "rounded_weights"])])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == ["image_missing", "rounded_weights"]

    def test_order_is_preserved_not_sorted(self):
        """Worst first is the whole point of the order, so sorting would destroy the meaning."""
        _, body = _worker_tasks([_task(coaching_indicators=["zero_danger", "image_missing"])])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == ["zero_danger", "image_missing"]

    def test_a_task_created_before_this_existed_returns_an_empty_list(self):
        """Not an error and not a guess: an older task simply has no key."""
        _, body = _worker_tasks([_task()])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == []

    @pytest.mark.parametrize("junk", ["image_missing", {"a": 1}, 7, None])
    def test_a_non_list_value_is_dropped_rather_than_coerced(self, junk):
        """`task.data` is free-form JSON with no schema. Guessing at a malformed value would
        invent a denominator, which is worse than admitting we do not know."""
        _, body = _worker_tasks([_task(coaching_indicators=junk)])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == []

    def test_unusable_entries_are_dropped_but_usable_ones_survive(self):
        _, body = _worker_tasks([_task(coaching_indicators=["image_missing", 7, None, "  ", "zero_danger"])])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == ["image_missing", "zero_danger"]

    def test_duplicates_collapse_keeping_first_position(self):
        _, body = _worker_tasks([_task(coaching_indicators=["image_missing", "zero_danger", "image_missing"])])

        assert body["tasks"]["flw_a"][0]["coaching_indicators"] == ["image_missing", "zero_danger"]

    def test_every_other_field_still_comes_back(self):
        """This is an additive change to an endpoint three dashboards already read."""
        _, body = _worker_tasks([_task(coaching_indicators=["image_missing"], review="satisfied")])
        row = body["tasks"]["flw_a"][0]

        assert set(row) == {
            "task_id",
            "status",
            "title",
            "created_at",
            "review",
            "session_ids",
            "workflow_run_id",
            "coaching_indicators",
        }


# ----------------------------------------------------------------------------- the numerator


def _participant(identifier, status=None, topics=None, chatbot_id=EXP, extra_entries=()):
    entry_data = {}
    if status is not None:
        entry_data["chatbot_task_status"] = status
    if topics is not None:
        entry_data["chatbot_topics_done"] = topics
    data = [{"chatbot": "[Test] KMC Audit bot", "chatbot_id": chatbot_id, "data": entry_data}]
    data.extend(extra_entries)
    return {"id": "p1", "identifier": identifier, "platform": "commcare_connect", "data": data}


def _status(participants=None, token_valid=True, raises=None, experiment=EXP):
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
    return response, json.loads(response.content)


class TestTheBotReportsWhatItFinished:
    def test_finished_topics_come_back_in_order(self):
        _, body = _status([_participant("flw_a", "in_progress", ["image_missing", "rounded_weights"])])

        assert body["topics_done"]["flw_a"] == ["image_missing", "rounded_weights"]

    def test_a_worker_with_no_topics_yet_is_simply_absent(self):
        """Absent and "finished nothing" are the same thing here, and both read as zero."""
        _, body = _status([_participant("flw_a", "initiated")])

        assert body["topics_done"] == {}

    def test_a_bare_string_is_read_as_one_topic(self):
        """set_participant_data_key writes a string where the append helper writes a list.
        Reading that as zero topics would understate real progress."""
        _, body = _status([_participant("flw_a", "in_progress", "image_missing")])

        assert body["topics_done"]["flw_a"] == ["image_missing"]

    @pytest.mark.parametrize("junk", [{"a": 1}, 7])
    def test_anything_else_is_dropped(self, junk):
        _, body = _status([_participant("flw_a", "in_progress", junk)])

        assert body["topics_done"] == {}

    def test_repeats_collapse(self):
        """The list only grows, so a bot that marks the same topic twice is expected."""
        _, body = _status([_participant("flw_a", "in_progress", ["image_missing", "image_missing"])])

        assert body["topics_done"]["flw_a"] == ["image_missing"]

    def test_a_topic_this_endpoint_has_never_heard_of_still_reaches_the_caller(self):
        """The dashboard compares against the list IT wrote on the task. Filtering here would
        hide a mismatch that the reader needs to see."""
        _, body = _status([_participant("flw_a", "in_progress", ["something_invented"])])

        assert body["topics_done"]["flw_a"] == ["something_invented"]

    def test_another_chatbots_progress_is_not_counted(self):
        """Participant data is keyed by (participant, experiment). The FLW/mother bot's topics
        must never be shown as this bot's."""
        other = {
            "chatbot": "FLW mothers bot",
            "chatbot_id": "a4ce4094-other",
            "data": {"chatbot_topics_done": ["image_missing"]},
        }
        _, body = _status([_participant("flw_a", "initiated", extra_entries=(other,))])

        assert body["topics_done"] == {}

    def test_topics_are_collected_even_when_the_entry_carries_no_status(self):
        """The two values are written by different turns, so one can arrive first."""
        _, body = _status([_participant("flw_a", None, ["image_missing"])])

        assert body["topics_done"]["flw_a"] == ["image_missing"]
        assert body["statuses"] == {}


class TestTheGapIsVisible:
    def test_completed_with_fewer_topics_than_expected_is_still_reported_honestly(self):
        """The case this whole feature exists for: the bot says the conversation is finished
        after one topic. The endpoint reports both facts and lets the caller see the gap — it
        does not decide the conversation was fine, and it does not suppress the status."""
        _, status_body = _status([_participant("flw_a", "completed", ["image_missing"])])
        _, tasks_body = _worker_tasks([_task(coaching_indicators=["image_missing", "rounded_weights", "zero_danger"])])

        assert status_body["statuses"]["flw_a"] == "completed"
        assert status_body["topics_done"]["flw_a"] == ["image_missing"]
        assert len(tasks_body["tasks"]["flw_a"][0]["coaching_indicators"]) == 3


class TestTheShapeIsStableOnEveryPath:
    def test_no_ocs_auth_still_carries_the_key(self):
        _, body = _status(token_valid=False)

        assert body["ocs_auth_required"] is True
        assert body["topics_done"] == {}

    def test_an_ocs_failure_still_carries_the_key(self):
        response, body = _status(raises=OCSAPIError("upstream said no"))

        assert response.status_code == 502
        assert body["topics_done"] == {}
