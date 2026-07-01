"""Integration tests for Flag HTTP endpoints.

We mock FlagsDataAccess at the view boundary; tests focus on
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


@patch("connect_labs.flags.views.FlagsDataAccess")
def test_post_flag_creates_via_data_access(MockDA, rf):
    from connect_labs.flags.models import FlagRecord

    instance = MockDA.return_value
    instance.create_flag.return_value = FlagRecord(
        {
            "id": 777,
            "experiment": "flags",
            "type": "Flag",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {"flw_id": "amina", "flag_key": "sam_low", "flag_label": "SAM rate low"},
        }
    )
    # mock the completion-guard helper to allow the write
    with patch("connect_labs.flags.views._refuse_if_run_completed") as guard:
        guard.return_value = None

        url = "/labs/workflow/api/503/flags/"
        req = _attach_user(
            _post(
                rf,
                url,
                {
                    "opportunity_id": 10001,
                    "flw_id": "amina",
                    "flag_key": "sam_low",
                    "flag_label": "SAM rate low",
                },
            )
        )
        from connect_labs.flags import views as v

        response = v.create_flag_for_run(req, workflow_run_id=503)

    assert response.status_code == 201
    body = json.loads(response.content)
    assert body["id"] == 777
    call = instance.create_flag.call_args.kwargs
    assert call["workflow_run_id"] == 503
    assert call["flw_id"] == "amina"
    assert call["flag_key"] == "sam_low"
    assert call["flagged_by"] == "jane_okeke"


@patch("connect_labs.flags.views.FlagsDataAccess")
def test_post_flag_returns_400_on_missing_fields(MockDA, rf):
    instance = MockDA.return_value
    instance.create_flag.side_effect = ValueError("flw_id is required")

    with patch("connect_labs.flags.views._refuse_if_run_completed", return_value=None):
        url = "/labs/workflow/api/503/flags/"
        req = _attach_user(_post(rf, url, {"opportunity_id": 10001, "flag_key": "sam_low"}))
        from connect_labs.flags import views as v

        response = v.create_flag_for_run(req, workflow_run_id=503)

    assert response.status_code == 400
    body = json.loads(response.content)
    assert "flw_id" in body["error"]


@patch("connect_labs.flags.views.FlagsDataAccess")
def test_post_flag_refused_when_run_completed(MockDA, rf):
    from django.http import JsonResponse

    with patch("connect_labs.flags.views._refuse_if_run_completed") as guard:
        guard.return_value = JsonResponse(
            {"error": "Workflow run 503 is completed; flags are read-only"},
            status=409,
        )
        url = "/labs/workflow/api/503/flags/"
        req = _attach_user(
            _post(
                rf,
                url,
                {
                    "opportunity_id": 10001,
                    "flw_id": "amina",
                    "flag_key": "sam_low",
                },
            )
        )
        from connect_labs.flags import views as v

        response = v.create_flag_for_run(req, workflow_run_id=503)

    assert response.status_code == 409
    # create_flag should NOT have been called
    MockDA.return_value.create_flag.assert_not_called()


@patch("connect_labs.flags.views.FlagsDataAccess")
def test_get_flags_for_run_returns_list(MockDA, rf):
    from connect_labs.flags.models import FlagRecord

    instance = MockDA.return_value
    instance.get_flags_for_run.return_value = [
        FlagRecord(
            {
                "id": 11,
                "experiment": "flags",
                "type": "Flag",
                "username": "amina",
                "opportunity_id": 10001,
                "data": {
                    "flw_id": "amina",
                    "flag_key": "sam_low",
                    "flag_label": "SAM rate low",
                    "evidence": {"sam_pct": 0.2},
                    "source": "auto",
                    "flagged_at": "2025-11-11T11:42:00Z",
                },
            }
        ),
        FlagRecord(
            {
                "id": 12,
                "experiment": "flags",
                "type": "Flag",
                "username": "binta",
                "opportunity_id": 10001,
                "data": {
                    "flw_id": "binta",
                    "flag_key": "gender_skew",
                    "flag_label": "Gender skew",
                    "source": "auto",
                    "flagged_at": "2025-11-11T11:43:00Z",
                },
            }
        ),
    ]

    req = rf.get("/labs/workflow/api/503/flags/")
    req.session = {"labs_oauth": {"access_token": "stub-token"}}
    req.user = MagicMock(username="jane_okeke")

    from connect_labs.flags import views as v

    response = v.list_flags_for_run(req, workflow_run_id=503)

    assert response.status_code == 200
    body = json.loads(response.content)
    assert body["count"] == 2
    flags = body["flags"]
    assert flags[0]["id"] == 11
    assert flags[0]["flag_key"] == "sam_low"
    assert flags[0]["evidence"] == {"sam_pct": 0.2}
    assert flags[1]["flag_key"] == "gender_skew"
    instance.get_flags_for_run.assert_called_once_with(503)
