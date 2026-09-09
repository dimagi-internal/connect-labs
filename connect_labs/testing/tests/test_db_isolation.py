"""The suite must not be talking to the development database.

`db_template.py` clones a template database per xdist worker, and a clone is
not the same thing as a connection: the first version of it copied four
databases correctly and then left every worker pointed at DATABASE_URL,
because Django restores the original name when the template is handed back.
Nothing failed. The tests passed, against the developer's own data.

That is not a failure any other test can report -- they all pass either way --
so it is asserted here directly.
"""
import pytest
from django.conf import settings
from django.db import connection


@pytest.mark.django_db
def test_we_are_on_a_test_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        current = cursor.fetchone()[0]

    assert current.startswith("test_"), (
        f"tests are connected to {current!r}, which is not a test database. "
        "Every test runs in a rolled-back transaction, so this passes silently "
        "while reading and writing real data."
    )


@pytest.mark.django_db
def test_django_and_postgres_agree_on_which_database():
    """Belt and braces: the settings could name one database while the open
    connection is on another, which is exactly how the original bug looked."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        current = cursor.fetchone()[0]

    # The name Django believes it is using must be the one we are actually on.
    assert current == settings.DATABASES["default"]["NAME"]


@pytest.mark.django_db
def test_each_xdist_worker_has_its_own_database():
    """Two workers sharing one database would corrupt each other's fixtures."""
    import os

    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        pytest.skip("serial run: there is only one database")

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        current = cursor.fetchone()[0]

    assert current.endswith(f"_{worker}"), f"worker {worker} is on {current!r}, which is not its own database"
