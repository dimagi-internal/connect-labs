"""Derivation rules for the org/program shell of labs-only synthetic opps.

Shared by ``labs.context._merge_labs_only_opps`` (the UI's user_organizations
tree) and the ``/api/export/opp_org_program_list`` endpoint so the synthesized
org slugs and program ids can never drift between the two surfaces.

A labs-only organisation is a ``labs.LabsOrg`` row (``SyntheticOpportunity.labs_org``),
and its key everywhere -- the org tree, Settings, ``/labs/p/org/<key>/`` -- is that
row's ``slug``. It used to be ``labs-synthetic-<slug of org_name>``, worked out on
every request, so every synthetic opp anyone gave the same organisation name was one
organisation and renaming one moved it. Rows made from those buckets kept the key
they had (migration ``synthetic/0010``), so nothing that names one changed address.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from connect_labs.labs.models import LABS_ONLY_ORG_PREFIX as SYNTHETIC_ORG_PREFIX


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, alnum-only slug. Empty/blank input -> 'labs'."""
    return "".join(c if c.isalnum() else "-" for c in (value or "").strip().lower()).strip("-") or "labs"


def legacy_org_slug(org_name: str) -> str:
    """The key an organisation name used to stand for: ``labs-synthetic-<slugified name>``."""
    return f"{SYNTHETIC_ORG_PREFIX}{slugify(org_name or 'Labs Synthetic')}"


def is_labs_only_org_row(org) -> bool:
    """Whether a LabsOrg row can stand for a labs-only organisation.

    Unlinked, and keyed in the labs-only namespace. A row Connect knows is a real
    organisation: naming it from a synthetic opp would make everyone who can see
    that opp a holder of the real one (`labs.access.scopes.holdings`).
    """
    return org is not None and org.connect_organization_id is None and org.slug.startswith(SYNTHETIC_ORG_PREFIX)


def synthetic_org_slug(opp) -> str:
    """The key of the labs-only organisation `opp` is filed under.

    Its registry row's slug. An opp with no row yet (unsaved, or saved by a path
    that bypassed `save()`) falls back to the old name-derived key, which is
    always in the labs-only namespace and so can never name a real organisation.
    """
    org = getattr(opp, "labs_org", None) if getattr(opp, "labs_org_id", None) else None
    if is_labs_only_org_row(org):
        return org.slug
    return legacy_org_slug(opp.org_name)


def synthetic_org_name(opp) -> str:
    """What the organisation is called: its registry row's name, else the opp's org_name."""
    org = getattr(opp, "labs_org", None) if getattr(opp, "labs_org_id", None) else None
    if is_labs_only_org_row(org) and org.name:
        return org.name
    return opp.org_name or "Labs Synthetic"


def synthetic_program_id(opp) -> int:
    """Program id for a synthetic opp: its registered ``program_id``, else its own opp id."""
    return opp.program_id or opp.opportunity_id


# --- filing an opp under an organisation ---------------------------------------


def _key_taken(key: str) -> bool:
    """A key some organisation has, had, or is still named by somewhere."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.scope_config.models import ScopeConfig

    if LabsOrg.objects.filter(slug=key).exists() or LabsOrg.objects.filter(aliases__contains=[key]).exists():
        return True
    # Settings written for an organisation that has since gone: a new one under
    # its key would inherit them.
    return ScopeConfig.objects.filter(scope_type="organization", scope_key=key).exists()


def _new_key(name: str) -> str:
    base = legacy_org_slug(name)[:110]
    if not _key_taken(base):
        return base
    n = 2
    while _key_taken(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def _mint(name: str, created_by):
    from connect_labs.labs.models import LabsOrg
    from connect_labs.marketplace.models import OrgMembership

    for _attempt in range(5):
        try:
            with transaction.atomic():
                org = LabsOrg.objects.create(slug=_new_key(name), name=name or "Labs Synthetic")
                if created_by is not None:
                    OrgMembership.objects.create(org=org, user=created_by, role="admin")
                return org
        except IntegrityError:
            # Someone took the key between the check and the insert; take the next one.
            continue
    raise RuntimeError(f"could not find a free key for a labs-only organisation named {name!r}")


def assign_labs_org(opp):
    """The organisation a labs-only opp with none yet is filed under.

    In order:
      1. its programme's: every opp in one programme has one organisation, so a
         programme's owner is never ambiguous;
      2. the one its creator already filed opps of this organisation name under:
         one person's "Baobab Trust" is one organisation;
      3. a new one, administered by its creator.

    Never another person's organisation because the names match -- that was the
    old bucket, and the reason only staff could change a labs-only org's settings.
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    siblings = SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=False).exclude(pk=opp.pk)
    if opp.program_id:
        sibling = siblings.filter(program_id=opp.program_id).select_related("labs_org").first()
        if sibling is not None and is_labs_only_org_row(sibling.labs_org):
            return sibling.labs_org
    same_name = siblings.filter(org_name=opp.org_name or "", created_by_id=opp.created_by_id)
    for sibling in same_name.select_related("labs_org").order_by("pk"):
        if is_labs_only_org_row(sibling.labs_org):
            return sibling.labs_org
    return _mint(opp.org_name or "Labs Synthetic", opp.created_by)
