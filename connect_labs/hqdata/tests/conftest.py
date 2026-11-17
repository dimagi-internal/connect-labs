import secrets
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from connect_labs.hqdata.identity import hq_host
from connect_labs.hqdata.models import HQIdentity
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User


@pytest.fixture(autouse=True)
def hq_settings(settings):
    settings.COMMCARE_HQ_URL = "https://www.commcarehq.org"
    settings.COMMCARE_OAUTH_CLIENT_ID = "client-id"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "client-secret"


@pytest.fixture
def queued():
    """Stands in for the job queue: records what a page asked to run."""
    with mock.patch("connect_labs.hqdata.web.views.queue_sync", return_value={"job_id": "j", "state": "queued"}) as q:
        yield q


def make_hq_user(client, *, domains=("demo",), username="simon@example.org", **user_kwargs):
    """A CommCare-only person, signed in: no Connect token, a live CommCare one, a fresh identity."""
    # Not UserFactory: it is get_or_create on email, so email=None would hand back whichever
    # no-email user already exists (a service account a migration made) instead of a new one.
    user = User.objects.create(username=f"cchq:{hq_host()}:{username}", email=None, **user_kwargs)
    user.set_unusable_password()
    user.save()
    UserCCHQToken.objects.create(
        user=user, access_token="hq-access", refresh_token="hq-refresh", expires_at=timezone.now() + timedelta(hours=1)
    )
    HQIdentity.objects.create(
        user=user, hq_host=hq_host(), hq_username=username, domains=list(domains), refreshed_at=timezone.now()
    )
    client.force_login(user)
    session = client.session
    session["hq_auth"] = {"host": hq_host(), "at": timezone.now().timestamp()}
    session.save()
    return user


# -- a second, real PostgreSQL database standing in for a customer's -------------------------


@pytest.fixture
def encryption_key(settings):
    from cryptography.fernet import Fernet

    settings.LABS_FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    settings.LABS_FIELD_ENCRYPTION_REQUIRED = True


@pytest.fixture
def customer_db(settings, django_db_blocker, encryption_key):
    """A database and a LIMITED role that owns it, as a customer would give labs.

    Created in the test cluster, dropped afterwards. The host check is bypassed (the cluster is on
    loopback, which labs refuses by design) and so is the TLS requirement; both are tested on their
    own in test_netguard and test_external_store.
    """
    import psycopg2
    from psycopg2 import sql

    base = settings.DATABASES["default"]
    name, role, password = f"hq_cust_{secrets.token_hex(4)}", f"hq_role_{secrets.token_hex(4)}", secrets.token_hex(12)
    settings.HQ_EXTERNAL_DB_ALLOW_INSECURE_TLS = True

    def admin():
        conn = psycopg2.connect(
            host=base["HOST"], port=base["PORT"], user=base["USER"], password=base["PASSWORD"], dbname="postgres"
        )
        conn.autocommit = True
        return conn

    with django_db_blocker.unblock():
        conn = admin()
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(role)))
        conn.close()
        try:
            with mock.patch("connect_labs.hqdata.external.check_host", return_value=""):
                yield {
                    "host": base["HOST"],
                    "port": int(base["PORT"]),
                    "dbname": name,
                    "username": role,
                    "password": password,
                    "admin_user": base["USER"],
                    "admin_password": base["PASSWORD"],
                }
        finally:
            from django.db import connections

            for alias in [a for a in list(connections.settings) if a.startswith("hqstore_")]:
                try:
                    connections[alias].close()
                except Exception:
                    pass
                connections.settings.pop(alias, None)
                try:
                    del connections[alias]
                except Exception:
                    pass
            conn = admin()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    [name],
                )
                cur.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
                cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            conn.close()


def read_customer(customer_db, query, params=None):
    """Run a query in the customer's database as its OWN user, the way the customer would look."""
    import psycopg2

    conn = psycopg2.connect(
        host=customer_db["host"],
        port=customer_db["port"],
        user=customer_db["username"],
        password=customer_db["password"],
        dbname=customer_db["dbname"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


GOOD_SCHEMA = {
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [{"name": "forms", "path": "form.meta.username", "aggregation": "count"}],
}


def make_pipeline(project, *, name="per worker", store_kind="forms", schema=None, created_by=None, **narrowing):
    """Save a pipeline in the project as one of its members (the first, unless ``created_by``); returns the record."""
    from connect_labs.hqdata.models import HQProjectMember
    from connect_labs.hqdata.pipelines import save_pipeline

    user = created_by or HQProjectMember.objects.filter(project=project).order_by("pk").first().user
    return save_pipeline(user, project, name=name, store_kind=store_kind, schema=schema or GOOD_SCHEMA, **narrowing)


def force_schema(user, project, record, schema):
    """Write a schema onto a saved pipeline WITHOUT ``save_pipeline``'s checks."""
    from connect_labs.hqdata.workflows import pipeline_dao

    dao = pipeline_dao(user, project)
    try:
        return dao.update_definition(record.id, schema=schema)
    finally:
        dao.close()
