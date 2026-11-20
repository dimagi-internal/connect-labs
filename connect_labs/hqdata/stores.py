"""Where a project's rows live: the labs database, or a database the customer hosts.

``HQForm`` and ``HQCase`` rows are the project's DATA and go to its store; projects, sources,
checkpoints and run history are the CONTROL PLANE and always stay in labs. Everything that
reads or writes rows (the sync, the pipeline reader, the pages, purge) goes through
``store_for(project)``, so a project moves between stores by changing what that returns.

A store is a Django database alias. The labs store is ``default``. An external store registers
its alias at runtime from a ``ConnectionSpec``, and its connection is closed after use: labs
does not hold open connections into a customer's network.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.db import DatabaseError, InterfaceError, connections, transaction

from connect_labs.hqdata.models import HQCase, HQForm

logger = logging.getLogger(__name__)

# A schema that was ready a moment ago is not re-inspected on every call (three catalog queries over
# a connection into a customer's network). A table dropped behind labs' back is noticed within this.
_ENSURE_EVERY_S = 300
_ENSURED: dict[tuple, float] = {}


class StoreUnavailable(Exception):
    """The project's store cannot be reached or prepared right now. Says why, for a person."""


@dataclass(frozen=True)
class ConnectionSpec:
    """Everything needed to connect to a customer's PostgreSQL, already vetted.

    ``hostaddr`` is the address ``netguard`` resolved and approved. The connection is made to
    it directly while ``host`` stays the name the certificate is checked against, so the name
    cannot be re-pointed at a different address between the check and the connect.
    """

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    schema: str = "public"
    sslmode: str = "verify-full"
    sslrootcert: str = ""
    hostaddr: str = ""
    statement_timeout_ms: int = 120_000
    connect_timeout_s: int = 10

    def django_settings(self) -> dict:
        options = {
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout_s,
            "options": f"-c statement_timeout={int(self.statement_timeout_ms)} -c search_path={self.schema}",
        }
        if self.hostaddr:
            options["hostaddr"] = self.hostaddr
        if self.sslrootcert:
            options["sslrootcert"] = self.sslrootcert
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": self.dbname,
            "USER": self.user,
            "PASSWORD": self.password,
            "HOST": self.host,
            "PORT": str(self.port),
            "OPTIONS": options,
            # What Django fills in for a database named in settings (ConnectionHandler.configure_settings),
            # which cannot be asked to do it for one alias on its own.
            "CONN_MAX_AGE": 0,
            "CONN_HEALTH_CHECKS": False,
            "ATOMIC_REQUESTS": False,
            "AUTOCOMMIT": True,
            "TIME_ZONE": None,
            "TEST": {"CHARSET": None, "COLLATION": None, "MIGRATE": True, "MIRROR": None, "NAME": None},
        }


class Store:
    """A database alias that holds a project's forms and cases."""

    alias: str = "default"

    # -- rows -------------------------------------------------------------------

    def forms(self, project_id: int):
        return HQForm.objects.using(self.alias).filter(project_id=project_id)

    def cases(self, project_id: int):
        return HQCase.objects.using(self.alias).filter(project_id=project_id)

    def write_lock(self, project_id: int) -> None:
        """Take, for the rest of the current transaction, the right to write this project's rows.

        Writes to a project's rows take turns, so their change numbers (``models.ChangeNumber``, the
        writing transaction's id) follow the order they commit in: a batch can only get its number
        after the one before it committed. That is what lets a reader of the changes
        (``pipeline_feed``) move its cursor past every row it has seen without ever skipping one that
        commits later. Call it first in the transaction, before anything is written.
        """
        from django.db import connections

        with connections[self.alias].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [74_202, project_id])

    def upsert_forms(self, rows: list[HQForm]) -> None:
        if not rows:
            return
        with self.atomic():
            self.write_lock(rows[0].project_id)
            self._upsert_forms(rows)

    def _upsert_forms(self, rows: list[HQForm]) -> None:
        HQForm.objects.using(self.alias).bulk_create(
            rows,
            update_conflicts=True,
            unique_fields=["project_id", "form_id"],
            update_fields=[
                "xmlns",
                "app_id",
                "build_id",
                "received_on",
                "server_modified_on",
                "indexed_on",
                "username",
                "user_id",
                "archived",
                "form_json",
                "deleted_at",
                "synced_at",
                "change_seq",
            ],
        )

    def upsert_cases(self, rows: list[HQCase]) -> None:
        if not rows:
            return
        with self.atomic():
            self.write_lock(rows[0].project_id)
            self._upsert_cases(rows)

    def _upsert_cases(self, rows: list[HQCase]) -> None:
        HQCase.objects.using(self.alias).bulk_create(
            rows,
            update_conflicts=True,
            unique_fields=["project_id", "case_id"],
            update_fields=[
                "case_type",
                "case_name",
                "owner_id",
                "external_id",
                "closed",
                "date_opened",
                "last_modified",
                "server_last_modified",
                "indexed_on",
                "properties",
                "indices",
                "deleted_at",
                "synced_at",
                "change_seq",
            ],
        )

    def purge(self, project_id: int) -> dict[str, int]:
        forms, _ = self.forms(project_id).delete()
        cases, _ = self.cases(project_id).delete()
        return {"forms": forms, "cases": cases}

    # -- lifecycle ----------------------------------------------------------------

    def atomic(self):
        return transaction.atomic(using=self.alias)

    def ensure_schema(self) -> None:
        """Make the store ready to hold rows. Nothing to do for labs' own database."""

    def close(self) -> None:
        """Release the connection. Nothing to do for labs' own database."""

    @contextmanager
    def translated(self):
        """Inside this block a database failure is a ``StoreUnavailable``. Labs' own store has none to translate."""
        yield

    @contextmanager
    def session(self):
        """Use the store, then let go of its connection."""
        try:
            with self.translated():
                yield self
        finally:
            self.close()


class LabsStore(Store):
    alias = "default"


class ExternalStore(Store):
    """A customer-hosted PostgreSQL holding this project's rows in tables labs creates there."""

    def __init__(self, project_id: int, spec: ConnectionSpec):
        self.project_id = project_id
        self.spec = spec
        self.alias = f"hqstore_{project_id}"
        self._register()

    def _register(self) -> None:
        """Register (or re-register, after the customer's details changed) this store's alias."""
        wanted = self.spec.django_settings()
        current = connections.settings.get(self.alias)
        if current == wanted:
            return
        if current is not None:
            self.close()
        connections.settings[self.alias] = wanted

    def close(self) -> None:
        if self.alias in connections.settings:
            try:
                connections[self.alias].close()
            except Exception:  # closing must never be what fails a sync
                logger.warning("could not close the connection for %s", self.alias, exc_info=True)
            try:
                del connections[self.alias]
            except Exception:
                pass

    @contextmanager
    def translated(self):
        """A customer's database being down, refusing us or answering badly is theirs to fix and ours to
        report: the caller gets one exception type, and a reason with nothing in it but the database's first line."""
        try:
            yield
        except DatabaseError as exc:
            raise StoreUnavailable(reason_for(exc)) from exc
        except InterfaceError as exc:
            raise StoreUnavailable(reason_for(exc)) from exc

    def ensure_schema(self) -> None:
        from connect_labs.hqdata.store_schema import ensure_schema

        key = (self.alias, self.spec.host, self.spec.port, self.spec.dbname, self.spec.schema)
        if time.monotonic() - _ENSURED.get(key, -_ENSURE_EVERY_S) < _ENSURE_EVERY_S:
            return
        try:
            ensure_schema(connections[self.alias], self.spec.schema)
        except StoreUnavailable:
            raise
        except Exception as exc:
            raise StoreUnavailable(reason_for(exc)) from exc
        _ENSURED[key] = time.monotonic()


def reason_for(exc: Exception) -> str:
    """A reason fit to show a person, without the connection details a driver puts in its message."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:300]


def store_for(project) -> Store:
    """The store that holds ``project``'s rows."""
    if getattr(project, "store", "labs") == "labs":
        return LabsStore()
    from connect_labs.hqdata.external import external_store_for

    return external_store_for(project)
