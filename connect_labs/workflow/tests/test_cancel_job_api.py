"""cancel_job_api must also flip the cooperative-cancel flag that
run_audit_creation checks, not just revoke() the Celery task -- a workflow
job handler (weekly_dual_track_audit, muac_picture_audit) invokes
run_audit_creation via .apply() *inside* this task, so it gets its own
throwaway celery task_id that this endpoint never sees. This task's OWN id
(the URL path parameter here) is what the browser already has and what
run_workflow_job threads into job_config as "_task_id" for such handlers --
using it (rather than the long-lived run_id) also means a later retry on the
same run always gets a fresh key, so no stale flag can carry over.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.fixture
def rf():
    return RequestFactory()


def _authed_request(rf, body):
    req = rf.post("/labs/workflow/api/job/abc-123/cancel/", data=body, content_type="application/json")
    req.user = MagicMock(username="jane_okeke")
    req.session = {}
    return req


@patch("celery.result.AsyncResult")
def test_marks_this_task_cancelled_regardless_of_run_id(mock_async_result, rf):
    from connect_labs.workflow.views import cancel_job_api

    mock_async_result.return_value.state = "PROGRESS"

    with (
        patch("config.celery_app.app.control.revoke") as mock_revoke,
        patch("connect_labs.audit.data_access.mark_audit_creation_cancelled") as mock_mark,
    ):
        resp = cancel_job_api(_authed_request(rf, {}), "abc-123")

    assert resp.status_code == 200
    mock_revoke.assert_called_once_with("abc-123", terminate=True, signal="SIGTERM")
    mock_mark.assert_called_once_with("abc-123")


@patch("celery.result.AsyncResult")
def test_run_id_present_still_marks_by_task_id_not_run_id(mock_async_result, rf):
    """run_id must never end up in the cancel-flag key -- it's a long-lived
    DB id reused on every future dispatch of the same run, so keying on it
    would leave a stale flag that silently no-ops a later retry."""
    from connect_labs.workflow.views import cancel_job_api

    mock_async_result.return_value.state = "PROGRESS"

    with (
        patch("config.celery_app.app.control.revoke"),
        patch("connect_labs.audit.data_access.mark_audit_creation_cancelled") as mock_mark,
    ):
        resp = cancel_job_api(_authed_request(rf, {"run_id": 555}), "abc-123")

    assert resp.status_code == 200
    mock_mark.assert_called_once_with("abc-123")


@patch("celery.result.AsyncResult")
def test_task_not_running_skips_both_revoke_and_flag(mock_async_result, rf):
    from connect_labs.workflow.views import cancel_job_api

    mock_async_result.return_value.state = "SUCCESS"

    with (
        patch("config.celery_app.app.control.revoke") as mock_revoke,
        patch("connect_labs.audit.data_access.mark_audit_creation_cancelled") as mock_mark,
    ):
        resp = cancel_job_api(_authed_request(rf, {"run_id": 555}), "abc-123")

    assert resp.status_code == 400
    mock_revoke.assert_not_called()
    mock_mark.assert_not_called()
