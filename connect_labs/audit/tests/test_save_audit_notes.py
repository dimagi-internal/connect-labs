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

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
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
