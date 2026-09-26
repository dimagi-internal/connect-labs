"""Give every pre-existing supply row a `create` Revision.

An as-of rewind (design doc §3.5) undoes revisions recorded after a given
instant, working newest-first back to nothing. A row written before history
existed has no `create` revision to stop at, so rewinding past its actual
creation would erase it -- the wrong answer, since it did exist. This
backfills one `create` revision per row, including the link rows of
auto-created many-to-many through models (a tender's invited organisations,
an update link's contracts), stamped with the moment the row came to exist
as nearly as the data says:

- the row's own `created_at`, where it has one;
- otherwise its own event time, where it records one (`OWN_STAMP`: an alert
  state's first report, a notice's detection, a submission's submit time);
- otherwise its parent's `created_at` -- a shipment line or a link row
  exists from when its parent does, so rewinding to before this migration
  ran must not strip lines off parents that stay;
- and only when none of those resolves, this migration's run time.

`backfill` is a module-level function (not a closure) so a test can import
and call it directly against a real database, per
docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.5 and
`tests/test_history_backfill.py`.
"""

import logging

from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.db import migrations
from django.utils import timezone

logger = logging.getLogger(__name__)

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
    # Auto-created many-to-many through models (program.py).
    "Tender_invited_orgs": ["tender"],
    "UpdateLink_contracts": ["updatelink"],
    "UpdateLink_supply_points": ["updatelink"],
    "UpdateLink_approvals": ["updatelink"],
}

# A model with no `created_at` of its own but a field saying when the row
# came to be. Better than its parent's `created_at`: a submission through a
# link happened when it was submitted, not when the link was issued.
OWN_STAMP = {
    "AlertCheckState": "first_reported_at",
    "AlertNotice": "detected_at",
    "UpdateLinkSubmission": "submitted_at",
}


def _queryset_for(model):
    """Every row of `model`, pre-joined along whatever `program_of` will walk."""
    queryset = model._default_manager.all()
    paths = SELECT_RELATED.get(model.__name__)
    return queryset.select_related(*paths) if paths else queryset


def _stamp(instance, name, run_time):
    """When this row came to exist, as nearly as the data says (module docstring)."""
    stamp = getattr(instance, "created_at", None) or getattr(instance, OWN_STAMP.get(name, ""), None)
    if stamp is None:
        # The parent is the first hop of the chain `program_of` walks, already
        # joined by `_queryset_for`, so this costs no query.
        for path in SELECT_RELATED.get(name, []):
            parent = getattr(instance, path.split("__", 1)[0], None)
            stamp = getattr(parent, "created_at", None)
            if stamp is not None:
                break
    return stamp or run_time


def backfill(apps, schema_editor):
    # Imported here, not at module scope: a migration module is imported by
    # Django's loader long before app registry setup finishes, and these
    # modules import real (non-historical) model classes at import time.
    from connect_labs.supply_chain.history.capture import _snapshot
    from connect_labs.supply_chain.history.program import program_of

    ContentType = apps.get_model("contenttypes", "ContentType")
    Revision = apps.get_model("supply_chain", "Revision")
    run_time = timezone.now()

    # `include_auto_created`: many-to-many through models are captured going
    # forward (capture.py), so their existing rows need a create revision too.
    unscoped = {}
    for model in apps.get_app_config("supply_chain").get_models(include_auto_created=True):
        if model.__name__ in EXCLUDED_MODELS:
            continue

        content_type, _ = ContentType.objects.get_or_create(
            app_label="supply_chain", model=model._meta.model_name
        )
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
            except (AttributeError, KeyError, LookupError, ValueError, ObjectDoesNotExist, FieldDoesNotExist):
                # A relation this migration's frozen apps registry can't
                # walk loses only its program scope, not the whole backfill --
                # counted and logged below, never silently.
                program_id = None
                unscoped[model.__name__] = unscoped.get(model.__name__, 0) + 1
            recorded_at = _stamp(instance, model.__name__, run_time)
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

    if unscoped:
        logger.warning(
            "history backfill: %d row(s) fell back to program None because their program could not be "
            "resolved: %s",
            sum(unscoped.values()),
            ", ".join(f"{name}={count}" for name, count in sorted(unscoped.items())),
        )
    return unscoped


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
