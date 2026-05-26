"""Integration tests for Decision HTTP endpoints.

We mock DecisionsDataAccess at the view boundary; tests focus on
- correct routing
- payload validation
- HTTP status codes
- ACL/precondition behavior (refusing writes against completed runs)
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.fixture
def rf():
    return RequestFactory()


def _post(rf, url, body):
    req = rf.post(url, data=json.dumps(body), content_type="application/json")
    # Stub the labs OAuth session so BaseDataAccess can spin up.
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    return req


def _attach_user(req, username="jane_okeke"):
    """Attach a Django user-like stub to the request."""
    user = MagicMock()
    user.username = username
    req.user = user
    return req


@patch("commcare_connect.decisions.views.DecisionsDataAccess")
def test_post_decision_creates_via_data_access(MockDA, rf):
    from commcare_connect.decisions.models import DecisionRecord

    instance = MockDA.return_value
    instance.create_decision.return_value = DecisionRecord(
        {
            "id": 777,
            "experiment": "decisions",
            "type": "Decision",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {"flw_id": "amina", "decision_type": "no_issues"},
        }
    )
    # mock the completion-guard helper to allow the write
    with patch("commcare_connect.decisions.views._refuse_if_run_completed") as guard:
        guard.return_value = None

        url = "/labs/workflow/api/503/decisions/"
        req = _attach_user(_post(rf, url, {
            "opportunity_id": 10001,
            "flw_id": "amina",
            "decision_type": "no_issues",
        }))
        from commcare_connect.decisions import views as v
        response = v.create_decision_for_run(req, workflow_run_id=503)

    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["id"] == 777
    call = instance.create_decision.call_args.kwargs
    assert call["workflow_run_id"] == 503
    assert call["flw_id"] == "amina"
    assert call["decision_type"] == "no_issues"
    assert call["decided_by"] == "jane_okeke"


@patch("commcare_connect.decisions.views.DecisionsDataAccess")
def test_post_decision_returns_400_on_missing_fields(MockDA, rf):
    instance = MockDA.return_value
    instance.create_decision.side_effect = ValueError("flw_id is required")

    with patch("commcare_connect.decisions.views._refuse_if_run_completed", return_value=None):
        url = "/labs/workflow/api/503/decisions/"
        req = _attach_user(_post(rf, url, {"opportunity_id": 10001, "decision_type": "no_issues"}))
        from commcare_connect.decisions import views as v
        response = v.create_decision_for_run(req, workflow_run_id=503)

    assert response.status_code == 400
    body = json.loads(response.content)
    assert "flw_id" in body["error"]


@patch("commcare_connect.decisions.views.DecisionsDataAccess")
def test_post_decision_refused_when_run_completed(MockDA, rf):
    from django.http import JsonResponse

    with patch("commcare_connect.decisions.views._refuse_if_run_completed") as guard:
        guard.return_value = JsonResponse(
            {"error": "Workflow run 503 is completed; decisions are read-only"},
            status=409,
        )
        url = "/labs/workflow/api/503/decisions/"
        req = _attach_user(_post(rf, url, {
            "opportunity_id": 10001, "flw_id": "amina", "decision_type": "no_issues",
        }))
        from commcare_connect.decisions import views as v
        response = v.create_decision_for_run(req, workflow_run_id=503)

    assert response.status_code == 409
    # create_decision should NOT have been called
    MockDA.return_value.create_decision.assert_not_called()


@patch("commcare_connect.decisions.views.DecisionsDataAccess")
def test_get_decisions_for_run_returns_list(MockDA, rf):
    from commcare_connect.decisions.models import DecisionRecord

    instance = MockDA.return_value
    instance.get_decisions_for_run.return_value = [
        DecisionRecord(
            {
                "id": 11,
                "experiment": "decisions",
                "type": "Decision",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "flw_id": "amina",
                    "decision_type": "action_taken",
                    "reason_key": "bad_muac_distribution",
                    "reason_label": "Bad MUAC pattern",
                    "audit_session_ids": [46],
                    "task_ids": [123],
                    "decided_at": "2025-11-11T11:42:00Z",
                },
            }
        ),
        DecisionRecord(
            {
                "id": 12,
                "experiment": "decisions",
                "type": "Decision",
                "username": "binta",
                "opportunity_id": 10001,
                "data": {
                    "flw_id": "binta",
                    "decision_type": "no_issues",
                    "decided_at": "2025-11-11T11:43:00Z",
                },
            }
        ),
    ]

    req = rf.get("/labs/workflow/api/503/decisions/")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.user = MagicMock(username="jane_okeke")

    from commcare_connect.decisions import views as v
    response = v.list_decisions_for_run(req, workflow_run_id=503)

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["count"] == 2
    decisions = body["decisions"]
    assert decisions[0]["id"] == 11
    assert decisions[0]["decision_type"] == "action_taken"
    assert decisions[0]["audit_session_ids"] == [46]
    assert decisions[0]["task_ids"] == [123]
    assert decisions[1]["decision_type"] == "no_issues"
    instance.get_decisions_for_run.assert_called_once_with(503)
