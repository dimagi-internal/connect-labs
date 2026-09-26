"""Signal receivers that turn every supply-record write into a `Revision`.

`connect_receivers()`, called once from `apps.py::ready()`, connects
`pre_save`, `post_save`, `pre_delete` and `post_delete` to each
`supply_chain` model (including auto-created through models, excluding the
history tables themselves), plus `m2m_changed` for the many-to-many link rows
an `add()` bulk-inserts. Every connection names its sender. A receiver
connected with no sender makes `pre_delete.has_listeners()` true for EVERY
model in the project, which turns off Django's fast delete everywhere -- a
visit-cache purge or a retention sweep would then load every row it deletes.
The only non-supply models that get a receiver are those a supply through
model points at (an organisation), whose delete takes link rows with it.

The diff is taken between the row as loaded fresh from the database in
`pre_save` (never the in-memory instance, which may itself be stale) and the
instance as it is about to be saved in `post_save`. A save outside any
`write_context` still writes a revision with `call=None` -- nothing escapes
history, per design doc §3.2.
"""

import json

from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save

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


def _content_type(model):
    """The ContentType for `model`, safe for auto-created through models.

    `migrate` creates content types only for a registry's regular models, so
    a through model's is made on first use -- and `get_for_model` would cache
    it for the life of the process even if the transaction that made it
    rolled back, leaving every later write pointing at a row that is not
    there. For those, ask the table each time; everything else keeps the cache.
    """
    if model._meta.auto_created:
        return ContentType.objects.get_or_create(app_label=model._meta.app_label, model=model._meta.model_name)[0]
    return ContentType.objects.get_for_model(model)


def _write(instance, action, changes, program_id):
    overrides = current_overrides()
    kwargs = {}
    if "recorded_at" in overrides:
        kwargs["recorded_at"] = overrides["recorded_at"]
    Revision.objects.create(
        call=current_call(),
        program_id=program_id,
        content_type=_content_type(type(instance)),
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
    if is_suspended() or sender.__module__ == "__fake__":
        return
    if _tracked(sender):
        # Captured before the row (or, in a cascade, any of its parents) is
        # actually deleted, so a program path that walks a foreign key still
        # resolves.
        instance._history_program = program_of(instance)
        instance._history_snapshot = _snapshot(instance)
    # Django's collector never sends delete signals for an auto-created
    # through model, so the link rows a cascade takes with this row (a
    # deleted tender's invitations, a deleted contract's update-link entries)
    # would vanish unrecorded. Record them here, while they -- and the
    # parents their program path walks -- still exist. Any sender, not only
    # a supply one: deleting an organisation drops its invitations too.
    for through, fk in _links_pointing_at(sender):
        _write_link_deletes(through._base_manager.filter(**{fk.attname: instance.pk}))


def on_post_delete(sender, instance, **kwargs):
    if _tracked(sender) and not is_suspended():
        _write(instance, "delete", instance._history_snapshot, instance._history_program)


_LINKS = {}


def _links_pointing_at(model):
    """[(through model, its FK to `model`)] over this app's auto-created through models."""
    if model not in _LINKS:
        from django.apps import apps

        _LINKS[model] = [
            (through, field)
            for through in apps.get_app_config("supply_chain").get_models(include_auto_created=True)
            if through._meta.auto_created
            for field in through._meta.concrete_fields
            if field.is_relation and field.related_model == model._meta.concrete_model
        ]
    return _LINKS[model]


def _write_link_deletes(rows):
    for row in rows.order_by("pk"):
        _write(row, "delete", _snapshot(row), program_of(row))


def _link_rows(sender, instance, model, pk_set):
    """The through rows joining `instance` to `pk_set` (every one, when None).

    Found by which FK points at `instance`'s model and which at `model`, not
    by direction, so the same code serves `reverse=True` (called from the
    other side). No through model in this app relates a model to itself,
    where that would be ambiguous.
    """
    links = [f for f in sender._meta.concrete_fields if f.is_relation]
    own = next(f for f in links if f.related_model == instance._meta.concrete_model)
    other = next(f for f in links if f is not own and f.related_model == model._meta.concrete_model)
    rows = sender._base_manager.filter(**{own.attname: instance.pk})
    return rows if pk_set is None else rows.filter(**{f"{other.attname}__in": pk_set})


def on_m2m_changed(sender, instance, action, model, pk_set, **kwargs):
    """Many-to-many link rows, which Django writes and deletes without the
    per-row signals the receivers above rely on.

    - `post_add`: `add()` inserts with `bulk_create`, so no `post_save`.
      `pk_set` holds only the rows actually inserted.
    - `pre_remove` / `pre_clear`: `remove()` and `clear()` delete through a
      queryset whose collector skips delete signals for auto-created
      models. Snapshotted here, before they go.
    """
    if not _tracked(sender) or is_suspended():
        return
    if action == "post_add" and pk_set:
        for row in _link_rows(sender, instance, model, pk_set).order_by("pk"):
            _write(row, "create", {k: [None, v] for k, v in _snapshot(row).items()}, program_of(row))
    elif action == "pre_remove" and pk_set:
        _write_link_deletes(_link_rows(sender, instance, model, pk_set))
    elif action == "pre_clear":
        _write_link_deletes(_link_rows(sender, instance, model, None))


def _supply_models():
    from django.apps import apps

    return [
        model
        for model in apps.get_app_config("supply_chain").get_models(include_auto_created=True)
        if model not in (OperationCall, Revision)
    ]


def connect_receivers():
    """Connect every receiver above to exactly the senders it serves. Idempotent (dispatch_uid)."""
    supply = _supply_models()
    for model in supply:
        uid = model._meta.label_lower
        pre_save.connect(on_pre_save, sender=model, dispatch_uid=f"supply_history_pre_save:{uid}")
        post_save.connect(on_post_save, sender=model, dispatch_uid=f"supply_history_post_save:{uid}")
        pre_delete.connect(on_pre_delete, sender=model, dispatch_uid=f"supply_history_pre_delete:{uid}")
        post_delete.connect(on_post_delete, sender=model, dispatch_uid=f"supply_history_post_delete:{uid}")
        if model._meta.auto_created:
            m2m_changed.connect(on_m2m_changed, sender=model, dispatch_uid=f"supply_history_m2m_changed:{uid}")
    # A model outside this app that a supply through model points at: deleting
    # it cascades to link rows that must be recorded (on_pre_delete). Nothing
    # else outside the app is listened to.
    for model in link_targets_outside_app(supply):
        pre_delete.connect(
            on_pre_delete, sender=model, dispatch_uid=f"supply_history_pre_delete:{model._meta.label_lower}"
        )


def link_targets_outside_app(supply=None):
    """Non-supply models some supply through model has a foreign key to."""
    supply = supply if supply is not None else _supply_models()
    targets = []
    for through in supply:
        if not through._meta.auto_created:
            continue
        for field in through._meta.concrete_fields:
            target = field.related_model._meta.concrete_model if field.is_relation else None
            if target is not None and target._meta.app_label != "supply_chain" and target not in targets:
                targets.append(target)
    return targets
