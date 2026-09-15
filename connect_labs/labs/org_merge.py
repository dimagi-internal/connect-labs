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
            if points_at_org(field):
                found.append((model, field.name))
    return found


def points_at_org(field) -> bool:
    """Whether this field is a forward relation whose target is a LabsOrg.

    Its own function so it can be tested against a field directly. Asserting
    on the SOURCE of the sweep -- "does it mention one_to_one" -- passes
    whether or not the condition actually uses it, which is how the first
    version of this test survived deleting the very clause it was guarding.
    """
    # `concrete` keeps this to FORWARD fields: a reverse relation's
    # related_model is the other end, so it would never match anyway, but
    # saying so means the filter reads as what it means.
    #
    # `one_to_one` is here because leaving it out re-creates the bug the
    # docstring above is about, one field type over: a OneToOneField to
    # LabsOrg has many_to_one False, so an FK-only sweep skips it and
    # merge_orgs then tries to delete a row something still points at. There
    # are none today; the cost of covering it is a word, and the cost of not
    # covering it is silent.
    if not getattr(field, "concrete", False):
        return False
    if getattr(field, "related_model", None) is not LabsOrg:
        return False
    return bool(getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))


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
        # The merged-away slug becomes an alias. Somebody referred to the
        # organisation by it, and a lookup by that name should still find the
        # body it meant rather than nothing.
        #
        # Deduplicated against what is already there AND against itself: a
        # merged-away row can carry its own slug in its aliases (this function
        # puts it there), so a chain of merges appended the same name twice.
        existing = list(keep.aliases or [])
        added = []
        for alias in [*inherited_aliases, merged_slug]:
            if alias and alias not in existing and alias not in added and alias != keep.slug:
                added.append(alias)
        if added:
            keep.aliases = [*existing, *added]
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
