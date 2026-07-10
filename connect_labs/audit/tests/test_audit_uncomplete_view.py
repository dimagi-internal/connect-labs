"""Coverage for ExperimentAuditUncompleteView — the endpoint behind the
review screen's "Reopen Audit" button (issue #899).

The endpoint existed before the button did; these tests pin the reopen
semantics the UI now depends on: status flips back to in_progress,
completed_at clears, and the mutated session is persisted.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()


def _make_request(rf, session_id):
    request = rf.post(f"/audit/api/{session_id}/uncomplete/")
    user = MagicMock()
    user.is_authenticated = True
    request.user = user
    request.labs_context = {"opportunity_id": 1973}
    request.session = {"labs_oauth": {"access_token": "t"}}
    return request


class TestExperimentAuditUncompleteView:
    def test_reopens_completed_session(self, rf: RequestFactory):
        from connect_labs.audit.views import ExperimentAuditUncompleteView

        session = MagicMock()
        session.data = {"status": "completed", "completed_at": "2026-07-09T12:00:00+00:00"}

        with patch("connect_labs.audit.views.AuditDataAccess") as MockDA:
            mock_da = MagicMock()
            MockDA.return_value = mock_da
            mock_da.get_audit_session.return_value = session
            mock_da.save_audit_session.return_value = session

            request = _make_request(rf, 7)
            response = ExperimentAuditUncompleteView.as_view()(request, session_id=7)

        assert response.status_code == 200
        assert session.data["status"] == "in_progress"
        assert session.data["completed_at"] is None
        mock_da.save_audit_session.assert_called_once_with(session)

    def test_missing_session_returns_404(self, rf: RequestFactory):
        from connect_labs.audit.views import ExperimentAuditUncompleteView

        with patch("connect_labs.audit.views.AuditDataAccess") as MockDA:
            mock_da = MagicMock()
            MockDA.return_value = mock_da
            mock_da.get_audit_session.return_value = None

            request = _make_request(rf, 999)
            response = ExperimentAuditUncompleteView.as_view()(request, session_id=999)

        assert response.status_code == 404
        mock_da.save_audit_session.assert_not_called()
