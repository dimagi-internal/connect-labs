"""Standard MCP authorization (OAuth 2.1) for the labs MCP server.

The MCP spec defines how any client signs a user in to a remote server, and
nothing here is specific to one client:

  1. The server answers an unauthenticated request with a 401 whose
     ``WWW-Authenticate`` names its protected-resource metadata (RFC 9728).
  2. That document names the authorization server; the client reads the
     authorization server's metadata (RFC 8414).
  3. The client registers itself (RFC 7591 dynamic client registration).
  4. The client runs an authorization-code flow with PKCE in the user's
     browser and gets a token.

So a user adds ``https://labs.connect.dimagi.com/mcp/`` to their client and
signs in. That is the whole setup.

Labs already runs an OAuth 2 authorization server (django-oauth-toolkit,
mounted at ``/o/``). Its login is the labs login, which signs the user in to
Connect and stores their Connect token -- the token every tool needs -- so a
user who signs in here can use every tool straight away. This module adds only
what the MCP flow needs on top of it:

  * the two discovery documents (served at the host root from ``config/asgi.py``),
  * dynamic client registration (``register_client``, at ``/o/register/``),
  * scope confinement: a client registered here can only obtain the ``mcp``
    scope, and no other application can obtain it. An MCP sign-in therefore
    never mints a token for labs' other OAuth APIs, and a token minted for
    those APIs never calls an MCP tool,
  * loopback redirects on any port for native clients (RFC 8252 section 7.3):
    a CLI listens on whatever port is free when it signs in,
  * ``resolve_mcp_access_token``, which the MCP token verifier calls.

Personal Access Tokens are unchanged and keep working alongside this, for
scripts and headless agents: the verifier tries a PAT first, then an OAuth
access token.
"""

from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from oauth2_provider.contrib.rest_framework import OAuth2Authentication
from oauth2_provider.models import get_access_token_model, get_application_model
from oauth2_provider.oauth2_validators import OAuth2Validator
from oauth2_provider.scopes import SettingsScopes
from oauth2_provider.views.base import AuthorizationView

from .models import MCPOAuthClient

logger = logging.getLogger(__name__)

#: The one scope an MCP client can hold. It means "use the labs MCP tools as me".
MCP_SCOPE = "mcp"

REGISTRATION_PATH = "/o/register/"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_MAX_REDIRECT_URIS = 10
_MAX_REDIRECT_URI_LENGTH = 512
_MAX_CLIENT_NAME = 100
_SUPPORTED_GRANTS = frozenset({"authorization_code", "refresh_token"})
#: Registration is open, as the MCP spec expects, so cap it per caller. A real
#: client registers once per user per machine; anything near this is abuse.
_REGISTRATIONS_PER_HOUR_PER_IP = 20


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------


def public_base_url() -> str:
    """The public origin clients reach labs at, or "" when this instance has none.

    A setting, not the request's host: labs runs behind a load balancer, so the
    scheme and host a request arrives with are not the ones a client used, and
    the metadata must name the URL the client actually connected to.
    """
    return (settings.LABS_PUBLIC_URL or "").rstrip("/")


def sign_in_configured() -> bool:
    """Whether this instance offers the MCP sign-in at all.

    An instance with no public origin set cannot describe itself to a client:
    every endpoint in the discovery documents is absolute. Rather than publish
    another instance's URLs, such an instance offers no sign-in and stays
    PAT-only, which is what labs was before this existed.
    """
    return bool(public_base_url())


def resource_url() -> str:
    """The MCP endpoint, exactly as users enter it (RFC 9728 ``resource``)."""
    return f"{public_base_url()}/mcp/"


def protected_resource_metadata_url() -> str:
    return f"{public_base_url()}/.well-known/oauth-protected-resource/mcp"


def protected_resource_metadata() -> dict:
    """RFC 9728 protected-resource metadata for the MCP endpoint."""
    base = public_base_url()
    return {
        "resource": resource_url(),
        "authorization_servers": [base],
        "scopes_supported": [MCP_SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Connect Labs",
        "resource_documentation": f"{base}/labs/mcp/tokens/",
    }


def authorization_server_metadata() -> dict:
    """RFC 8414 authorization-server metadata for labs' OAuth server."""
    base = public_base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/o/authorize/",
        "token_endpoint": f"{base}/o/token/",
        "registration_endpoint": f"{base}{REGISTRATION_PATH}",
        "revocation_endpoint": f"{base}/o/revoke_token/",
        "response_types_supported": ["code"],
        "grant_types_supported": sorted(_SUPPORTED_GRANTS),
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [MCP_SCOPE],
    }


# ---------------------------------------------------------------------------
# Which applications are MCP clients
# ---------------------------------------------------------------------------


def is_mcp_client(application) -> bool:
    """True for an application registered through ``register_client``."""
    if application is None:
        return False
    try:
        application.mcp_client
    except (ObjectDoesNotExist, AttributeError):
        # AttributeError: a caller handing us a client_id string rather than an
        # Application. The toolkit does not today, but this is a security gate
        # and it must fail closed rather than raise.
        return False
    return True


# ---------------------------------------------------------------------------
# Redirect URIs
# ---------------------------------------------------------------------------


def _is_loopback_http(parsed) -> bool:
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS


def redirect_uri_acceptable(uri: str) -> bool:
    """https anywhere, or http on a loopback address. Nothing else.

    Plain http to a remote host would send the authorization code across the
    network in the clear, and schemes like ``javascript:`` are not redirects.
    """
    # Whitespace first: redirect URIs are stored space-separated and the toolkit
    # re-splits them, so one registered string containing a space would smuggle
    # in a second URI that this function never saw.
    if any(character.isspace() for character in uri):
        return False
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment or not parsed.hostname:
        return False
    return parsed.scheme == "https" or _is_loopback_http(parsed)


def _loopback_match_any_port(requested: str, registered: list[str]) -> bool:
    """RFC 8252 section 7.3: a loopback redirect matches on everything but the port.

    django-oauth-toolkit already relaxes the port for ``127.0.0.1`` and ``::1``
    but not for ``localhost``, and native MCP clients commonly register
    ``http://localhost:<port>/callback`` with whatever port was free at the time.
    """
    try:
        wanted = urlparse(requested)
    except ValueError:
        return False
    if not _is_loopback_http(wanted):
        return False
    for uri in registered:
        try:
            allowed = urlparse(uri)
        except ValueError:
            continue
        if (
            _is_loopback_http(allowed)
            and allowed.hostname == wanted.hostname
            and allowed.path == wanted.path
            and allowed.query == wanted.query
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# django-oauth-toolkit hooks (wired in settings.OAUTH2_PROVIDER)
# ---------------------------------------------------------------------------


class MCPScopes(SettingsScopes):
    """Confine the ``mcp`` scope to MCP clients, and MCP clients to it."""

    def get_available_scopes(self, application=None, request=None, *args, **kwargs):
        if is_mcp_client(application):
            return [MCP_SCOPE]
        scopes = super().get_available_scopes(application, request, *args, **kwargs)
        return [s for s in scopes if s != MCP_SCOPE]

    def get_default_scopes(self, application=None, request=None, *args, **kwargs):
        if is_mcp_client(application):
            return [MCP_SCOPE]
        scopes = super().get_default_scopes(application, request, *args, **kwargs)
        return [s for s in scopes if s != MCP_SCOPE]


class MCPAwareOAuth2Authentication(OAuth2Authentication):
    """labs' REST API, minus MCP tokens.

    django-oauth-toolkit authenticates a REST request with
    ``verify_request(scopes=[])``, and ``allow_scopes([])`` is always True, so no
    scope is checked anywhere on the API (nothing in this repo uses
    ``TokenHasScope``). An MCP access token would therefore authenticate on
    labs' own API, which is not what the user approved on the consent screen:
    they approved MCP tools. Refuse it here -- the mirror of
    ``resolve_mcp_access_token`` refusing an API token at the MCP endpoint, so
    the two token families stay separate in BOTH directions rather than only in
    the one the tests happened to cover.
    """

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        _user, token = result
        if is_mcp_client(getattr(token, "application", None)):
            return None
        return result


class MCPAuthorizationView(AuthorizationView):
    """The toolkit's consent screen, with two rules that apply only to MCP clients.

    Both exist because MCP clients register themselves, unauthenticated, and the
    name they register is shown to the user:

    * **Consent cannot be skipped.** ``approval_prompt=auto`` is read from the
      QUERY STRING and overrides the server's setting, so a caller that knows an
      already-approved ``client_id`` could mint a further token with no UI at
      all. For MCP clients the prompt is always forced.
    * **PKCE must be S256.** oauthlib defaults ``code_challenge_method`` to
      ``plain`` when it is absent, which the metadata does not advertise and
      which protects nothing: a ``plain`` challenge is the verifier itself.

    Everything else, including every non-MCP application, is the toolkit's
    behaviour unchanged.
    """

    def dispatch(self, request, *args, **kwargs):
        params = request.GET if request.method == "GET" else request.POST
        application = _application_from_client_id(params.get("client_id"))
        if is_mcp_client(application):
            if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
                return HttpResponseBadRequest(
                    "This server requires PKCE with code_challenge_method=S256, "
                    "as its authorization-server metadata advertises."
                )
            if request.method == "GET" and request.GET.get("approval_prompt") != "force":
                forced = request.GET.copy()
                forced["approval_prompt"] = "force"
                request.GET = forced
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        application = context.get("application")
        context["is_self_registered"] = is_mcp_client(application)
        redirect_uri = context.get("redirect_uri") or ""
        parsed = urlparse(redirect_uri)
        context["redirect_origin"] = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else redirect_uri
        return context


def _application_from_client_id(client_id):
    if not client_id:
        return None
    return get_application_model().objects.filter(client_id=client_id).first()


class MCPOAuth2Validator(OAuth2Validator):
    """The toolkit's validator, plus RFC 8252 loopback matching for MCP clients.

    Every other behaviour -- including the bearer-token introspection labs'
    own OAuth APIs rely on -- is inherited unchanged.
    """

    def validate_redirect_uri(self, client_id, redirect_uri, request, *args, **kwargs):
        if super().validate_redirect_uri(client_id, redirect_uri, request, *args, **kwargs):
            return True
        client = getattr(request, "client", None)
        return is_mcp_client(client) and _loopback_match_any_port(redirect_uri, client.redirect_uris.split())


# ---------------------------------------------------------------------------
# Dynamic client registration (RFC 7591)
# ---------------------------------------------------------------------------


def _registration_error(code: str, description: str) -> JsonResponse:
    return JsonResponse({"error": code, "error_description": description}, status=400)


def _client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _registration_rate_exceeded(request) -> bool:
    """Cap registrations per caller per hour. Fails OPEN.

    A cache outage must not stop people signing in; the cap is an abuse brake,
    not an authorization decision (the redis backend is configured with
    ``IGNORE_EXCEPTIONS``, so a read there returns None rather than raising).
    """
    key = f"mcp-dcr:{_client_ip(request)}"
    try:
        seen = cache.get(key) or 0
        cache.set(key, seen + 1, 3600)
    except Exception:  # noqa: BLE001 -- see docstring: the brake never blocks a sign-in
        logger.warning("MCP registration rate limiting unavailable", exc_info=True)
        return False
    return seen >= _REGISTRATIONS_PER_HOUR_PER_IP


@csrf_exempt
@require_POST
def register_client(request):
    """Register a public MCP client. Open, as the MCP spec expects.

    Registration grants nothing by itself: every token still needs a signed-in
    labs user to approve the client on the consent screen, and the client can
    only ever hold the ``mcp`` scope.
    """
    if _registration_rate_exceeded(request):
        return JsonResponse(
            {
                "error": "too_many_requests",
                "error_description": "Too many client registrations from this address. Try again in an hour.",
            },
            status=429,
        )

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return _registration_error("invalid_client_metadata", "The request body must be a JSON object.")
    if not isinstance(payload, dict):
        return _registration_error("invalid_client_metadata", "The request body must be a JSON object.")

    redirect_uris = payload.get("redirect_uris")
    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or len(redirect_uris) > _MAX_REDIRECT_URIS
        or not all(isinstance(uri, str) for uri in redirect_uris)
    ):
        return _registration_error(
            "invalid_redirect_uri", f"redirect_uris must be a list of 1 to {_MAX_REDIRECT_URIS} URLs."
        )
    overlong = [uri for uri in redirect_uris if len(uri) > _MAX_REDIRECT_URI_LENGTH]
    if overlong:
        return _registration_error(
            "invalid_redirect_uri", f"A redirect URI may be at most {_MAX_REDIRECT_URI_LENGTH} characters."
        )
    rejected = [uri for uri in redirect_uris if not redirect_uri_acceptable(uri)]
    if rejected:
        return _registration_error(
            "invalid_redirect_uri",
            f"Redirect URIs must be https, or http on a loopback address (localhost, 127.0.0.1, ::1): "
            f"{rejected[0]!r} is neither.",
        )

    if payload.get("token_endpoint_auth_method", "none") != "none":
        return _registration_error(
            "invalid_client_metadata",
            "Only public clients are registered here: token_endpoint_auth_method must be 'none'. "
            "The flow is protected by PKCE instead of a client secret.",
        )

    grant_types = payload.get("grant_types") or sorted(_SUPPORTED_GRANTS)
    if (
        not isinstance(grant_types, list)
        or not all(isinstance(grant, str) for grant in grant_types)
        or not set(grant_types) <= _SUPPORTED_GRANTS
        or "authorization_code" not in grant_types
    ):
        return _registration_error(
            "invalid_client_metadata", "grant_types must be authorization_code, optionally with refresh_token."
        )

    if (payload.get("response_types") or ["code"]) != ["code"]:
        return _registration_error("invalid_client_metadata", "response_types must be ['code'].")

    scope = payload.get("scope")
    if scope is not None and (not isinstance(scope, str) or set(scope.split()) - {MCP_SCOPE}):
        return _registration_error("invalid_client_metadata", f"The only scope available is '{MCP_SCOPE}'.")

    client_name = payload.get("client_name")
    name = client_name.strip() if isinstance(client_name, str) and client_name.strip() else "MCP client"
    name = name[:_MAX_CLIENT_NAME]

    Application = get_application_model()
    with transaction.atomic():
        application = Application.objects.create(
            name=name,
            client_type=Application.CLIENT_PUBLIC,
            authorization_grant_type=Application.GRANT_AUTHORIZATION_CODE,
            redirect_uris=" ".join(redirect_uris),
            skip_authorization=False,
        )
        MCPOAuthClient.objects.create(application=application)

    logger.info("Registered MCP OAuth client %s (%r)", application.client_id, name)
    return JsonResponse(
        {
            "client_id": application.client_id,
            "client_id_issued_at": int(application.created.timestamp()),
            "client_name": name,
            "redirect_uris": redirect_uris,
            "grant_types": sorted(set(grant_types)),
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": MCP_SCOPE,
        },
        status=201,
    )


# ---------------------------------------------------------------------------
# Token resolution for the MCP verifier
# ---------------------------------------------------------------------------


def resolve_mcp_access_token(raw: str):
    """Return ``(user, client_id)`` for a live MCP access token, else None.

    Live means: known, not expired, carrying the ``mcp`` scope, issued to an MCP
    client, and belonging to an active user.
    """
    if not raw:
        return None
    AccessToken = get_access_token_model()
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    try:
        token = AccessToken.objects.select_related("user", "application").get(token_checksum=checksum)
    except AccessToken.DoesNotExist:
        return None
    if token.user is None or not token.user.is_active:
        return None
    if not token.is_valid([MCP_SCOPE]):
        return None
    if not is_mcp_client(token.application):
        return None
    return token.user, token.application.client_id
