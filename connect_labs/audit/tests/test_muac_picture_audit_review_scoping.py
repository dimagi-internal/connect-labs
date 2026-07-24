"""Tests for _is_muac_picture_audit_session, which scopes the Duplicate/Fake
button split and always-editable-after-completion behavior on the bulk
assessment review screen to workflow 6840 ("Muac Picture Audit") only -- every
other workflow's audit sessions must see the unmodified, generic behavior.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from connect_labs.audit import views
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.views import (
    MUAC_PICTURE_AUDIT_PROGRAM_ID,
    MUAC_PICTURE_AUDIT_WORKFLOW_DEFINITION_ID,
    ExperimentBulkAssessmentView,
    _is_muac_picture_audit_session,
)


def _make_session(workflow_run_id):
    # AuditSessionRecord.workflow_run_id reads the top-level labs_record_id,
    # not anything nested in `data` -- it points at the WorkflowRunRecord that
    # created this session.
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "labs_record_id": workflow_run_id,
            "data": {},
        }
    )


def test_no_workflow_run_id_is_never_scoped(monkeypatch):
    session = _make_session(None)
    # No WorkflowDataAccess call should even happen -- assert by not patching it
    # and letting any accidental call raise (ValueError on missing OAuth token).
    assert _is_muac_picture_audit_session(session, request=None) is False


def test_matching_definition_id_is_scoped(monkeypatch):
    session = _make_session(7117)
    fake_run = MagicMock(definition_id=MUAC_PICTURE_AUDIT_WORKFLOW_DEFINITION_ID)
    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run

    captured_kwargs = {}

    def fake_ctor(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_wda

    monkeypatch.setattr(views, "WorkflowDataAccess", fake_ctor)

    assert _is_muac_picture_audit_session(session, request="req") is True
    fake_wda.get_run.assert_called_once_with(7117)
    fake_wda.close.assert_called_once()
    # Scoped by program_id only (never opportunity_id) -- mixing scope
    # dimensions AND-filters production API lookups and 404s program-owned
    # records like this one.
    assert captured_kwargs["program_id"] == MUAC_PICTURE_AUDIT_PROGRAM_ID
    assert "opportunity_id" not in captured_kwargs


def test_other_workflow_definition_id_is_not_scoped(monkeypatch):
    session = _make_session(999)
    fake_run = MagicMock(definition_id=6621)  # some other program-owned workflow
    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run
    monkeypatch.setattr(views, "WorkflowDataAccess", lambda **k: fake_wda)

    assert _is_muac_picture_audit_session(session, request="req") is False


def test_run_not_found_is_not_scoped(monkeypatch):
    session = _make_session(999)
    fake_wda = MagicMock()
    fake_wda.get_run.return_value = None
    monkeypatch.setattr(views, "WorkflowDataAccess", lambda **k: fake_wda)

    assert _is_muac_picture_audit_session(session, request="req") is False


def test_lookup_error_fails_closed(monkeypatch):
    session = _make_session(7117)
    fake_wda = MagicMock()
    fake_wda.get_run.side_effect = RuntimeError("production API down")
    monkeypatch.setattr(views, "WorkflowDataAccess", lambda **k: fake_wda)

    assert _is_muac_picture_audit_session(session, request="req") is False
    fake_wda.close.assert_called_once()


def _fake_request():
    """A GET request with a fake authenticated user and session -- built via
    RequestFactory (no DB) rather than the Django test Client, since the
    User model needs Postgres, which isn't available in this environment
    (see connect_labs_codebase memory: pre-existing local infra gap)."""
    request = RequestFactory().get("/audit/7199/bulk/")
    request.user = SimpleNamespace(is_authenticated=True, username="testuser", id=42, email="testuser@example.com")
    request.session = {"labs_oauth": {"access_token": "test-token-abc"}}
    return request


def _full_session(status="in_progress"):
    return AuditSessionRecord(
        {
            "id": 7199,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1973,
            "labs_record_id": 7117,
            "data": {
                "title": "All-Tuesday-Visits-2",
                "tag": "muac",
                "status": status,
                "opportunity_id": 1973,
                "visit_ids": [],
                "visit_results": {},
                "visit_images": {},
                "completed_at": "2026-07-20T10:00:00Z" if status == "completed" else None,
            },
        }
    )


@pytest.mark.parametrize("scoped", [True, False])
@pytest.mark.parametrize("status", ["in_progress", "completed"])
def test_bulk_assessment_page_renders_for_every_scoping_and_status_combo(monkeypatch, scoped, status):
    """Smoke-tests the template changes (the {% elif %} banner branch, the
    isMuacPictureAuditWorkflow const, and every `<template x-if>` split) don't
    break Django template compilation or rendering in any of the 4 states this
    page can actually be in. Uses RequestFactory (no DB) since the User model
    needs Postgres, unavailable in this environment."""
    session = _full_session(status=status)

    class FakeAuditDataAccess:
        def __init__(self, *a, **k):
            pass

        def get_audit_session(self, session_id, try_multiple_opportunities=False):
            return session

        def close(self):
            pass

    monkeypatch.setattr(views, "AuditDataAccess", FakeAuditDataAccess)
    monkeypatch.setattr(views, "_is_muac_picture_audit_session", lambda s, r: scoped)
    monkeypatch.setattr(views, "get_org_data", lambda request: {"opportunities": []})

    response = ExperimentBulkAssessmentView.as_view()(_fake_request(), pk=session.id)
    response.render()

    assert response.status_code == 200
    body = response.content.decode()
    # The bug this whole change started from: a comment inside the x-data="..."
    # HTML attribute embedded literal double quotes, which HTML parses as the
    # end of the attribute value -- breaking the tag and leaking the rest of
    # the JS source onto the page as visible text. Assert the smoking-gun
    # quoted phrases from that comment are gone for good.
    assert '"(...)" detail' not in body
    assert '"MUAC Mismatch (form:' not in body
    # The button/option split is an Alpine <template x-if>, so both branches
    # are always present in the server-rendered HTML -- only the JS const
    # controls which one Alpine actually mounts in the browser.
    assert f"isMuacPictureAuditWorkflow = {'true' if scoped else 'false'};" in body
    # The completed-report banner IS a server-side {% if %}/{% elif %}, so its
    # branch selection is a real, testable signal.
    if status == "completed":
        if scoped:
            assert "you can still change pass/fail/duplicate/fake" in body.lower()
            assert "reopen it to make changes" not in body.lower()
        else:
            assert "reopen it to make changes" in body.lower()
            assert "you can still change pass/fail/duplicate/fake" not in body.lower()
