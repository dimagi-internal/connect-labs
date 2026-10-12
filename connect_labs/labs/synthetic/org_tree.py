"""Derivation rules for the org/program shell of labs-only synthetic opps.

Shared by ``labs.context._merge_labs_only_opps`` (the UI's user_organizations
tree) and the ``/api/export/opp_org_program_list`` endpoint so the synthesized
org slugs and program ids can never drift between the two surfaces.

A labs-only organisation is a ``labs.LabsOrg`` row with ``labs_only`` set
(``SyntheticOpportunity.labs_org``), and its key everywhere -- the org tree,
Settings, ``/labs/p/org/<key>/`` -- is that row's ``slug``. It used to be
``labs-synthetic-<slug of org_name>``, worked out on every request, so every
synthetic opp anyone gave the same organisation name was one organisation and
renaming one moved it. Rows made from those buckets kept the key they had
(migration ``synthetic/0010``), so nothing that names one changed address.

The ``labs-synthetic-`` prefix is reserved: nothing but this module mints a key
with it (``marketplace.identity.slug_for`` steers clear, ``org_upsert`` refuses
it), and Connect organisations carrying it are dropped from what a caller holds
(``drop_labs_only_keys``). But the prefix only reserves; the ``labs_only`` flag
decides.
"""

from __future__ import annotations

import logging
import zlib

from django.db import IntegrityError, connection, transaction
from django.db.models import Q

from connect_labs.labs.models import LABS_ONLY_ORG_PREFIX as SYNTHETIC_ORG_PREFIX

logger = logging.getLogger(__name__)


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, alnum-only slug. Empty/blank input -> 'labs'."""
    return "".join(c if c.isalnum() else "-" for c in (value or "").strip().lower()).strip("-") or "labs"


def legacy_org_slug(org_name: str) -> str:
    """The key an organisation name used to stand for: ``labs-synthetic-<slugified name>``.

    Now only how the old buckets are found (the migration, the backfill command)
    and which keys a new organisation must not take. Never what an opp is keyed by.
    """
    return f"{SYNTHETIC_ORG_PREFIX}{slugify(org_name or 'Labs Synthetic')}"


UNFILED_PREFIX = f"{SYNTHETIC_ORG_PREFIX}unfiled-"


def bucket_key(org_name: str) -> str:
    """The key an organisation name files under: its legacy key, kept out of `unfiled_key`'s namespace.

    A name like "Unfiled 10502" would otherwise make `labs-synthetic-unfiled-10502`
    -- opp 10502's own unfiled key -- and put that opp's settings in this
    organisation's hands. Such a name moves to `labs-synthetic-org-unfiled-...`.
    """
    key = legacy_org_slug(org_name)
    if key.startswith(UNFILED_PREFIX):
        key = f"{SYNTHETIC_ORG_PREFIX}org-{key[len(SYNTHETIC_ORG_PREFIX):]}"
    return key


def unfiled_key(opp) -> str:
    """The key of an opp not (or no longer) filed under a labs-only organisation.

    Its own: nobody else's opps share it, so it can never put one tenant's opp in
    another's organisation. It is in the reserved namespace and has no registry
    row, so it reads as labs-only and only staff change its settings.
    """
    return f"{UNFILED_PREFIX}{opp.opportunity_id}"


def is_reserved_key(key) -> bool:
    return str(key or "").startswith(SYNTHETIC_ORG_PREFIX)


def is_labs_only_org_row(org) -> bool:
    """Whether a LabsOrg row is a labs-only organisation: the registry says so, and Connect does not know it."""
    return org is not None and bool(org.labs_only) and org.connect_organization_id is None


def synthetic_org_slug(opp) -> str:
    """The key of the labs-only organisation `opp` is filed under.

    Its registry row's slug -- and never anything derived from its name. An opp
    with no row (unsaved, or left by a path that bypassed `save()`), or whose row
    is no longer a labs-only organisation, gets `unfiled_key`: a key of its own,
    which only staff can configure, rather than one another tenant might hold.
    """
    org = getattr(opp, "labs_org", None) if getattr(opp, "labs_org_id", None) else None
    if is_labs_only_org_row(org):
        return org.slug
    return unfiled_key(opp)


def synthetic_org_name(opp) -> str:
    """What the organisation is called: its registry row's name, else the opp's org_name."""
    org = getattr(opp, "labs_org", None) if getattr(opp, "labs_org_id", None) else None
    if is_labs_only_org_row(org) and org.name:
        return org.name
    return opp.org_name or "Labs Synthetic"


def synthetic_program_id(opp) -> int:
    """Program id for a synthetic opp: its registered ``program_id``, else its own opp id."""
    return opp.program_id or opp.opportunity_id


def drop_labs_only_keys(org_data: dict) -> dict:
    """`org_data` without Connect organisations that claim a reserved labs-only key.

    The tree's organisations come from Connect, where anyone can name an
    organisation "Labs Synthetic Something" and so hold the slug
    `labs-synthetic-something`. Kept, that slug would make its members holders of
    the labs-only organisation with that key. Labs-only entries are added after
    this, by `labs.context._merge_labs_only_opps`, from the registry.
    """
    organizations = (org_data or {}).get("organizations")
    if not organizations:
        return org_data
    kept = [o for o in organizations if o.get("labs_only") or not _claims_reserved_key(o)]
    if len(kept) == len(organizations):
        return org_data
    logger.warning("dropped %d Connect organisation(s) claiming a labs-only key", len(organizations) - len(kept))
    return {**org_data, "organizations": kept}


def _claims_reserved_key(entry: dict) -> bool:
    return is_reserved_key(entry.get("slug")) or is_reserved_key(entry.get("id"))


# --- filing an opp under an organisation ---------------------------------------

# One lock for all filing. Filing is rare (an opp's first save), and two saves
# filing the same programme or the same person's same-named organisation at once
# would otherwise each find nothing and mint two organisations.
_FILING_LOCK = zlib.crc32(b"labs.synthetic.org_tree.filing")


def take_filing_lock() -> None:
    """Serialise filing until the surrounding transaction ends. Call inside `transaction.atomic()`."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_FILING_LOCK])


def _key_taken(key: str) -> bool:
    """A key some organisation has, had, or is still named by somewhere."""
    from connect_labs.labs.models import LabsOrg
    from connect_labs.labs.synthetic.models import SyntheticOpportunity
    from connect_labs.scope_config.models import ScopeConfig

    if key.startswith(UNFILED_PREFIX):
        # Reserved for `unfiled_key`: every opp has one, filed or not.
        return True
    if LabsOrg.objects.filter(slug=key).exists() or LabsOrg.objects.filter(aliases__contains=[key]).exists():
        return True
    # Settings written for an organisation that has since gone: a new one under
    # its key would inherit them.
    if ScopeConfig.objects.filter(scope_type="organization", scope_key=key).exists():
        return True
    # An opp not filed yet (made by the previous code during a deploy) still
    # belongs to its name's old bucket; `labs_only_orgs_backfill` will file it
    # under that key, so a new organisation must not take it first.
    unfiled = SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=True)
    return any(bucket_key(name) == key for name in unfiled.values_list("org_name", flat=True))


def _new_key(name: str) -> str:
    base = bucket_key(name)[:110]
    if not _key_taken(base):
        return base
    n = 2
    while _key_taken(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def _grant_admin(org, user) -> None:
    from connect_labs.marketplace.models import OrgMembership

    if user is not None:
        # Through save(), so it is stamped as granted for this organisation.
        OrgMembership.objects.get_or_create(org=org, user=user, defaults={"role": "admin"})


def _mint(name: str, created_by):
    from connect_labs.labs.models import LabsOrg

    for _attempt in range(5):
        try:
            with transaction.atomic():
                org = LabsOrg.objects.create(slug=_new_key(name), name=name or "Labs Synthetic", labs_only=True)
                _grant_admin(org, created_by)
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
    Joining a programme (1) never makes anyone an administrator. Run under
    `take_filing_lock` (SyntheticOpportunity.save does).
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    siblings = SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=False).exclude(pk=opp.pk)
    if opp.program_id:
        # A programme is named by its opps' program_id -- or, for an opp that set
        # none, by that opp's own id (synthetic_program_id).
        in_programme = Q(program_id=opp.program_id) | Q(program_id__isnull=True, opportunity_id=opp.program_id)
        sibling = siblings.filter(in_programme).select_related("labs_org").order_by("pk").first()
        if sibling is not None and is_labs_only_org_row(sibling.labs_org):
            return sibling.labs_org
    same_name = siblings.filter(org_name=opp.org_name or "", created_by_id=opp.created_by_id)
    for sibling in same_name.select_related("labs_org").order_by("pk"):
        if is_labs_only_org_row(sibling.labs_org):
            return sibling.labs_org
    return _mint(opp.org_name or "Labs Synthetic", opp.created_by)


def file_unfiled_opps() -> dict:
    """File labs-only opps that have no organisation, the way migration `synthetic/0010` did.

    For opps the previous code made during a deploy, after the migration ran.
    Each goes into its name's old bucket: the labs-only row already holding that
    key, else a new labs-only row under it. A bucket made new here is
    administered by its creator when one person made every opp in it. A key a
    non-labs-only row already holds is left alone: those opps stay unfiled,
    keyed by `unfiled_key`, for staff to sort out. Idempotent.
    """
    from connect_labs.labs.models import LabsOrg
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    report = {"filed": 0, "made": [], "left_unfiled": []}
    with transaction.atomic():
        take_filing_lock()
        buckets: dict[str, list] = {}
        for opp in SyntheticOpportunity.objects.filter(labs_only=True, labs_org__isnull=True).order_by("pk"):
            buckets.setdefault(bucket_key(opp.org_name), []).append(opp)
        for key, opps in buckets.items():
            org = LabsOrg.objects.filter(slug=key).first()
            if org is None:
                org = LabsOrg.objects.create(slug=key, name=opps[0].org_name or "Labs Synthetic", labs_only=True)
                creators = {o.created_by_id for o in opps}
                if len(creators) == 1 and None not in creators:
                    _grant_admin(org, opps[0].created_by)
                report["made"].append(key)
            elif not is_labs_only_org_row(org):
                report["left_unfiled"].extend(o.opportunity_id for o in opps)
                continue
            report["filed"] += SyntheticOpportunity.objects.filter(pk__in=[o.pk for o in opps]).update(labs_org=org)
    return report
