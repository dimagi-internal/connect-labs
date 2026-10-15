"""Migration 0014 reverses with labs-created responses present."""

import importlib

import pytest
from django.apps import apps

from connect_labs.solicitations.local_models import SolicitationResponse
from connect_labs.solicitations.tests.factories import make_call, make_response

pytestmark = pytest.mark.django_db

migration = importlib.import_module("connect_labs.solicitations.migrations.0014_solicitations_owned_by_organisations")


def test_reverse_fills_null_source_rows_before_not_null_returns():
    call = make_call()
    web = make_response(call)
    imported = make_response(call, source_row=7)
    assert web.source_row is None
    migration.fill_null_source_rows(apps, None)
    web.refresh_from_db()
    imported.refresh_from_db()
    assert web.source_row == -web.pk  # unique per call, and never a real sheet row
    assert imported.source_row == 7
    assert not SolicitationResponse.objects.filter(source_row=None).exists()


def test_fill_runs_on_reverse_after_the_not_null_alter_is_undone():
    ops = migration.Migration.operations
    alter = next(
        i
        for i, op in enumerate(ops)
        if getattr(op, "name", None) == "source_row" and op.__class__.__name__ == "AlterField"
    )
    fill = next(i for i, op in enumerate(ops) if getattr(op, "reverse_code", None) is migration.fill_null_source_rows)
    assert fill > alter  # operations reverse last-first, so the fill runs before NOT NULL is restored
