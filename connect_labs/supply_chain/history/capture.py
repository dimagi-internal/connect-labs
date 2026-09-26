"""Signal receivers that turn every supply-record write into a `Revision`.

Wired up for side effect: importing this module connects `pre_save`,
`post_save`, `pre_delete` and `post_delete` on every concrete Django model,
filtered down to `supply_chain`-labelled models (excluding the history
tables themselves) in the receivers below. `apps.py::ready()` imports it so
the connection happens once, at startup.

The diff is taken between the row as loaded fresh from the database in
`pre_save` (never the in-memory instance, which may itself be stale) and the
instance as it is about to be saved in `post_save`. A save outside any
`write_context` still writes a revision with `call=None` -- nothing escapes
history, per design doc §3.2.
"""

import json

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save

from connect_labs.supply_chain.history.context import current_call, current_overrides, is_suspended
from connect_labs.supply_chain.history.models import OperationCall, Revision
from connect_labs.supply_chain.history.program import program_of

# Timestamp-only fields excluded from `changes` -- a save that only touched
# `updated_at` is not a change worth a revision (see `test_noop_save_writes_nothing`).
CAPTURE_EXCLUDED_FIELDS = {"created_at", "updated_at"}


def _snapshot(instance):
    """A JSON-safe {field.attname: value} map, run through DjangoJSONEncoder
    and back so every value lands in the same shape a stored revision would
    hand back on read (Decimal -> str, date/datetime -> isoformat, ...)."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in CAPTURE_EXCLUDED_FIELDS:
            continue
        data[field.attname] = getattr(instance, field.attname)
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _tracked(sender):
    # Historical model classes Django renders for a migration's `apps` registry
    # (`MigrationExecutor(...).loader.project_state(...).apps`) carry
    # `__module__ == "__fake__"` -- Django's own marker for this, also relied
    # on by django-simple-history/django-reversion for the same reason. A
    # migration test can roll the schema back to a point before the history
    # tables existed and then create rows with one of these classes; it still
    # reports `app_label == "supply_chain"`, so without this check a receiver
    # would try to INSERT into `supply_chain_revision` against a schema where
    # that table has not been migrated in yet.
    if sender.__module__ == "__fake__":
        return False
    return sender._meta.app_label == "supply_chain" and sender not in (OperationCall, Revision)


def _write(instance, action, changes, program_id):
    overrides = current_overrides()
    kwargs = {}
    if "recorded_at" in overrides:
        kwargs["recorded_at"] = overrides["recorded_at"]
    Revision.objects.create(
        call=current_call(),
        program_id=program_id,
        content_type=ContentType.objects.get_for_model(type(instance)),
        object_id=str(instance.pk),
        action=action,
        changes=changes,
        **kwargs,
    )


def on_pre_save(sender, instance, **kwargs):
    if not _tracked(sender) or is_suspended() or kwargs.get("raw"):
        return
    instance._history_before = None
    if instance.pk is not None:
        before = sender._base_manager.filter(pk=instance.pk).first()
        instance._history_before = _snapshot(before) if before is not None else None


def on_post_save(sender, instance, created, **kwargs):
    if not _tracked(sender) or is_suspended() or kwargs.get("raw"):
        return
    after = _snapshot(instance)
    before = getattr(instance, "_history_before", None)
    if created or before is None:
        _write(instance, "create", {k: [None, v] for k, v in after.items()}, program_of(instance))
        return
    diff = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
    if diff:
        _write(instance, "update", diff, program_of(instance))


def on_pre_delete(sender, instance, **kwargs):
    if _tracked(sender) and not is_suspended():
        # Captured before the row (or, in a cascade, any of its parents) is
        # actually deleted, so a program path that walks a foreign key still
        # resolves.
        instance._history_program = program_of(instance)
        instance._history_snapshot = _snapshot(instance)


def on_post_delete(sender, instance, **kwargs):
    if _tracked(sender) and not is_suspended():
        _write(instance, "delete", instance._history_snapshot, instance._history_program)


pre_save.connect(on_pre_save, dispatch_uid="supply_history_pre_save")
post_save.connect(on_post_save, dispatch_uid="supply_history_post_save")
pre_delete.connect(on_pre_delete, dispatch_uid="supply_history_pre_delete")
post_delete.connect(on_post_delete, dispatch_uid="supply_history_post_delete")
