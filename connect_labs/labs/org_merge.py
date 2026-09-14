"""Merging two organisation rows that turn out to be one organisation.

The matching rule (`LabsOrg`) says two local rows resolving to one Connect
id is a conflict to REPORT rather than merge, because merging on the
strength of a string folds two histories together. This is the other half of
that: the deliberate, named act a person takes once they have decided the
two really are the same body.

It is needed the first time reconciliation finds a duplicate, and it is
needed now for one this work created -- an importer invented a "Programme
team" organisation for Dimagi, which the migration then faithfully carried
across, leaving two rows meaning one organisation and a document attributed
to the wrong one.
"""

from django.apps import apps
from django.db import transaction

from connect_labs.labs.models import LabsOrg


def references_to_org():
    """Every foreign key in the project that points at a LabsOrg.

    Found by scanning fields rather than by reading `_meta.related_objects`,
    and the difference is not academic: `recorded_by_org` is declared with
    `related_name="+"`, so it has no reverse accessor and does not appear in
    related_objects at all. A sweep built on that API sees three references
    where there are thirteen, and silently leaves every provenance row
    pointing at an organisation that no longer exists.
    """
    found = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if getattr(field, "many_to_one", False) and getattr(field, "related_model", None) is LabsOrg:
                found.append((model, field.name))
    return found


def merge_orgs(*, keep_id: int, merge_id: int) -> dict:
    """Point everything at `keep`, then delete `merge`.

    Returns what moved, per reference, because a merge that reports only
    "done" gives no way to notice it moved nothing -- which is what a missed
    reference looks like from outside.
    """
    if keep_id == merge_id:
        raise ValueError("an organisation cannot be merged into itself")

    keep = LabsOrg.objects.filter(pk=keep_id).first()
    merge = LabsOrg.objects.filter(pk=merge_id).first()
    if keep is None:
        raise ValueError(f"organisation {keep_id} does not exist")
    if merge is None:
        raise ValueError(f"organisation {merge_id} does not exist")

    # Both linked to DIFFERENT Connect organisations means they are not the
    # same body, whatever the names suggest. Refusing is the point: the
    # matching rule exists so a merge cannot be done on a resemblance.
    if (
        keep.connect_organization_id is not None
        and merge.connect_organization_id is not None
        and keep.connect_organization_id != merge.connect_organization_id
    ):
        raise ValueError(
            f"{keep.slug} and {merge.slug} are linked to different Connect organisations "
            f"({keep.connect_organization_id} and {merge.connect_organization_id}), so they are "
            "not one organisation. Unlink the wrong one first."
        )

    moved = {}
    with transaction.atomic():
        for model, field_name in references_to_org():
            n = model.objects.filter(**{field_name: merge}).update(**{field_name: keep})
            if n:
                moved[f"{model._meta.label}.{field_name}"] = n

        # Capture what the other row carried, then DELETE it before applying
        # any of it. Writing the inherited Connect id onto the survivor while
        # the other still holds it puts the same id on two rows for an
        # instant, and the unique constraint -- the one that makes "two rows,
        # one Connect org" impossible -- fires on the way through.
        inherited_id = merge.connect_organization_id
        inherited_slug = merge.connect_organization_slug
        inherited_aliases = list(merge.aliases or [])
        merged_slug = merge.slug
        merge.delete()

        changed = []
        if keep.connect_organization_id is None and inherited_id is not None:
            keep.connect_organization_id = inherited_id
            changed.append("connect_organization_id")
        if not keep.connect_organization_slug and inherited_slug:
            keep.connect_organization_slug = inherited_slug
            changed.append("connect_organization_slug")
        inherited = [a for a in inherited_aliases if a not in (keep.aliases or [])]
        extra = [merged_slug] if merged_slug not in (keep.aliases or []) else []
        if inherited or extra:
            # The merged-away slug becomes an alias. Somebody referred to the
            # organisation by it, and a lookup by that name should still find
            # the body it meant rather than nothing.
            keep.aliases = [*(keep.aliases or []), *inherited, *extra]
            changed.append("aliases")
        if changed:
            keep.save(update_fields=changed)

    return {
        "kept": {"id": keep.pk, "slug": keep.slug, "name": keep.name},
        "merged_away": {"id": merge_id, "slug": merged_slug},
        "moved": moved,
        "total_moved": sum(moved.values()),
        "inherited": changed,
    }
