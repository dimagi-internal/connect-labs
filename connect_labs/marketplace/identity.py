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


def ensure_org(name: str, *, short_name: str = "", country: str = "") -> LabsOrg:
    """Find or create the organisation this directory name refers to.

    Identity fields are only ever written when a value is supplied. A cleared
    cell in the sheet must not blank a value another source filled in — an
    import should add knowledge, never subtract it.
    """
    name = (name or "").strip()
    slug = slug_for(name)

    org = LabsOrg.objects.filter(slug=slug).first()
    if org is not None and org.name.strip().lower() != name.lower():
        # Two different names normalised onto one slug — which truncation makes
        # likely for long names. Give the newcomer its own row rather than
        # folding it into an organisation it is not.
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        slug = f"{slug[: SLUG_MAX - 7]}-{digest}"
        org = LabsOrg.objects.filter(slug=slug).first()

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
