"""The seeded demo world, captured once and replayed cheaply into each test.

``seed_demo_world()`` builds 704 rows in ~4,250 queries and takes ~1.8s. Nearly
every narrative test in this package needs that exact world and nothing else,
and 70-odd of them used to call ``call_command("seed_supply_demo")`` inline —
about two minutes of the suite spent rebuilding an identical fixture.

Building it once per session and COMMITTING it is not an option: the same
package has ~200 factory-based tests that assert against an empty database, and
committed rows outlive the per-test rollback that keeps them honest. (Tried it:
78 failures.)

So the world is built once in a transaction that is rolled straight back, its
rows are kept in memory, and each test that asks for it gets them replayed with
one ``bulk_create`` per table — inside that test's own transaction, so the
rollback still cleans up and a test that does not ask still sees an empty
database. Same rows, same primary keys, ~30 queries instead of ~4,250.
"""
import copy
from contextlib import contextmanager

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.color import no_style
from django.db import connection, transaction

# Models the seeder writes outside its own app. The demo world creates login
# users for every persona, and a test that logs one in needs them replayed too.
EXTRA_MODELS = [get_user_model()]


def _models():
    """Every model the demo world writes, parents before children.

    A plain topological sort over concrete FK/O2O edges. bulk_create does not
    defer constraint checks, so a child inserted before its parent fails on the
    foreign key rather than being fixed up later.
    """
    models = list(apps.get_app_config("supply").get_models()) + EXTRA_MODELS
    known = set(models)
    ordered, seen = [], set()

    def visit(model):
        if model in seen:
            return
        seen.add(model)
        for field in model._meta.get_fields():
            if field.is_relation and field.concrete and (field.many_to_one or field.one_to_one):
                parent = getattr(field, "related_model", None)
                if parent in known and parent is not model:
                    visit(parent)
        ordered.append(model)

    for model in models:
        visit(model)
    return ordered


def _capture():
    """Seed the world, take every row, then roll the seeding back."""
    from connect_labs.supply.demo import seed_demo_world

    ordered = _models()
    snapshot = []
    with transaction.atomic():
        seed_demo_world()
        for model in ordered:
            rows = list(model.objects.all())
            if rows:
                snapshot.append((model, rows))
        transaction.set_rollback(True)
    return snapshot


@contextmanager
def _auto_timestamps_off(models):
    """Stop ``auto_now``/``auto_now_add`` re-stamping the replayed rows.

    ``bulk_create`` runs ``pre_save``, and an ``auto_now_add`` field stamps
    itself with the current time there — which would overwrite the dates the
    seeder deliberately set. The demo world is a dated narrative: ``Award
    .awarded_at`` is 2026-07-10 because a contract must not start before it was
    awarded, and a test says so. Replaying it as "now" silently breaks that.
    """
    touched = []
    for model in models:
        for field in model._meta.get_fields():
            if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                touched.append((field, field.auto_now, field.auto_now_add))
                field.auto_now = field.auto_now_add = False
    try:
        yield
    finally:
        for field, auto_now, auto_now_add in touched:
            field.auto_now, field.auto_now_add = auto_now, auto_now_add


def _restore(snapshot):
    """Replay the captured rows into the current transaction."""
    with _auto_timestamps_off([model for model, _ in snapshot]):
        for model, rows in snapshot:
            # deepcopy: bulk_create stamps ``_state`` onto the instances it is
            # handed, and these are replayed into every test in the session.
            model.objects.bulk_create(copy.deepcopy(rows))

    # The rows carry their original primary keys, which leaves each table's
    # sequence behind them. Without this, the first row a test creates itself
    # collides with a replayed one.
    statements = connection.ops.sequence_reset_sql(no_style(), [model for model, _ in snapshot])
    if statements:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


@pytest.fixture(scope="session")
def _supply_world_snapshot(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        return _capture()


@pytest.fixture
def seeded_world(db, _supply_world_snapshot):
    """The Operation End Starvation demo world, as ``seed_supply_demo`` builds it.

    Ask for this instead of calling the management command: it is the same
    world, built by the same seeder, at a fraction of the cost. Tests of the
    SEEDING ITSELF — idempotency, ``--reset``, password rotation — must keep
    calling the command, because what they assert is what it does on a second
    run, not what it leaves behind on the first.
    """
    _restore(_supply_world_snapshot)
