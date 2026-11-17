"""A saved pipeline over a CommCare project: what it reads, who may, and when it recomputes."""

from datetime import datetime, timezone
from unittest import mock

import pytest

from connect_labs.hqdata.access import ProjectAccessDenied, create_project
from connect_labs.hqdata.models import HQCase, HQForm
from connect_labs.hqdata.pipelines import PipelineProblem, run_pipeline, save_pipeline
from connect_labs.hqdata.sync import invalidate_project_caches
from connect_labs.hqdata.tests.conftest import force_schema, make_pipeline
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def hq_form(form_id, username, weight, *, at="2026-11-01T10:00:00Z", **extra):
    return {
        "id": form_id,
        "received_on": at,
        "form": {"@xmlns": "http://x/1", "weight": weight, "meta": {"username": username}},
        **extra,
    }


def store_form(project, form_id, username, weight, *, xmlns="http://x/1", app_id="app1", **row):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=form_id,
        xmlns=xmlns,
        app_id=app_id,
        username=username,
        received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
        form_json=hq_form(form_id, username, weight),
        **row,
    )


@pytest.fixture
def alice(db):
    return UserFactory()


@pytest.fixture
def project(alice):
    return create_project(alice, "demo", hq_host=HOST, can_read_domain=lambda: True)


@pytest.fixture
def per_worker(project, alice):
    store_form(project, "f1", "ann", "3.5")
    store_form(project, "f2", "ann", "4.5")
    store_form(project, "f3", "bob", "2.0")
    return make_pipeline(
        project,
        created_by=alice,
        schema={
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [
                {"name": "forms", "path": "form.meta.username", "aggregation": "count"},
                {"name": "weight_total", "path": "form.weight", "transform": "float", "aggregation": "sum"},
            ],
        },
    )


def by_username(result):
    return {r["username"]: r for r in result["rows"]}


def test_a_pipeline_aggregates_the_forms_a_project_keeps(alice, project, per_worker):
    result = run_pipeline(alice, project, per_worker)
    rows = by_username(result)
    assert result["count"] == 2 and not result["truncated"]
    assert rows["ann"]["forms"] == 2 and rows["bob"]["forms"] == 1
    assert rows["ann"]["weight_total"] == pytest.approx(8.0) and rows["bob"]["weight_total"] == pytest.approx(2.0)


def test_archived_and_deleted_forms_are_not_read(alice, project, per_worker):
    store_form(project, "f4", "ann", "100", archived=True)
    store_form(project, "f5", "ann", "100", deleted_at=datetime(2026, 11, 2, tzinfo=timezone.utc))
    ann = by_username(run_pipeline(alice, project, per_worker))["ann"]
    assert ann["forms"] == 2 and ann["weight_total"] == pytest.approx(8.0)


def test_narrowing_to_one_form_leaves_the_others_out(alice, project, per_worker):
    store_form(project, "g1", "ann", "50", xmlns="http://x/2")
    per_worker = save_pipeline(
        alice,
        project,
        name="per worker",
        store_kind="forms",
        schema=per_worker.schema,
        xmlns="http://x/1",
        pipeline_id=per_worker.id,
    )
    ann = by_username(run_pipeline(alice, project, per_worker, force_refresh=True))["ann"]
    assert ann["forms"] == 2 and ann["weight_total"] == pytest.approx(8.0)  # the 50 on the other form is not in it


def test_a_cases_pipeline_reads_case_properties(alice, project):
    HQCase.objects.create(
        project_id=project.pk, case_id="c1", case_type="child", case_name="A", properties={"age": "4"}
    )
    HQCase.objects.create(project_id=project.pk, case_id="c2", case_type="household", properties={"age": "40"})
    pipeline = make_pipeline(
        project,
        name="children",
        store_kind="cases",
        case_type="child",
        schema={
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [{"name": "age", "path": "case.properties.age", "aggregation": "first"}],
        },
    )
    result = run_pipeline(alice, project, pipeline)
    assert result["count"] == 1
    row = result["rows"][0]
    assert (row["age"], row["entity_id"], row["entity_name"]) == ("4", "c1", "A")


def test_a_schemas_own_data_source_cannot_point_it_at_another_project(alice, project, per_worker):
    other_owner = UserFactory()
    other = create_project(other_owner, "secret", hq_host=HOST, can_read_domain=lambda: True)
    store_form(other, "s1", "spy", "999")
    per_worker = force_schema(
        alice,
        project,
        per_worker,
        {**per_worker.schema, "data_source": {"type": "hq_store", "project_id": other.pk, "store_kind": "forms"}},
    )
    assert set(by_username(run_pipeline(alice, project, per_worker))) == {"ann", "bob"}


def test_a_non_member_cannot_run_it(project, per_worker):
    with pytest.raises(ProjectAccessDenied):
        run_pipeline(UserFactory(), project, per_worker)


def test_a_cached_result_is_not_the_next_persons_to_have(alice, project, per_worker):
    run_pipeline(alice, project, per_worker)  # builds and caches it
    with pytest.raises(ProjectAccessDenied):
        run_pipeline(UserFactory(), project, per_worker)


def test_the_second_read_does_not_touch_the_stored_rows_until_a_sync_changes_them(alice, project, per_worker):
    from connect_labs.hqdata import pipeline_source

    real = pipeline_source.fetch_store_as_visit_dicts
    with mock.patch.object(pipeline_source, "fetch_store_as_visit_dicts", side_effect=real) as fetch:
        run_pipeline(alice, project, per_worker)
        run_pipeline(alice, project, per_worker)
        assert fetch.call_count == 1
        store_form(project, "f9", "cy", "1.0")
        run_pipeline(alice, project, per_worker)
        assert fetch.call_count == 1  # nothing told the cache the rows changed
        invalidate_project_caches(project.pk)
        assert "cy" in by_username(run_pipeline(alice, project, per_worker))
        assert fetch.call_count == 2


def test_a_person_with_no_connect_token_cannot_read_a_connect_source(alice):
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig, DataSourceConfig, FieldComputation
    from connect_labs.labs.analysis.pipeline import AnalysisPipeline

    config = AnalysisPipelineConfig(
        grouping_key="username",
        fields=[FieldComputation(name="n", path="form.x", aggregation="count")],
        data_source=DataSourceConfig(type="connect_csv"),
    )
    with pytest.raises((PermissionError, RuntimeError)):
        AnalysisPipeline(None, local_only_user=alice).stream_analysis_ignore_events(config, opportunity_id=1)


def test_a_pipeline_needs_fields_and_cannot_join_others(alice, project):
    one = [{"name": "n", "path": "form.x", "aggregation": "count"}]
    for schema, match in (
        ({"fields": []}, "fields"),
        ({"fields": one, "joins": [{"x": 1}]}, "join"),
        ({"fields": [{"name": "n", "path": "form.x", "aggregation": "no-such-aggregation"}]}, ""),
    ):
        with pytest.raises(PipelineProblem, match=match):
            save_pipeline(alice, project, name="bad", store_kind="forms", schema=schema)


def test_hq_store_validation():
    from connect_labs.labs.analysis.config import DataSourceConfig

    ok = DataSourceConfig(type="hq_store", project_id=3, store_kind="forms", app_id="a", xmlns="x")
    assert ok.project_id == 3
    for bad in (
        {"type": "hq_store", "store_kind": "forms"},
        {"type": "hq_store", "project_id": 3},
        {"type": "hq_store", "project_id": 3, "store_kind": "forms", "case_type": "child"},
        {"type": "hq_store", "project_id": 3, "store_kind": "cases", "app_id": "a"},
        {"type": "hq_store", "project_id": 3, "store_kind": "forms", "domain": "other"},
        {"type": "cchq_forms", "project_id": 3},
    ):
        with pytest.raises(ValueError):
            DataSourceConfig(**bad)
