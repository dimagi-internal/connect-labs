"""Rewind a program's supply records to a past instant, inside a transaction
the caller rolls back (design doc §3.5).

Every revision this program recorded after `as_of` is undone, newest first,
as a plain ORM write with capture suspended:

- `update` puts back the old value of each changed field;
- `create` deletes the row -- rows created later go first, so a child (or an
  award that PROTECTs its quote) leaves before its parent;
- `delete` re-inserts the row from its flat snapshot, with the same pk. The
  snapshot leaves out `created_at`/`updated_at` (capture ignores them), so
  they are rebuilt from the row's own revisions: created when its first
  revision says, last updated when its last one before the delete says.

Nothing here is ever kept: the caller renders the page inside the same
`transaction.atomic()` block and then calls `set_rollback(True)`. That is why
`rewind` refuses to run outside an atomic block -- in autocommit each undo
would commit on its own and rewrite live data.
"""

from functools import cache

from django.db import connection
from django.db.models import Q

from connect_labs.supply_chain.history.context import capture_suspended
from connect_labs.supply_chain.history.models import Revision


@cache
def _fields(model):
    """{attname: concrete field} for `model`, built once per model."""
    return {f.attname: f for f in model._meta.concrete_fields}


def _coerce(model, attname, value):
    """A stored JSON value back to the Python type its field expects.

    `changes` went through DjangoJSONEncoder, so a Decimal is a string and a
    date an ISO string. `to_python` restores them; a ForeignKey delegates to
    its target's pk field, and a JSONField hands its value back unchanged.
    """
    return None if value is None else _fields(model)[attname].to_python(value)


def _known(model, values):
    """Only the attnames `model` still has, coerced.

    A revision can outlive a field: one recorded before a field was removed
    or renamed still names it. Skipping it keeps every as-of page before
    that change readable, rather than failing all of them.
    """
    fields = _fields(model)
    return {k: _coerce(model, k, v) for k, v in values.items() if k in fields}


def _timestamps(model, rev, values):
    """Fill the auto_now / auto_now_add fields a delete snapshot does not carry.

    A raw insert skips `pre_save`, so without this they would be NULL and the
    insert would fail. The row's own earlier revisions -- earlier in the
    order rewind itself uses, recorded_at then id, since a seeder can
    backdate `recorded_at` out of id order -- say when it was made and last
    changed; the delete itself is the fallback for a row whose history starts
    at its deletion.
    """
    missing = [
        f
        for f in model._meta.concrete_fields
        if f.attname not in values and (getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False))
    ]
    if not missing:
        return values
    earlier = Revision.objects.filter(content_type_id=rev.content_type_id, object_id=rev.object_id).filter(
        Q(recorded_at__lt=rev.recorded_at) | Q(recorded_at=rev.recorded_at, id__lt=rev.id)
    )
    first = earlier.order_by("recorded_at", "id").values_list("recorded_at", flat=True).first()
    last = earlier.order_by("-recorded_at", "-id").values_list("recorded_at", flat=True).first()
    for field in missing:
        stamp = last if field.auto_now else first
        values[field.attname] = stamp or rev.recorded_at
    return values


def rewind(program_id, as_of):
    """Undo this program's revisions after `as_of`, newest first. The caller must roll back.

    Returns the number of revisions undone.
    """
    # This proves only that a transaction is open, not that the caller will
    # roll it back -- the as-of view wrapper (Task 6) is what enforces that.
    if not connection.in_atomic_block:
        raise RuntimeError("rewind() must run inside a transaction the caller rolls back")
    later = (
        Revision.objects.filter(program_id=program_id, recorded_at__gt=as_of)
        .select_related("content_type")
        .order_by("-recorded_at", "-id")
    )
    undone = 0
    with capture_suspended():
        for rev in later.iterator():
            model = rev.content_type.model_class()
            if model is None:  # the model has since been removed
                continue
            manager = model._base_manager
            if rev.action == "update":
                # `updated_at` is left at its live value: QuerySet.update()
                # does not touch auto_now, and nothing rewinds it here.
                old = _known(model, {k: old for k, (old, _new) in rev.changes.items()})
                if old:
                    manager.filter(pk=rev.object_id).update(**old)
            elif rev.action == "create":
                manager.filter(pk=rev.object_id).delete()
            elif rev.action == "delete":
                values = _timestamps(model, rev, _known(model, rev.changes))
                model(**values).save_base(raw=True, force_insert=True)
            undone += 1
    return undone
