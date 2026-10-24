"""Data statements on a connection that logs in as ``labs_data_reader`` itself.

``SET LOCAL ROLE`` from the app's login (``engine._lock``) contains a statement only
while the statement cannot switch back to the app's role. A session that LOGS IN
as the reader has no other role to switch to: ``RESET ROLE`` is the reader again,
and ``set_config('role', ...)`` is "permission denied". What it can read is what
the database grants that role (labs migrations 0039 and 0040), whatever the SQL.

One statement, two connections:

1. The app's own login, in autocommit, writes the statement's grants under a fresh
   random token and commits them, so the reader's session can see them.
2. A reader login, given the token in its startup options (no other session can
   read those), runs the statement read-only, time-boxed, with no search path,
   and is closed.
3. The app deletes the grants, whatever happened. A grant left behind by a process
   that died expires on its own (``expires_at``), and the next statement sweeps it.

The login is turned on lazily: if the reader cannot log in (a fresh role is
NOLOGIN; a rotated secret is a new password), the app sets ``LOGIN`` and the
configured password on the role, as a SCRAM verifier so the password itself is
never sent, and tries once more.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import closing

import psycopg2
from django.conf import settings
from django.db import connection
from psycopg2 import sql
from psycopg2.extensions import encrypt_password

logger = logging.getLogger(__name__)

ROLE = "labs_data_reader"
#: How many reader sessions may be open at once, across every labs process.
CONNECTION_LIMIT = 20
CONNECT_TIMEOUT_S = 5
#: How long past a statement's own timeout its grants stay valid.
GRANT_MARGIN_S = 30

#: libpq parameters of the app's connection a reader connection reuses: where the
#: database is and how to reach it, never who to be or what to set.
_WHERE_KEYS = ("database", "dbname", "host", "port", "sslmode", "sslrootcert", "target_session_attrs")


def configured() -> bool:
    return bool(getattr(settings, "LABSDATA_READER_PASSWORD", ""))


def required() -> bool:
    return bool(getattr(settings, "LABSDATA_READER_LOGIN_REQUIRED", False))


def _where() -> dict:
    params = connection.get_connection_params()
    out = {k: params[k] for k in _WHERE_KEYS if params.get(k) not in (None, "")}
    out["connect_timeout"] = CONNECT_TIMEOUT_S
    return out


def _app_connection():
    params = connection.get_connection_params()
    conn = psycopg2.connect(**_where(), user=params.get("user"), password=params.get("password"))
    conn.autocommit = True
    return conn


def _reader_connection(token: str):
    return psycopg2.connect(
        **_where(),
        user=ROLE,
        password=settings.LABSDATA_READER_PASSWORD,
        options=f"-c labsdata.token={token}",
    )


def ensure_login() -> None:
    """Give the role ``LOGIN`` and the configured password (as a SCRAM verifier)."""
    with closing(_app_connection()) as app, app.cursor() as cur:
        verifier = encrypt_password(settings.LABSDATA_READER_PASSWORD, ROLE, app, "scram-sha-256")
        cur.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {} CONNECTION LIMIT {}").format(
                sql.Identifier(ROLE), sql.Literal(verifier), sql.Literal(CONNECTION_LIMIT)
            )
        )


def _connect_reader(token: str):
    try:
        return _reader_connection(token)
    except psycopg2.OperationalError as first:
        logger.info("labsdata: the reader could not log in (%s); setting its login", str(first).strip())
    try:
        ensure_login()
    except psycopg2.Error as e:
        # Another process may be setting it at the same moment; the retry decides.
        logger.warning("labsdata: could not set the reader's login: %s", str(e).strip())
    return _reader_connection(token)


def run(
    final_sql: str, params: list, grants: list[tuple[int, int]], read, statement_timeout_ms: int, search_path: str
):
    """Run ``final_sql`` as the reader login over exactly ``grants``; ``read(cursor)``
    executes it and returns what the caller wants from the cursor."""
    token = secrets.token_hex(32)
    with closing(_app_connection()) as app:
        with app.cursor() as cur:
            cur.execute("DELETE FROM public.labsdata_scope_grant WHERE expires_at < now()")
            if grants:
                values = ", ".join(["(%s, %s, %s, now() + make_interval(secs => %s))"] * len(grants))
                ttl = statement_timeout_ms / 1000 + GRANT_MARGIN_S
                cur.execute(
                    "INSERT INTO public.labsdata_scope_grant (opportunity_id, pipeline_id, token, expires_at) "
                    f"VALUES {values}",
                    [v for opp, pipeline in grants for v in (opp, pipeline, token, ttl)],
                )
        try:
            with closing(_connect_reader(token)) as reader:
                reader.set_session(readonly=True)
                with reader.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
                    cur.execute(f"SET LOCAL search_path = {search_path}")
                    try:
                        return read(cur, final_sql, params)
                    finally:
                        reader.rollback()
        finally:
            with app.cursor() as cur:
                cur.execute("DELETE FROM public.labsdata_scope_grant WHERE token = %s", [token])
