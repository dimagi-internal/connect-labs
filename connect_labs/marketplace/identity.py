"""A directory name in, a stable ``LabsOrg`` out.

The importer runs daily over a sheet people edit by hand, so the same
organisation arrives repeatedly with incidental differences — a trailing space,
a changed capitalisation, an accent typed two ways. Any of those forking a new
row would duplicate the registry within a week, so the slug is normalised hard.

What it does NOT do is merge. Two different names that normalise close to each
other stay two rows, and a slug collision is resolved by suffixing rather than
by reuse: merging two organisations on the strength of a truncated string folds
two histories together, and ``LabsOrg``'s own matching rules already refuse that
for exactly this reason.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from connect_labs.labs.models import LabsOrg

SLUG_MAX = 120


def _strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def slug_for(name: str) -> str:
    """A deterministic handle for an organisation name.

    Deterministic is the whole requirement: the same name must give the same
    slug on every run, on every machine, forever. A hash of the name would also
    be deterministic, but a readable slug is what makes a row identifiable in
    the admin and in a URL.
    """
    base = _strip_accents((name or "").strip().lower())
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        # A name of pure punctuation still needs a handle. Hash the original so
        # two such rows do not collide on one shared fallback.
        digest = hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:10]
        return f"org-{digest}"
    return base[:SLUG_MAX].rstrip("-")


def _slot_for(name: str) -> tuple[str, LabsOrg | None]:
    """The slug this name owns, and the organisation already holding it, if any.

    Two different names normalised onto one slug — which truncation makes
    likely for long names — do not share a row: the newcomer gets its own,
    suffixed slug rather than being folded into an organisation it is not.
    """
    slug = slug_for(name)
    org = LabsOrg.objects.filter(slug=slug).first()
    if org is not None and org.name.strip().lower() != name.lower():
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: SLUG_MAX - 7]}-{digest}"
        org = LabsOrg.objects.filter(slug=slug).first()
    return slug, org


def find_org(name: str) -> LabsOrg | None:
    """The organisation this name already refers to, or None. Writes nothing.

    The read half of `ensure_org`, for callers that must not rename an
    organisation on the way past -- supply links a supplier to a company, and
    a company Connect names is not supply's to rename.
    """
    return _slot_for((name or "").strip())[1]


def mint_org(name: str, *, country: str = "") -> LabsOrg:
    """A new organisation for a name `find_org` found nothing for."""
    name = (name or "").strip()
    slug, existing = _slot_for(name)
    if existing is not None:
        return existing
    return LabsOrg.objects.create(slug=slug, name=name, country=country)


class OrgExists(ValueError):
    """A new organisation was asked for under a name one already answers to."""


def taken_by(name: str) -> LabsOrg | None:
    """An organisation this name already refers to, however it is punctuated.

    Stricter than `find_org`, for the one caller that must never create a
    second row: a self-registration. "Acme Ltd." and "ACME Ltd" are one
    company, and so is a name an organisation carries as its short name or an
    alias. Compared on the normalised slug, over every organisation -- a
    registration is rare and a duplicate is expensive to undo.
    """
    key = slug_for(name)
    for org in LabsOrg.objects.only("pk", "slug", "name", "short_name", "aliases"):
        names = [org.name, org.short_name, *(org.aliases or [])]
        if org.slug == key or any(n and slug_for(n) == key for n in names):
            return org
    return None


def mint_new_org(name: str, *, country: str = "") -> LabsOrg:
    """A brand-new organisation, or `OrgExists` -- never an existing row.

    `mint_org` returns the organisation already holding the name, which is
    right for linking and wrong for registering: two people registering one
    name at once would otherwise both pass the form's check and the second
    would become an admin of the first's organisation. The unique slug is the
    lock; losing the race is a refusal, not a merge.
    """
    from django.db import IntegrityError, transaction

    name = (name or "").strip()
    if taken_by(name) is not None:
        raise OrgExists(f"“{name}” is already on file")
    try:
        with transaction.atomic():
            return LabsOrg.objects.create(slug=slug_for(name), name=name, country=country)
    except IntegrityError:
        raise OrgExists(f"“{name}” is already on file")


def ensure_org(name: str, *, short_name: str = "", country: str = "") -> LabsOrg:
    """Find or create the organisation this directory name refers to.

    Identity fields are only ever written when a value is supplied. A cleared
    cell in the sheet must not blank a value another source filled in — an
    import should add knowledge, never subtract it.
    """
    name = (name or "").strip()
    slug, org = _slot_for(name)

    if org is None:
        return LabsOrg.objects.create(slug=slug, name=name, short_name=short_name, country=country)

    updates = {}
    if short_name and org.short_name != short_name:
        updates["short_name"] = short_name
    if country and org.country != country:
        updates["country"] = country
    if name and org.name != name:
        updates["name"] = name
    if updates:
        for field, value in updates.items():
            setattr(org, field, value)
        org.save(update_fields=[*updates, "updated_at"])
    return org
