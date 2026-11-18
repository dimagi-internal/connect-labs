"""Redirects whose target came from the request."""

from django.utils.http import url_has_allowed_host_and_scheme


def referer_or(request, default: str) -> str:
    """The page the request came from, when it is on this site; else ``default``.

    ``Referer`` is whatever the previous page was, including another site that
    linked or posted here, so sending a person "back" to it unchecked is an open
    redirect off labs' own origin.
    """
    referer = request.headers.get("referer") or ""
    if referer and url_has_allowed_host_and_scheme(
        referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return referer
    return default
