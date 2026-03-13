"""
OAuth CLI Client for CommCare Connect.

Implements the OAuth Authorization Code flow with PKCE for CLI tools.
This allows scripts to authenticate users via browser and obtain access tokens.

Usage:
    from commcare_connect.labs.integrations.connect.cli import get_oauth_token

    token = get_oauth_token(
        client_id="your_client_id",
        production_url="https://production.com"
    )
"""

import base64
import hashlib
import secrets
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures OAuth callback with authorization code."""

    received_code = None
    received_error = None

    def do_GET(self):
        """Handle GET request from OAuth provider redirect."""
        query = parse_qs(urlparse(self.path).query)

        # Capture authorization code or error
        OAuthCallbackHandler.received_code = query.get("code", [None])[0]
        OAuthCallbackHandler.received_error = query.get("error", [None])[0]

        # Send response to browser
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        if OAuthCallbackHandler.received_code:
            html = """
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #28a745;">[SUCCESS] Authorization Successful!</h1>
                    <p>You can close this window and return to your terminal.</p>
                    <script>setTimeout(() => window.close(), 2000);</script>
                </body></html>
            """
        else:
            error_msg = OAuthCallbackHandler.received_error or "Unknown error"
            html = f"""
                <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #dc3545;">[ERROR] Authorization Failed</h1>
                    <p>Error: {error_msg}</p>
                    <p>Please check the terminal for details.</p>
                </body></html>
            """

        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        """Suppress HTTP server logs."""
        pass


def is_port_available(port):
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", port))
            return True
    except OSError:
        return False


def generate_pkce_pair():
    """Generate PKCE code verifier and challenge for secure OAuth flow."""
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8").rstrip("=")
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("utf-8")).digest()).decode("utf-8").rstrip("=")
    )
    return code_verifier, code_challenge


def get_oauth_token(
    client_id: str,
    production_url: str,
    client_secret: str | None = None,
    port: int = 8765,
    callback_path: str = "/callback",
    scope: str = "export",
    verbose: bool = True,
    authorize_path: str = "/o/authorize/",
    token_path: str = "/o/token/",
) -> dict | None:
    """
    Obtain an OAuth access token via browser-based authorization.

    This implements the OAuth Authorization Code flow with PKCE. It:
    1. Starts a local HTTP server to receive the callback
    2. Opens the user's browser to the authorization page
    3. Waits for the user to authorize
    4. Exchanges the authorization code for an access token

    Args:
        client_id: OAuth client ID
        production_url: Base URL of the OAuth provider
        client_secret: OAuth client secret (optional, not needed for public clients with PKCE)
        port: Local port for OAuth callback (default: 8765)
        callback_path: Path for OAuth callback (default: "/callback")
        scope: OAuth scopes to request (default: "export")
        verbose: Print status messages (default: True)
        authorize_path: OAuth authorization endpoint path (default: "/o/authorize/")
        token_path: OAuth token endpoint path (default: "/o/token/")

    Returns:
        Dict with token data including 'access_token', 'token_type', 'expires_in', etc.
        Returns None if authorization fails.

    Example:
        >>> token_data = get_oauth_token(
        ...     client_id="abc123",
        ...     production_url="https://connect.dimagi.com"
        ... )
        >>> access_token = token_data['access_token']
    """
    redirect_uri = f"http://localhost:{port}{callback_path}"

    # Check if port is available
    if not is_port_available(port):
        if verbose:
            print(f"Error: Port {port} is already in use.")
            print("Please close the application using it or choose a different port.")
        return None

    # Generate PKCE values for security
    code_verifier, code_challenge = generate_pkce_pair()

    # Build authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{production_url}{authorize_path}?{urlencode(auth_params)}"

    if verbose:
        print("\n" + "=" * 70)
        print("OAuth Authorization Flow")
        print("=" * 70)
        print("\nAuthorization URL:")
        print(auth_url)
        print("\nPlease authorize the application in your browser.")
        print("Waiting for authorization...")

    # Open browser for user authorization
    webbrowser.open(auth_url)

    # Start local server and wait for callback
    server = HTTPServer(("localhost", port), OAuthCallbackHandler)
    server.handle_request()

    # Check if we received an authorization code
    if OAuthCallbackHandler.received_error:
        if verbose:
            print(f"\n[ERROR] Authorization failed: {OAuthCallbackHandler.received_error}")
        return None

    if not OAuthCallbackHandler.received_code:
        if verbose:
            print("\n[ERROR] No authorization code received")
        return None

    if verbose:
        print("\n[OK] Authorization code received")
        print("Exchanging code for access token...")

    # Exchange authorization code for access token
    token_data = {
        "grant_type": "authorization_code",
        "code": OAuthCallbackHandler.received_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }

    # Include client secret if provided (for confidential clients)
    if client_secret:
        token_data["client_secret"] = client_secret

    try:
        response = httpx.post(
            f"{production_url}{token_path}",
            data=token_data,
            timeout=10,
        )
        response.raise_for_status()
        token_response = response.json()

        if verbose:
            print("\n[OK] Successfully obtained OAuth token!")
            print("=" * 70)
            print(f"\nAccess Token: {token_response['access_token'][:20]}...")
            print(f"Token Type: {token_response.get('token_type', 'Bearer')}")
            print(f"Expires In: {token_response.get('expires_in', 'Unknown')} seconds")
            if token_response.get("refresh_token"):
                print("Refresh Token: Available")
            print()

        return token_response

    except httpx.HTTPStatusError as e:
        if verbose:
            print(f"\n[ERROR] Token exchange failed: {e.response.status_code}")
            print(f"Response: {e.response.text}")
        return None
    except Exception as e:
        if verbose:
            print(f"\n[ERROR] Error exchanging token: {str(e)}")
        return None


def get_labs_user_from_token(
    token_manager=None,
    client_id: str | None = None,
    client_secret: str | None = None,
    production_url: str | None = None,
):
    """
    Create a Django User instance by introspecting saved CLI token at runtime.

    This is the recommended way for CLI scripts to get an authenticated User object.
    It loads the token saved by `python manage.py get_cli_token` and
    introspects it to get fresh user profile data.

    Args:
        token_manager: Optional TokenManager instance (defaults to new TokenManager())
        client_id: OAuth client ID for introspection (defaults to settings.CONNECT_OAUTH_CLIENT_ID)
        client_secret: OAuth client secret for introspection (defaults to settings.CONNECT_OAUTH_CLIENT_SECRET)
        production_url: Production URL (defaults to settings.CONNECT_PRODUCTION_URL)

    Returns:
        User instance or None if token invalid/expired or introspection fails

    Example:
        >>> from commcare_connect.labs.integrations.connect.cli import get_labs_user_from_token
        >>> user = get_labs_user_from_token()
        >>> if user:
        >>>     print(f"Authenticated as: {user.username}")
    """
    from django.conf import settings

    from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager
    from commcare_connect.labs.integrations.connect.oauth import introspect_token
    from commcare_connect.users.models import User

    # Load token
    if token_manager is None:
        token_manager = TokenManager()

    access_token = token_manager.get_valid_token()
    if not access_token:
        return None

    # Get OAuth credentials from settings if not provided
    # Note: We use the WEB OAuth credentials for introspection (confidential client)
    # because the CLI app is public and cannot introspect tokens
    if client_id is None:
        client_id = getattr(settings, "CONNECT_OAUTH_CLIENT_ID", None)
    if client_secret is None:
        client_secret = getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", None)
    if production_url is None:
        production_url = getattr(settings, "CONNECT_PRODUCTION_URL", None)

    if not client_id or not client_secret or not production_url:
        return None

    # Introspect token at runtime to get fresh user profile
    user_profile = introspect_token(
        access_token=access_token,
        client_id=client_id,
        client_secret=client_secret,
        production_url=production_url,
    )

    if not user_profile:
        return None

    # Create Django User from profile data
    user, _ = User.objects.update_or_create(
        username=user_profile.get("username"),
        defaults={
            "email": user_profile.get("email", ""),
            "name": f"{user_profile.get('first_name', '')} {user_profile.get('last_name', '')}".strip(),
        },
    )
    return user


def create_cli_request(
    opportunity_id: int | None = None,
    program_id: int | None = None,
    organization_id: str | None = None,
    url_path: str = "/",
    include_commcare: bool = True,
):
    """
    Create a mock Django request with full labs context for CLI usage.

    This is the recommended way for CLI scripts to get a request object that
    mirrors what the web app would create. It:
    1. Loads the OAuth token from the CLI token cache
    2. Introspects the token to get user profile
    3. Fetches organization data to populate labs_context with full objects
    4. Creates a mock request with properly populated session and labs_context
    5. Optionally loads CommCare HQ OAuth token (from ``get_commcare_token``)

    Args:
        opportunity_id: Optional opportunity ID to include in context
        program_id: Optional program ID to include in context
        organization_id: Optional organization slug to include in context
        url_path: URL path for the request (default: "/")
        include_commcare: Load CommCare HQ token into session if available (default: True)

    Returns:
        Mock HttpRequest with labs_oauth session and labs_context, or None if auth fails

    Example:
        >>> from commcare_connect.labs.integrations.connect.cli import create_cli_request
        >>> request = create_cli_request(opportunity_id=814)
        >>> if request:
        >>>     # Use request with analysis framework
        >>>     result = compute_flw_analysis(request, config)
    """
    from django.conf import settings
    from django.test import RequestFactory

    from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager
    from commcare_connect.labs.integrations.connect.oauth import fetch_user_organization_data, introspect_token
    from commcare_connect.users.models import User

    # Load token
    token_manager = TokenManager()
    access_token = token_manager.get_valid_token()
    if not access_token:
        return None

    # Get OAuth credentials from settings
    client_id = getattr(settings, "CONNECT_OAUTH_CLIENT_ID", None)
    client_secret = getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", None)
    production_url = getattr(settings, "CONNECT_PRODUCTION_URL", None)

    if not client_id or not client_secret or not production_url:
        return None

    # Introspect token to get user profile
    user_profile = introspect_token(
        access_token=access_token,
        client_id=client_id,
        client_secret=client_secret,
        production_url=production_url,
    )

    if not user_profile:
        return None

    # Fetch organization data (includes opportunities with visit_count)
    org_data = fetch_user_organization_data(access_token)
    if not org_data:
        org_data = {}

    # Create Django User from profile data
    first_name = user_profile.get("first_name", "")
    last_name = user_profile.get("last_name", "")
    name = f"{first_name} {last_name}".strip() or user_profile.get("username", "")
    user, _ = User.objects.update_or_create(
        username=user_profile.get("username"),
        defaults={
            "email": user_profile.get("email", ""),
            "name": name,
        },
    )

    # Build labs_context similar to what LabsContextMiddleware does
    labs_context = {}

    # Find and validate opportunity
    if opportunity_id:
        opportunities = org_data.get("opportunities", [])
        for opp in opportunities:
            if opp.get("id") == opportunity_id:
                labs_context["opportunity_id"] = opportunity_id
                labs_context["opportunity"] = opp  # Full object with visit_count
                labs_context["opportunity_name"] = opp.get("name", f"Opportunity {opportunity_id}")
                break
        else:
            # Not found in user's data, still include ID for API validation
            labs_context["opportunity_id"] = opportunity_id
            labs_context["opportunity_name"] = f"Opportunity {opportunity_id}"

    # Find and validate program
    if program_id:
        programs = org_data.get("programs", [])
        for prog in programs:
            if prog.get("id") == program_id:
                labs_context["program_id"] = program_id
                labs_context["program"] = prog
                break
        else:
            labs_context["program_id"] = program_id

    # Find and validate organization
    if organization_id:
        organizations = org_data.get("organizations", [])
        for org in organizations:
            if org.get("slug") == organization_id:
                labs_context["organization_id"] = org.get("id")
                labs_context["organization_slug"] = organization_id
                labs_context["organization"] = org
                break
        else:
            labs_context["organization_id"] = organization_id

    # Create mock request
    factory = RequestFactory()
    request = factory.get(url_path)
    request.user = user
    request.session = {
        "labs_oauth": {
            "access_token": access_token,
            "token_type": "Bearer",
            "user_profile": user_profile,
            "organization_data": org_data,
        }
    }
    request.labs_context = labs_context

    # Optionally load CommCare HQ OAuth token
    if include_commcare:
        _inject_commcare_token(request)

    return request


def _inject_commcare_token(request):
    """Load CommCare HQ CLI token into request session if available.

    Reads the token saved by ``python manage.py get_commcare_token``
    (stored at ``~/.commcare-connect/commcare_token.json``) and injects
    it into ``request.session["commcare_oauth"]`` so that
    ``CommCareDataAccess`` can use it transparently.
    """
    from pathlib import Path

    from commcare_connect.labs.integrations.connect.cli.token_manager import TokenManager

    token_file = Path.home() / ".commcare-connect" / "commcare_token.json"
    tm = TokenManager(str(token_file))
    token_data = tm.load_token()

    if not token_data or not tm.get_valid_token():
        return

    # Build the session dict that CommCareDataAccess expects
    expires_at = 0
    if "expires_at" in token_data:
        from datetime import datetime

        try:
            expires_at = datetime.fromisoformat(token_data["expires_at"]).timestamp()
        except (ValueError, TypeError):
            pass

    request.session["commcare_oauth"] = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token"),
        "expires_at": expires_at,
        "token_type": token_data.get("token_type", "Bearer"),
    }
