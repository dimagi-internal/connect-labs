"""cancel_job_api must also flip the cooperative-cancel flag (keyed by run_id)
that run_audit_creation checks, not just revoke() the Celery task -- a
workflow job handler (weekly_dual_track_audit, muac_picture_audit) invokes
run_audit_creation via .apply() *inside* this task, so it gets its own
celery task_id that this endpoint never sees. run_id is the one identifier
both the browser and the job handler agree on.
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
def test_marks_workflow_run_cancelled_when_run_id_given(mock_async_result, rf):
    from connect_labs.workflow.views import cancel_job_api

    mock_async_result.return_value.state = "PROGRESS"

    with (
        patch("config.celery_app.app.control.revoke") as mock_revoke,
        patch("connect_labs.audit.data_access.mark_audit_creation_cancelled") as mock_mark,
    ):
        resp = cancel_job_api(_authed_request(rf, {"run_id": 555}), "abc-123")

    assert resp.status_code == 200
    mock_revoke.assert_called_once_with("abc-123", terminate=True, signal="SIGTERM")
    mock_mark.assert_called_once_with("workflow_run:555")


@patch("celery.result.AsyncResult")
def test_no_run_id_skips_cooperative_flag(mock_async_result, rf):
    from connect_labs.workflow.views import cancel_job_api

    mock_async_result.return_value.state = "PROGRESS"

    with (
        patch("config.celery_app.app.control.revoke"),
        patch("connect_labs.audit.data_access.mark_audit_creation_cancelled") as mock_mark,
    ):
        resp = cancel_job_api(_authed_request(rf, {}), "abc-123")

    assert resp.status_code == 200
    mock_mark.assert_not_called()


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
