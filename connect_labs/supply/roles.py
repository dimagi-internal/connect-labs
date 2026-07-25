SUPPLIER = "supplier"


def resolve_role(user):
    """Return the acting role for a user, or None if they have no supply access.

    A seeded staff role always wins over supplier membership — staff who also
    happen to own a demo org act as staff.
    """
    if not user.is_authenticated:
        return None
    staff = getattr(user, "supply_staff_role", None)
    if staff:
        return staff.role
    if getattr(user, "supply_membership", None):
        return SUPPLIER
    return None
