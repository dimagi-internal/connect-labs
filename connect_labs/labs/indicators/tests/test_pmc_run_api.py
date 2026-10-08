"""The live-EMOD run endpoints and Celery task: caching, queueing, retry of failures, lease serialisation."""

from __future__ import annotations

import threading
import time

import pytest
from django.db import connection
from django.urls import reverse

from connect_labs.labs.indicators.emod import runner, tasks
from connect_labs.labs.indicators.models import PmcModelRun

MONTHLY = {"code": "monthly", "rounds": [[0, 91, 8, 0.25, 2.0, 0.85]]}
CUSTOM = {"code": "custom", "rounds": [[10, 91, 4, 0.5, 2.0, 0.85]]}


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="tester", password="pw")  # noqa: S106


@pytest.fixture
def client_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture(autouse=True)
def configured(settings, monkeypatch):
    settings.LABS_EMOD_INSTANCE_ID = "i-test"
    settings.LABS_EMOD_REGION = "test-region-1"
    settings.LABS_EMOD_BUCKET = "test-bucket"
    monkeypatch.setattr(runner, "state_fit", lambda state: "near")


@pytest.fixture
def mock_delay(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks.run_pmc_model, "delay", lambda pk: calls.append(pk))
    return calls


def post(client, **body):
    return client.post(reverse("targeting:pmc_run"), body, content_type="application/json")


def make_run(schedules, status=PmcModelRun.QUEUED, **fields):
    req = runner.build_request("Ondo", schedules)
    run, _ = runner.get_or_create_run(req)
    PmcModelRun.objects.filter(pk=run.pk).update(status=status, **fields)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
class TestRunEndpoint:
    def test_requires_login_when_deployed(self, client, settings, mock_delay):
        settings.DEBUG = False
        assert post(client, state="Ondo", schedules=[MONTHLY]).status_code == 302
        assert client.get(reverse("targeting:pmc_run_status", args=[1])).status_code == 302

    def test_identical_inputs_hit_cache(self, client_in, mock_delay):
        run = make_run([MONTHLY], PmcModelRun.COMPLETED, result={"ok": 1}, timings={"total_s": 90})
        r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 200
        assert r.json() == {
            "run_id": run.pk,
            "status": "completed",
            "cached": True,
            "result": {"ok": 1},
            "timings": {"total_s": 90},
        }
        assert mock_delay == []

    def test_new_inputs_queue_a_run(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            r = post(client_in, state="Ondo", schedules=[CUSTOM])
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "queued" and body["cached"] is False
        assert mock_delay == [body["run_id"]]

    def test_enqueue_waits_for_commit(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=False):
            post(client_in, state="Ondo", schedules=[CUSTOM])
            assert mock_delay == []

    @pytest.mark.parametrize("status", [PmcModelRun.QUEUED, PmcModelRun.RUNNING])
    def test_in_flight_run_is_not_enqueued_again(
        self, client_in, mock_delay, status, django_capture_on_commit_callbacks
    ):
        run = make_run([MONTHLY], status)
        with django_capture_on_commit_callbacks(execute=True):
            r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 202
        assert r.json() == {"run_id": run.pk, "status": status, "cached": False}
        assert mock_delay == []

    def test_failed_run_is_requeued_on_the_same_row(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        run = make_run([MONTHLY], PmcModelRun.FAILED, error="boom")
        with django_capture_on_commit_callbacks(execute=True):
            r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 202 and r.json()["run_id"] == run.pk and r.json()["status"] == "queued"
        assert mock_delay == [run.pk]
        run.refresh_from_db()
        assert run.status == PmcModelRun.QUEUED and run.error == ""

    def test_refused_state_is_400_with_the_reason(self, client_in, monkeypatch, mock_delay):
        monkeypatch.setattr(runner, "state_fit", lambda state: "more_seasonal")
        r = post(client_in, state="Sokoto", schedules=[MONTHLY])
        assert r.status_code == 400 and "SMC, not PMC" in r.json()["error"]

    @pytest.mark.parametrize(
        "body",
        [
            {"schedules": [MONTHLY]},
            {"state": "Ondo"},
            {"state": "Ondo", "schedules": []},
            {"state": "Ondo", "schedules": [3]},
        ],
    )
    def test_malformed_input_is_400(self, client_in, mock_delay, body):
        assert post(client_in, **body).status_code == 400

    def test_bad_json_is_400(self, client_in):
        r = client_in.post(reverse("targeting:pmc_run"), "{nope", content_type="application/json")
        assert r.status_code == 400

    def test_missing_configuration_is_503(self, client_in, settings, mock_delay):
        settings.LABS_EMOD_BUCKET = None
        r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 503 and r.json()["error"].startswith("live model unavailable: ")
        assert mock_delay == []


@pytest.mark.django_db
class TestStatusEndpoint:
    def test_running_has_no_result_and_an_eta(self, client_in):
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["status"] == "running" and body["result"] is None and body["cached"] is False
        assert 10 <= body["eta_s"] <= runner_eta_cold()

    def test_completed_carries_result_and_zero_eta(self, client_in):
        run = make_run([MONTHLY], PmcModelRun.COMPLETED, result={"a": 1}, timings={"total_s": 5})
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["result"] == {"a": 1} and body["eta_s"] == 0 and body["timings"] == {"total_s": 5}

    def test_failed_carries_error(self, client_in):
        run = make_run([MONTHLY], PmcModelRun.FAILED, error="boom")
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["status"] == "failed" and body["error"] == "boom" and body["result"] is None

    def test_unknown_run_is_404(self, client_in):
        assert client_in.get(reverse("targeting:pmc_run_status", args=[999999])).status_code == 404


def runner_eta_cold():
    from connect_labs.labs.indicators.views import PMC_RUN_ETA_COLD_S

    return PMC_RUN_ETA_COLD_S


class FakeRedis:
    """The few Redis commands Lease uses, with Lua scripts recognised by their opcode."""

    def __init__(self):
        self._d = {}
        self._lock = threading.Lock()

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            if nx and key in self._d:
                return None
            self._d[key] = value
            return True

    def get(self, key):
        return self._d.get(key)

    def eval(self, lua, numkeys, key, owner, *args):
        with self._lock:
            if self._d.get(key) != owner:
                return 0
            if "'del'" in lua:
                del self._d[key]
            return 1


@pytest.mark.django_db(transaction=True)
class TestTask:
    def _run(self, run_id):
        try:
            tasks.run_pmc_model.run(run_id)
        finally:
            connection.close()

    def test_two_runs_share_one_instance(self, monkeypatch):
        r = FakeRedis()
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        spans = []

        def fake_execute(run, instance, bucket, reclaim_stale_after_s=None):
            t0 = time.monotonic()
            time.sleep(0.3)
            spans.append((t0, time.monotonic()))
            PmcModelRun.objects.filter(pk=run.pk).update(status=PmcModelRun.COMPLETED)

        monkeypatch.setattr(tasks, "execute", fake_execute)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks.Lease, "acquire_wait", _fast_wait)
        a, b = make_run([MONTHLY]), make_run([CUSTOM])
        ts = [threading.Thread(target=self._run, args=(x.pk,)) for x in (a, b)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        (s1, e1), (s2, e2) = sorted(spans)
        assert e1 <= s2
        assert r.get("ondemand:emod:lock") is None  # released

    def test_busy_runner_marks_run_failed(self, monkeypatch):
        r = FakeRedis()
        r.set("ondemand:emod:lock", "someone-else", nx=True, ex=1800)
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "LEASE_WAIT_S", 0)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "execute", lambda *a, **k: pytest.fail("must not execute"))
        run = make_run([MONTHLY])
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.FAILED and "runner busy" in run.error
        assert r.get("ondemand:emod:lock") == "someone-else"  # not ours to release

    def test_release_even_when_execute_raises(self, monkeypatch):
        r = FakeRedis()
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: object())

        def boom(*a, **k):
            raise RuntimeError("x")

        monkeypatch.setattr(tasks, "execute", boom)
        run = make_run([MONTHLY])
        with pytest.raises(RuntimeError):
            tasks.run_pmc_model.run(run.pk)
        assert r.get("ondemand:emod:lock") is None

    def test_unconfigured_deploy_fails_the_run(self, monkeypatch, settings):
        settings.LABS_EMOD_BUCKET = None
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        run = make_run([MONTHLY])
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.FAILED and "LABS_EMOD_BUCKET" in run.error

    def test_heartbeat_refreshes_the_lease(self):
        calls = []

        class L:
            def refresh(self, owner):
                calls.append(owner)
                return True

        stop = threading.Event()
        t = threading.Thread(target=tasks._heartbeat, args=(L(), "o", stop, 0.01))
        t.start()
        time.sleep(0.1)
        stop.set()
        t.join()
        assert len(calls) >= 2


def _fast_wait(self, owner, timeout_s, poll_s=2.0):
    """Poll quickly so the lease test does not sleep 2 s between attempts."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if self.acquire(owner):
            return True
        time.sleep(0.02)
    return False
