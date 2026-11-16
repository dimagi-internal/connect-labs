"""A project whose data lives in a customer's database: provisioning, sync, reads, and what never leaves."""

from datetime import timedelta

import pytest

from connect_labs.hqdata import sync as sync_module
from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.external import check_connection, external_store_for, passed, spec_for
from connect_labs.hqdata.models import HQCase, HQExternalDatabase, HQForm, HQPipeline, HQProject, HQSource, HQSyncRun
from connect_labs.hqdata.pipelines import run_pipeline
from connect_labs.hqdata.store_schema import SCHEMA_VERSION, ensure_schema
from connect_labs.hqdata.stores import StoreUnavailable
from connect_labs.hqdata.sync import OVERLAP, sync_source
from connect_labs.hqdata.tests.conftest import read_customer
from connect_labs.hqdata.tests.test_sync import T0, FakeBackend, case, form
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


@pytest.fixture
def owner(db):
    return UserFactory()


@pytest.fixture
def project(owner, customer_db):
    project = create_project(owner, "demo", hq_host=HOST, can_read_domain=lambda: True)
    HQExternalDatabase.objects.create(
        project=project,
        host=customer_db["host"],
        port=customer_db["port"],
        dbname=customer_db["dbname"],
        username=customer_db["username"],
        password=customer_db["password"],
        schema="labs_commcare",
        sslmode="disable",
    )
    HQProject.objects.filter(pk=project.pk).update(store=HQProject.STORE_EXTERNAL)
    project.refresh_from_db()
    return project


@pytest.fixture
def forms_source(project):
    return HQSource.objects.create(project=project, kind=HQSource.KIND_FORMS)


@pytest.fixture
def cases_source(project):
    return HQSource.objects.create(project=project, kind=HQSource.KIND_CASES)


def customer_count(customer_db, table, project):
    return read_customer(
        customer_db, f"SELECT count(*) FROM labs_commcare.{table} WHERE project_id = %s", [project.pk]
    )[0][0]


# -- the connection record ------------------------------------------------------------------


def test_the_password_is_encrypted_at_rest_and_never_in_a_repr(project, customer_db):
    record = HQExternalDatabase.objects.get(project=project)
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT password FROM hqdata_hqexternaldatabase WHERE id = %s", [record.pk])
        raw = cursor.fetchone()[0]
    assert raw.startswith("fernet1:") and customer_db["password"] not in raw
    assert record.password == customer_db["password"]
    assert customer_db["password"] not in repr(record) and customer_db["password"] not in str(record)
    assert customer_db["password"] not in repr(spec_for(record))


def test_a_record_edited_behind_the_forms_back_is_still_held_to_the_rules(project, settings):
    record = HQExternalDatabase.objects.get(project=project)
    settings.HQ_EXTERNAL_DB_ALLOW_INSECURE_TLS = False
    with pytest.raises(StoreUnavailable, match="certificate"):
        spec_for(record)
    settings.HQ_EXTERNAL_DB_ALLOW_INSECURE_TLS = True
    record.schema = 'x"; DROP SCHEMA public; --'
    with pytest.raises(StoreUnavailable, match="schema"):
        spec_for(record)


def test_the_real_address_check_runs_when_it_is_not_bypassed(project):
    from unittest import mock

    record = HQExternalDatabase.objects.get(project=project)
    record.host = "169.254.169.254"
    with mock.patch(
        "connect_labs.hqdata.external.check_host",
        side_effect=lambda h, **k: __import__("connect_labs.hqdata.netguard", fromlist=["check_host"]).check_host(
            h, **k
        ),
    ):
        with pytest.raises(StoreUnavailable, match="link-local"):
            spec_for(record)


# -- provisioning ---------------------------------------------------------------------------


def test_provisioning_creates_the_tables_in_the_customers_schema_and_is_repeatable(project, customer_db):
    store = external_store_for(project)
    with store.session():
        store.ensure_schema()
        store.ensure_schema()
    tables = {
        r[0]
        for r in read_customer(
            customer_db, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'labs_commcare'"
        )
    }
    assert tables == {
        "labs_commcare_form",
        "labs_commcare_case",
        "labs_commcare_schema_version",
        "labs_raw_visit_cache",
        "labs_computed_visit_cache",
        "labs_computed_flw_cache",
        "labs_computed_entity_cache",
    }
    assert (
        read_customer(customer_db, "SELECT max(version) FROM labs_commcare.labs_commcare_schema_version")[0][0]
        == SCHEMA_VERSION
    )
    # nothing labs makes lands anywhere else
    assert not read_customer(
        customer_db,
        "SELECT 1 FROM information_schema.tables WHERE table_name LIKE 'labs_commcare%' AND table_schema = 'public'",
    )


def test_a_schema_the_customer_made_is_used_not_recreated(project, customer_db):
    import psycopg2

    conn = psycopg2.connect(
        host=customer_db["host"],
        port=customer_db["port"],
        user=customer_db["username"],
        password=customer_db["password"],
        dbname=customer_db["dbname"],
    )
    conn.autocommit = True
    conn.cursor().execute("CREATE SCHEMA labs_commcare")
    conn.close()
    store = external_store_for(project)
    with store.session():
        store.ensure_schema()
    assert customer_count(customer_db, "labs_commcare_form", project) == 0


def test_a_table_of_the_customers_in_the_way_is_refused_not_overwritten(project, customer_db):
    import psycopg2

    conn = psycopg2.connect(
        host=customer_db["host"],
        port=customer_db["port"],
        user=customer_db["username"],
        password=customer_db["password"],
        dbname=customer_db["dbname"],
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA labs_commcare")
    cur.execute("CREATE TABLE labs_commcare.labs_commcare_form (mine text)")
    cur.execute("INSERT INTO labs_commcare.labs_commcare_form VALUES ('theirs')")
    conn.close()
    store = external_store_for(project)
    with pytest.raises(StoreUnavailable, match="did not create"):
        with store.session():
            store.ensure_schema()
    assert read_customer(customer_db, "SELECT mine FROM labs_commcare.labs_commcare_form") == [("theirs",)]


def test_schema_names_are_checked_before_they_reach_sql(project):
    store = external_store_for(project)
    from django.db import connections

    with pytest.raises(ValueError):
        ensure_schema(connections[store.alias], 'a"; DROP TABLE x; --')
    store.close()


# -- sync -----------------------------------------------------------------------------------


def test_a_sync_writes_the_rows_to_the_customers_database_and_none_to_labs(
    project, forms_source, cases_source, customer_db
):
    sync_source(forms_source, FakeBackend([form("f1"), form("f2")]), now=T0)
    sync_source(cases_source, FakeBackend([case("c1")]), now=T0)
    assert customer_count(customer_db, "labs_commcare_form", project) == 2
    assert customer_count(customer_db, "labs_commcare_case", project) == 1
    assert HQForm.objects.filter(project_id=project.pk).count() == 0
    assert HQCase.objects.filter(project_id=project.pk).count() == 0
    forms_source.refresh_from_db()
    assert forms_source.watermark == T0 and forms_source.backfill_complete  # the checkpoint is labs' own


def test_the_second_sync_updates_in_place_and_reconcile_marks_what_vanished(project, forms_source, customer_db):
    sync_source(forms_source, FakeBackend([form("f1", text="old"), form("f2")]), now=T0)
    sync_source(
        forms_source, FakeBackend([form("f1", text="new", at=T0 + timedelta(hours=1))]), now=T0 + timedelta(hours=2)
    )
    rows = dict(
        read_customer(
            customer_db, "SELECT form_id, form_json->'form'->>'q' FROM labs_commcare.labs_commcare_form ORDER BY 1"
        )
    )
    assert rows == {"f1": "new", "f2": "a"}
    run = sync_source(
        forms_source, FakeBackend([form("f1")]), mode=HQSyncRun.MODE_RECONCILE, now=T0 + timedelta(days=9)
    )
    assert run.deleted == 1
    gone = read_customer(
        customer_db, "SELECT form_id FROM labs_commcare.labs_commcare_form WHERE deleted_at IS NOT NULL"
    )
    assert gone == [("f2",)]


def test_a_failed_run_resumes_from_its_checkpoint_in_the_customers_database(
    project, forms_source, customer_db, monkeypatch
):
    monkeypatch.setattr(sync_module, "BATCH_SIZE", 1)
    t1, t2 = T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    with pytest.raises(RuntimeError):
        sync_source(
            forms_source, FakeBackend([form("f1", at=T0), form("f2", at=t1), form("f3", at=t2)], fail_after=2), now=t2
        )
    forms_source.refresh_from_db()
    assert forms_source.watermark == t1 and customer_count(customer_db, "labs_commcare_form", project) == 2
    backend = FakeBackend([form("f2", at=t1), form("f3", at=t2)])
    sync_source(forms_source, backend, now=t2 + timedelta(hours=1))
    assert backend.asked_since == [t1 - OVERLAP]
    assert customer_count(customer_db, "labs_commcare_form", project) == 3


def test_rows_written_but_not_yet_checkpointed_are_simply_written_again(project, forms_source, customer_db):
    """A crash between the customer's database and labs' checkpoint leaves rows ahead of it."""
    store = external_store_for(project)
    with store.session():
        store.ensure_schema()
        store.upsert_forms([HQForm(project_id=project.pk, form_id="f1", form_json={"id": "f1"})])
    assert forms_source.watermark is None
    sync_source(forms_source, FakeBackend([form("f1"), form("f2")]), now=T0)
    assert customer_count(customer_db, "labs_commcare_form", project) == 2


def test_an_unreachable_database_fails_the_run_with_a_reason_and_no_secret(project, forms_source, customer_db):
    HQExternalDatabase.objects.filter(project=project).update(port=1)
    with pytest.raises(StoreUnavailable) as caught:
        sync_source(forms_source, FakeBackend([form("f1")]), now=T0)
    forms_source.refresh_from_db()
    assert forms_source.watermark is None and forms_source.last_error
    for text in (str(caught.value), forms_source.last_error, HQSyncRun.objects.get().error):
        assert customer_db["password"] not in text


def test_every_run_lets_go_of_its_connection(project, forms_source, customer_db):
    """Labs does not hold connections open into a customer's network: seen from the customer's side."""
    sync_source(forms_source, FakeBackend([form("f1")]), now=T0)
    open_sessions = read_customer(
        customer_db,
        "SELECT count(*) FROM pg_stat_activity WHERE datname = %s AND usename = %s AND pid <> pg_backend_pid()",
        [customer_db["dbname"], customer_db["username"]],
    )[0][0]
    assert open_sessions == 0


# -- reads ----------------------------------------------------------------------------------


def test_a_pipeline_reads_from_the_customers_database(project, forms_source, owner, customer_db):
    sync_source(forms_source, FakeBackend([form("f1"), form("f2"), form("f3")]), now=T0)
    pipeline = HQPipeline.objects.create(
        project=project,
        name="n",
        store_kind="forms",
        schema={
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [{"name": "forms", "path": "form.q", "aggregation": "count"}],
        },
    )
    from connect_labs.hqdata.models import HQProjectMember

    assert HQProjectMember.objects.filter(project=project, user=owner).exists()
    result = run_pipeline(owner, pipeline)
    assert result["count"] == 1 and result["rows"][0]["forms"] == 3


# -- the checks -----------------------------------------------------------------------------


def test_a_good_database_passes_every_check_and_leaves_no_probe_row_behind(project, customer_db):
    checks = check_connection(HQExternalDatabase.objects.get(project=project))
    assert passed(checks) and [c.name for c in checks][-1] == "Labs can add, change and remove rows"
    assert customer_count(customer_db, "labs_commcare_form", project) == 0
    assert read_customer(customer_db, "SELECT count(*) FROM labs_commcare.labs_commcare_form")[0][0] == 0


def test_a_wrong_password_fails_the_first_connect_without_repeating_it(project, customer_db):
    HQExternalDatabase.objects.filter(project=project).update(password="not-the-password")
    record = HQExternalDatabase.objects.get(project=project)
    checks = check_connection(record)
    assert not passed(checks) and checks[-1].name == "Labs can connect and sign in" and not checks[-1].ok
    assert "not-the-password" not in " ".join(c.detail for c in checks)


def test_a_database_user_with_power_to_spare_is_refused(project, customer_db):
    HQExternalDatabase.objects.filter(project=project).update(
        username=customer_db["admin_user"], password=customer_db["admin_password"], dbname=customer_db["dbname"]
    )
    checks = check_connection(HQExternalDatabase.objects.get(project=project))
    assert not passed(checks)
    failed = next(c for c in checks if not c.ok)
    assert failed.name == "The database user is limited" and "owns only the schema" in failed.detail


def test_an_address_labs_will_not_connect_to_stops_the_checks_before_any_connection(project):
    from unittest import mock

    from connect_labs.hqdata.netguard import check_host

    HQExternalDatabase.objects.filter(project=project).update(host="169.254.169.254")
    with mock.patch("connect_labs.hqdata.external.check_host", side_effect=check_host):
        checks = check_connection(HQExternalDatabase.objects.get(project=project))
    assert [c.ok for c in checks] == [False] and "link-local" in checks[0].detail


# -- pipelines over an external store: nothing staged or computed lands in labs ------------------

LABS_CACHE_TABLES = (
    "labs_raw_visit_cache",
    "labs_computed_visit_cache",
    "labs_computed_flw_cache",
    "labs_computed_entity_cache",
)


def labs_cache_rows(project):
    from django.db import connection

    total = 0
    with connection.cursor() as cursor:
        for table in LABS_CACHE_TABLES:
            cursor.execute(f"SELECT count(*) FROM {table} WHERE opportunity_id = %s", [project.scope_id])
            total += cursor.fetchone()[0]
    return total


def customer_cache_rows(customer_db, project, table):
    return read_customer(
        customer_db, f"SELECT count(*) FROM labs_commcare.{table} WHERE opportunity_id = %s", [project.scope_id]
    )[0][0]


@pytest.fixture
def per_worker(project, forms_source, owner, customer_db):
    sync_source(forms_source, FakeBackend([form("f1"), form("f2"), form("f3")]), now=T0)
    return HQPipeline.objects.create(
        project=project,
        name="per worker",
        store_kind="forms",
        schema={
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [{"name": "forms", "path": "form.q", "aggregation": "count"}],
        },
    )


def test_a_pipeline_stages_and_caches_in_the_customers_database_and_writes_nothing_to_labs(
    project, per_worker, owner, customer_db
):
    result = run_pipeline(owner, per_worker)
    assert result["count"] == 1 and result["rows"][0]["forms"] == 3
    assert labs_cache_rows(project) == 0
    assert customer_cache_rows(customer_db, project, "labs_raw_visit_cache") == 3
    assert customer_cache_rows(customer_db, project, "labs_computed_flw_cache") >= 1


def test_the_second_run_is_served_from_the_customers_cache_without_reading_the_rows_again(
    project, per_worker, owner, customer_db
):
    from unittest import mock

    from connect_labs.hqdata import pipeline_source

    real = pipeline_source.fetch_store_as_visit_dicts
    with mock.patch.object(pipeline_source, "fetch_store_as_visit_dicts", side_effect=real) as fetch:
        first = run_pipeline(owner, per_worker)
        second = run_pipeline(owner, per_worker)
    assert fetch.call_count == 1 and second["rows"] == first["rows"]
    assert labs_cache_rows(project) == 0


def test_a_sync_that_changes_rows_clears_the_customers_cache_not_just_labs(
    project, per_worker, forms_source, owner, customer_db
):
    run_pipeline(owner, per_worker)
    assert customer_cache_rows(customer_db, project, "labs_raw_visit_cache") == 3
    sync_source(forms_source, FakeBackend([form("f4", at=T0 + timedelta(hours=1))]), now=T0 + timedelta(hours=2))
    for table in LABS_CACHE_TABLES:
        assert customer_cache_rows(customer_db, project, table) == 0, table
    assert run_pipeline(owner, per_worker)["rows"][0]["forms"] == 4


def test_a_non_member_is_refused_and_the_customers_database_is_not_touched(project, per_worker, owner, customer_db):
    with pytest.raises(PermissionError):
        run_pipeline(UserFactory(), per_worker)
    assert customer_cache_rows(customer_db, project, "labs_raw_visit_cache") == 0


def test_labs_own_connection_is_back_after_a_run_and_after_a_failed_one(project, per_worker, owner, customer_db):
    from unittest import mock

    from django.db import connection, connections

    labs_name = connections["default"].settings_dict["NAME"]
    run_pipeline(owner, per_worker)
    assert (
        connections["default"].settings_dict["NAME"] == labs_name and HQProject.objects.filter(pk=project.pk).exists()
    )

    with mock.patch(
        "connect_labs.labs.analysis.backends.sql.backend.SQLBackend.process_and_cache",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(Exception):
            run_pipeline(owner, per_worker, force_refresh=True)
    assert (
        connections["default"].settings_dict["NAME"] == labs_name and HQProject.objects.filter(pk=project.pk).exists()
    )
    assert connection.alias == "default"


def test_the_customers_cache_tables_are_created_with_the_rest_of_the_schema(project, customer_db):
    store = external_store_for(project)
    with store.session():
        store.ensure_schema()
    tables = {
        r[0]
        for r in read_customer(
            customer_db, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'labs_commcare'"
        )
    }
    assert set(LABS_CACHE_TABLES) <= tables


# -- the redirect itself -----------------------------------------------------------------------


def test_redirects_do_not_nest(project, customer_db):
    from connect_labs.hqdata.redirect import RedirectError, default_connection_is

    store = external_store_for(project)
    try:
        with default_connection_is(store.alias):
            with pytest.raises(RedirectError):
                with default_connection_is(store.alias):
                    pass
    finally:
        store.close()


def test_a_backend_call_that_returns_a_generator_is_refused(project, customer_db):
    from connect_labs.hqdata.redirect import RedirectedBackend, RedirectError

    class Backend:
        label = "x"

        def streams(self):
            yield 1

        def returns(self):
            return 5

    store = external_store_for(project)
    try:
        backend = RedirectedBackend(Backend(), store.alias)
        assert backend.label == "x" and backend.returns() == 5
        with pytest.raises(RedirectError):
            backend.streams()
    finally:
        store.close()
