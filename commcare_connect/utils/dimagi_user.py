"""Utility for checking if a user is a Dimagi staff member."""


def is_dimagi_user(user) -> bool:
    """Return True if the user is a Dimagi staff member."""
    email = getattr(user, "email", "") or ""
    return email.endswith("@dimagi.com")
