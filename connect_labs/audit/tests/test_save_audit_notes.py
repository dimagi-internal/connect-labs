"""Regression coverage for session-level notes on a PROGRESS save.

The Notes box sits on the same page as the image grid, but only the
completion endpoint ever read `notes`/`kpi_notes` from the POST. Every
progress save (manual "Save Progress", the debounced autosave, and the
Save-Progress branch of the image-review button) posted visit_results alone,
so editing the note and saving silently discarded the edit — it only stuck if
you completed the audit.
"""

import json
import time

import pytest
from django.test import Client

from connect_labs.audit.models import AuditSessionRecord


@pytest.fixture
def labs_client(db):
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


def _make_session(**data_overrides):
    data = {
        "status": "in_progress",
        "visit_ids": [111],
        "visit_results": {},
        "notes": "original note",
        "kpi_notes": "original kpi note",
    }
    data.update(data_overrides)
    return AuditSessionRecord(
        {
            "id": 7371,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1978,
            "data": data,
        }
    )


def _patch_data_access(monkeypatch, session, saved):
    from connect_labs.audit import views

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, opportunity_id=None):
            return session

        def save_audit_session(self, s):
            saved.append(json.loads(json.dumps(s.data)))
            return s

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)


def test_progress_save_persists_edited_notes(labs_client, monkeypatch):
    session, saved = _make_session(), []
    _patch_data_access(monkeypatch, session, saved)

    response = labs_client.post(
        f"/audit/api/{session.id}/save/",
        {"visit_results": "{}", "notes": "edited note", "kpi_notes": "edited kpi note"},
    )

    assert response.status_code == 200
    assert saved[0]["notes"] == "edited note"
    assert saved[0]["kpi_notes"] == "edited kpi note"


def test_progress_save_can_clear_a_note(labs_client, monkeypatch):
    """An emptied box must actually empty the stored note."""
    session, saved = _make_session(), []
    _patch_data_access(monkeypatch, session, saved)

    labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}", "notes": ""})

    assert saved[0]["notes"] == ""


def test_caller_that_omits_notes_does_not_blank_them(labs_client, monkeypatch):
    """Presence-keyed, not truthiness-keyed: a payload with no notes field at
    all must leave the stored notes untouched rather than wiping them."""
    session, saved = _make_session(), []
    _patch_data_access(monkeypatch, session, saved)

    labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})

    assert saved[0]["notes"] == "original note"
    assert saved[0]["kpi_notes"] == "original kpi note"


def test_progress_save_persists_flagged_to_llo(labs_client, monkeypatch):
    session, saved = _make_session(), []
    _patch_data_access(monkeypatch, session, saved)

    labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}", "flagged_to_llo": "true"})

    assert saved[0]["flagged_to_llo"] is True


def test_progress_save_can_untick_flagged_to_llo(labs_client, monkeypatch):
    session, saved = _make_session(flagged_to_llo=True), []
    _patch_data_access(monkeypatch, session, saved)

    labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}", "flagged_to_llo": "false"})

    assert saved[0]["flagged_to_llo"] is False


def test_caller_that_omits_flagged_to_llo_does_not_clear_it(labs_client, monkeypatch):
    session, saved = _make_session(flagged_to_llo=True), []
    _patch_data_access(monkeypatch, session, saved)

    labs_client.post(f"/audit/api/{session.id}/save/", {"visit_results": "{}"})

    assert saved[0]["flagged_to_llo"] is True


def test_completion_persists_flagged_to_llo(labs_client, monkeypatch):
    from connect_labs.audit import views

    session, saved = _make_session(), []

    class FakeDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, opportunity_id=None):
            return session

        def complete_audit_session(self, session, overall_result, notes, kpi_notes):
            session.data.update(status="completed", overall_result=overall_result, notes=notes, kpi_notes=kpi_notes)
            return session

        def save_audit_session(self, s):
            saved.append(json.loads(json.dumps(s.data)))
            return s

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeDataAccess)
    monkeypatch.setattr(views.s3_export, "upsert_audit_session", lambda session: None)

    response = labs_client.post(
        f"/audit/api/{session.id}/complete/",
        {"overall_result": "fail", "notes": "Needs to change the MUAC tape", "flagged_to_llo": "true"},
    )

    assert response.status_code == 200
    assert saved[-1]["flagged_to_llo"] is True
    assert saved[-1]["notes"] == "Needs to change the MUAC tape"


def test_question_summary_carries_notes_and_llo_flag():
    """The sessions-summary endpoint is what reports read; the audit log report needs both."""
    summary = _make_session(flagged_to_llo=True).to_question_summary_dict()

    assert summary["notes"] == "original note"
    assert summary["kpi_notes"] == "original kpi note"
    assert summary["flagged_to_llo"] is True
    assert _make_session().to_question_summary_dict()["flagged_to_llo"] is False
