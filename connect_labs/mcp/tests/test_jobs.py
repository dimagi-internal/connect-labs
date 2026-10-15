"""Long-running MCP tools run as durable jobs (connect_labs/mcp/jobs.py).

Pinned here: a long-running tool returns a job instead of running in the request; the
job runs as its caller, under the caller's restricted-scope rules, and records its result
or error; only its owner -- on a call at least as privileged -- can read it; a job whose
worker died is taken over and run once more, idempotently; cancel; the standalone
route; housekeeping.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

from config import celery_app
from connect_labs.mcp import jobs, server, token_scopes
from connect_labs.mcp.models import MCPAuditLog, MCPJob
from connect_labs.mcp.tasks import run_mcp_job
from connect_labs.mcp.tool_registry import _REGISTRY, MCPToolError, get_tool, register
from connect_labs.users.models import User

SLOW = "test_jobs_slow_tool"
BROKEN = "test_jobs_broken_tool"
CRASHING = "test_jobs_crashing_tool"
WHO = "test_jobs_who_tool"
STANDALONE = "test_jobs_standalone_tool"


@pytest.fixture(autouse=True)
def tools():
    """Register throwaway long-running tools; unregister them afterwards."""
    calls: list[dict] = []

    def slow(user, *, n: int, progress=None):
        calls.append({"user": user.username, "n": n})
        if progress:
            progress(1, 1, "graded")
        return {"doubled": n * 2, "_private": "stripped"}

    def broken(user, **kw):
        raise MCPToolError("INVALID_SCHEMA", "as_of is in the future")

    def crashing(user, **kw):
        raise ZeroDivisionError("division by zero")

    def who(user, **kw):
        from connect_labs.mcp.visit_access import caller_restricted

        return {"username": user.username, "restricted": caller_restricted()}

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "additionalProperties": False}
    open_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    register(name=SLOW, description="t", input_schema=schema, long_running=True, wants_progress=True)(slow)
    register(name=BROKEN, description="t", input_schema=open_schema, long_running=True)(broken)
    register(name=CRASHING, description="t", input_schema=open_schema, long_running=True)(crashing)
    register(name=WHO, description="t", input_schema=open_schema, long_running=True)(who)
    register(name=STANDALONE, description="t", input_schema=schema, long_running=True, job_runner="standalone")(slow)
    yield calls
    for name in (SLOW, BROKEN, CRASHING, WHO, STANDALONE):
        _REGISTRY.pop(name, None)


def _user(name="alice"):
    return User.objects.create(username=name, email=f"{name}@example.org")


def _access(user, *, restricted=False):
    scope = token_scopes.NO_USERVISIT_DATA if restricted else token_scopes.FULL
    return AccessToken(
        token="t",
        client_id=str(user.pk),
        scopes=[token_scopes.TOKEN_SCOPE_STRINGS[scope]],
        claims={"sub": str(user.pk), "user_id": user.pk, "auth_method": "pat"},
    )


def _call(user, tool, arguments=None, *, restricted=False):
    """Call a tool exactly as the server does, as ``user`` (restricted or not)."""
    with mock.patch.object(server, "get_access_token", return_value=_access(user, restricted=restricted)):
        return server._run_registry_tool(get_tool(tool), dict(arguments or {})).structured_content


def _queue_only():
    """Queue the job without running it (as production does: the worker runs it later)."""
    return mock.patch.object(jobs, "_dispatch", lambda job: None)


# ---------------------------------------------------------------------------
# Registry and submission
# ---------------------------------------------------------------------------


def test_a_long_running_tool_takes_a_wait_argument_and_a_bad_runner_is_refused():
    assert get_tool(SLOW).long_running is True
    assert get_tool(SLOW).input_schema["properties"]["wait"]["type"] == "boolean"
    assert get_tool(SLOW).input_schema["properties"]["n"] == {"type": "integer"}
    with pytest.raises(ValueError, match="job_runner"):
        register(name="test_jobs_never", description="t", input_schema={}, long_running=True, job_runner="lambda")


def test_the_migrated_history_tools_are_long_running():
    from connect_labs.mcp import tools  # noqa: F401

    assert get_tool("workflow_preview_as_of").long_running
    assert get_tool("workflow_rebuild_history").long_running
    assert get_tool("workflow_history_eligibility").long_running is False


@pytest.mark.django_db
def test_a_long_running_call_returns_a_job_id_and_does_not_run_the_tool_in_the_request(tools):
    user = _user()
    with _queue_only():
        out = _call(user, SLOW, {"n": 21, "wait": False})

    assert tools == []  # the handler did not run in the request
    assert out["poll_with"] == "job_status"
    assert out["state"] == MCPJob.QUEUED
    job = MCPJob.objects.get(pk=out["job_id"])
    assert (job.user, job.tool_name, job.arguments, job.restricted) == (user, SLOW, {"n": 21}, False)
    assert MCPAuditLog.objects.filter(user=user, tool_name=SLOW, success=True).count() == 1


@pytest.mark.django_db
@override_settings(MCP_JOB_WAIT_SECONDS=0)
def test_a_job_still_running_after_the_wait_returns_its_id():
    user = _user()
    with _queue_only():
        out = _call(user, SLOW, {"n": 1})
    assert out["poll_with"] == "job_status"
    assert MCPJob.objects.get(pk=out["job_id"]).state == MCPJob.QUEUED


@pytest.mark.django_db
def test_a_job_that_finishes_within_the_wait_returns_its_result_and_stores_it(tools):
    user = _user()
    out = _call(user, SLOW, {"n": 21})

    assert out["doubled"] == 42
    assert "_private" not in out
    job = MCPJob.objects.get(pk=out["job_id"])
    assert job.state == MCPJob.SUCCEEDED
    assert job.result == {"doubled": 42}
    assert job.progress["message"] == "graded"
    assert tools == [{"user": "alice", "n": 21}]


@pytest.mark.django_db
def test_a_tool_error_is_recorded_on_the_job_and_raised_to_a_waiting_caller():
    user = _user()
    with pytest.raises(ToolError, match="as_of is in the future"):
        _call(user, BROKEN)
    job = MCPJob.objects.get(tool_name=BROKEN)
    assert (job.state, job.error_code, job.error) == (MCPJob.FAILED, "INVALID_SCHEMA", "as_of is in the future")


@pytest.mark.django_db
def test_an_unexpected_exception_is_recorded_as_an_upstream_error():
    user = _user()
    out = _call(user, CRASHING, {"wait": False})
    job = MCPJob.objects.get(pk=out["job_id"])
    assert job.state == MCPJob.FAILED
    assert job.error_code == "UPSTREAM_ERROR"
    assert "ZeroDivisionError" in job.error


@pytest.mark.django_db
@override_settings(MCP_JOB_MAX_LIVE_PER_USER=2)
def test_a_person_can_hold_only_so_many_live_jobs():
    user = _user()
    with _queue_only():
        _call(user, SLOW, {"n": 1, "wait": False})
        _call(user, SLOW, {"n": 2, "wait": False})
        with pytest.raises(ToolError, match="limit is 2"):
            _call(user, SLOW, {"n": 3, "wait": False})
    assert MCPJob.objects.filter(user=user).count() == 2


# ---------------------------------------------------------------------------
# job_status: who can read a job
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_job_status_returns_the_result_to_its_owner_and_nothing_to_anyone_else():
    alice, mallory = _user("alice"), _user("mallory")
    job_id = _call(alice, SLOW, {"n": 5})["job_id"]

    mine = _call(alice, "job_status", {"job_id": job_id})
    assert mine["state"] == MCPJob.SUCCEEDED
    assert mine["result"] == {"doubled": 10}

    with pytest.raises(ToolError, match="No job"):
        _call(mallory, "job_status", {"job_id": job_id})
    assert _call(mallory, "job_status", {})["jobs"] == []
    # An unknown id reads exactly like somebody else's.
    with pytest.raises(ToolError, match="No job"):
        _call(mallory, "job_status", {"job_id": str(uuid.uuid4())})


@pytest.mark.django_db
def test_job_status_with_no_id_lists_your_jobs_without_their_results():
    alice = _user()
    _call(alice, SLOW, {"n": 1})
    _call(alice, SLOW, {"n": 2})
    listed = _call(alice, "job_status", {})["jobs"]
    assert [j["tool"] for j in listed] == [SLOW, SLOW]
    assert all("result" not in j for j in listed)


@pytest.mark.django_db
def test_a_restricted_call_cannot_read_a_job_a_full_access_call_started():
    alice = _user()
    full_job = MCPJob.objects.create(
        id=uuid.uuid4(), user=alice, tool_name="workflow_preview_as_of", restricted=False, state=MCPJob.SUCCEEDED
    )
    restricted_job = MCPJob.objects.create(
        id=uuid.uuid4(), user=alice, tool_name="workflow_preview_as_of", restricted=True, state=MCPJob.SUCCEEDED
    )

    with pytest.raises(ToolError, match="No job"):
        _call(alice, "job_status", {"job_id": str(full_job.pk)}, restricted=True)
    assert _call(alice, "job_status", {"job_id": str(restricted_job.pk)}, restricted=True)["state"] == "succeeded"
    listed = _call(alice, "job_status", {}, restricted=True)["jobs"]
    assert [j["job_id"] for j in listed] == [str(restricted_job.pk)]
    # The full-access call sees both.
    assert len(_call(alice, "job_status", {})["jobs"]) == 2


@pytest.mark.django_db
def test_job_status_follows_a_synthetic_job_by_its_task_id():
    alice = _user()
    task_id = str(uuid.uuid4())
    with mock.patch(
        "connect_labs.mcp.tools.synthetic.synthetic_job_status",
        return_value={"task_id": task_id, "state": "PROGRESS", "status": "Step 1 of 2"},
    ) as synthetic:
        out = _call(alice, "job_status", {"job_id": task_id})
    synthetic.assert_called_once_with(alice, task_id=task_id)
    assert (out["kind"], out["state"], out["job_id"]) == ("synthetic", "PROGRESS", task_id)


# ---------------------------------------------------------------------------
# Restricted scope ("no user visit data") still applies
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_restricted_call_is_refused_before_any_job_is_queued():
    """workflow_preview_as_of is generated-only for a restricted caller; 501 is a real opp."""
    from connect_labs.mcp import tools  # noqa: F401

    alice = _user()
    with pytest.raises(ToolError, match="user visit data"):
        _call(
            alice,
            "workflow_preview_as_of",
            {"definition_id": 1, "opportunity_id": 501, "as_of": "2026-09-01"},
            restricted=True,
        )
    assert not MCPJob.objects.exists()


@pytest.mark.django_db
def test_the_job_re_applies_the_restricted_rules_when_it_runs():
    """A restricted job for a real opportunity is refused at run time, without running the tool."""
    from connect_labs.mcp import tools  # noqa: F401

    alice = _user()
    job = MCPJob.objects.create(
        id=uuid.uuid4(),
        user=alice,
        tool_name="workflow_preview_as_of",
        arguments={"definition_id": 1, "opportunity_id": 501, "as_of": "2026-09-01"},
        restricted=True,
    )
    with mock.patch.object(get_tool("workflow_preview_as_of"), "handler") as handler:
        jobs.execute(str(job.pk), owner="w:1", heartbeat=False)
    handler.assert_not_called()
    job.refresh_from_db()
    assert (job.state, job.error_code) == (MCPJob.FAILED, "PERMISSION_DENIED")
    assert "user visit data" in job.error


@pytest.mark.django_db
@pytest.mark.parametrize("restricted", [True, False])
def test_the_job_runs_as_its_caller_and_carries_the_calls_restriction(restricted):
    alice = _user()
    job = MCPJob.objects.create(id=uuid.uuid4(), user=alice, tool_name=WHO, restricted=restricted)
    jobs.execute(str(job.pk), owner="w:1", heartbeat=False)
    job.refresh_from_db()
    assert job.result == {"username": "alice", "restricted": restricted}


def test_a_delegated_token_that_can_start_a_job_can_follow_it():
    access = AccessToken(token="t", client_id="canopy", scopes=["x"], claims={"auth_method": "delegated"})
    with mock.patch("connect_labs.labs.canopy.allowed_tools", return_value=frozenset({SLOW})):
        assert token_scopes.JOB_TOOLS <= server.allowed_tools(access)
    with mock.patch("connect_labs.labs.canopy.allowed_tools", return_value=frozenset({"labs_context"})):
        assert not (token_scopes.JOB_TOOLS & server.allowed_tools(access))


def test_the_job_tools_reach_a_restricted_caller():
    assert token_scopes.JOB_TOOLS <= token_scopes.RESTRICTED_TOOLS


# ---------------------------------------------------------------------------
# Surviving a deploy: redelivery, the lease, attempts
# ---------------------------------------------------------------------------


def test_the_worker_task_is_acknowledged_late_and_requeued_if_its_worker_is_lost():
    assert run_mcp_job.acks_late is True
    assert run_mcp_job.reject_on_worker_lost is True
    # Under the Redis broker's one-hour visibility timeout: no redelivery while legitimately running.
    assert run_mcp_job.soft_time_limit < 3600
    assert run_mcp_job.time_limit < 3600


def _queued_job(user, tool=SLOW, arguments=None):
    return MCPJob.objects.create(id=uuid.uuid4(), user=user, tool_name=tool, arguments=arguments or {"n": 4})


@pytest.mark.django_db
def test_a_job_whose_worker_died_mid_run_is_taken_over_by_the_redelivered_message(tools):
    alice = _user()
    job = _queued_job(alice)
    # The first worker claimed it and died: the job reads "running", its lease lapses.
    assert jobs._claim(str(job.pk), "old-worker:1:a", "old-worker", 120) == "claimed"
    MCPJob.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))

    # The broker redelivers the message to the new worker.
    run_mcp_job.run(str(job.pk))

    job.refresh_from_db()
    assert job.state == MCPJob.SUCCEEDED
    assert job.result == {"doubled": 8}
    assert job.attempts == 2
    assert tools == [{"user": "alice", "n": 4}]  # ran once to completion


@pytest.mark.django_db
def test_a_copy_delivered_while_the_job_is_held_waits_instead_of_running_it_twice(tools):
    alice = _user()
    job = _queued_job(alice)
    jobs._claim(str(job.pk), "busy-worker:1:a", "busy-worker", 120)

    with mock.patch.object(run_mcp_job, "retry", side_effect=RuntimeError("retry later")) as retry:
        with pytest.raises(RuntimeError, match="retry later"):
            run_mcp_job.run(str(job.pk))
    assert 100 < retry.call_args.kwargs["countdown"] <= 130  # when the lease would lapse
    assert tools == []
    job.refresh_from_db()
    assert (job.state, job.lease_owner, job.attempts) == (MCPJob.RUNNING, "busy-worker:1:a", 1)


@pytest.mark.django_db
def test_a_deploy_hands_the_job_back_and_the_new_worker_runs_it_without_counting_the_attempt(tools):
    alice = _user()
    job = _queued_job(alice)
    other = _queued_job(alice)
    jobs._claim(str(job.pk), "celery@old:1:a", "celery@old", 120)
    jobs._claim(str(other.pk), "celery@other:1:a", "celery@other", 120)

    # The old worker's shutdown hook (tasks._on_worker_shutting_down).
    from connect_labs.mcp.tasks import _on_worker_shutting_down

    _on_worker_shutting_down(sender="celery@old", how="Cold")
    job.refresh_from_db()
    assert (job.state, job.lease_owner, job.attempts) == (MCPJob.QUEUED, "", 0)
    assert MCPJob.objects.get(pk=other.pk).state == MCPJob.RUNNING  # another worker's job is untouched

    run_mcp_job.run(str(job.pk))  # the restored message, on the new worker
    job.refresh_from_db()
    assert (job.state, job.attempts) == (MCPJob.SUCCEEDED, 1)
    # The old attempt can no longer record anything over it.
    assert jobs._finish(str(job.pk), "celery@old:1:a", MCPJob.FAILED, error="late") is False


@pytest.mark.django_db
def test_a_warm_shutdown_keeps_the_job_it_lets_finish():
    """A warm shutdown runs the job to the end and acknowledges its message: nothing to hand back to."""
    from connect_labs.mcp.tasks import _on_worker_shutting_down

    alice = _user()
    job = _queued_job(alice)
    jobs._claim(str(job.pk), "celery@old:1:a", "celery@old", 120)

    _on_worker_shutting_down(sender="celery@old", how="Warm")

    job.refresh_from_db()
    assert (job.state, job.lease_owner, job.attempts) == (MCPJob.RUNNING, "celery@old:1:a", 1)
    # So the attempt still records its result.
    assert jobs._finish(str(job.pk), "celery@old:1:a", MCPJob.SUCCEEDED, result={"x": 1}) is True


@pytest.mark.django_db
def test_a_late_copy_of_a_finished_job_does_nothing(tools):
    alice = _user()
    job_id = _call(alice, SLOW, {"n": 3})["job_id"]
    assert run_mcp_job.run(job_id) == "done"
    assert len(tools) == 1
    assert MCPJob.objects.get(pk=job_id).result == {"doubled": 6}


@pytest.mark.django_db
@override_settings(MCP_JOB_MAX_ATTEMPTS=3)
def test_a_job_that_keeps_killing_its_worker_stops_after_its_attempts(tools):
    alice = _user()
    job = _queued_job(alice)
    MCPJob.objects.filter(pk=job.pk).update(
        state=MCPJob.RUNNING, attempts=3, lease_until=timezone.now() - timedelta(seconds=1)
    )
    assert run_mcp_job.run(str(job.pk)) == "done"
    job.refresh_from_db()
    assert job.state == MCPJob.FAILED
    assert "3 attempts" in job.error
    assert tools == []


@pytest.mark.django_db(transaction=True)
@override_settings(MCP_JOB_HEARTBEAT_SECONDS=0.05, MCP_JOB_LEASE_SECONDS=120)
def test_the_heartbeat_renews_the_lease_while_the_job_runs():
    alice = _user()
    job = _queued_job(alice)
    jobs._claim(str(job.pk), "w:1:a", "w", 1)
    before = MCPJob.objects.get(pk=job.pk).lease_until
    beat = jobs._Heartbeat(str(job.pk), "w:1:a")
    beat.start()
    import time

    deadline = time.monotonic() + 5
    while MCPJob.objects.get(pk=job.pk).lease_until <= before + timedelta(seconds=60) and time.monotonic() < deadline:
        time.sleep(0.05)
    beat.stop()
    beat.join(2)
    assert MCPJob.objects.get(pk=job.pk).lease_until > before + timedelta(seconds=60)


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_cancelled_queued_job_never_runs(tools):
    alice = _user()
    with _queue_only():
        job_id = _call(alice, SLOW, {"n": 1, "wait": False})["job_id"]
    out = _call(alice, "job_cancel", {"job_id": job_id})
    assert out["state"] == MCPJob.CANCELLED

    assert run_mcp_job.run(job_id) == "done"
    assert tools == []
    assert MCPJob.objects.get(pk=job_id).state == MCPJob.CANCELLED


@pytest.mark.django_db
def test_a_running_job_cancelled_cannot_record_a_result_afterwards():
    alice = _user()
    job = _queued_job(alice)
    jobs._claim(str(job.pk), "w:1:a", "w", 120)
    with override_settings(MCP_JOBS_RUN_INLINE=False), mock.patch.object(celery_app.control, "revoke") as revoke:
        jobs.cancel(job)
    revoke.assert_called_once_with(str(job.pk), terminate=True)
    assert jobs._finish(str(job.pk), "w:1:a", MCPJob.SUCCEEDED, result={"x": 1}) is False
    job.refresh_from_db()
    assert (job.state, job.result) == (MCPJob.CANCELLED, None)


@pytest.mark.django_db
def test_nobody_else_can_cancel_your_job():
    alice, mallory = _user("alice"), _user("mallory")
    with _queue_only():
        job_id = _call(alice, SLOW, {"n": 1, "wait": False})["job_id"]
    with pytest.raises(ToolError, match="No job"):
        _call(mallory, "job_cancel", {"job_id": job_id})
    assert MCPJob.objects.get(pk=job_id).state == MCPJob.QUEUED


# ---------------------------------------------------------------------------
# The standalone route (a Fargate task of its own)
# ---------------------------------------------------------------------------


def _ecs():
    client = mock.Mock()
    client.describe_services.return_value = {
        "services": [{"taskDefinition": "td:1", "networkConfiguration": {"awsvpcConfiguration": {}}}]
    }
    client.describe_task_definition.return_value = {"taskDefinition": {"containerDefinitions": [{"name": "worker"}]}}
    client.run_task.return_value = {"tasks": [{"taskArn": "arn:aws:ecs:task/1"}], "failures": []}
    return client


@pytest.mark.django_db
@override_settings(MCP_JOBS_ON_ECS=True)
def test_a_standalone_job_is_launched_on_its_own_task_once_and_runs_there(tools, monkeypatch):
    # Recorded as originally unset, so teardown removes the "1" the command writes.
    monkeypatch.setenv(jobs.KIND.runner_env, "0")
    alice = _user()
    with _queue_only():
        job_id = _call(alice, STANDALONE, {"n": 7, "wait": False})["job_id"]
    assert MCPJob.objects.get(pk=job_id).runner == MCPJob.STANDALONE

    client = _ecs()
    with mock.patch("connect_labs.labs.ecs_tasks.client", return_value=client):
        assert run_mcp_job.run(job_id) == "launched"
        run_mcp_job.run(job_id)  # a redelivered message
    assert client.run_task.call_count == 1
    kwargs = client.run_task.call_args.kwargs
    assert kwargs["startedBy"] == "mcp-job"
    assert kwargs["overrides"]["containerOverrides"][0]["command"] == ["python", "manage.py", "run_mcp_job", job_id]
    assert MCPJob.objects.get(pk=job_id).task_arn == "arn:aws:ecs:task/1"
    assert tools == []  # not run on the worker

    # On the task: manage.py run_mcp_job <id>.
    from django.core.management import call_command

    call_command("run_mcp_job", job_id)
    job = MCPJob.objects.get(pk=job_id)
    assert (job.state, job.result) == (MCPJob.SUCCEEDED, {"doubled": 14})


@pytest.mark.django_db
@override_settings(MCP_JOBS_ON_ECS=True)
def test_housekeeping_fails_a_standalone_job_whose_machine_died():
    alice = _user()
    job = _queued_job(alice, tool=STANDALONE)
    MCPJob.objects.filter(pk=job.pk).update(runner=MCPJob.STANDALONE, state=MCPJob.RUNNING, task_arn="arn:k")
    stopped = {
        "taskArn": "arn:k",
        "stoppedReason": "Essential container in task exited",
        "containers": [{"reason": "OutOfMemoryError", "exitCode": 137}],
        "overrides": {"containerOverrides": [{"command": ["python", "manage.py", "run_mcp_job", str(job.pk)]}]},
    }
    client = mock.Mock()
    client.list_tasks.side_effect = lambda **kw: {"taskArns": ["arn:k"] if kw["desiredStatus"] == "STOPPED" else []}
    client.describe_tasks.return_value = {"tasks": [stopped]}

    out = jobs.housekeeping(ecs=client)

    assert out["failed"] == [str(job.pk)]
    job.refresh_from_db()
    assert job.state == MCPJob.FAILED
    assert "OutOfMemoryError" in job.error


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(MCP_JOB_RETENTION_DAYS=7, MCP_JOB_LOST_AFTER_SECONDS=2 * 3600)
def test_housekeeping_fails_lost_jobs_and_prunes_old_ones_only():
    alice = _user()
    now = timezone.now()
    lost = _queued_job(alice)
    MCPJob.objects.filter(pk=lost.pk).update(state=MCPJob.RUNNING, lease_until=now - timedelta(hours=3))
    alive = _queued_job(alice)
    MCPJob.objects.filter(pk=alive.pk).update(state=MCPJob.RUNNING, lease_until=now + timedelta(minutes=1))
    old = _queued_job(alice)
    MCPJob.objects.filter(pk=old.pk).update(state=MCPJob.SUCCEEDED, finished_at=now - timedelta(days=8))
    recent = _queued_job(alice)
    MCPJob.objects.filter(pk=recent.pk).update(state=MCPJob.SUCCEEDED, finished_at=now - timedelta(days=6))

    out = jobs.housekeeping(now=now)

    assert out["lost"] == [str(lost.pk)]
    assert MCPJob.objects.get(pk=lost.pk).state == MCPJob.FAILED
    assert MCPJob.objects.get(pk=alive.pk).state == MCPJob.RUNNING
    assert out["pruned"] == 1
    assert not MCPJob.objects.filter(pk=old.pk).exists()
    assert MCPJob.objects.filter(pk=recent.pk).exists()
