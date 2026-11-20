"""A clone job says which opportunities it could not clone, and why.

Before these, every per-opportunity failure went to the server log only: a job whose
only opportunity failed (EHA 1236, 2026-11-16) finished SUCCESS with ``clones: []``
and its page said "Done".
"""

import os

import pytest

from connect_labs.labs.synthetic import clone_from_prod, tasks
from connect_labs.labs.synthetic.clone_from_prod import CloneFailure, CloneResult


def _result(source, opp_id):
    return CloneResult(
        source_opportunity_id=source,
        opportunity_id=opp_id,
        gdrive_folder_id=None,
        folder_url=None,
        record_counts={},
        app_structure_present=False,
        skipped=False,
    )


def test_bulk_profile_records_each_failure_with_its_reason(monkeypatch, tmp_path):
    def fetch(base_url, opp_id, key, token):
        raise RuntimeError(f"403 Forbidden for opp {opp_id}")

    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", fetch)
    failures = []
    _, handles = clone_from_prod.profile_opps_bulk(
        [1236], base_url="https://x", oauth_token="t", bundle_root=str(tmp_path), failures=failures
    )

    assert handles == []
    assert failures == [CloneFailure(1236, "measure", "RuntimeError: 403 Forbidden for opp 1236")]


def test_a_long_error_is_cut_to_fit_a_page():
    failures = []
    clone_from_prod._note_failure(failures, 1, "measure", ValueError("x" * 5000))
    assert len(failures[0].error) == clone_from_prod._ERROR_CHARS


def _child_that_dies_on_2(sid):
    # Module level: the pool sends the function to its children by reference.
    if sid == 2:
        os._exit(1)  # as the kernel's OOM killer would
    return f"h{sid}"


def test_a_dead_profiling_process_does_not_fail_the_others(monkeypatch, tmp_path):
    """A child killed mid-job breaks the pool; its siblings are profiled again, not dropped."""
    monkeypatch.setattr(clone_from_prod, "_profile_one_forked", _child_that_dies_on_2)
    monkeypatch.setattr(clone_from_prod, "profile_opp_to_bundle", lambda sid, **kwargs: f"h{sid}")
    monkeypatch.setattr(clone_from_prod, "_close_before_fork", lambda: None)
    failures = []

    handles = clone_from_prod._profile_in_parallel(
        [1, 2, 3],
        workers=3,
        store=object(),
        bundle_root=str(tmp_path),
        base_url="https://x",
        oauth_token="t",
        curate=False,
        mirror=False,
        progress=lambda *a, **k: None,
        failures=failures,
    )

    assert sorted(handles) == ["h1", "h2", "h3"]
    assert failures == []


@pytest.fixture
def clone_job(monkeypatch, django_user_model):
    """Run the clone task's body with both phases stubbed; returns (run, calls)."""
    calls = {"generate": 0}
    behaviour = {}

    def _profile(spec, *, failures, **kwargs):
        failures.extend(behaviour.get("profile_failures", []))
        return spec

    def _generate(spec, *, failures, **kwargs):
        calls["generate"] += 1
        calls["generate_kwargs"] = kwargs
        # As generate_cohort does: a first build is handed a new programme.
        spec.program_id = spec.program_id or 10500
        failures.extend(behaviour.get("generate_failures", []))
        return spec, behaviour.get("results", [])

    monkeypatch.setattr(clone_from_prod, "profile_cohort", _profile)
    monkeypatch.setattr(clone_from_prod, "generate_cohort", _generate)
    monkeypatch.setattr("connect_labs.labs.synthetic.gdrive.DriveClient", lambda: object())
    monkeypatch.setattr(tasks, "_progress_reporter", lambda task, **k: (lambda *a, **kw: None))
    user = django_user_model.objects.create_user(username="cloner", password="x")

    def run(source_ids, **set_behaviour):
        behaviour.clear()
        behaviour.update(set_behaviour)
        return tasks.run_synthetic_clone_opp.run(
            source_opportunity_ids=source_ids,
            program_name="A clone",
            case_timelines=True,
            oauth_token="placeholder",
            user_id=user.pk,
            restricted=False,
        )

    return run, calls


@pytest.mark.django_db
def test_a_job_whose_only_opportunity_fails_to_measure_fails_and_says_why(clone_job):
    run, calls = clone_job

    with pytest.raises(tasks.CloneJobFailed) as raised:
        run([1236], profile_failures=[CloneFailure(1236, "measure", "HTTPStatusError: 403 Forbidden")])

    assert "#1236 failed while measuring the real opportunity: HTTPStatusError: 403 Forbidden" in str(raised.value)
    # Step 2 never ran, so no program id was handed out for a program with no clone.
    assert calls["generate"] == 0


@pytest.mark.django_db
def test_a_job_that_generates_nothing_fails(clone_job):
    run, _ = clone_job

    with pytest.raises(tasks.CloneJobFailed) as raised:
        run([523], generate_failures=[CloneFailure(523, "generate", "ValueError: bad manifest")])

    assert "#523 failed while generating the clone" in str(raised.value)


@pytest.mark.django_db
def test_a_partly_failed_job_lists_what_failed_beside_what_was_cloned(clone_job):
    run, _ = clone_job

    out = run(
        [523, 1236],
        profile_failures=[CloneFailure(1236, "measure", "HTTPStatusError: 403 Forbidden")],
        results=[_result(523, 10300)],
    )

    assert out["clones"] == [{"source_opportunity_id": 523, "opportunity_id": 10300}]
    assert out["failed"] == [
        {"source_opportunity_id": 1236, "step": "measure", "error": "HTTPStatusError: 403 Forbidden"}
    ]
