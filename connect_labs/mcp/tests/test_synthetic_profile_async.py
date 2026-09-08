"""Queued profiling: the only path that can complete a large opportunity.

The web tier caps one request at 600s (`gunicorn --timeout 600` in docker/start).
That is a TOTAL-duration cap, not an idle timer, so per-stage progress (#1220)
cannot save a call that simply takes longer — opp 874 (11,581 visits) is killed
mid-call every time and its finished bundles go with the request (#1581).

These pin the two properties that make the queued path safe to use in its place.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

# Trigger @register side effect
import connect_labs.mcp.tools.synthetic  # noqa: F401
from connect_labs.mcp.tool_registry import MCPToolError, get_tool

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="profiler", password="p")


@pytest.fixture
def queue_tool():
    return get_tool("synthetic_profile_opp_async")


@pytest.fixture
def status_tool():
    return get_tool("synthetic_profile_status")


def test_the_queue_returns_immediately_with_a_task_id(queue_tool, user):
    """The whole point: no work happens in the request, so the cap cannot fire."""
    with (
        patch("connect_labs.mcp.tools.synthetic._require_opportunity_access"),
        patch("connect_labs.mcp.tools.synthetic.require_connect_token", return_value="tok"),
        patch("connect_labs.labs.synthetic.tasks.run_synthetic_profile_opp.apply_async") as sent,
    ):
        out = queue_tool.handler(user, source_opportunity_id=874, out_dir="gdrive:abc", mirror=True, curate=True)

    assert out["state"] == "QUEUED"
    assert out["task_id"]
    # queued, not executed
    sent.assert_called_once()
    kwargs = sent.call_args.kwargs["kwargs"]
    assert kwargs["source_opportunity_id"] == 874
    assert kwargs["mirror"] is True and kwargs["curate"] is True
    # the task id is pre-generated so a poll can find the job before it starts
    assert sent.call_args.kwargs["task_id"] == out["task_id"]


def test_access_is_checked_in_the_request_not_the_worker(queue_tool, user):
    """The worker has no request and cannot re-derive permission, so an unchecked
    queue would hand a production token to a job nobody authorised."""
    with (
        patch(
            "connect_labs.mcp.tools.synthetic._require_opportunity_access",
            side_effect=MCPToolError("PERMISSION_DENIED", "nope"),
        ),
        patch("connect_labs.labs.synthetic.tasks.run_synthetic_profile_opp.apply_async") as sent,
    ):
        with pytest.raises(MCPToolError):
            queue_tool.handler(user, source_opportunity_id=874, out_dir="gdrive:abc")
    sent.assert_not_called()


def test_a_missing_connect_token_never_queues_a_job(queue_tool, user):
    with (
        patch("connect_labs.mcp.tools.synthetic._require_opportunity_access"),
        patch(
            "connect_labs.mcp.tools.synthetic.require_connect_token",
            side_effect=MCPToolError("PERMISSION_DENIED", "no token"),
        ),
        patch("connect_labs.labs.synthetic.tasks.run_synthetic_profile_opp.apply_async") as sent,
    ):
        with pytest.raises(MCPToolError):
            queue_tool.handler(user, source_opportunity_id=874, out_dir="gdrive:abc")
    sent.assert_not_called()


def test_status_reports_progress_then_the_bundle(status_tool, user):
    class _Res:
        def __init__(self, state, info=None, result=None):
            self.state, self.info, self.result = state, info, result

    running = _Res("PROGRESS", info={"current": 2, "total": 6, "message": "fetched 11581 visits"})
    with patch("celery.result.AsyncResult", return_value=running):
        out = status_tool.handler(user, task_id="t1")
    assert out["state"] == "PROGRESS" and out["current"] == 2
    assert "11581" in out["message"]

    done = _Res("SUCCESS", result={"bundle_dir": "874", "bundle_root": "gdrive:abc"})
    with patch("celery.result.AsyncResult", return_value=done):
        out = status_tool.handler(user, task_id="t1")
    assert out["state"] == "SUCCESS"
    assert out["result"]["bundle_root"] == "gdrive:abc"


def test_a_failure_surfaces_as_a_string_not_a_traceback(status_tool, user):
    class _Res:
        state = "FAILURE"
        info = RuntimeError("boom")
        result = None

    with patch("celery.result.AsyncResult", return_value=_Res()):
        out = status_tool.handler(user, task_id="t1")
    assert out["state"] == "FAILURE"
    assert out["error"] == "boom"
    assert isinstance(out["error"], str)
