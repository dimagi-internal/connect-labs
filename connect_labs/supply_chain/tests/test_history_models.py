"""Append-only history tables and the program resolver they depend on.

THIS REPOSITORY IS PUBLIC. Every id and reference below is invented.
"""

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType

from connect_labs.supply_chain.history.models import AppendOnlyError, OperationCall, Revision
from connect_labs.supply_chain.history.program import SKIPPED_MODELS, program_of
from connect_labs.supply_chain.tests.conftest import quote as make_quote

pytestmark = pytest.mark.django_db


def test_operation_call_refuses_update_and_delete():
    call = OperationCall.objects.create(program_id=1, operation="tender_create", channel="web")
    call.operation = "other"
    with pytest.raises(AppendOnlyError):
        call.save()
    with pytest.raises(AppendOnlyError):
        call.delete()


def test_revision_refuses_update_and_delete():
    ct = ContentType.objects.get_for_model(OperationCall)
    rev = Revision.objects.create(program_id=1, content_type=ct, object_id="1", action="create", changes={})
    rev.action = "update"
    with pytest.raises(AppendOnlyError):
        rev.save()
    with pytest.raises(AppendOnlyError):
        rev.delete()


def test_duplicate_source_ref_is_refused_by_the_database():
    from django.db import IntegrityError, transaction

    OperationCall.objects.create(program_id=1, operation="quote_record", channel="mcp", source_ref="<m1>")
    with pytest.raises(IntegrityError), transaction.atomic():
        OperationCall.objects.create(program_id=1, operation="quote_record", channel="mcp", source_ref="<m1>")
    # blank refs never collide
    OperationCall.objects.create(program_id=1, operation="quote_record", channel="web", source_ref="")
    OperationCall.objects.create(program_id=1, operation="quote_record", channel="web", source_ref="")


def test_every_supply_model_has_a_program_path():
    """A new model without a path would write revisions no as-of view can rewind."""
    from connect_labs.supply_chain.history import program

    # Auto-created many-to-many through models too: their rows are captured
    # like any other, so they need a program as much as a real model does.
    for model in apps.get_app_config("supply_chain").get_models(include_auto_created=True):
        if model in (OperationCall, Revision) or model.__name__ in SKIPPED_MODELS:
            continue
        assert model.__name__ in program.PATHS, f"{model.__name__} has no program path"


def test_program_of_resolves_through_a_foreign_key_chain_and_a_scope_key(rutf, tender_2000_cartons):
    """A quote resolves through its tender; a commodity resolves off its own scope_key.

    Reuses `test_procurement_operations.py`'s world builders: `rutf` and
    `tender_2000_cartons` from conftest.py, plus the module-level `quote()`
    helper that builds a Quote pointed at tender id 1. The tender is saved so
    the FK chain has something real to resolve against; the quote itself
    stays unsaved (its supplier is never touched by the resolver, so it
    never needs to exist).
    """
    tender_2000_cartons.save()
    q = make_quote()

    assert program_of(q) == tender_2000_cartons.program_id
    assert program_of(rutf) == int(rutf.scope_key.split(":", 1)[1])
