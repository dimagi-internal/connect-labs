"""Utility for checking if a user is a Dimagi staff member."""


def is_dimagi_user(user) -> bool:
    """Return True if the user has a @dimagi.com email address.

    Checks the Django User's email field, which is populated from the OIDC
    userinfo endpoint during OAuth login.
    """
    email = getattr(user, "email", "") or ""
    return email.endswith("@dimagi.com")
