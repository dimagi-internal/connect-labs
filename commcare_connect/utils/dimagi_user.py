"""Utility for checking if a user is a Dimagi staff member."""


def is_dimagi_user(user) -> bool:
    """Return True if the user is a Dimagi staff member.

    TODO: Re-enable email check once OIDC_RSA_PRIVATE_KEY is configured
    on Connect prod (CI-578). The check should be:
        email = getattr(user, "email", "") or ""
        return email.endswith("@dimagi.com")
    """
    return True
