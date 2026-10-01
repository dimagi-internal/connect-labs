"""Synthetic generation runs on the worker, under a system-wide slot cap, behind one
"clone this opportunity" entry point."""

from unittest.mock import MagicMock

import pytest

import connect_labs.mcp.tools.synthetic as synthetic
from connect_labs.labs.synthetic import tasks
from connect_labs.mcp import token_scopes, visit_access
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.users.models import User


@pytest.fixture
def user(db):
    return User.objects.create(username="jobs", email="jobs@dimagi.com")


@pytest.fixture
def queued(monkeypatch):
    """Capture apply_async instead of reaching a broker."""
    calls = []
    # As if the call arrived over HTTP: only those are moved to the worker.
    monkeypatch.setattr(synthetic, "_in_web_request", lambda: True)
    monkeypatch.setattr(tasks.run_synthetic_tool, "apply_async", lambda **kw: calls.append(kw))
    monkeypatch.setattr(tasks.run_synthetic_clone_opp, "apply_async", lambda **kw: calls.append(kw))
    return calls


class _Result:
    def __init__(self, state, value):
        self.state, self.result = state, value

    def ready(self):
        return self.state in ("SUCCESS", "FAILURE")

    def failed(self):
        return self.state == "FAILURE"


def _async_result(monkeypatch, state, value):
    import celery.result

    monkeypatch.setattr(celery.result, "AsyncResult", lambda task_id, app=None: _Result(state, value))


# ---------------------------------------------------------------------------
# Heavy tools queue their work on the worker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_heavy_tool_queues_on_the_worker_and_returns_a_job_when_not_waiting(user, queued, monkeypatch):
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: True)
    out = get_tool("synthetic_env_ensure").handler(user=user, env="kmc", wait=False)

    assert out["state"] == "QUEUED" and out["poll_with"] == "synthetic_job_status"
    (call,) = queued
    assert call["kwargs"]["tool_name"] == "synthetic_env_ensure"
    assert call["kwargs"]["arguments"] == {"env": "kmc"}
    assert call["kwargs"]["restricted"] is True  # carried to the worker
    assert call["task_id"] == out["task_id"]


@pytest.mark.django_db
def test_a_waiting_tool_returns_the_jobs_result_unchanged(user, queued, monkeypatch):
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: False)
    _async_result(monkeypatch, "SUCCESS", {"ensured": True})

    out = get_tool("synthetic_env_ensure").handler(user=user, env="kmc")

    assert out["ensured"] is True and out["task_id"]


@pytest.mark.django_db
def test_a_tool_error_on_the_worker_reaches_the_caller_as_itself(user, queued, monkeypatch):
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: False)
    _async_result(monkeypatch, "FAILURE", MCPToolError("NOT_FOUND", "no such env"))

    with pytest.raises(MCPToolError) as e:
        get_tool("synthetic_env_ensure").handler(user=user, env="nope")
    assert e.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_on_the_worker_the_tool_body_runs_instead_of_queueing_again(user, queued, monkeypatch):
    from connect_labs.labs.synthetic.ensure import engine, registry

    monkeypatch.setattr(registry, "get_env_path", lambda env: f"/envs/{env}.yaml")
    monkeypatch.setattr(engine, "ensure_synthetic_data", lambda path, fresh=False: {"path": path, "fresh": fresh})

    out = synthetic.run_on_this_worker("synthetic_env_ensure", user, {"env": "kmc"})

    assert out == {"path": "/envs/kmc.yaml", "fresh": False}
    assert queued == []


@pytest.mark.django_db
def test_the_worker_runs_as_restricted_as_the_call_that_queued_it(user, monkeypatch):
    seen = {}

    def body(tool_name, u, arguments, progress=None):
        seen["restricted"] = visit_access.caller_restricted()
        return {}

    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(synthetic, "run_on_this_worker", body)
    tasks.run_synthetic_tool.run(tool_name="synthetic_env_ensure", arguments={}, user_id=user.pk, restricted=True)
    assert seen["restricted"] is True
    assert visit_access.caller_restricted() is False  # and only inside the job


# ---------------------------------------------------------------------------
# The system-wide slot cap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_only_n_synthetic_jobs_hold_a_slot_at_once(settings):
    from django.core.cache import cache

    cache.clear()
    settings.SYNTHETIC_JOB_SLOTS = 2
    a, b = tasks._acquire_slot("a"), tasks._acquire_slot("b")
    assert {a, b} == {0, 1}
    assert tasks._acquire_slot("c") is None
    tasks._release_slot(a, "a")
    assert tasks._acquire_slot("c") == a


@pytest.mark.django_db
def test_a_job_with_no_free_slot_waits_on_the_queue(settings):
    from django.core.cache import cache

    cache.clear()
    settings.SYNTHETIC_JOB_SLOTS = 0
    ran = []

    @tasks.holds_a_synthetic_slot
    def body(self):
        ran.append(True)

    task = MagicMock()
    task.request.id = "t"
    task.retry.side_effect = RuntimeError("retry")
    with pytest.raises(RuntimeError, match="retry"):
        body(task)
    assert ran == []


# ---------------------------------------------------------------------------
# The starting point
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clone_opp_checks_access_then_queues_one_job(user, queued, monkeypatch):
    from django.core.cache import cache

    cache.clear()
    checked = []
    monkeypatch.setattr(synthetic, "_require_opportunity_access", lambda u, opp: checked.append(opp))
    monkeypatch.setattr(synthetic, "require_connect_token", lambda u: "tok")
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: True)

    out = get_tool("synthetic_clone_opp").handler(user=user, source_opportunity_ids=[523, 874])

    assert checked == [523, 874]
    assert out["poll_with"] == "synthetic_job_status"
    (call,) = queued
    assert call["kwargs"]["source_opportunity_ids"] == [523, 874]
    assert call["kwargs"]["case_timelines"] is True  # the default
    assert call["kwargs"]["restricted"] is True
    # The owner can follow it; nobody else can.
    other = User.objects.create(username="other", email="other@dimagi.com")
    with pytest.raises(MCPToolError):
        get_tool("synthetic_job_status").handler(user=other, task_id=out["task_id"])


@pytest.mark.django_db
def test_clone_opp_refuses_a_source_the_caller_cannot_access(user, queued, monkeypatch):
    def deny(u, opp):
        raise MCPToolError("PERMISSION_DENIED", "no")

    monkeypatch.setattr(synthetic, "_require_opportunity_access", deny)
    with pytest.raises(MCPToolError):
        get_tool("synthetic_clone_opp").handler(user=user, source_opportunity_ids=[1])
    assert queued == []


@pytest.mark.django_db
def test_job_status_says_where_it_is_in_plain_words(user, monkeypatch):
    synthetic._set_job_owner(user, "job-1")
    import celery.result

    class _Pending:
        state, info = "RETRY", None

    monkeypatch.setattr(celery.result, "AsyncResult", lambda task_id, app=None: _Pending())
    out = get_tool("synthetic_job_status").handler(user=user, task_id="job-1")
    assert "Waiting for a free synthetic slot" in out["status"]


def test_the_safe_address_offers_the_starting_point_not_the_step_by_step_tools():
    assert {"synthetic_clone_opp", "synthetic_job_status"} <= token_scopes.RESTRICTED_TOOLS
    assert (
        not {
            "synthetic_generate_opp",
            "synthetic_profile_opp",
            "synthetic_generate_from_manifest",
            "synthetic_clone_generate",
        }
        & token_scopes.RESTRICTED_TOOLS
    )


def test_every_heavy_tool_takes_wait():
    for name in (
        "synthetic_generate_from_manifest",
        "synthetic_env_ensure",
        "synthetic_fidelity_vs_source",
        "synthetic_generate_opp",
        "synthetic_generate_opps_bulk",
        "synthetic_clone_generate",
    ):
        assert "wait" in get_tool(name).input_schema["properties"], name
        assert hasattr(get_tool(name).handler, "runs_on_worker"), name


@pytest.mark.django_db
def test_an_in_process_call_runs_inline_as_before(user, monkeypatch):
    from connect_labs.labs.synthetic.ensure import engine, registry

    monkeypatch.setattr(registry, "get_env_path", lambda env: f"/envs/{env}.yaml")
    monkeypatch.setattr(engine, "ensure_synthetic_data", lambda path, fresh=False: {"inline": True})
    queued = []
    monkeypatch.setattr(tasks.run_synthetic_tool, "apply_async", lambda **kw: queued.append(kw))

    assert get_tool("synthetic_env_ensure").handler(user=user, env="kmc") == {"inline": True}
    assert queued == []
