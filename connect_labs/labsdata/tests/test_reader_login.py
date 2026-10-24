"""Data SQL on a connection that logs in as ``labs_data_reader``: nothing to escape to.

The validator is switched off in most of these, as if it had a bug: what is pinned is
what the DATABASE allows the reader's own login. The grants must be committed for
the reader's connection to see them, hence transactional tests.

The password is a fixed test value, not a secret: the role is cluster-wide, and test
workers running these at once must all set the same one. Fixture data is invented.
"""

from datetime import date, timedelta

import psycopg2
import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.labsdata import reader_login
from connect_labs.labsdata.engine import QueryError, run_query
from connect_labs.labsdata.scope import Opportunity

pytestmark = pytest.mark.django_db(transaction=True)

TEST_PASSWORD = "labsdata-reader-test-only"
HELD = Opportunity(id=911, name="Opp L", llo="LLO L")
NOT_HELD = 913


@pytest.fixture
def login(settings):
    settings.LABSDATA_READER_PASSWORD = TEST_PASSWORD


@pytest.fixture(autouse=True)
def no_grants_left_behind():
    # Not a Django model, so a transactional test's flush does not empty it.
    def clear():
        with connection.cursor() as c:
            c.execute("DELETE FROM public.labsdata_scope_grant")

    clear()
    yield
    clear()


@pytest.fixture
def cache():
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=opp,
                pipeline_id=USER_VISITS_RAW_SLOT,
                visit_count=10,
                expires_at=timezone.now() + timedelta(hours=1),
                visit_id=f"{opp}-{n}",
                username=f"flw{n % 2}",
                visit_date=date(2026, 9, 1) + timedelta(days=n),
                status="approved",
                form_json={"form": {"n": n}},
            )
            for opp, count in ((HELD.id, 4), (NOT_HELD, 6))
            for n in range(count)
        ]
    )


def _without_the_validator(monkeypatch):
    from connect_labs.labsdata.validator import ExplorerValidator

    monkeypatch.setattr(ExplorerValidator, "_validate_table_access", lambda self, statement, sql: None)
    monkeypatch.setattr(ExplorerValidator, "_validate_no_dangerous_functions", lambda self, statement, sql: None)


def _grants_left():
    with connection.cursor() as c:
        c.execute("SELECT COUNT(*) FROM public.labsdata_scope_grant")
        return c.fetchone()[0]


def test_a_query_runs_as_the_reader_login_over_its_grants(login, cache):
    result = run_query([HELD], "SELECT COUNT(*), MIN(form_json #>> '{form,n}') FROM visits")
    assert result.rows == [[4, "0"]]
    assert _grants_left() == 0


def test_the_session_user_is_the_reader_itself(login):
    def read(cursor, sql, params):
        cursor.execute(sql, params)
        return cursor.fetchall()

    rows = reader_login.run("SELECT session_user, current_user", [], [], read, 1000, "labs_explorer_no_tables")
    assert rows == [("labs_data_reader", "labs_data_reader")]


def test_past_the_validator_the_cache_shows_only_the_granted_opportunity(login, cache, monkeypatch):
    _without_the_validator(monkeypatch)
    sql = "SELECT opportunity_id, COUNT(*) FROM public.labs_raw_visit_cache GROUP BY 1"
    assert run_query([HELD], sql).rows == [[HELD.id, 4]]


@pytest.mark.parametrize(
    "sql",
    [
        # The way back to the app's role under SET ROLE: here there is none.
        "SELECT set_config('role', 'postgres', true)",
        "SELECT set_config('session_authorization', 'postgres', true)",
        # Neither the grants nor any other table is readable.
        "SELECT COUNT(*) FROM public.labsdata_scope_grant",
        "SELECT COUNT(*) FROM public.django_migrations",
    ],
)
def test_past_the_validator_there_is_no_way_out(login, cache, monkeypatch, sql):
    _without_the_validator(monkeypatch)
    with pytest.raises(QueryError) as e:
        run_query([HELD], sql)
    assert e.value.code in {"invalid_sql", "query_failed"}


def test_another_statements_grants_are_invisible(login, cache, monkeypatch):
    # A grant committed for another statement (another token) on the other opportunity
    # is in the table while this query runs; the reader still sees only its own.
    _without_the_validator(monkeypatch)
    with connection.cursor() as c:
        c.execute(
            "INSERT INTO public.labsdata_scope_grant VALUES (%s, %s, %s, now() + interval '1 hour')",
            [NOT_HELD, USER_VISITS_RAW_SLOT, "f" * 64],
        )
    sql = "SELECT DISTINCT opportunity_id FROM public.labs_raw_visit_cache"
    assert run_query([HELD], sql).rows == [[HELD.id]]
    assert _grants_left() == 1


def test_a_reader_that_cannot_log_in_is_given_its_login(login, cache):
    # A fresh role is NOLOGIN, and a rotated secret is a password the role does not
    # have yet: the engine sets the login and tries again.
    with connection.cursor() as c:
        c.execute("ALTER ROLE labs_data_reader NOLOGIN")
    assert run_query([HELD], "SELECT COUNT(*) FROM visits").rows == [[4]]
    # And only with that password.
    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(**reader_login._where(), user=reader_login.ROLE, password="not-the-password").close()


def test_where_the_login_is_required_it_never_falls_back(settings, cache):
    settings.LABSDATA_READER_PASSWORD = ""
    settings.LABSDATA_READER_LOGIN_REQUIRED = True
    with pytest.raises(QueryError) as e:
        run_query([HELD], "SELECT COUNT(*) FROM visits")
    assert e.value.code == "query_failed"
