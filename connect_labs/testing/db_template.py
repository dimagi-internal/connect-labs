"""Build the test database once, then clone it for each xdist worker.

Every worker used to run all 259 migrations into its own `test_<db>_gwN`. That
is the same schema four times over, and on a fresh database it is the single
largest fixed cost in CI: the pytest step starts, and 54 seconds pass before
the first assertion runs.

Postgres can copy a database wholesale (`CREATE DATABASE ... TEMPLATE ...`),
and on this schema that takes **0.08s against 13.5s for a migrate**. So one
worker migrates into a template, under a lock the others wait on, and then each
worker clones it.

Two things fall out of this that are worth having on their own:

* A clone is cheap enough that workers no longer *reuse* a database across
  runs, they re-clone it. `--reuse-db` now means "reuse the migrated
  TEMPLATE", not "reuse whatever schema this worker happened to end up with
  last time". That removes the stale-worker-database failure mode entirely --
  the one where a new migration lands and half the suite fails on a column
  that does not exist, in a way that names neither migrations nor the database.
* `--create-db` still rebuilds everything, template included.

Non-Postgres backends, `--no-migrations`, and serial runs fall back to
pytest-django's own behaviour: there is nothing to share when there is one
database.

**Off unless ``LABS_TEST_DB_TEMPLATE=1``.** Two reasons it is not the default:

* It trades wall clock for total work, and which one wins depends on the
  machine. Four workers already migrate CONCURRENTLY, so on an idle laptop with
  spare cores the parallel migrate beats one serialised template build
  (measured: 45-49s against 62s). What the template buys is *variance*: it does
  a quarter of the work, so it is unmoved by load, while the parallel path
  ranges from 45s to 73s on the same machine depending on what else is running.
  A CI runner with four shared vCPUs, Postgres in a container, and four pytest
  workers is the loaded case -- which is why it is enabled there and measured
  rather than assumed.
* It DROPS and re-clones each worker database at session start. Worker database
  names are per-machine, not per-checkout, so a second suite running in another
  worktree at the same time would have its database pulled out from under it.
  Fine on a CI runner, which owns its Postgres; not fine on a laptop running
  several worktrees at once.
"""
import os

# How long a worker will wait for whichever one is building the template.
# A cold migrate is ~15s locally and slower on CI; this is a deadlock guard,
# not a timeout anyone should ever reach.
TEMPLATE_BUILD_TIMEOUT = 600


def _test_db_name(alias="default"):
    from django.conf import settings

    db = settings.DATABASES[alias]
    return db.get("TEST", {}).get("NAME") or f"test_{db['NAME']}"


def _template_name(worker_db):
    """`test_labs_gw2` -> `test_labs_tpl`. Shared by every worker in the run."""
    base = worker_db.rsplit("_gw", 1)[0]
    return f"{base}_tpl"


def _admin_cursor():
    """A cursor on the maintenance database, not on any test database.

    CREATE/DROP DATABASE cannot run from inside the database it names, and a
    template cannot be copied while anything is connected to it.
    """
    from django.db import connections

    return connections["default"]._nodb_cursor()


def _database_exists(name):
    with _admin_cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
        return cursor.fetchone() is not None


def _drop(name):
    with _admin_cursor() as cursor:
        # Anything still attached would block the drop. Nothing should be, but a
        # crashed previous run leaves connections behind and the failure that
        # produces ("being accessed by other users") is opaque.
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity " "WHERE datname = %s AND pid <> pg_backend_pid()",
            [name],
        )
        cursor.execute(f'DROP DATABASE IF EXISTS "{name}"')


def _clone(template, target):
    with _admin_cursor() as cursor:
        cursor.execute(f'CREATE DATABASE "{target}" WITH TEMPLATE "{template}"')


def _is_postgres(alias="default"):
    from django.conf import settings

    return "postgres" in settings.DATABASES[alias]["ENGINE"] or "postgis" in settings.DATABASES[alias]["ENGINE"]


def django_db_setup_with_template(
    request,
    django_test_environment,
    django_db_blocker,
    django_db_use_migrations,
    django_db_keepdb,
    django_db_createdb,
    django_db_modify_db_settings,
):
    """Drop-in replacement for pytest-django's ``django_db_setup``."""
    from pytest_django.fixtures import django_db_setup as pytest_django_fixture

    fallback = pytest_django_fixture.__wrapped__
    args = (
        request,
        django_test_environment,
        django_db_blocker,
        django_db_use_migrations,
        django_db_keepdb,
        django_db_createdb,
        django_db_modify_db_settings,
    )

    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if (
        worker is None
        or os.environ.get("LABS_TEST_DB_TEMPLATE") != "1"
        or not django_db_use_migrations
        or not _is_postgres()
    ):
        # Serial run, --no-migrations, or a backend without CREATE DATABASE
        # ... TEMPLATE. Nothing to share.
        yield from fallback(*args)
        return

    from filelock import FileLock

    worker_db = _test_db_name()
    template = _template_name(worker_db)
    # xdist gives every worker its own tmp dir under one shared parent.
    shared = request.getfixturevalue("tmp_path_factory").getbasetemp().parent
    lock = FileLock(str(shared / "labs-test-db-template.lock"), timeout=TEMPLATE_BUILD_TIMEOUT)

    with django_db_blocker.unblock(), lock:
        rebuild = django_db_createdb or not _database_exists(template)
        if rebuild:
            _build_template(request, template, django_db_blocker)

        # Always re-clone. It costs ~0.1s and it is what makes a worker
        # database provably match the migrations on this branch.
        _drop(worker_db)
        _clone(template, worker_db)
        _point_django_at(worker_db)

    yield

    # The template is the expensive artifact and is deliberately left behind for
    # the next run, exactly as --reuse-db leaves a test database behind. The
    # worker's clone is disposable; --create-db rebuilds both.
    if not django_db_keepdb:
        with django_db_blocker.unblock():
            _drop(worker_db)


def _point_django_at(name, alias="default"):
    """Make ``name`` the database this process actually talks to.

    Django's ``create_test_db`` does this as a side effect, and
    ``teardown_databases`` undoes it -- so after building the template and
    handing it back, the connection points at the *development* database again.
    Cloning a database is not the same as connecting to it, and without this the
    entire suite silently runs against DATABASE_URL. `test_db_isolation.py`
    exists because that is not a failure any test would otherwise report.
    """
    from django.conf import settings
    from django.db import connections

    settings.DATABASES[alias]["NAME"] = name
    settings.DATABASES[alias].setdefault("TEST", {})["NAME"] = name
    connection = connections[alias]
    connection.close()
    connection.settings_dict["NAME"] = name


def _build_template(request, template, django_db_blocker):
    """Run the migrations once, into the template database."""
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases

    _drop(template)

    db = settings.DATABASES["default"]
    original = db.get("TEST", {}).get("NAME")
    db.setdefault("TEST", {})["NAME"] = template
    try:
        cfg = setup_databases(verbosity=request.config.option.verbose, interactive=False)
        # Hand the template back with nothing connected to it: Postgres refuses
        # to copy a database that has an open session.
        teardown_databases(cfg, verbosity=request.config.option.verbose, keepdb=True)
    finally:
        db["TEST"]["NAME"] = original
