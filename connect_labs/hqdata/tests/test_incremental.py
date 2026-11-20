"""A pipeline over a CommCare project is kept up to date in place: each stage carries only what changed.

These run with real transactions (``transaction=True``): a stored row's change number is the id of the
transaction that wrote it, so a test that wrote everything in one transaction could not tell a later
write from an earlier one. Every row and name here is invented.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest
from django.db import connection

from connect_labs.hqdata.models import HQCase, HQForm
from connect_labs.hqdata.pipelines import run_pipeline
from connect_labs.hqdata.stores import LabsStore
from connect_labs.hqdata.tests.conftest import make_pipeline
from connect_labs.hqdata.tests.test_pipelines import by_username, hq_form
from connect_labs.labs.analysis.backends.sql import incremental
from connect_labs.labs.analysis.backends.sql.models import RawSlotState, RawVisitCache, StageState

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def alice(transactional_db):
    from connect_labs.users.tests.factories import UserFactory

    return UserFactory()


@pytest.fixture
def project(alice):
    from connect_labs.hqdata.access import create_project

    return create_project(alice, "demo", hq_host="www.commcarehq.org", can_read_domain=lambda: True)


def per_worker(project, alice):
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


def per_visit(project, alice):
    return make_pipeline(
        project,
        created_by=alice,
        name="visits",
        schema={
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [{"name": "weight", "path": "form.weight", "transform": "float"}],
        },
    )


def per_weight(project, alice):
    return make_pipeline(
        project,
        created_by=alice,
        name="by weight",
        schema={
            "grouping_key": "username",
            "terminal_stage": "entity",
            "linking_field": "weight",
            "fields": [
                {"name": "weight", "path": "form.weight"},
                {"name": "forms", "path": "form.meta.username", "aggregation": "count"},
            ],
        },
    )


def sync_like(project, *rows):
    """Write rows the way a sync does: one batch, in its own transaction, under the project's write lock."""
    LabsStore().upsert_forms(list(rows))


def form_row(project, form_id, username, weight, **extra):
    return HQForm(
        project_id=project.pk,
        form_id=form_id,
        xmlns="http://x/1",
        app_id="app1",
        username=username,
        received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
        form_json=hq_form(form_id, username, weight),
        **extra,
    )


@pytest.fixture
def stages():
    """What each run's stages did: ``[{"visits": {...}, "flw": {...}}, ...]``."""
    seen = []
    real = incremental.maintain_stages

    def spy(*args, **kwargs):
        seen.append(real(*args, **kwargs))
        return seen[-1]

    with mock.patch.object(incremental, "maintain_stages", side_effect=spy):
        yield seen


def test_new_forms_are_carried_to_just_the_workers_they_belong_to(alice, project, stages):
    sync_like(project, form_row(project, "f1", "ann", "3.5"), form_row(project, "f2", "bob", "2.0"))
    pipeline = per_worker(project, alice)
    assert by_username(run_pipeline(alice, project, pipeline))["ann"]["forms"] == 1
    sync_like(project, form_row(project, "f3", "ann", "4.5"))
    rows = by_username(run_pipeline(alice, project, pipeline))

    assert rows["ann"]["forms"] == 2 and rows["ann"]["weight_total"] == pytest.approx(8.0)
    assert rows["bob"]["forms"] == 1
    assert stages[0]["flw"]["mode"] == "rebuilt"
    assert stages[1]["flw"] == {"mode": "incremental", "groups": 1}  # ann only: bob's row was not recomputed
    assert stages[1]["visits"] == {"mode": "incremental", "rows": 1}


def test_nothing_changed_means_nothing_recomputed(alice, project, stages):
    sync_like(project, form_row(project, "f1", "ann", "3.5"))
    pipeline = per_worker(project, alice)
    first = run_pipeline(alice, project, pipeline)
    # A delta sync re-reads its overlap: the same form, written again with the same content.
    sync_like(project, form_row(project, "f1", "ann", "3.5"))
    second = run_pipeline(alice, project, pipeline)

    assert second["rows"] == first["rows"]
    assert stages[1] == {"visits": {"mode": "current"}, "flw": {"mode": "current"}}
    assert RawSlotState.objects.get().generation == 0  # an unchanged re-write is not a change


def test_an_archived_or_deleted_form_leaves_the_slot_and_the_totals(alice, project, stages):
    sync_like(
        project,
        form_row(project, "f1", "ann", "3.5"),
        form_row(project, "f2", "ann", "4.5"),
        form_row(project, "f3", "bob", "2.0"),
    )
    pipeline = per_worker(project, alice)
    run_pipeline(alice, project, pipeline)
    sync_like(project, form_row(project, "f2", "ann", "4.5", archived=True))
    sync_like(project, form_row(project, "f3", "bob", "2.0", deleted_at=datetime.now(timezone.utc)))
    rows = by_username(run_pipeline(alice, project, pipeline))

    assert set(rows) == {"ann"} and rows["ann"]["forms"] == 1
    assert set(RawVisitCache.objects.values_list("visit_id", flat=True)) == {"f1"}
    assert stages[1]["flw"]["mode"] == "rebuilt"  # a row left a group it was counted in
    assert stages[1]["visits"]["mode"] == "incremental"


def test_an_edited_form_moves_between_entities(alice, project, stages):
    sync_like(project, form_row(project, "f1", "ann", "3.5"), form_row(project, "f2", "bob", "3.5"))
    pipeline = per_weight(project, alice)
    before = {r["entity_id"]: r["forms"] for r in run_pipeline(alice, project, pipeline)["rows"]}
    sync_like(project, form_row(project, "f2", "bob", "9.0"))
    after = {r["entity_id"]: r["forms"] for r in run_pipeline(alice, project, pipeline)["rows"]}

    assert before == {"3.5": 2} and after == {"3.5": 1, "9.0": 1}


def test_new_forms_reach_an_entity_stage_by_their_keys(alice, project, stages):
    sync_like(project, form_row(project, "f1", "ann", "3.5"), form_row(project, "f2", "bob", "7.0"))
    pipeline = per_weight(project, alice)
    run_pipeline(alice, project, pipeline)
    sync_like(project, form_row(project, "f3", "cy", "3.5"))
    rows = {r["entity_id"]: r["forms"] for r in run_pipeline(alice, project, pipeline)["rows"]}

    assert rows == {"3.5": 2, "7.0": 1}
    assert stages[1]["entity"] == {"mode": "incremental", "groups": 1}


def test_a_visit_stage_built_a_chunk_at_a_time_matches_one_built_whole(alice, project, monkeypatch):
    sync_like(project, *[form_row(project, f"f{i}", f"w{i % 3}", str(i)) for i in range(7)])
    pipeline = per_visit(project, alice)
    whole = sorted(r["id"] for r in run_pipeline(alice, project, pipeline)["rows"])
    monkeypatch.setattr(incremental, "VISIT_CHUNK", 2)
    chunked = sorted(r["id"] for r in run_pipeline(alice, project, pipeline, force_refresh=True)["rows"])
    assert whole == chunked == [f"f{i}" for i in range(7)]


def test_a_stopped_build_resumes_where_it_stopped(alice, project, monkeypatch):
    from connect_labs.labs.analysis.backends.sql import query_builder

    sync_like(project, *[form_row(project, f"f{i}", "ann", str(i)) for i in range(6)])
    pipeline = per_visit(project, alice)
    monkeypatch.setattr(incremental, "VISIT_CHUNK", 2)
    real = query_builder.execute_visit_extraction
    calls = []

    def fails_third(*args, **kwargs):
        calls.append(kwargs.get("extra_where"))
        if len(calls) == 3:
            raise RuntimeError("worker killed")
        return real(*args, **kwargs)

    with mock.patch.object(query_builder, "execute_visit_extraction", side_effect=fails_third):
        with pytest.raises(Exception):
            run_pipeline(alice, project, pipeline)
    state = StageState.objects.get(stage="visits")
    assert state.cursor  # two chunks were kept

    calls.clear()
    with mock.patch.object(query_builder, "execute_visit_extraction", side_effect=real) as resumed:
        rows = run_pipeline(alice, project, pipeline)["rows"]
    assert len(rows) == 6 and resumed.call_count == 1  # only the last chunk was left


def test_a_batch_committed_after_a_read_with_an_older_sync_stamp_is_not_skipped(alice, project):
    """A sync stamps every batch with the moment the RUN began, so ordering by that stamp would skip a
    batch that commits after a reader passed it. Change numbers follow commit order instead."""
    run_began = datetime(2026, 11, 20, 9, tzinfo=timezone.utc)
    sync_like(project, form_row(project, "f1", "ann", "1", synced_at=run_began))
    pipeline = per_worker(project, alice)
    run_pipeline(alice, project, pipeline)
    sync_like(project, form_row(project, "f2", "ann", "2", synced_at=run_began))  # the run's next batch
    assert by_username(run_pipeline(alice, project, pipeline))["ann"]["forms"] == 2


def test_rows_written_before_change_numbers_existed_are_read_first(alice, project):
    sync_like(project, form_row(project, "f1", "ann", "1"))
    HQForm.objects.update(change_seq=None)  # as the migration leaves every older row
    pipeline = per_worker(project, alice)
    assert by_username(run_pipeline(alice, project, pipeline))["ann"]["forms"] == 1
    sync_like(project, form_row(project, "f2", "ann", "2"))
    assert by_username(run_pipeline(alice, project, pipeline))["ann"]["forms"] == 2


def test_two_pipelines_over_the_same_forms_share_one_copy(alice, project):
    sync_like(project, form_row(project, "f1", "ann", "1"), form_row(project, "f2", "bob", "2"))
    run_pipeline(alice, project, per_worker(project, alice))
    run_pipeline(alice, project, per_visit(project, alice))
    assert RawVisitCache.objects.count() == 2 and RawSlotState.objects.count() == 1


def test_the_slot_holds_what_the_python_normalisers_would_have(alice, project):
    """The feed builds raw rows in SQL; they must equal what ``cchq_forms`` / ``cchq_cases`` produce."""
    from connect_labs.hqdata.pipeline_source import case_as_hq_dict
    from connect_labs.hqdata.scope import hq_scope_id
    from connect_labs.labs.analysis.backends.sql.cchq_cases_fetcher import normalize_cchq_case_to_visit_dict
    from connect_labs.labs.analysis.backends.sql.cchq_fetcher import normalize_cchq_form_to_visit_dict

    sync_like(project, form_row(project, "f1", "ann", "1"))
    LabsStore().upsert_cases(
        [
            HQCase(
                project_id=project.pk,
                case_id="c1",
                case_type="child",
                case_name="Baby A",
                owner_id="o1",
                closed=False,
                date_opened=datetime(2026, 10, 1, 8, 30, tzinfo=timezone.utc),
                last_modified=datetime(2026, 11, 2, 9, 15, 30, 123456, tzinfo=timezone.utc),
                properties={"dob": "2026-09-30"},
                indices={"parent": {"case_id": "m1"}},
            )
        ]
    )
    run_pipeline(alice, project, per_worker(project, alice))
    run_pipeline(
        alice,
        project,
        make_pipeline(
            project,
            created_by=alice,
            name="cases",
            store_kind="cases",
            schema={
                "grouping_key": "username",
                "terminal_stage": "visit_level",
                "fields": [{"name": "n", "path": "case.case_name"}],
            },
        ),
    )

    def expected(visit):
        return {
            "visit_id": str(visit["id"]),
            "username": visit["username"],
            "visit_date": visit["visit_date"],
            "entity_id": visit["entity_id"],
            "entity_name": visit["entity_name"],
            "form_json": visit["form_json"],
        }

    def stored(visit_id):
        row = RawVisitCache.objects.get(visit_id=visit_id)
        return {
            "visit_id": row.visit_id,
            "username": row.username,
            "visit_date": row.visit_date.isoformat(),
            "entity_id": row.entity_id,
            "entity_name": row.entity_name,
            "form_json": row.form_json,
        }

    form = HQForm.objects.get(form_id="f1")
    assert stored("f1") == expected(normalize_cchq_form_to_visit_dict(form.form_json, 0))
    case = HQCase.objects.get(case_id="c1")
    assert stored("c1") == expected(
        normalize_cchq_case_to_visit_dict(case_as_hq_dict(case), hq_scope_id(project.pk), 0)
    )


def test_deleting_the_cache_forgets_the_progress_so_it_is_rebuilt(alice, project):
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    sync_like(project, form_row(project, "f1", "ann", "1"))
    pipeline = per_worker(project, alice)
    run_pipeline(alice, project, pipeline)
    SQLCacheManager.delete_all_cache(project.scope_id)
    assert not RawSlotState.objects.exists() and not StageState.objects.exists()
    assert by_username(run_pipeline(alice, project, pipeline))["ann"]["forms"] == 1


def test_a_whole_replacement_of_a_slot_by_another_writer_makes_its_stages_rebuild(alice, project):
    """Connect's visits are written whole; a slot that has a state hears about it (``cache.note_replaced``)."""
    from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

    RawSlotState.objects.create(opportunity_id=10_001, slot_id=-1, generation=4)
    SQLCacheManager(10_001).store_raw_visits([{"id": 1, "username": "u", "form_json": {}}], 1)
    state = RawSlotState.objects.get()
    assert state.generation == 5 and state.changed_through is not None
    assert RawVisitCache.objects.get(opportunity_id=10_001).changed_at is not None
    # A slot nothing keeps up to date in place is not given a state.
    SQLCacheManager(10_002).store_raw_visits([{"id": 1, "username": "u", "form_json": {}}], 1)
    assert not RawSlotState.objects.filter(opportunity_id=10_002).exists()


def test_writes_to_a_project_take_turns(project):
    """The lock that keeps change numbers in commit order is held for the batch's transaction."""
    from django.db import transaction

    with transaction.atomic():
        LabsStore().write_lock(project.pk)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = %s", [project.pk])
            assert cursor.fetchone()[0] == 1
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = %s", [project.pk])
        assert cursor.fetchone()[0] == 0
