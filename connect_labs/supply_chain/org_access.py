"""Who may change or merge an organisation row.

`LabsOrg` is one registry for all of labs, and `SupplyDataAccess` authorises
only the programme or opportunity a call names -- an organisation call names
neither, so until this module any signed-in caller could rewrite, re-link or
merge any organisation. A merge moves every reference and membership onto the
row it keeps, which made it a way to take an organisation over (#2435 review, S1).

    labs-only (synthetic) row        Dimagi staff, or a system caller. Its admins
                                     change its Settings, never the row itself:
                                     linking it to Connect is how it would be
                                     re-keyed into someone else's organisation.
    any other row: change            staff or system; an admin of it (a membership
                                     granted for it, not one a merge moved there);
                                     or a Connect member of the organisation it
                                     is linked to.
    any other row: merge             staff or system; or an admin of BOTH rows.

"Staff" is the Django staff bit, not a Dimagi email address: labs-only data is
visible across the whole Dimagi domain, so the domain is exactly who an attacker
in this case would be.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied

from connect_labs.labs.synthetic.org_tree import is_labs_only_org_row, is_reserved_key
from connect_labs.supply_chain.identity import caller_org_ids

STAFF_ONLY = "only Dimagi staff change a labs-only (synthetic) organisation's record"
NOT_YOURS = (
    "{name} is not an organisation you administer: ask one of its admins, a member of its Connect "
    "organisation, or Dimagi staff to change it"
)
RESERVED = "keys beginning 'labs-synthetic-' are reserved for labs-only organisations, which labs files itself"


def _user(access):
    caller = getattr(access, "caller", None)
    return getattr(access, "user", None) or getattr(caller, "user", None)


def is_staff_or_system(access) -> bool:
    caller = getattr(access, "caller", None)
    if caller is not None and caller.is_system:
        return True
    return bool(getattr(_user(access), "is_staff", False))


def administers(access, org) -> bool:
    """An admin membership granted for `org` itself, not one a merge carried onto it."""
    from connect_labs.marketplace.models import OrgMembership

    user = _user(access)
    if user is None or not getattr(user, "pk", None):
        return False
    return OrgMembership.objects.filter(org=org, user=user, role="admin", granted_for_org_id=org.pk).exists()


def _connect_member(access, org) -> bool:
    if org.connect_organization_id is None:
        return False
    try:
        ids = caller_org_ids(access)
    except Exception:  # noqa: BLE001 -- unknowable membership grants nothing
        return False
    return bool(ids) and org.connect_organization_id in ids


def require_change(access, org) -> None:
    if is_staff_or_system(access):
        return
    if is_labs_only_org_row(org) or org.labs_only:
        raise PermissionDenied(STAFF_ONLY)
    if administers(access, org) or _connect_member(access, org):
        return
    raise PermissionDenied(NOT_YOURS.format(name=org.name))


def require_merge(access, keep, merge) -> None:
    if is_staff_or_system(access):
        return
    for org in (keep, merge):
        if org.labs_only:
            raise PermissionDenied(STAFF_ONLY)
        if not administers(access, org):
            raise PermissionDenied(NOT_YOURS.format(name=org.name))


def refuse_reserved_keys(data: dict) -> None:
    """No caller names a reserved key: labs-only rows are made by filing, never by hand."""
    for field in ("slug", "connect_organization_slug"):
        if is_reserved_key(data.get(field)):
            raise PermissionDenied(f"{field}: {RESERVED}")
    if any(is_reserved_key(alias) for alias in data.get("aliases") or []):
        raise PermissionDenied(f"aliases: {RESERVED}")
