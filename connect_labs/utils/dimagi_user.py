"""Who counts as Dimagi staff."""


def is_dimagi_user(user) -> bool:
    """Return True if the user signs in with a Dimagi email (@dimagi.com or @dimagi-ai.com)."""
    email = getattr(user, "email", "") or ""
    return email.endswith("@dimagi.com") or email.endswith("@dimagi-ai.com")


def is_dimagi_staff(user) -> bool:
    """Dimagi staff, for a permission decision that names them (Settings, pages).

    Django's `is_staff` (a flag set by hand, which an OAuth-created labs user never
    gets) OR a Dimagi sign-in (`is_dimagi_user`) -- the definition the rest of labs
    already gates on. Before 2026-10-12 Settings read `is_staff` alone, so a Dimagi
    person was told "Dimagi staff may change this" and then refused.
    """
    if user is None:
        return False
    return bool(getattr(user, "is_staff", False)) or is_dimagi_user(user)
