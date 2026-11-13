"""The sync engine, against a backend that returns what we tell it to."""

from datetime import datetime, timedelta, timezone

import pytest

from connect_labs.hqdata import sync as sync_module
from connect_labs.hqdata.backends.base import BackendUnavailable
from connect_labs.hqdata.models import HQCase, HQForm, HQProject, HQSource, HQSyncRun
from connect_labs.hqdata.records import CaseRecord, FormRecord
from connect_labs.hqdata.scope import hq_scope_id, is_hq_scope, project_id_from_scope
from connect_labs.hqdata.sync import OVERLAP, SourceBusy, choose_mode, sync_project, sync_source

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class FakeBackend:
    """Serves records from a list; remembers what it was asked for."""

    def __init__(self, records, fail_after=None):
        self.records = list(records)
        self.fail_after = fail_after
        self.asked_since = []

    def iter_records(self, source, since):
        self.asked_since.append(since)
        for i, record in enumerate(self.records):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("HQ went away")
            yield record


def form(form_id, *, archived=False, text="a", xmlns="http://x/1", at=T0):
    return FormRecord(
        form_id=form_id,
        xmlns=xmlns,
        app_id="app1",
        username="u",
        archived=archived,
        indexed_on=at,
        form_json={"id": form_id, "form": {"q": text}},
    )


def case(case_id, *, closed=False, case_type="child", at=T0):
    return CaseRecord(case_id=case_id, case_type=case_type, closed=closed, indexed_on=at, properties={"k": "v"})


@pytest.fixture
def project(db):
    return HQProject.objects.create(domain="demo", storage_mode=HQProject.STORAGE_PERSISTENT)


@pytest.fixture
def forms_source(project):
    return HQSource.objects.create(project=project, kind=HQSource.KIND_FORMS)


@pytest.fixture
def cases_source(project):
    return HQSource.objects.create(project=project, kind=HQSource.KIND_CASES)


def test_scope_ids_cannot_meet_opportunities_or_programmes():
    scope = hq_scope_id(7)
    assert scope < 0 and is_hq_scope(scope) and project_id_from_scope(scope) == 7
    assert not is_hq_scope(-25)  # a programme's Drive scope
    assert not is_hq_scope(10_001) and not is_hq_scope(None)
    with pytest.raises(ValueError):
        project_id_from_scope(5)


def test_first_sync_is_full_and_stores_everything(forms_source):
    backend = FakeBackend([form("f1"), form("f2")])
    run = sync_source(forms_source, backend, now=T0)
    assert run.mode == HQSyncRun.MODE_FULL and run.status == HQSyncRun.STATUS_OK and run.fetched == 2
    assert backend.asked_since == [None]
    assert set(HQForm.objects.values_list("form_id", flat=True)) == {"f1", "f2"}
    forms_source.refresh_from_db()
    assert forms_source.watermark == T0  # HQ's own indexed_on of the newest row, not our clock
    assert forms_source.backfill_complete and forms_source.last_reconciled_at == T0
    assert forms_source.running_since is None


def test_delta_starts_from_the_watermark_and_upserts_edits_and_archives(forms_source):
    sync_source(forms_source, FakeBackend([form("f1", text="old"), form("f2")]), now=T0)
    later = T0 + timedelta(hours=1)
    backend = FakeBackend([form("f1", text="new"), form("f2", archived=True), form("f3")])
    run = sync_source(forms_source, backend, now=later)
    assert run.mode == HQSyncRun.MODE_DELTA
    assert backend.asked_since == [T0 - OVERLAP]
    stored = {f.form_id: f for f in HQForm.objects.all()}
    assert stored["f1"].form_json["form"]["q"] == "new"
    assert stored["f2"].archived is True
    assert set(stored) == {"f1", "f2", "f3"}
    assert HQForm.objects.count() == 3  # an edit is not a second row


def test_a_form_repeated_inside_one_batch_is_stored_once(forms_source):
    run = sync_source(forms_source, FakeBackend([form("f1", text="a"), form("f1", text="b")]), now=T0)
    assert run.fetched == 2 and HQForm.objects.count() == 1
    assert HQForm.objects.get().form_json["form"]["q"] == "b"


def test_a_failed_run_keeps_what_it_committed_and_the_next_run_resumes_there(forms_source, monkeypatch):
    monkeypatch.setattr(sync_module, "BATCH_SIZE", 1)
    sync_source(forms_source, FakeBackend([form("f1", at=T0)]), now=T0)

    t1, t2 = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    with pytest.raises(RuntimeError):
        sync_source(
            forms_source,
            FakeBackend([form("f2", at=t1), form("f3", at=t2)], fail_after=1),
            now=T0 + timedelta(hours=3),
        )
    forms_source.refresh_from_db()
    assert forms_source.watermark == t1  # f2 was committed, so the checkpoint moved with it
    assert "HQ went away" in forms_source.last_error
    assert forms_source.running_since is None
    failed = HQSyncRun.objects.filter(status=HQSyncRun.STATUS_ERROR).get()
    assert failed.fetched == 1 and "HQ went away" in failed.error
    assert set(HQForm.objects.values_list("form_id", flat=True)) == {"f1", "f2"}

    backend = FakeBackend([form("f2", at=t1), form("f3", at=t2)])
    sync_source(forms_source, backend, now=T0 + timedelta(hours=4))
    assert backend.asked_since == [t1 - OVERLAP]
    assert set(HQForm.objects.values_list("form_id", flat=True)) == {"f1", "f2", "f3"}
    forms_source.refresh_from_db()
    assert forms_source.last_error == "" and forms_source.watermark == t2


def test_an_unfinished_first_backfill_resumes_instead_of_starting_over(forms_source, monkeypatch):
    monkeypatch.setattr(sync_module, "BATCH_SIZE", 1)
    t1, t2 = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    with pytest.raises(RuntimeError):
        sync_source(forms_source, FakeBackend([form("f1", at=T0), form("f2", at=t1)], fail_after=1), now=t2)
    forms_source.refresh_from_db()
    assert forms_source.watermark == T0 and not forms_source.backfill_complete
    assert choose_mode(forms_source, t2) == HQSyncRun.MODE_DELTA  # not full: it resumes

    backend = FakeBackend([form("f1", at=T0), form("f2", at=t1)])
    run = sync_source(forms_source, backend, now=t2)
    assert backend.asked_since == [T0 - OVERLAP]
    forms_source.refresh_from_db()
    assert run.status == HQSyncRun.STATUS_OK and forms_source.backfill_complete
    assert forms_source.last_reconciled_at == t2  # the finished backfill counts as a full walk
    assert HQForm.objects.count() == 2


def test_reconcile_marks_what_a_full_walk_did_not_see_and_a_return_clears_it(forms_source):
    sync_source(forms_source, FakeBackend([form("f1"), form("f2")]), now=T0)
    later = T0 + timedelta(days=8)
    assert choose_mode(HQSource.objects.get(pk=forms_source.pk), later) == HQSyncRun.MODE_RECONCILE

    run = sync_source(forms_source, FakeBackend([form("f1")]), now=later)
    assert run.mode == HQSyncRun.MODE_RECONCILE and run.deleted == 1
    assert HQForm.objects.get(form_id="f2").deleted_at == later
    assert HQForm.objects.get(form_id="f1").deleted_at is None

    sync_source(forms_source, FakeBackend([form("f2")]), mode=HQSyncRun.MODE_DELTA, now=later + timedelta(hours=1))
    assert HQForm.objects.get(form_id="f2").deleted_at is None


def test_reconcile_only_marks_rows_its_own_selector_covers(project):
    narrow = HQSource.objects.create(project=project, kind=HQSource.KIND_FORMS, xmlns="http://x/1")
    HQForm.objects.create(project_id=project.pk, form_id="other", xmlns="http://x/2")
    HQForm.objects.create(project_id=project.pk, form_id="mine", xmlns="http://x/1")
    sync_source(narrow, FakeBackend([]), mode=HQSyncRun.MODE_RECONCILE, now=T0)
    assert HQForm.objects.get(form_id="mine").deleted_at == T0
    assert HQForm.objects.get(form_id="other").deleted_at is None


def test_two_runs_of_one_source_do_not_overlap(forms_source):
    assert forms_source.claim(T0)
    with pytest.raises(SourceBusy):
        sync_source(forms_source, FakeBackend([form("f1")]), now=T0 + timedelta(minutes=1))
    assert HQForm.objects.count() == 0
    # ...but a lease nobody has touched for half an hour is a dead run's, and is taken over.
    sync_source(forms_source, FakeBackend([form("f1")]), now=T0 + timedelta(hours=1))
    assert HQForm.objects.count() == 1


def test_cases_sync_and_a_closed_case_is_an_update(cases_source):
    sync_source(cases_source, FakeBackend([case("c1"), case("c2")]), now=T0)
    sync_source(cases_source, FakeBackend([case("c1", closed=True)]), now=T0 + timedelta(hours=1))
    assert HQCase.objects.get(case_id="c1").closed is True
    assert HQCase.objects.get(case_id="c2").closed is False
    assert HQCase.objects.get(case_id="c1").properties == {"k": "v"}


def test_sync_project_records_success_and_isolates_a_failing_source(project, forms_source, cases_source):
    class Backend(FakeBackend):
        def iter_records(self, source, since):
            if source.kind == HQSource.KIND_CASES:
                raise RuntimeError("cases broke")
            yield from super().iter_records(source, since)

    results = sync_project(project, Backend([form("f1")]))
    assert results[forms_source.pk].status == HQSyncRun.STATUS_OK
    assert isinstance(results[cases_source.pk], RuntimeError)
    project.refresh_from_db()
    assert project.status == HQProject.STATUS_ERROR and "cases broke" in project.last_error
    assert project.last_synced_at is None
    assert HQForm.objects.count() == 1


def test_auth_failure_marks_the_project_as_needing_the_owner_to_sign_in(project, forms_source):
    from connect_labs.labs.integrations.commcare.api_client import CCHQAuthError

    class Denied(FakeBackend):
        def iter_records(self, source, since):
            raise CCHQAuthError("rejected", status_code=403, domain="demo")
            yield  # pragma: no cover

    sync_project(project, Denied([]))
    project.refresh_from_db()
    assert project.status == HQProject.STATUS_NEEDS_REAUTH
    assert not project.sync_due(T0 + timedelta(days=1))


def test_a_project_with_no_sync_owner_cannot_be_read_headlessly(project, forms_source):
    results = sync_project(project)
    assert isinstance(results[None], BackendUnavailable)
    project.refresh_from_db()
    assert project.status == HQProject.STATUS_NEEDS_REAUTH


def test_sync_due_follows_mode_interval_and_status(db):
    live = HQProject.objects.create(domain="a")
    assert not live.sync_due(T0)
    p = HQProject.objects.create(domain="b", storage_mode=HQProject.STORAGE_PERSISTENT, sync_interval_minutes=30)
    assert p.sync_due(T0)  # never synced
    p.last_synced_at = T0
    assert not p.sync_due(T0 + timedelta(minutes=29))
    assert p.sync_due(T0 + timedelta(minutes=30))
    p.status = HQProject.STATUS_PAUSED
    assert not p.sync_due(T0 + timedelta(days=1))


def test_deleting_a_project_purges_its_rows_and_only_its_rows(project, forms_source, cases_source):
    other = HQProject.objects.create(domain="other")
    HQForm.objects.create(project_id=other.pk, form_id="theirs")
    sync_source(forms_source, FakeBackend([form("f1")]), now=T0)
    sync_source(cases_source, FakeBackend([case("c1")]), now=T0)
    project.delete()
    assert not HQForm.objects.filter(form_id="f1").exists() and not HQCase.objects.filter(case_id="c1").exists()
    assert HQForm.objects.filter(form_id="theirs").exists()
