"""Who counts as Dimagi: the one list of Dimagi sign-in domains, and the checks on it."""

#: The addresses that make someone Dimagi in labs: staff (@dimagi.com), the AI
#: team (@dimagi-ai.com) and Dimagi's associates (@dimagi-associate.com). One list for
#: every rule -- staff gates, membership of the ``dimagi`` organisation, and
#: access to labs-only (synthetic) opportunities. Until 2026-10-25 associates
#: were Dimagi members but not staff, and synthetic access kept a third list of
#: its own; the owner collapsed all three into this one.
DIMAGI_DOMAINS = ("@dimagi.com", "@dimagi-ai.com", "@dimagi-associate.com")


def is_dimagi_email(email: str | None) -> bool:
    """Return True if `email` is a Dimagi address (`DIMAGI_DOMAINS`)."""
    return (email or "").strip().lower().endswith(DIMAGI_DOMAINS)


def is_dimagi_user(user) -> bool:
    """Return True if the user signs in with a Dimagi email (`DIMAGI_DOMAINS`)."""
    return is_dimagi_email(getattr(user, "email", "") if user is not None else "")


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
