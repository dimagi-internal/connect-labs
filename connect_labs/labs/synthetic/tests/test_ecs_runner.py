"""Synthetic jobs run on a standalone ECS task of their own, which a deploy does not stop."""

from types import SimpleNamespace
from unittest import mock

import pytest
from celery.exceptions import Ignore
from django.core.cache import cache
from django.test import override_settings

from connect_labs.labs.synthetic import ecs_runner, tasks

WORKER_TD = "arn:aws:ecs:us-east-1:1:task-definition/labs-jj-worker:1368"
TASK_ARN = "arn:aws:ecs:us-east-1:1:task/labs-jj-cluster/abc"
NETWORK = {"awsvpcConfiguration": {"subnets": ["subnet-1"], "securityGroups": ["sg-1"], "assignPublicIp": "ENABLED"}}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cache.clear()
    monkeypatch.delenv(ecs_runner.RUNNER_ENV, raising=False)
    yield
    cache.clear()


def _ecs(running=0):
    client = mock.Mock()
    client.list_tasks.return_value = {"taskArns": [f"t{i}" for i in range(running)]}
    client.describe_services.return_value = {
        "services": [{"taskDefinition": WORKER_TD, "networkConfiguration": NETWORK}]
    }
    client.describe_task_definition.return_value = {"taskDefinition": {"containerDefinitions": [{"name": "worker"}]}}
    client.run_task.return_value = {"tasks": [{"taskArn": TASK_ARN}], "failures": []}
    return client


def _task(job_id="job-1", name="connect_labs.labs.synthetic.tasks.run_synthetic_clone_profile"):
    return SimpleNamespace(
        name=name,
        request=SimpleNamespace(id=job_id),
        retry=mock.Mock(side_effect=RuntimeError("retry")),
        update_state=mock.Mock(),
    )


def test_launch_runs_the_job_command_from_the_workers_current_task_definition():
    client = _ecs()
    arn = ecs_runner.launch("some.task", "job-1", {"spec_yaml": "x"}, client=client)

    assert arn == TASK_ARN
    kwargs = client.run_task.call_args.kwargs
    assert kwargs["taskDefinition"] == WORKER_TD
    assert kwargs["networkConfiguration"] == NETWORK
    assert kwargs["launchType"] == "FARGATE"
    assert kwargs["startedBy"] == ecs_runner.STARTED_BY
    override = kwargs["overrides"]["containerOverrides"][0]
    assert override["name"] == "worker"
    assert override["command"] == ["python", "manage.py", "run_synthetic_job", "job-1"]
    assert {"name": ecs_runner.RUNNER_ENV, "value": "1"} in override["environment"]
    # One math-library thread per forked process, or a cohort oversubscribes the machine.
    assert {"name": "OMP_NUM_THREADS", "value": "1"} in override["environment"]
    # The arguments wait for the task, and the task's arn is remembered for cancel.
    assert cache.get("synthetic:ecs-job:job-1") == {"task": "some.task", "kwargs": {"spec_yaml": "x"}}
    assert ecs_runner.task_arn("job-1") == TASK_ARN


def test_a_redelivered_message_does_not_launch_the_job_twice():
    client = _ecs()
    ecs_runner.launch("some.task", "job-1", {}, client=client)
    assert ecs_runner.launch("some.task", "job-1", {}, client=client) is None
    assert client.run_task.call_count == 1


def test_a_launch_ecs_refuses_can_be_retried():
    client = _ecs()
    client.run_task.return_value = {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]}
    with pytest.raises(RuntimeError, match="RESOURCE:CPU"):
        ecs_runner.launch("some.task", "job-1", {}, client=client)
    client.run_task.return_value = {"tasks": [{"taskArn": TASK_ARN}], "failures": []}
    assert ecs_runner.launch("some.task", "job-1", {}, client=client) == TASK_ARN


@override_settings(SYNTHETIC_JOBS_ON_ECS=True)
def test_the_celery_task_hands_the_job_off_and_records_no_result():
    ran = mock.Mock()

    @tasks.holds_a_synthetic_slot
    def job(self, **kwargs):
        ran()

    task = _task()
    with mock.patch.object(ecs_runner, "_ecs", return_value=_ecs()), pytest.raises(Ignore):
        job(task, spec_yaml="x", oauth_token="t")
    ran.assert_not_called()
    assert cache.get("synthetic:ecs-job:job-1")["kwargs"] == {"spec_yaml": "x", "oauth_token": "t"}
    assert task.update_state.call_args.kwargs["state"] == "PROGRESS"
    # It took no worker slot.
    assert cache.get("synthetic:job-slot:0") is None


@override_settings(SYNTHETIC_JOBS_ON_ECS=True, SYNTHETIC_ECS_MAX_TASKS=2)
def test_at_the_cap_the_job_waits_on_the_queue():
    @tasks.holds_a_synthetic_slot
    def job(self, **kwargs):
        raise AssertionError("must not run")

    task = _task()
    client = _ecs(running=2)
    with mock.patch.object(ecs_runner, "_ecs", return_value=client), pytest.raises(RuntimeError, match="retry"):
        job(task)
    client.run_task.assert_not_called()


@override_settings(SYNTHETIC_JOBS_ON_ECS=True)
def test_on_its_own_task_the_job_runs_without_a_slot(monkeypatch):
    monkeypatch.setenv(ecs_runner.RUNNER_ENV, "1")

    @tasks.holds_a_synthetic_slot
    def job(self, **kwargs):
        assert cache.get("synthetic:job-slot:0") is None
        return kwargs

    assert job(_task(), a=1) == {"a": 1}


def test_off_the_job_runs_on_the_worker_as_before():
    @tasks.holds_a_synthetic_slot
    def job(self):
        return "done"

    with (
        mock.patch.object(tasks, "_failed_by_restart", return_value=False),
        mock.patch.object(ecs_runner, "_ecs") as ecs,
    ):
        assert job(_task()) == "done"
    ecs.assert_not_called()


def test_run_job_records_progress_and_the_result_under_the_job_id(monkeypatch):
    calls = []

    def body(self, **kwargs):
        calls.append((self.request.id, kwargs))
        return {"ok": True}

    fake_task = mock.Mock()
    fake_task.run.side_effect = lambda **kw: body(SimpleNamespace(request=SimpleNamespace(id="job-9")), **kw)
    cache.set("synthetic:ecs-job:job-9", {"task": "t.name", "kwargs": {"x": 1}}, 60)
    with (
        mock.patch.dict(tasks.celery_app.tasks, {"t.name": fake_task}),
        mock.patch.object(tasks.celery_app.backend, "store_result") as store,
    ):
        assert ecs_runner.run_job("job-9") == {"ok": True}

    assert calls == [("job-9", {"x": 1})]
    fake_task.push_request.assert_called_once_with(id="job-9")
    store.assert_called_once_with("job-9", {"ok": True}, "SUCCESS")
    assert cache.get("synthetic:ecs-job:job-9") is None


def test_run_job_records_a_failure():
    fake_task = mock.Mock()
    fake_task.run.side_effect = ValueError("boom")
    cache.set("synthetic:ecs-job:job-9", {"task": "t.name", "kwargs": {}}, 60)
    with (
        mock.patch.dict(tasks.celery_app.tasks, {"t.name": fake_task}),
        mock.patch.object(tasks.celery_app.backend, "store_result") as store,
        pytest.raises(ValueError),
    ):
        ecs_runner.run_job("job-9")
    job_id, exc, state = store.call_args.args
    assert (job_id, str(exc), state) == ("job-9", "boom", "FAILURE")


def test_cancel_stops_the_jobs_task():
    client = _ecs()
    ecs_runner.launch("some.task", "job-1", {}, client=client)
    with (
        mock.patch.object(ecs_runner, "_ecs", return_value=client),
        mock.patch.object(tasks.celery_app.control, "revoke"),
        mock.patch.object(tasks.celery_app.backend, "store_result"),
    ):
        tasks.cancel_job("job-1")
    client.stop_task.assert_called_once()
    assert client.stop_task.call_args.kwargs["task"] == TASK_ARN


def test_profiling_is_parallel_only_on_a_jobs_own_machine(monkeypatch):
    from connect_labs.labs.synthetic import clone_from_prod

    with override_settings(SYNTHETIC_ECS_CPU="8192"):
        assert clone_from_prod._profile_parallelism() == 1
        monkeypatch.setenv(ecs_runner.RUNNER_ENV, "1")
        assert clone_from_prod._profile_parallelism() == 8


def test_on_its_own_machine_a_cohort_profiles_in_parallel_into_one_store(monkeypatch, tmp_path):
    """Real forked processes: every opportunity lands in the same bundle root, and one failure is skipped."""
    from connect_labs.labs.synthetic import clone_from_prod

    def fake_profile(sid, *, store, **kwargs):
        if sid == 3:
            raise RuntimeError("bad opp")
        (tmp_path / str(sid)).write_text(str(store.root if hasattr(store, "root") else store))
        return f"bundle-{sid}"

    monkeypatch.setattr(clone_from_prod, "profile_opp_to_bundle", fake_profile)
    monkeypatch.setenv(ecs_runner.RUNNER_ENV, "1")
    progress = mock.Mock()
    with override_settings(SYNTHETIC_ECS_CPU="2048"):
        resolved, handles = clone_from_prod.profile_opps_bulk(
            [1, 2, 3], base_url="x", oauth_token="t", bundle_root=str(tmp_path / "root"), progress=progress
        )

    assert resolved == str(tmp_path / "root")
    assert sorted(handles) == ["bundle-1", "bundle-2"]
    assert {p.name for p in tmp_path.iterdir()} >= {"1", "2"}
    # The last report says every opportunity is accounted for.
    assert progress.call_args.args[:2] == (3, 3)


def test_run_job_finds_a_task_whose_module_nothing_has_imported_yet():
    """A bare manage.py process has not autodiscovered tasks; run_job imports the task's module itself."""
    name = "connect_labs.labs.synthetic.tasks.run_synthetic_clone_profile"
    cache.set("synthetic:ecs-job:job-7", {"task": name, "kwargs": {}}, 60)
    real = tasks.celery_app.tasks[name]
    with (
        mock.patch.object(real, "run", return_value="ok"),
        mock.patch.object(tasks.celery_app.backend, "store_result"),
        mock.patch("importlib.import_module") as imp,
    ):
        assert ecs_runner.run_job("job-7") == "ok"
    imp.assert_called_once_with("connect_labs.labs.synthetic.tasks")
