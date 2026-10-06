"""A worker restart (every labs deploy) fails the synthetic jobs it held, and says so."""

from types import SimpleNamespace
from unittest import mock

import pytest
from celery.exceptions import Ignore
from django.core.cache import cache

from connect_labs.labs.synthetic import tasks

CLONE = "connect_labs.labs.synthetic.tasks.run_synthetic_clone_opp"


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def test_only_synthetic_jobs_are_failed_and_their_slots_freed():
    cache.set("synthetic:job-slot:0", "job-running", 600)
    cache.set("synthetic:job-slot:1", "someone-else", 600)
    held = [
        SimpleNamespace(name=CLONE, id="job-running"),
        SimpleNamespace(name=CLONE, id="job-waiting"),
        SimpleNamespace(name="connect_labs.pulse.tasks.poll_visit_tail", id="pulse-1"),
    ]
    with mock.patch.object(tasks.celery_app.backend, "store_result") as store:
        failed = tasks.fail_jobs_held_by_this_worker(held)

    assert failed == ["job-running", "job-waiting"]
    stored = {call.args[0]: (str(call.args[1]), call.args[2]) for call in store.call_args_list}
    assert stored == {
        "job-running": (tasks.RESTARTED, "FAILURE"),
        "job-waiting": (tasks.RESTARTED, "FAILURE"),
    }
    # Its own slot is freed; another job's is not touched.
    assert cache.get("synthetic:job-slot:0") is None
    assert cache.get("synthetic:job-slot:1") == "someone-else"


def test_a_late_copy_of_a_job_a_restart_failed_does_not_run():
    ran = mock.Mock()

    @tasks.holds_a_synthetic_slot
    def job(self):
        ran()

    task = SimpleNamespace(request=SimpleNamespace(id="job-1"), retry=mock.Mock())
    with mock.patch.object(tasks, "_failed_by_restart", return_value=True), pytest.raises(Ignore):
        job(task)
    ran.assert_not_called()
    # It took no slot either.
    assert cache.get("synthetic:job-slot:0") is None


def test_a_job_not_failed_by_a_restart_runs_and_gives_its_slot_back():
    @tasks.holds_a_synthetic_slot
    def job(self):
        assert cache.get("synthetic:job-slot:0") == "job-2"
        return "done"

    task = SimpleNamespace(request=SimpleNamespace(id="job-2"), retry=mock.Mock())
    with mock.patch.object(tasks, "_failed_by_restart", return_value=False):
        assert job(task) == "done"
    assert cache.get("synthetic:job-slot:0") is None


def test_the_shutdown_handler_is_connected():
    from celery import signals

    receivers = [ref() for _, ref in signals.worker_shutting_down.receivers]
    assert tasks._on_worker_shutting_down in receivers
