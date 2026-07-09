"""Regression coverage for WorkflowSessionsAPIView's FLW-name resolution.

get_sessions_by_workflow_run() can now return sessions spanning MULTIPLE
opportunities for a program-scoped workflow run (see
test_get_audit_sessions_program_scope.py). The view's FLW display-name
lookup used to resolve names from only the FIRST session's opportunity_id
("all should be same opportunity") — wrong once a run can span opps, since
FLWs from any opportunity but the first would fall back to their raw
username instead of a display name.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from connect_labs.users.tests.factories import UserFactory


class FakeSession:
    def __init__(self, opportunity_id, flw_username):
        self._opportunity_id = opportunity_id
        self._flw_username = flw_username

    def to_summary_dict(self):
        return {
            "id": 1,
            "opportunity_id": self._opportunity_id,
            "flw_username": self._flw_username,
        }


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


@pytest.fixture
def dimagi_user(db):
    user = UserFactory()
    user.email = "test@dimagi.com"
    user.save()
    return user


class TestWorkflowSessionsAPIViewMultiOpp:
    def test_resolves_flw_names_per_opportunity_not_just_the_first_session(self, dimagi_user, rf: RequestFactory):
        request = rf.get("/audit/api/workflow-sessions/42/")
        request.user = dimagi_user
        request.labs_context = {"program_id": 176}
        request.session = {"labs_oauth": {"access_token": "t"}}

        sessions = [
            FakeSession(opportunity_id=1973, flw_username="flw_a"),
            FakeSession(opportunity_id=1976, flw_username="flw_b"),
        ]

        with patch("connect_labs.audit.views.AuditDataAccess") as MockDA:
            mock_da = MagicMock()
            MockDA.return_value = mock_da
            mock_da.get_sessions_by_workflow_run.return_value = sessions

            def fake_get_flw_names(opp_id):
                return {1973: {"flw_a": "Jane Doe"}, 1976: {"flw_b": "John Smith"}}[opp_id]

            mock_da.get_flw_names.side_effect = fake_get_flw_names

            from connect_labs.audit.views import WorkflowSessionsAPIView

            response = WorkflowSessionsAPIView.as_view()(request, workflow_run_id=42)

        import json

        body = json.loads(response.content)
        assert body["success"] is True
        by_username = {s["flw_username"]: s["flw_display_name"] for s in body["sessions"]}
        assert by_username == {"flw_a": "Jane Doe", "flw_b": "John Smith"}
        # get_flw_names must have been called once per DISTINCT opportunity present.
        assert mock_da.get_flw_names.call_count == 2

    def test_single_opportunity_still_works(self, dimagi_user, rf: RequestFactory):
        request = rf.get("/audit/api/workflow-sessions/42/")
        request.user = dimagi_user
        request.labs_context = {"opportunity_id": 1973}
        request.session = {"labs_oauth": {"access_token": "t"}}

        sessions = [FakeSession(opportunity_id=1973, flw_username="flw_a")]

        with patch("connect_labs.audit.views.AuditDataAccess") as MockDA:
            mock_da = MagicMock()
            MockDA.return_value = mock_da
            mock_da.get_sessions_by_workflow_run.return_value = sessions
            mock_da.get_flw_names.return_value = {"flw_a": "Jane Doe"}

            from connect_labs.audit.views import WorkflowSessionsAPIView

            response = WorkflowSessionsAPIView.as_view()(request, workflow_run_id=42)

        import json

        body = json.loads(response.content)
        assert body["sessions"][0]["flw_display_name"] == "Jane Doe"
        mock_da.get_flw_names.assert_called_once_with(1973)
