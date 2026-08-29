"""The progress heartbeat must cost a bounded number of network round-trips.

These pin a COST, not a behaviour. The defect they guard against shipped and ran
for months while every functional test stayed green: `progress_callback` fires
PER IMAGE, and each call was a full read-modify-write of the whole workflow_run
record across the network to production Connect.

Measured on one real job (2026-08-26, 5h15m, 4,896 images): 10,737
`GET …type=workflow_run` against 5,902 writes — the 2:1 ratio being a duplicate
read, because `_update_job_state` fetched the run and then `update_run_state`
fetched it again. About 42 minutes of a 5-hour job spent maintaining a counter.

A test that asserted "progress is reported" passes on every one of those
round-trips. So these count them.
"""

from unittest import mock

import pytest


def _run_job(monkeypatch, handler, *, calls):
    """Drive run_workflow_job with a fake data access that RECORDS round-trips."""
    from connect_labs.workflow import tasks as m

    def wda_factory(*_a, request=None, access_token=None, opportunity_id=None, program_id=None, **_k):
        inst = mock.Mock()

        def fake_get_run(_run_id):
            calls["get_run"] += 1
            return mock.Mock(data={"state": {}}, definition_id=5110)

        def fake_update_run_state(_run_id, updates, run=None):
            calls["update"] += 1
            # The whole point of `run=`: when the caller already holds the
            # record, update_run_state must not go back to the network for it.
            if run is None:
                calls["update_without_run"] += 1
            return None

        inst.get_run.side_effect = fake_get_run
        inst.update_run_state.side_effect = fake_update_run_state
        return inst

    monkeypatch.setitem(m.JOB_HANDLERS, "test_cost_job", handler)
    monkeypatch.setattr(m, "set_task_progress", lambda *a, **k: None)
    monkeypatch.setattr(m, "_save_item_result", lambda *a, **k: None)

    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", wda_factory):
        m.run_workflow_job.apply(
            kwargs={
                "job_config": {"job_type": "test_cost_job", "records": [{"id": 1}]},
                "access_token": "t",
                "run_id": 5112,
                "opportunity_id": 101,
            }
        ).get()


@pytest.mark.django_db
def test_update_run_state_never_refetches_a_record_the_caller_already_holds(monkeypatch):
    """Every state write must carry `run=`; omitting it doubles the GETs."""
    calls = {"get_run": 0, "update": 0, "update_without_run": 0}

    def handler(job_config, access_token, progress_callback):
        for i in range(5):
            progress_callback(f"image {i}", processed=i, total=5)
        return {"successful": 1, "failed": 0}

    _run_job(monkeypatch, handler, calls=calls)

    assert calls["update"] > 0, "expected some state writes"
    assert calls["update_without_run"] == 0, (
        "a state write omitted run=, so update_run_state will re-fetch the record "
        "over HTTP — this is the 2:1 GET:POST ratio measured in production"
    )


@pytest.mark.django_db
def test_per_image_progress_does_not_produce_a_write_per_image(monkeypatch):
    """200 images must not cost 200 network round-trips.

    The throttle floor is 15s, so a fast loop collapses to the first and last
    writes plus the job's own init/terminal ones. Asserting a bound rather than
    an exact count keeps this robust to those bookkeeping writes while still
    failing hard if the per-item write ever comes back.
    """
    calls = {"get_run": 0, "update": 0, "update_without_run": 0}
    images = 200

    def handler(job_config, access_token, progress_callback):
        for i in range(images):
            progress_callback(f"image {i}", processed=i + 1, total=images)
        return {"successful": 1, "failed": 0}

    _run_job(monkeypatch, handler, calls=calls)

    assert calls["update"] <= 6, (
        f"{calls['update']} state writes for {images} images — the per-image "
        "heartbeat write is back (it was ~1 write + 2 GETs per image in production)"
    )


@pytest.mark.django_db
def test_first_and_final_progress_are_always_written(monkeypatch):
    """The throttle must not swallow the endpoints of the run.

    The first write is what gives the UI a real `total` instead of the zero the
    init write seeds; the last is what makes the finished count exact rather
    than whatever the final interval happened to catch. A throttle that drops
    either is worse than no throttle, because the run then *looks* wrong.
    """
    from connect_labs.workflow import tasks as m

    written = []

    def wda_factory(*_a, **_k):
        inst = mock.Mock()
        inst.get_run.return_value = mock.Mock(data={"state": {}}, definition_id=5110)

        def fake_update_run_state(_run_id, updates, run=None):
            job = updates.get("active_job")
            if job is not None:
                written.append(job)
            return None

        inst.update_run_state.side_effect = fake_update_run_state
        return inst

    def handler(job_config, access_token, progress_callback):
        for i in range(50):
            progress_callback(f"image {i}", processed=i + 1, total=50)
        return {"successful": 1, "failed": 0}

    monkeypatch.setitem(m.JOB_HANDLERS, "test_cost_job", handler)
    monkeypatch.setattr(m, "set_task_progress", lambda *a, **k: None)
    monkeypatch.setattr(m, "_save_item_result", lambda *a, **k: None)

    with mock.patch("connect_labs.workflow.data_access.WorkflowDataAccess", wda_factory):
        m.run_workflow_job.apply(
            kwargs={
                "job_config": {"job_type": "test_cost_job", "records": [{"id": 1}]},
                "access_token": "t",
                "run_id": 5112,
                "opportunity_id": 101,
            }
        ).get()

    progressed = [w for w in written if "processed" in w and w.get("total") == 50]
    assert progressed, "expected progress writes carrying processed/total"
    assert progressed[0]["processed"] == 1, "the FIRST progress tick was throttled away"
    assert progressed[-1]["processed"] == 50, "the FINAL progress tick was throttled away"
