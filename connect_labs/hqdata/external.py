"""A customer's database as a project's store: the connection record, and how it is used safely."""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from connect_labs.hqdata.models import HQExternalDatabase, HQProject
from connect_labs.hqdata.netguard import HostRefused, check_host, check_port
from connect_labs.hqdata.store_schema import SCHEMA_NAME_RE, SchemaConflict
from connect_labs.hqdata.stores import ConnectionSpec, ExternalStore, StoreUnavailable, reason_for

SECURE_SSLMODES = ("verify-full", "verify-ca")


def spec_for(record: HQExternalDatabase) -> ConnectionSpec:
    """The connection spec for a record, with its host checked and pinned. Raises StoreUnavailable.

    Always checks. A record edited behind the form's back (the admin, a shell) is held to the
    same rules as one saved through it, because this is the only way a spec is made.
    """
    try:
        port = check_port(record.port)
        hostaddr = check_host(record.host, allow_private=record.allow_private_network)
    except HostRefused as exc:
        raise StoreUnavailable(str(exc)) from exc
    if not SCHEMA_NAME_RE.match(record.schema):
        raise StoreUnavailable("The schema name may only use lower-case letters, digits and underscores.")
    if record.sslmode not in SECURE_SSLMODES and not getattr(settings, "HQ_EXTERNAL_DB_ALLOW_INSECURE_TLS", False):
        raise StoreUnavailable("The connection must verify the database's certificate (verify-full or verify-ca).")
    return ConnectionSpec(
        host=record.host.strip().lower().rstrip("."),
        port=port,
        dbname=record.dbname,
        user=record.username,
        password=record.password,
        schema=record.schema,
        sslmode=record.sslmode,
        sslrootcert="",
        hostaddr=hostaddr,
    )


def external_store_for(project: HQProject) -> ExternalStore:
    record = HQExternalDatabase.objects.filter(project=project).first()
    if record is None:
        raise StoreUnavailable("No customer database has been set up for this project.")
    return ExternalStore(project.pk, spec_for(record))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def check_connection(record: HQExternalDatabase) -> list[Check]:
    """Everything that must hold before a project may keep its data in this database.

    Stops at the first failure, because later checks need the earlier ones. Never raises and never
    puts the password, or a driver's message that may contain connection details, in a result.
    """
    checks: list[Check] = []
    try:
        spec = spec_for(record)
    except StoreUnavailable as exc:
        return [Check("The address is acceptable", False, str(exc))]
    checks.append(Check("The address is acceptable", True))

    store = ExternalStore(record.project_id, spec)
    try:
        try:
            with store.session():
                from django.db import connections

                connection = connections[store.alias]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    checks.append(Check("Labs can connect and sign in", True))

                    cursor.execute(
                        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                    flags = cursor.fetchone() or (False,) * 5
                    if any(flags):
                        checks.append(
                            Check(
                                "The database user is limited",
                                False,
                                "That user can create roles or databases, replicate or bypass row security. "
                                "Use a user that owns only the schema labs writes to.",
                            )
                        )
                        return checks
                    checks.append(Check("The database user is limited", True))

                try:
                    store.ensure_schema()
                    checks.append(Check("Labs can create its tables in the schema", True))
                except SchemaConflict as exc:
                    checks.append(Check("Labs can create its tables in the schema", False, str(exc)))
                    return checks
                except StoreUnavailable as exc:
                    checks.append(Check("Labs can create its tables in the schema", False, str(exc)))
                    return checks

                if not _can_write(store):
                    checks.append(Check("Labs can add, change and remove rows", False, "A test write was refused."))
                    return checks
                checks.append(Check("Labs can add, change and remove rows", True))
        except StoreUnavailable as exc:
            checks.append(Check("Labs can connect and sign in", False, str(exc)))
        except Exception as exc:
            checks.append(Check("Labs can connect and sign in", False, reason_for(exc)))
    finally:
        store.close()
    return checks


def _can_write(store: ExternalStore) -> bool:
    """Insert, change and delete one probe row, leaving nothing behind."""
    from connect_labs.hqdata.models import HQForm

    probe = -1  # no project has this id
    try:
        with store.atomic():
            row = HQForm(project_id=probe, form_id="labs-write-probe", form_json={})
            store.upsert_forms([row])
            row.form_json = {"probe": True}
            store.upsert_forms([row])
            store.forms(probe).delete()
        return True
    except Exception:
        return False


def passed(checks: list[Check]) -> bool:
    return bool(checks) and all(c.ok for c in checks)
