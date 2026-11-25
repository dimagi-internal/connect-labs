"""CommCare HQ: which server an opportunity's app lives on, the worker's HQ username, and an HQ token.

Connect apps live on the HQ server the opportunity names, not labs' own ``COMMCARE_HQ_URL`` (whose
default is staging). The server is the host of the app's ``install_url``; with no install_url,
``CONNECT_WEB_DEFAULT_HQ_URL`` (production, https://www.commcarehq.org).

Because the HQ token request sends the worker's PersonalID access token as the password, the host
must be https and on ``CONNECT_WEB_HQ_HOSTS`` — a bad install_url can never make labs hand a
worker's token to an arbitrary server.

Token: ``POST <hq>/oauth/token/`` (form) ``grant_type=password, client_id=CONNECT_WEB_HQ_CLIENT_ID,
scope="mobile_access sync", username=<personalid username lowercased>@<domain>.commcarehq.org,
password=<PersonalID access token>`` → ``{access_token, expires_in}`` (about 15 minutes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import http
from .errors import HQError, NotConfigured

HQ_SCOPE = "mobile_access sync"


def hq_username(personalid_username: str, domain: str) -> str:
    return f"{personalid_username.lower()}@{domain}.commcarehq.org"


def hq_base_for(install_url: str | None) -> str:
    """``https://<host>`` of the HQ server an app's install_url points at, checked against the allow-list."""
    try:
        parts = urlsplit(install_url or settings.CONNECT_WEB_DEFAULT_HQ_URL)
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except ValueError:  # a malformed URL or port
        raise HQError("The app's CommCare HQ address is not a valid URL") from None
    allowed = {h.strip().lower() for h in settings.CONNECT_WEB_HQ_HOSTS if h.strip()}
    if scheme != "https" or host not in allowed:
        raise HQError(f"CommCare HQ host {host or '(none)'!r} is not an allowed https host (CONNECT_WEB_HQ_HOSTS)")
    return f"https://{host}{f':{port}' if port else ''}"


@dataclass(frozen=True)
class HQToken:
    access_token: str = field(repr=False)
    expires_in: int


def client_id() -> str:
    value = (getattr(settings, "CONNECT_WEB_HQ_CLIENT_ID", "") or "").strip()
    if not value:
        raise NotConfigured("CONNECT_WEB_HQ_CLIENT_ID is not set, so Connect Web can't get CommCare HQ tokens.")
    return value


@sensitive_variables("personalid_access_token", "body", "response")
def request_token(hq_base: str, personalid_username: str, domain: str, personalid_access_token: str) -> HQToken:
    response = http.request(
        "POST",
        f"{hq_base.rstrip('/')}/oauth/token/",
        HQError,
        log_name="hq.token",
        data={
            "grant_type": "password",
            "client_id": client_id(),
            "scope": HQ_SCOPE,
            "username": hq_username(personalid_username, domain),
            "password": personalid_access_token,
        },
    )
    body = http.json_or_none(response) or {}
    if not isinstance(body, dict) or not body.get("access_token"):
        raise HQError("CommCare HQ issued no access token", status=response.status_code)
    return HQToken(access_token=body["access_token"], expires_in=http.seconds(body.get("expires_in")))
