SUPPLIER = "supplier"
PARTNER = "partner"


def resolve_role(user):
    """Return the acting role for a user, or None if they have no supply access.

    A seeded staff role always wins over org membership — staff who also
    happen to own a demo org act as staff. Otherwise the role follows the
    organisation's ``kind``: a member of an implementing partner acts as a
    partner, everyone else as a supplier. Membership stays single-path, which
    is the whole reason partners are a discriminator on the org rather than a
    parallel model.
    """
    if not user.is_authenticated:
        return None
    staff = getattr(user, "supply_staff_role", None)
    if staff:
        return staff.role
    membership = getattr(user, "supply_membership", None)
    if membership:
        from .models import SupplierOrg

        if membership.org.kind == SupplierOrg.Kind.IMPLEMENTING_PARTNER:
            return PARTNER
        return SUPPLIER
    return None
