"""Tests for ExperimentAuditCompleteView's duplicate-submission guard.

Completing an audit that's already completed (e.g. the same audit open in two
browser tabs, both submitting "Complete Review") used to attempt a second write
against the underlying API and surface whatever error that produced as a raw
"An internal error occurred" — confusing, and not actionable. It now fails fast
with a clear, specific message telling the user to refresh instead.
"""

import time

import pytest
from django.test import Client

from connect_labs.audit.models import AuditSessionRecord


@pytest.fixture
def labs_client(db):
    """Django test client with a valid labs session and authenticated user."""
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(
        username="testuser",
        defaults={"email": "testuser@example.com"},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token-abc",
        "expires_at": time.time() + 3600,
        "user_profile": {"username": "testuser", "id": 42, "email": "testuser@example.com"},
    }
    session.save()
    return client


def _make_session(status):
    return AuditSessionRecord(
        {
            "id": 6600,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1976,
            "data": {"status": status, "visit_ids": [], "visit_results": {}},
        }
    )


def test_completing_an_already_completed_session_fails_fast_with_clear_message(labs_client, monkeypatch):
    from connect_labs.audit import views

    session = _make_session("completed")

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def complete_audit_session(self, **kwargs):
            raise AssertionError("must not attempt to re-complete an already-completed session")

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)

    response = labs_client.post(
        f"/audit/api/{session.id}/complete/",
        data={"overall_result": "pass"},
    )

    assert response.status_code == 409
    body = response.json()
    assert "already" in body["error"].lower()
    assert "refresh" in body["error"].lower()


def test_completing_an_in_progress_session_still_works(labs_client, monkeypatch):
    from connect_labs.audit import views

    session = _make_session("in_progress")
    completed_calls = []

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def complete_audit_session(self, session, overall_result, notes, kpi_notes):
            completed_calls.append(overall_result)
            session.data["status"] = "completed"
            return session

        def save_audit_session(self, session):
            return session

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)
    monkeypatch.setattr(views.s3_export, "upsert_audit_session", lambda session: None)

    response = labs_client.post(
        f"/audit/api/{session.id}/complete/",
        data={"overall_result": "pass"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert completed_calls == ["pass"]
