"""Give every pre-existing supply row a `create` Revision.

An as-of rewind (design doc §3.5) undoes revisions recorded after a given
instant, working newest-first back to nothing. A row written before history
existed has no `create` revision to stop at, so rewinding past its actual
creation would erase it -- the wrong answer, since it did exist. This
backfills one `create` revision per row, stamped with the row's own
`created_at` where it has one, or this migration's run time where it does
not (`ShipmentLine`, `ReceiptLine`, `DistributionLine`, `AlertCheckState`,
`AlertNotice`, `UpdateLinkSubmission`).

`backfill` is a module-level function (not a closure) so a test can import
and call it directly against a real database, per
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5 and
`tests/test_history_backfill.py`.
"""

from django.db import migrations
from django.utils import timezone

BATCH_SIZE = 1000

# Every model this migration walks, not just the ones `program.PATHS` can
# name -- a row in `program.SKIPPED_MODELS` (SupplierProfile, SupplierOffering,
# Portfolio) still gets a `create` revision going forward (capture.py tracks
# every supply_chain model, program_id=None for these), so the backfill must
# match that shape rather than silently omitting them.
EXCLUDED_MODELS = {"OperationCall", "Revision"}

# `program_of` walks a foreign key (or a chain of them) for every model whose
# `program.PATHS` entry is a `_via(...)` resolver -- without help, that is one
# extra query PER HOP PER ROW. This names the full `select_related()` path(s)
# `program_of` might actually follow for each of those models, so the walk
# costs one JOIN per chunk instead. Built by hand rather than introspecting
# `_via`'s closures, because a model's real dependency is not just its own
# PATHS attribute: `program_of` recurses into whatever that attribute points
# at, and `_via` can offer SEVERAL alternate attributes (Receipt tries
# `contract`, then `shipment`, then `supply_point`) so every alternative's
# own chain needs covering too. `test_every_via_resolved_model_has_a_select_
# related_entry` (tests/test_history_backfill.py) keeps this in sync with
# `program.PATHS` by construction, not by memory.
SELECT_RELATED = {
    "Outreach": ["tender"],
    "Quote": ["tender"],
    "Award": ["tender"],
    "AwardApproval": ["award__tender"],
    "Shipment": ["contract"],
    "Invoice": ["contract"],
    "Charge": ["shipment__contract"],
    "ShipmentLine": ["shipment__contract"],
    "Receipt": ["contract", "shipment__contract", "supply_point"],
    "ReceiptLine": ["receipt__contract", "receipt__shipment__contract", "receipt__supply_point"],
    "Payment": ["invoice__contract"],
    "DistributionLine": ["distribution"],
    "AlertCheckState": ["subscription"],
    # `contract` is nullable on a submission; `link` never is (program.py).
    "UpdateLinkSubmission": ["link"],
}


def _queryset_for(model):
    """Every row of `model`, pre-joined along whatever `program_of` will walk."""
    queryset = model._default_manager.all()
    paths = SELECT_RELATED.get(model.__name__)
    return queryset.select_related(*paths) if paths else queryset


def backfill(apps, schema_editor):
    # Imported here, not at module scope: a migration module is imported by
    # Django's loader long before app registry setup finishes, and these
    # modules import real (non-historical) model classes at import time.
    from connect_labs.supply_chain.history.capture import _snapshot
    from connect_labs.supply_chain.history.program import program_of

    ContentType = apps.get_model("contenttypes", "ContentType")
    Revision = apps.get_model("supply_chain", "Revision")
    run_time = timezone.now()

    for model in apps.get_app_config("supply_chain").get_models():
        if model.__name__ in EXCLUDED_MODELS:
            continue

        content_type, _ = ContentType.objects.get_or_create(
            app_label="supply_chain", model=model._meta.model_name
        )
        has_created_at = any(field.name == "created_at" for field in model._meta.concrete_fields)
        already = set(
            Revision.objects.filter(content_type=content_type, action="create").values_list(
                "object_id", flat=True
            )
        )

        batch = []
        for instance in _queryset_for(model).iterator(chunk_size=BATCH_SIZE):
            object_id = str(instance.pk)
            if object_id in already:
                continue
            try:
                # `program_of` keys on `type(instance).__name__` and walks
                # plain attributes, both of which a historical model instance
                # carries identically to the real one -- see the design doc
                # note above.
                program_id = program_of(instance)
            except Exception:
                # A relation this migration's frozen apps registry can't
                # walk loses only its program scope, not the whole backfill.
                program_id = None
            recorded_at = getattr(instance, "created_at", None) if has_created_at else None
            if recorded_at is None:
                recorded_at = run_time
            changes = {field: [None, value] for field, value in _snapshot(instance).items()}
            batch.append(
                Revision(
                    call=None,
                    program_id=program_id,
                    content_type=content_type,
                    object_id=object_id,
                    action="create",
                    changes=changes,
                    recorded_at=recorded_at,
                )
            )
            if len(batch) >= BATCH_SIZE:
                Revision.objects.bulk_create(batch)
                batch = []
        if batch:
            Revision.objects.bulk_create(batch)


def backwards(apps, schema_editor):
    """Intentionally a no-op.

    Reversing this migration should not delete the create revisions it
    wrote -- a later forward run is idempotent anyway (it skips rows that
    already have one), so there is nothing to undo that re-running wouldn't
    already handle correctly.
    """


class Migration(migrations.Migration):

    dependencies = [
        ("supply_chain", "0032_history"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    # This repo's multi-DB router requires every RunPython to say which
    # database it belongs to (see 0027_round_is_a_tender.py). History is
    # primary-only, same as every other supply_chain table.
    operations = [migrations.RunPython(backfill, backwards, hints={"run_on_secondary": False})]
