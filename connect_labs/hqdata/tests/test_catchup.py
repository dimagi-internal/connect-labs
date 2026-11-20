"""A first read of a big project pipeline is carried in the background; a read after a sync is carried inline."""

from unittest.mock import patch

import pytest
from django.core.cache import cache

from connect_labs.hqdata import catchup
from connect_labs.hqdata.computed import ComputedWarming
from connect_labs.hqdata.pipelines import PipelineProblem, run_pipeline
from connect_labs.hqdata.tests.conftest import make_pipeline
from connect_labs.hqdata.tests.test_pipelines import by_username, store_form

pytestmark = pytest.mark.django_db


@pytest.fixture
def alice(db):
    from connect_labs.users.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def project(alice):
    from connect_labs.hqdata.access import create_project

    return create_project(alice, "demo", hq_host="www.commcarehq.org", can_read_domain=lambda: True)


@pytest.fixture
def per_worker(project, alice):
    for i, who in enumerate(("ann", "ann", "bob", "cy")):
        store_form(project, f"f{i}", who, "1")
    return make_pipeline(
        project,
        created_by=alice,
        schema={
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
        },
    )


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def queued_reads(alice, project, pipeline):
    """Read once with the background task captured instead of queued; return what was queued."""
    queued = []
    with patch("connect_labs.hqdata.tasks.catch_up_pipeline.delay", lambda *a: queued.append(a)):
        with pytest.raises(ComputedWarming) as waiting:
            run_pipeline(alice, project, pipeline)
    return queued, waiting.value


def test_a_small_backlog_is_carried_in_the_read(alice, project, per_worker):
    with patch("connect_labs.hqdata.tasks.catch_up_pipeline.delay") as task:
        rows = by_username(run_pipeline(alice, project, per_worker))
    assert rows["ann"]["forms"] == 2 and not task.called


def test_a_big_first_read_is_carried_in_the_background_and_then_read_inline(alice, project, per_worker, monkeypatch):
    monkeypatch.setattr(catchup, "INLINE_ROWS", 2)
    queued, waiting = queued_reads(alice, project, per_worker)
    assert len(queued) == 1 and "Reading 4 of the project's rows" in waiting.message
    with patch("connect_labs.hqdata.tasks.catch_up_pipeline.delay") as again:
        with pytest.raises(ComputedWarming):
            run_pipeline(alice, project, per_worker)  # a second reader waits for the same task
    assert not again.called

    project_id, user_id, schema, pipeline_id, reset, token = queued[0]
    catchup.run(alice, project, schema, pipeline_id, reset, token)
    store_form(project, "f9", "bob", "1")  # a sync after: one row, carried in the read
    with patch("connect_labs.hqdata.tasks.catch_up_pipeline.delay") as later:
        rows = by_username(run_pipeline(alice, project, per_worker))
    assert rows["bob"]["forms"] == 2 and not later.called


def test_a_background_part_that_runs_out_of_time_keeps_its_work_and_the_next_read_starts_the_next(
    alice, project, per_worker, monkeypatch
):
    from connect_labs.labs.analysis.backends.sql.models import StageState

    monkeypatch.setattr(catchup, "INLINE_ROWS", 2)
    monkeypatch.setattr(catchup, "PART_SECONDS", -1)
    queued, _ = queued_reads(alice, project, per_worker)
    catchup.run(alice, project, *queued[0][2:])
    assert not StageState.objects.filter(stage="flw").exists()  # stopped after the raw part, kept
    queued, waiting = queued_reads(alice, project, per_worker)
    assert len(queued) == 1  # the next part
    monkeypatch.setattr(catchup, "PART_SECONDS", 600)
    catchup.run(alice, project, *queued[0][2:])
    assert by_username(run_pipeline(alice, project, per_worker))["ann"]["forms"] == 2


def test_a_background_part_that_fails_is_said_once(alice, project, per_worker, monkeypatch):
    monkeypatch.setattr(catchup, "INLINE_ROWS", 2)
    queued, _ = queued_reads(alice, project, per_worker)
    with patch("connect_labs.labs.analysis.backends.sql.incremental.maintain", side_effect=RuntimeError("boom")):
        catchup.run(alice, project, *queued[0][2:])
    with pytest.raises(PipelineProblem, match="failed"):
        run_pipeline(alice, project, per_worker)
    queued, _ = queued_reads(alice, project, per_worker)  # spent: the next read tries again
    assert len(queued) == 1


def test_a_page_waits_for_a_pipeline_being_carried_like_a_slow_computed_source(
    alice, project, per_worker, monkeypatch
):
    from connect_labs.hqdata.workflows import create_page, read_project_pipeline_data

    monkeypatch.setattr(catchup, "INLINE_ROWS", 2)
    page = create_page(alice, project, name="P", pipeline_sources=[{"pipeline_id": per_worker.id, "alias": "w"}])
    with patch("connect_labs.hqdata.tasks.catch_up_pipeline.delay", lambda *a: None):
        with pytest.raises(ComputedWarming) as waiting:
            read_project_pipeline_data(alice, project, page.id)
    assert "Pipeline w: Reading 4" in waiting.value.message
