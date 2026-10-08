"""The live-EMOD runner client: request building, hashing, and executing against a fake worker."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest
from canopy_sdk.ondemand import InstanceGone, OnDemandError

from connect_labs.labs.indicators.emod import runner
from connect_labs.labs.indicators.models import PmcModelRun
from connect_labs.labs.indicators.tests.test_resolve import make_boundary, set_value

REPO = pathlib.Path(__file__).resolve().parents[4]
SCHEDULE = {"code": "custom", "rounds": [[0, 91, 8, 0.25, 2.0, 0.85]]}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def worker_module():
    return _load("run_scenarios_under_test", REPO / "tools/pmc_emod/worker/run_scenarios.py")


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        body = self.objects[(Bucket, Key)]
        return {"Body": MagicMock(read=lambda: body)}


class Result:
    def __init__(self, ok=True, exit_code=0, status="Success", stdout="", stderr=""):
        self.ok, self.exit_code, self.status, self.stdout, self.stderr = ok, exit_code, status, stdout, stderr


class FakeInstance:
    """Plays the worker: on run(), writes a result next to the request, as the box would."""

    def __init__(self, s3, bucket, run_result=None, ensure_error=None):
        self.s3, self.bucket, self.run_result, self.ensure_error = s3, bucket, run_result, ensure_error
        self.calls = []

    def ensure_running(self, boot_timeout_s, ready_timeout_s):
        self.calls.append(("ensure_running", boot_timeout_s, ready_timeout_s))
        if self.ensure_error:
            raise self.ensure_error
        return MagicMock(cold=True, timings={"ec2_start_s": 100.0, "ready_wait_s": 20.0})

    def run(self, commands, timeout_s=600):
        self.calls.append(("run", commands, timeout_s))
        if self.run_result is not None:
            return self.run_result
        req_uri = commands[0].split("--request ")[1].split()[0]
        out_uri = commands[0].split("--out ")[1].split()[0]
        assert req_uri.startswith(f"s3://{self.bucket}/requests/")
        assert out_uri.startswith(f"s3://{self.bucket}/results/")
        self.s3.objects[(self.bucket, out_uri.split(f"s3://{self.bucket}/")[1])] = json.dumps(
            {"hash": "h", "runs": [{"code": "none", "seed": 0}], "seconds": 5}
        ).encode()
        return Result()


def _run():
    req = runner.build_request("Ondo", [SCHEDULE], fit="near")
    return PmcModelRun.objects.create(inputs_hash=runner.request_hash(req), request=req)


class TestBuildRequest:
    def test_default_setting_matches_the_sweep_it_is_copied_from(self, monkeypatch):
        # pmc_sweep imports emodpy/idmtools at module level; stub them, we only need default_setting().
        for name in (
            "manifest",
            "emodpy",
            "emodpy.campaign",
            "emodpy.campaign.common",
            "emodpy.emod_task",
            "idmtools",
            "idmtools.builders",
            "idmtools.core",
            "idmtools.core.platform_factory",
            "idmtools.entities",
            "idmtools.entities.experiment",
        ):
            monkeypatch.setitem(sys.modules, name, MagicMock())
        monkeypatch.setenv("PMC_POP", str(runner.DEFAULT_SETTING["pop"]))
        monkeypatch.delenv("PMC_LARVAL", raising=False)
        sweep = _load("pmc_sweep_under_test", REPO / "tools/pmc_emod/pmc_sweep.py")
        assert runner.DEFAULT_SETTING == sweep.default_setting()

    def test_request_passes_the_workers_own_validation(self, worker_module):
        req = runner.build_request("Ondo", [SCHEDULE], fit="near")
        worker_module.validate_request(req)
        assert [s["code"] for s in req["schedules"]] == ["none", "custom"]
        assert req["seeds"] == [0, 1, 2]
        assert req["intervention_years"] == 2
        assert req["setting"]["pop"] == 5000

    def test_lower_confidence_fit_still_runs(self):
        assert runner.build_request("X", [SCHEDULE], seeds=1, fit="prevalence_differs")["seeds"] == [0]

    @pytest.mark.parametrize("fit", ["more_seasonal", "less_seasonal", "unknown"])
    def test_unfit_state_is_refused_with_the_reason(self, fit):
        with pytest.raises(ValueError, match="Kano cannot be modelled"):
            runner.build_request("Kano", [SCHEDULE], fit=fit)

    def test_sahel_refusal_names_smc(self):
        with pytest.raises(ValueError, match="SMC, not PMC"):
            runner.build_request("Kano", [SCHEDULE], fit="more_seasonal")

    def test_the_baseline_code_is_reserved(self):
        with pytest.raises(ValueError, match="reserved"):
            runner.build_request("Ondo", [{"code": "none", "rounds": []}], fit="near")

    def test_building_does_not_mutate_the_shared_setting(self):
        runner.build_request("Ondo", [SCHEDULE], fit="near")["setting"]["pop"] = 1
        assert runner.DEFAULT_SETTING["pop"] == 5000

    @pytest.mark.django_db
    def test_fit_is_read_from_the_registry_the_explorer_uses(self):
        make_boundary("NGA", 0, "Nigeria", "NGA-0", x=0)
        ondo = make_boundary("NGA", 1, "Ondo", "NGA-1-28", x=2)
        kano = make_boundary("NGA", 1, "Kano", "NGA-1-20", x=4)
        set_value(ondo, "malaria_prevalence", 44.8, year=2021)
        set_value(ondo, "rain_wettest_quarter", 41.5)
        set_value(kano, "malaria_prevalence", 40.0, year=2021)
        set_value(kano, "rain_wettest_quarter", 75.0)
        assert runner.build_request("ondo", [SCHEDULE])["setting"]["name"] == "SW_Nigeria_like"
        with pytest.raises(ValueError, match="SMC, not PMC"):
            runner.build_request("Kano", [SCHEDULE])
        with pytest.raises(ValueError, match="unknown state"):
            runner.build_request("Atlantis", [SCHEDULE])


class TestRequestHash:
    def test_identical_inputs_hash_the_same_whatever_the_key_order(self):
        a = runner.build_request("Ondo", [SCHEDULE], fit="near")
        b = json.loads(json.dumps(a, sort_keys=True))
        assert runner.request_hash(a) == runner.request_hash(b)
        assert len(runner.request_hash(a)) == 64

    def test_different_inputs_hash_differently(self):
        a = runner.build_request("Ondo", [SCHEDULE], fit="near")
        b = runner.build_request("Ondo", [SCHEDULE], seeds=4, fit="near")
        assert runner.request_hash(a) != runner.request_hash(b)

    @pytest.mark.django_db
    def test_same_request_finds_the_same_run(self):
        req = runner.build_request("Ondo", [SCHEDULE], fit="near")
        first, created = runner.get_or_create_run(req)
        second, created_again = runner.get_or_create_run(req)
        assert created and not created_again and first.pk == second.pk


@pytest.mark.django_db
class TestExecute:
    def test_a_good_run_is_completed_with_result_and_timings(self):
        s3, run = FakeS3(), _run()
        inst = FakeInstance(s3, "bkt")
        runner.execute(run, inst, "bkt", s3=s3)
        run.refresh_from_db()
        assert run.status == "completed"
        assert run.result["runs"][0]["code"] == "none"
        assert run.completed_at is not None and run.error == ""
        assert run.timings["boot_ec2_start_s"] == 100.0 and run.timings["cold_start"] is True
        assert "worker_s" in run.timings and "total_s" in run.timings
        key = f"requests/{run.inputs_hash}.json"
        assert json.loads(s3.objects[("bkt", key)]) == run.request
        _, boot, ready = inst.calls[0]
        assert (boot, ready) == (600, 900)
        _, cmds, timeout = inst.calls[1]
        assert cmds[0].startswith("/opt/emod/.venv/bin/python /opt/emod/run_scenarios.py --request s3://bkt/requests/")
        assert timeout >= 900

    def test_a_gone_instance_marks_the_run_failed_with_the_message(self):
        s3, run = FakeS3(), _run()
        runner.execute(
            run, FakeInstance(s3, "bkt", ensure_error=InstanceGone("runner i-x is 'missing'")), "bkt", s3=s3
        )
        run.refresh_from_db()
        assert run.status == "failed"
        assert "is 'missing'" in run.error
        assert run.result is None and run.completed_at is not None

    def test_a_nonzero_worker_exit_marks_the_run_failed_with_stderr(self):
        s3, run = FakeS3(), _run()
        bad = Result(ok=False, exit_code=2, status="Failed", stderr="RequestError: seeds must be unique")
        runner.execute(run, FakeInstance(s3, "bkt", run_result=bad), "bkt", s3=s3)
        run.refresh_from_db()
        assert run.status == "failed"
        assert "exited 2" in run.error and "seeds must be unique" in run.error

    def test_an_sdk_error_is_recorded_not_raised(self):
        s3, run = FakeS3(), _run()
        runner.execute(run, FakeInstance(s3, "bkt", ensure_error=OnDemandError("not ready after 900s")), "bkt", s3=s3)
        run.refresh_from_db()
        assert run.status == "failed" and "not ready" in run.error

    def test_a_missing_result_object_fails_the_run(self):
        s3, run = FakeS3(), _run()
        inst = FakeInstance(s3, "bkt", run_result=Result())  # claims success, wrote nothing
        runner.execute(run, inst, "bkt", s3=s3)
        run.refresh_from_db()
        assert run.status == "failed" and run.error
