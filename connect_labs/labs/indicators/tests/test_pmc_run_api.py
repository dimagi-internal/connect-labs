"""The live-EMOD run endpoints and Celery task: caching, queueing, retry of failures, lease serialisation."""

from __future__ import annotations

import pathlib
import threading
import time
from datetime import timedelta

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.indicators.emod import runner, service, tasks
from connect_labs.labs.indicators.models import PmcModelRun

REPO = pathlib.Path(__file__).resolve().parents[4]
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
    settings.PMC_STATE_GRID_PATH = "/nonexistent/pmc_state_grid.json"  # no fitted states unless a test opts in
    monkeypatch.setattr(runner, "state_fit", lambda state: "near")
    # The instance's results bucket: empty unless a test puts a finished result in it.
    s3 = S3()
    monkeypatch.setattr(runner, "default_s3", lambda: s3)
    return s3


class S3:
    """A results bucket that answers a missing key the way S3 does (ClientError NoSuchKey)."""

    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        from unittest.mock import MagicMock

        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = self.objects[Key]
        return {"Body": MagicMock(read=lambda: body)}

    def finish(self, run, result):
        import json

        self.objects[f"results/{run.inputs_hash}.json"] = json.dumps(result).encode()


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
        assert r.json() == {"run_id": run.pk, "status": status, "cached": False, "wait": service.WAIT_COLD}
        assert mock_delay == []

    def test_a_running_row_that_missed_three_heartbeats_is_requeued_once(
        self, client_in, mock_delay, django_capture_on_commit_callbacks
    ):
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        dead_since = timezone.now() - timedelta(seconds=runner.STALE_RUNNING_AFTER_S + 30)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=dead_since)
        with django_capture_on_commit_callbacks(execute=True):
            r = post(client_in, state="Ondo", schedules=[MONTHLY])
            assert post(client_in, state="Ondo", schedules=[MONTHLY]).status_code == 202
        assert r.status_code == 202 and r.json()["status"] == "queued"
        assert mock_delay == [run.pk]
        run.refresh_from_db()
        assert run.status == PmcModelRun.QUEUED and run.updated_at > dead_since
        assert run.timings["submitted_at"] > dead_since.timestamp()

    def test_a_running_row_inside_three_heartbeats_is_left_alone(
        self, client_in, mock_delay, django_capture_on_commit_callbacks
    ):
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        recent = timezone.now() - timedelta(seconds=runner.STALE_RUNNING_AFTER_S - 30)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=recent)
        with django_capture_on_commit_callbacks(execute=True):
            post(client_in, state="Ondo", schedules=[MONTHLY])
        assert mock_delay == []
        run.refresh_from_db()
        assert run.status == PmcModelRun.RUNNING

    def test_stale_queued_run_is_reenqueued_once(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        run = make_run([MONTHLY], PmcModelRun.QUEUED)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(seconds=300))
        with django_capture_on_commit_callbacks(execute=True):
            assert post(client_in, state="Ondo", schedules=[MONTHLY]).status_code == 202
            assert post(client_in, state="Ondo", schedules=[MONTHLY]).status_code == 202
        assert mock_delay == [run.pk]

    def test_fresh_queued_run_is_left_alone(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        make_run([MONTHLY], PmcModelRun.QUEUED)
        with django_capture_on_commit_callbacks(execute=True):
            post(client_in, state="Ondo", schedules=[MONTHLY])
        assert mock_delay == []

    def test_cached_result_served_without_configuration(self, client_in, settings, mock_delay):
        run = make_run([MONTHLY], PmcModelRun.COMPLETED, result={"ok": 1})
        settings.LABS_EMOD_BUCKET = None
        settings.LABS_EMOD_INSTANCE_ID = None
        r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 200 and r.json()["run_id"] == run.pk and r.json()["cached"] is True

    def test_requeued_failed_run_gets_a_fresh_updated_at_and_eta(
        self, client_in, mock_delay, django_capture_on_commit_callbacks
    ):
        run = make_run([MONTHLY], PmcModelRun.FAILED, error="boom")
        old = timezone.now() - timedelta(hours=1)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=old)
        with django_capture_on_commit_callbacks(execute=True):
            post(client_in, state="Ondo", schedules=[MONTHLY])
        run.refresh_from_db()
        assert run.updated_at > old + timedelta(minutes=59)
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["eta_s"] > 250

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

    def test_missing_configuration_is_503_with_the_public_sentence(self, client_in, settings, mock_delay):
        settings.LABS_EMOD_BUCKET = None
        r = post(client_in, state="Ondo", schedules=[MONTHLY])
        assert r.status_code == 503 and r.json()["error"] == "The live model is unavailable right now."
        assert "LABS_EMOD" not in r.content.decode()
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

    def test_failed_carries_the_public_error_never_the_internal_one(self, client_in):
        run = make_run([MONTHLY], PmcModelRun.FAILED, error="runner i-0abc123 is 'stopping' (InstanceGone)")
        r = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk]))
        body = r.json()
        assert body["status"] == "failed" and body["result"] is None
        assert body["error"] == "The live model is unavailable right now."
        assert "i-0abc123" not in r.content.decode()
        run.refresh_from_db()
        assert "i-0abc123" in run.error  # kept for logs and admins

    def test_a_busy_timeout_says_busy(self, client_in):
        run = make_run([MONTHLY], PmcModelRun.FAILED, error="runner busy: another model run held the worker")
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert "busy" in body["error"]

    def test_the_payload_carries_the_label_and_caveats(self, client_in):
        from connect_labs.labs.indicators import pmc

        run = make_run([MONTHLY], PmcModelRun.COMPLETED, result={"a": 1})
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["label"] == "illustrative \u00b7 live model run"
        assert body["caveats"] == list(pmc.CAVEATS) and body["caveats"]

    def test_a_running_rows_eta_counts_from_its_claim_not_its_last_heartbeat(self, client_in):
        started = timezone.now().timestamp() - 100
        run = make_run([MONTHLY], PmcModelRun.RUNNING, timings={"expected_s": 120, "started_at": started})
        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()
        assert body["eta_s"] <= 20  # updated_at is fresh (a heartbeat), but 100 s of 120 are gone

    def test_unknown_run_is_404(self, client_in):
        assert client_in.get(reverse("targeting:pmc_run_status", args=[999999])).status_code == 404


def runner_eta_cold():
    from connect_labs.labs.indicators.emod.service import PMC_RUN_ETA_COLD_S

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
        monkeypatch.setattr(tasks, "LEASE_POLL_S", 0.02)
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

    def test_lost_lease_is_recorded_in_timings(self, monkeypatch, caplog):
        r = FakeRedis()
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "HEARTBEAT_S", 0.01)

        def slow_execute(run, instance, bucket, reclaim_stale_after_s=None):
            r._d["ondemand:emod:lock"] = "thief"  # another holder took the lease
            time.sleep(0.2)
            PmcModelRun.objects.filter(pk=run.pk).update(status=PmcModelRun.COMPLETED, timings={"total_s": 1})

        monkeypatch.setattr(tasks, "execute", slow_execute)
        run = make_run([MONTHLY])
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.timings == {"total_s": 1, "lease_lost": True}
        assert any(rec.levelname == "ERROR" and "lease lost" in rec.message for rec in caplog.records)

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

    def test_a_duplicate_task_for_a_completed_row_does_nothing(self, monkeypatch):
        monkeypatch.setattr(tasks, "_redis", lambda: pytest.fail("must not touch the lease"))
        monkeypatch.setattr(tasks, "execute", lambda *a, **k: pytest.fail("must not execute"))
        run = make_run([MONTHLY], PmcModelRun.COMPLETED, result={"kept": 1})
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.COMPLETED and run.result == {"kept": 1}

    def test_a_duplicate_that_waited_behind_the_finishing_task_does_nothing(self, monkeypatch):
        r = FakeRedis()
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "execute", lambda *a, **k: pytest.fail("must not execute"))
        run = make_run([MONTHLY])

        def acquire_after_the_other_finished(self, owner):
            PmcModelRun.objects.filter(pk=run.pk).update(status=PmcModelRun.COMPLETED)
            return bool(r.set("ondemand:emod:lock", owner, nx=True))

        monkeypatch.setattr(tasks.Lease, "acquire", acquire_after_the_other_finished)
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.COMPLETED
        assert r.get("ondemand:emod:lock") is None

    def test_a_double_click_runs_the_model_once(self, monkeypatch):
        from connect_labs.labs.indicators.tests.test_emod_runner import FakeInstance, FakeS3

        r, s3 = FakeRedis(), FakeS3()
        inst = FakeInstance(s3, "test-bucket")
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: inst)
        monkeypatch.setattr(tasks, "LEASE_POLL_S", 0.02)
        real_execute = tasks.execute

        def slow_execute(run, instance, bucket, reclaim_stale_after_s=None):
            time.sleep(0.2)  # long enough for the second task to be waiting on the lease
            real_execute(run, instance, bucket, s3=s3, reclaim_stale_after_s=reclaim_stale_after_s)

        monkeypatch.setattr(tasks, "execute", slow_execute)
        run = make_run([MONTHLY])
        ts = [threading.Thread(target=self._run, args=(run.pk,)) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert [c[0] for c in inst.calls].count("run") == 1
        run.refresh_from_db()
        assert run.status == PmcModelRun.COMPLETED

    def test_an_unreachable_lease_store_fails_the_run_as_unavailable(self, monkeypatch):
        import redis

        class DownRedis(FakeRedis):
            def set(self, *a, **k):
                raise redis.exceptions.ConnectionError("Error 111 connecting to 10.0.0.5:6379")

        monkeypatch.setattr(tasks, "_redis", lambda: DownRedis())
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "execute", lambda *a, **k: pytest.fail("must not execute"))
        run = make_run([MONTHLY])
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.FAILED
        assert run.error == "live model unavailable: coordination store unreachable"
        assert service.public_error(run.error) == service.PUBLIC_UNAVAILABLE

    def test_an_unreachable_lease_store_at_construction_also_fails_the_run(self, monkeypatch):
        import redis

        def boom():
            raise redis.exceptions.ConnectionError("no route")

        monkeypatch.setattr(tasks, "_redis", boom)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        run = make_run([MONTHLY])
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.FAILED and "coordination store unreachable" in run.error

    def test_waiting_for_the_lease_keeps_a_queued_row_fresh(self, monkeypatch):
        r = FakeRedis()
        r.set("ondemand:emod:lock", "someone-else", nx=True)
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "LEASE_WAIT_S", 0.2)
        monkeypatch.setattr(tasks, "LEASE_POLL_S", 0.02)
        monkeypatch.setattr(tasks, "_fail", lambda run, message: None)  # keep the row queued to inspect it
        run = make_run([MONTHLY])
        old = timezone.now() - timedelta(seconds=service.PMC_RUN_REQUEUE_AFTER_S + 60)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=old)
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        # A request now would not mistake it for a lost enqueue.
        assert run.updated_at > timezone.now() - timedelta(seconds=5)

    def test_waiting_for_the_lease_does_not_touch_a_running_row(self, monkeypatch):
        """A running row is the lease holder's; if that worker died the row must age so it can be reclaimed."""
        r = FakeRedis()
        r.set("ondemand:emod:lock", "dead-worker", nx=True)
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: object())
        monkeypatch.setattr(tasks, "LEASE_WAIT_S", 0.1)
        monkeypatch.setattr(tasks, "LEASE_POLL_S", 0.02)
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        old = timezone.now() - timedelta(seconds=100)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=old)
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.updated_at == old and run.status == PmcModelRun.RUNNING

    def test_a_dead_workers_running_row_is_taken_over_once_its_lease_expires(self, monkeypatch):
        from connect_labs.labs.indicators.tests.test_emod_runner import FakeInstance, FakeS3

        r, s3 = FakeRedis(), FakeS3()
        inst = FakeInstance(s3, "test-bucket")
        monkeypatch.setattr(tasks, "_redis", lambda: r)
        monkeypatch.setattr(tasks, "_instance", lambda: inst)
        real_execute = tasks.execute

        def execute(run, instance, bucket, reclaim_stale_after_s=None):
            real_execute(run, instance, bucket, s3=s3, reclaim_stale_after_s=reclaim_stale_after_s)

        monkeypatch.setattr(tasks, "execute", execute)
        run = make_run([MONTHLY], PmcModelRun.RUNNING)  # its worker was killed; the lease has expired
        PmcModelRun.objects.filter(pk=run.pk).update(
            updated_at=timezone.now() - timedelta(seconds=runner.STALE_RUNNING_AFTER_S + 60)
        )
        tasks.run_pmc_model.run(run.pk)
        run.refresh_from_db()
        assert run.status == PmcModelRun.COMPLETED

    def test_the_heartbeat_touches_the_row(self):
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        old = timezone.now() - timedelta(seconds=500)
        PmcModelRun.objects.filter(pk=run.pk).update(updated_at=old)

        class L:
            def refresh(self, owner):
                return True

        stop = threading.Event()
        t = threading.Thread(target=tasks._heartbeat, args=(L(), "o", stop, 0.01, None, run.pk))
        t.start()
        time.sleep(0.1)
        stop.set()
        t.join()
        run.refresh_from_db()
        assert run.updated_at > old + timedelta(seconds=400)


def test_an_orphaned_lease_frees_within_five_minutes_and_is_refreshed_well_inside_that():
    assert tasks.LEASE_TTL_S <= 300
    assert tasks.HEARTBEAT_S == runner.ROW_HEARTBEAT_S == 60
    assert tasks.LEASE_TTL_S >= 3 * tasks.HEARTBEAT_S  # a missed beat or two does not lose the lease
    assert runner.STALE_RUNNING_AFTER_S == 3 * runner.ROW_HEARTBEAT_S


def test_the_celery_worker_registers_the_task():
    """autodiscover_tasks() imports only <app>.tasks: checked in a fresh interpreter, because this test
    module has already imported emod.tasks itself."""
    import os
    import subprocess
    import sys

    code = (
        "import django; django.setup()\n"
        "from config.celery_app import app\n"
        "app.loader.import_default_modules()\n"
        "app.finalize()\n"
        "print('connect_labs.labs.indicators.emod.tasks.run_pmc_model' in app.tasks)\n"
    )
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(REPO), timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "True"


class TestWaitPhrase:
    def _run(self, **timings):
        return PmcModelRun(status=PmcModelRun.QUEUED, timings=timings)

    def test_warm(self):
        assert service.wait_phrase(self._run(expected_s=120, queued_behind_s=0)) == "about two minutes"

    def test_cold_says_the_server_is_starting(self):
        assert service.wait_phrase(self._run(expected_s=300)) == (
            "about five minutes -- the model server is starting up"
        )

    def test_behind_another_run(self):
        assert service.wait_phrase(self._run(expected_s=120, queued_behind_s=200)) == (
            "about two minutes, after the run ahead finishes"
        )

    def test_keyed_on_the_instance_state_not_the_countdown(self):
        # A cold run nearly done still reads as a cold start, not 'two minutes'.
        run = PmcModelRun(status=PmcModelRun.RUNNING, timings={"expected_s": 300, "queued_behind_s": 100})
        assert service.wait_phrase(run) == service.WAIT_COLD


class TestBusyBound:
    def test_the_http_view_refuses_new_work_with_429_past_three_in_flight(self, client_in, mock_delay):
        for months in (1, 2, 3):
            make_run([{"code": f"c{months}", "rounds": [[0, 30, months, 0.25, 2.0, 0.85]]}], PmcModelRun.RUNNING)
        r = post(client_in, state="Ondo", schedules=[CUSTOM])
        assert r.status_code == 429 and "busy" in r.json()["error"]
        assert mock_delay == []

    def test_joining_an_in_flight_run_is_still_allowed(self, client_in, mock_delay):
        for months in (1, 2):
            make_run([{"code": f"c{months}", "rounds": [[0, 30, months, 0.25, 2.0, 0.85]]}], PmcModelRun.RUNNING)
        make_run([CUSTOM], PmcModelRun.QUEUED)
        assert post(client_in, state="Ondo", schedules=[CUSTOM]).status_code == 202

    def test_a_dead_row_does_not_count_as_load(self, client_in, mock_delay, django_capture_on_commit_callbacks):
        for months in (1, 2, 3):
            run = make_run([{"code": f"c{months}", "rounds": [[0, 30, months, 0.25, 2.0, 0.85]]}], PmcModelRun.RUNNING)
            PmcModelRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(hours=3))
        with django_capture_on_commit_callbacks(execute=True):
            assert post(client_in, state="Ondo", schedules=[CUSTOM]).status_code == 202


def kill(run):
    """The row as a deploy leaves it: running, its task gone, no heartbeat for over three beats."""
    dead_since = timezone.now() - timedelta(seconds=runner.STALE_RUNNING_AFTER_S + 30)
    PmcModelRun.objects.filter(pk=run.pk).update(status=PmcModelRun.RUNNING, updated_at=dead_since)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
class TestADeployKillsTheWaitingTask:
    """The instance finishes and writes S3 whatever happens to the labs task waiting on it."""

    def test_a_poll_completes_it_from_the_result_the_instance_wrote(self, client_in, configured, mock_delay):
        run = kill(make_run([MONTHLY]))
        configured.finish(run, {"runs": [{"seed": 0}]})

        body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()

        assert body["status"] == "completed" and body["result"] == {"runs": [{"seed": 0}]}
        assert body["timings"]["recovered_from_s3"] is True
        assert mock_delay == []  # nothing re-run

    def test_a_resubmit_answers_from_s3_instead_of_running_it_again(self, client_in, configured, mock_delay):
        run = kill(make_run([MONTHLY]))
        configured.finish(run, {"runs": []})

        r = post(client_in, state="Ondo", schedules=[MONTHLY])

        assert r.status_code == 200 and r.json()["cached"] is True and r.json()["result"] == {"runs": []}
        assert mock_delay == []

    def test_with_no_result_yet_a_poll_requeues_it_once(
        self, client_in, configured, mock_delay, django_capture_on_commit_callbacks
    ):
        run = kill(make_run([MONTHLY]))
        url = reverse("targeting:pmc_run_status", args=[run.pk])

        with django_capture_on_commit_callbacks(execute=True):
            first = client_in.get(url).json()
            client_in.get(url)

        assert first["status"] == "queued"
        assert mock_delay == [run.pk]

    def test_a_live_run_is_left_alone(self, client_in, configured, mock_delay):
        run = make_run([MONTHLY], PmcModelRun.RUNNING)
        configured.finish(run, {"runs": []})  # a result already there must not short-cut a live task

        assert client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()["status"] == "running"

    def test_the_sweep_heals_dead_runs_without_anyone_polling(
        self, configured, mock_delay, django_capture_on_commit_callbacks
    ):
        finished = kill(make_run([MONTHLY]))
        configured.finish(finished, {"runs": []})
        lost = kill(make_run([CUSTOM]))

        with django_capture_on_commit_callbacks(execute=True):
            assert tasks.sweep_dead_pmc_runs() == 2

        finished.refresh_from_db()
        lost.refresh_from_db()
        assert finished.status == PmcModelRun.COMPLETED and lost.status == PmcModelRun.QUEUED
        assert mock_delay == [lost.pk]

    def test_an_s3_error_falls_back_to_rerunning(
        self, client_in, monkeypatch, mock_delay, django_capture_on_commit_callbacks
    ):
        run = kill(make_run([MONTHLY]))

        def broken():
            raise RuntimeError("no credentials")

        monkeypatch.setattr(runner, "default_s3", broken)
        with django_capture_on_commit_callbacks(execute=True):
            body = client_in.get(reverse("targeting:pmc_run_status", args=[run.pk])).json()

        assert body["status"] == "queued" and mock_delay == [run.pk]


def test_the_sweep_is_on_the_beat_schedule(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["heal-dead-pmc-runs"]
    assert entry["task"] == tasks.sweep_dead_pmc_runs.name
