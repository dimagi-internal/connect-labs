"""Chatbot tasks in the audit reviews' Settings, and the Open Chat Studio bots a person can pick from.

A task names a bot and where it is offered; what the bot says lives in the bot (OCS). The send itself is
the workflow's own ``start_ocs_outreach`` action, run through Labs' confirm dialog.
"""

import json

import pytest
from django.test import RequestFactory

from connect_labs.audit_reviews import api_views, service
from connect_labs.audit_reviews.tests.test_api import dao  # noqa: F401  (fixture)
from connect_labs.audit_reviews.tests.test_api import WORKFLOW, _user
from connect_labs.audit_reviews.tests.test_service import kc_state
from connect_labs.audit_reviews.tests.test_settings import CASE, live  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


INVESTIGATION = {
    "id": "kc_investigation",
    "name": "KC investigation",
    "kind": "investigation",
    "bot": "bot-1",
    "botName": "KC investigator",
    "levels": ["flw"],
}
SURVEY = {"id": "quick_survey", "name": "Quick survey", "kind": "survey", "bot": "bot-2", "levels": ["llo", "program"]}


def test_chatbot_tasks_are_kept_for_the_page(live):  # noqa: F811
    clean = service.validate_settings(live, {"chatbotTasks": [INVESTIGATION, {**SURVEY, "extra": "dropped"}]})
    assert clean["chatbotTasks"] == [
        INVESTIGATION,
        {
            "id": "quick_survey",
            "name": "Quick survey",
            "kind": "survey",
            "bot": "bot-2",
            "botName": "",
            "levels": ["llo", "program"],
            "askQuestions": True,
        },
    ]


def test_no_chatbot_tasks_is_an_empty_list(live):  # noqa: F811
    assert service.validate_settings(live, {})["chatbotTasks"] == []


def test_a_standing_survey_does_not_ask_for_questions(live):  # noqa: F811
    clean = service.validate_settings(live, {"chatbotTasks": [{**SURVEY, "askQuestions": False}]})
    assert clean["chatbotTasks"][0]["askQuestions"] is False


@pytest.mark.parametrize(
    "bad, match",
    [
        ({"chatbotTasks": {}}, "must be a list"),
        ({"chatbotTasks": [INVESTIGATION, INVESTIGATION]}, "listed twice"),
        ({"chatbotTasks": [{**INVESTIGATION, "id": "Bad Id"}]}, "lower-case"),
        ({"chatbotTasks": [{**INVESTIGATION, "name": " "}]}, "needs a name"),
        ({"chatbotTasks": [{**INVESTIGATION, "kind": "coach"}]}, "investigation or survey"),
        ({"chatbotTasks": [{**INVESTIGATION, "bot": ""}]}, "choose its Open Chat Studio bot"),
        # An investigation talks to one FLW; a survey goes to everyone under a group.
        ({"chatbotTasks": [{**INVESTIGATION, "levels": ["llo"]}]}, "flw reviews only"),
        ({"chatbotTasks": [{**SURVEY, "levels": ["flw"]}]}, "llo, opportunity, program reviews only"),
        ({"chatbotTasks": [{**SURVEY, "levels": []}]}, "reviews only"),
        ({"chatbotTasks": [{**SURVEY, "levels": ["nowhere"]}]}, "levels must be"),
    ],
)
def test_invalid_chatbot_tasks_are_refused_with_the_reason(live, bad, match):  # noqa: F811
    with pytest.raises(service.Refused, match=match):
        service.validate_settings(live, bad)


def _bots_call(user):
    request = RequestFactory().get(f"/labs/audit-reviews/api/{WORKFLOW}/ocs-bots/", {"opportunity_id": 10301})
    request.user = user
    return api_views.ocs_bots_api(request, WORKFLOW)


def test_the_bot_list_is_staff_only_and_sorted_by_name(dao, monkeypatch):  # noqa: F811
    from connect_labs.workflow import actions

    monkeypatch.setattr(
        actions, "_ocs_bots", lambda user, request: [{"id": "b", "name": "Zed"}, {"id": "a", "name": "alpha"}]
    )
    assert _bots_call(_user(staff=False)).status_code == 403
    ok = _bots_call(_user())
    assert ok.status_code == 200
    assert json.loads(ok.content) == {"bots": [{"id": "a", "name": "alpha"}, {"id": "b", "name": "Zed"}]}


def test_the_bot_list_says_to_connect_ocs_when_not_connected(dao, monkeypatch):  # noqa: F811
    from connect_labs.workflow import actions

    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: None)
    body = json.loads(_bots_call(_user()).content)
    assert body == {"bots": None, "connect_url": "/labs/ocs/initiate/"}
