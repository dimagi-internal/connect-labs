"""
CommCare HQ API Client.

Provides access to CommCare Case API v2 for fetching case data.
"""

import logging
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

logger = logging.getLogger(__name__)


class CCHQAuthError(Exception):
    """
    CommCare HQ rejected the request with an auth-related status (401/403)
    that survived a token-refresh-and-retry attempt.

    This is *distinct* from a generic HTTP error: callers should treat it
    as a signal that the user needs to re-authorize CommCare access, not
    swallow it as "no data available". Surfacing this in the UI is the
    whole reason it exists — V1/V2 used to silently return 0 forms when
    CCHQ rejected the call, leaving users to wonder why their dashboards
    were empty.
    """

    def __init__(self, message: str, *, status_code: int | None = None, domain: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.domain = domain


class CCHQHeadlessError(Exception):
    """
    Raised when CCHQ access is attempted from a headless / non-web context
    (no Django ``request``).

    CommCare HQ access in labs is gated by a per-user OAuth token kept in
    ``request.session["commcare_oauth"]``. The MCP server, management
    commands, and other request-less callers do not have a session to
    read from, so any pipeline whose data source is ``cchq_forms`` cannot
    run via those entry points today.

    Surfacing this as a typed exception (instead of a NoneType crash) lets
    callers — notably the ``pipeline_preview`` MCP tool — translate it into
    a clean, actionable error: "this pipeline uses cchq_forms; preview it
    from the web UI, or convert it to a connect_csv data source".
    """


class CommCareDataAccess:
    """
    Fetch cases from CommCare Case API v2 using session OAuth.

    Uses the CommCare OAuth token stored in request.session["commcare_oauth"].

    Constructed with ``request=None`` only as a placeholder — every call
    that touches CCHQ will then raise :class:`CCHQHeadlessError` because
    there is no OAuth token to authenticate with. This shape lets headless
    callers (MCP, management commands) instantiate the client and surface a
    clean error rather than crashing with ``'NoneType' object has no
    attribute 'session'``.
    """

    def __init__(self, request: HttpRequest | None, domain: str):
        """
        Initialize CommCare data access.

        Args:
            request: HttpRequest with commcare_oauth in session, or ``None``
                for headless callers. ``None`` is allowed only so callers can
                handle the resulting :class:`CCHQHeadlessError` cleanly —
                CCHQ-touching methods will not work.
            domain: CommCare domain to query
        """
        self.request = request
        self.domain = domain

        # Get CommCare OAuth token from session. In headless mode (request=None)
        # there is no session, so we record an empty config and let downstream
        # methods raise CCHQHeadlessError. This is preferable to crashing here
        # because some callers (e.g. fetchers that probe with verify_hq_access)
        # want to instantiate the client and *then* check.
        if request is not None:
            self.commcare_oauth = request.session.get("commcare_oauth", {})
        else:
            self.commcare_oauth = {}
        self.access_token = self.commcare_oauth.get("access_token")
        self.base_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")

        if not self.access_token and request is not None:
            logger.warning("No CommCare OAuth token found in session")

    def check_token_valid(self) -> bool:
        """
        Check if CommCare OAuth token is configured and not expired.

        If the token is expired, attempts automatic refresh using the stored
        refresh token before returning False.

        IMPORTANT — this is a *local* check. It only verifies that the
        access_token exists and `expires_at` is in the future. It does NOT
        prove CommCare HQ will accept the token. For the loud version that
        actually pings CCHQ, use verify_hq_access().

        Returns:
            True if token is valid (or was successfully refreshed), False otherwise

        Raises:
            CCHQHeadlessError: If this client was constructed without a request
                (headless / MCP context). CCHQ data sources require a web
                session OAuth token — there is no fallback today, so failing
                fast with a typed error beats returning False (which callers
                tend to translate into a misleading "no data" empty result).
        """
        if self.request is None:
            raise CCHQHeadlessError(
                "CommCare HQ access requires a web session OAuth token, "
                "but this call is running in a headless context (no request). "
                "Pipelines that use the cchq_forms data source can only be "
                "executed from the web UI today. To exercise the pipeline "
                "from MCP / scripts, switch the data source to connect_csv, "
                "or run the preview from the web."
            )
        if not self.access_token:
            return False

        # Check expiration
        expires_at = self.commcare_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            logger.info("CommCare OAuth token expired, attempting refresh...")
            if self._refresh_token():
                logger.info("Successfully refreshed CommCare OAuth token")
                return True
            logger.warning(f"CommCare OAuth token expired at {expires_at} and refresh failed")
            return False

        return True

    def verify_token_alive(self) -> bool:
        """Is the CommCare OAuth token itself valid (independent of any domain)?

        Pings ``/api/v0.5/identity/`` — a domain-less endpoint that just
        requires an authenticated user (any authenticated user). Returns
        True iff CCHQ accepts the token at all.

        This is the right check for "user needs to re-authorize CommCare HQ"
        — distinct from "user is authorized but lacks access to a specific
        domain", which is what verify_hq_access() answers.

        Returns:
            True if the token is alive; False on 401/403 (token dead) or
            network/transport error.
        """
        if not self.access_token:
            return False
        if not self.check_token_valid():
            return False

        url = f"{self.base_url}/api/v0.5/identity/"
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=15.0,
            )
        except httpx.RequestError as e:
            logger.warning(f"[CCHQ verify_token_alive] network error: {e}")
            return False

        if response.status_code in (401, 403):
            logger.warning(
                f"[CCHQ verify_token_alive] token rejected ({response.status_code}). "
                f"User needs to re-authorize CommCare HQ."
            )
            return False
        if response.status_code >= 400:
            logger.warning(f"[CCHQ verify_token_alive] unexpected {response.status_code}: {response.text[:200]}")
            return False
        return True

    def verify_hq_access(self) -> bool:
        """Verify the token has access to forms in self.domain.

        Pings ``/a/{domain}/api/form/v1/?limit=1`` — the SAME endpoint the
        actual pipeline (fetch_forms) uses. Earlier we pinged
        application/v1, but that requires app-builder permissions some LLO
        accounts don't have, producing a false-negative loop where the gate
        kept saying "no HQ access" even though pipelines would work fine.

        This method answers a domain-specific question — "can this token
        read forms in this domain". For a token-alive check independent of
        domain, see verify_token_alive().

        Returns:
            True if the token can fetch forms for this domain right now;
            False on 401/403/404 (auth/permission/missing/wrong-domain) or
            any other non-success response. Logs at WARNING level when it
            returns False so callers can surface the cause to users.
        """
        if not self.access_token:
            logger.warning("[CCHQ verify_hq_access] no access_token in session")
            return False
        if not self.check_token_valid():
            return False

        url = f"{self.base_url}/a/{self.domain}/api/form/v1/?limit=1"
        try:
            response = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=15.0,
            )
        except httpx.RequestError as e:
            logger.warning(f"[CCHQ verify_hq_access] network error pinging {self.domain}: {e}")
            return False

        if response.status_code in (401, 403):
            logger.warning(
                f"[CCHQ verify_hq_access] token rejected for domain {self.domain!r} "
                f"({response.status_code}) at form/v1. Either token is dead "
                f"(check verify_token_alive) or account lacks form-read permission."
            )
            return False
        if response.status_code == 404:
            logger.warning(f"[CCHQ verify_hq_access] domain {self.domain!r} not found (404)")
            return False
        if response.status_code >= 400:
            logger.warning(
                f"[CCHQ verify_hq_access] unexpected {response.status_code} for {self.domain}: "
                f"{response.text[:200]}"
            )
            return False
        return True

    def _refresh_token(self) -> bool:
        """
        Attempt to refresh the CommCare OAuth token using the stored refresh token.

        Updates both the instance state and the session so the new token persists.

        Returns:
            True if refresh succeeded, False otherwise
        """
        refresh_token = self.commcare_oauth.get("refresh_token")
        if not refresh_token:
            logger.debug("No refresh token available for CommCare OAuth")
            return False

        client_id = getattr(settings, "COMMCARE_OAUTH_CLIENT_ID", "")
        client_secret = getattr(settings, "COMMCARE_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            logger.warning("CommCare OAuth client credentials not configured for token refresh")
            return False

        try:
            response = httpx.post(
                f"{self.base_url}/oauth/token/",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.warning(f"CommCare token refresh failed: {response.status_code} - {response.text}")
                return False

            token_data = response.json()
            new_oauth = {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", refresh_token),
                "expires_at": timezone.now().timestamp() + token_data.get("expires_in", 3600),
                "token_type": token_data.get("token_type", "Bearer"),
            }

            # Update instance state
            self.access_token = new_oauth["access_token"]
            self.commcare_oauth = new_oauth

            # Update session so it persists across requests
            self.request.session["commcare_oauth"] = new_oauth
            if hasattr(self.request.session, "modified"):
                self.request.session.modified = True

            return True
        except Exception as e:
            logger.warning(f"CommCare token refresh error: {e}")
            return False

    def _validate_pagination_url(self, url: str) -> bool:
        """Check that a pagination URL points to the expected CommCare HQ domain."""
        parsed = urlparse(url)
        expected = urlparse(self.base_url)
        if parsed.netloc and parsed.netloc != expected.netloc:
            logger.warning(f"Unexpected domain in pagination URL: {parsed.netloc} (expected {expected.netloc})")
            return False
        return True

    def fetch_cases(
        self,
        case_type: str,
        limit: int = 1000,
        additional_params: dict | None = None,
    ) -> list[dict]:
        """
        Fetch cases from CommCare Case API v2 with pagination.

        Args:
            case_type: Case type to fetch (e.g., 'deliver-unit')
            limit: Maximum cases per page (default 1000)
            additional_params: Optional additional query parameters

        Returns:
            List of case dictionaries from CommCare API

        Raises:
            ValueError: If OAuth token is not configured or expired
            httpx.HTTPError: If API request fails
        """
        if not self.check_token_valid():
            raise ValueError(
                "CommCare OAuth not configured or expired. "
                "Please authorize CommCare access at /labs/commcare/initiate/"
            )

        endpoint = f"{self.base_url}/a/{self.domain}/api/case/v2/"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        params = {"case_type": case_type, "limit": limit}
        if additional_params:
            params.update(additional_params)

        all_cases = []
        next_url = endpoint

        logger.info(f"Fetching {case_type} cases from CommCare: {endpoint}")

        # Paginate through results
        page = 0
        try:
            while next_url:
                page += 1
                logger.info(f"Fetching page {page} from {next_url}")

                response = httpx.get(
                    next_url,
                    params=params if next_url == endpoint else None,
                    headers=headers,
                    timeout=60.0,
                )
                response.raise_for_status()

                data = response.json()
                cases = data.get("cases", [])
                all_cases.extend(cases)

                logger.info(f"Retrieved {len(cases)} cases (total so far: {len(all_cases)})")

                next_url = data.get("next")
                if next_url and not self._validate_pagination_url(next_url):
                    break
                params = None  # Don't send params for next page URLs
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP {e.response.status_code} fetching {case_type} cases from CommCare " f"(page {page}): {e}"
            )
            return all_cases
        except httpx.RequestError as e:
            logger.error(f"Request error fetching {case_type} cases from CommCare (page {page}): {e}")
            return all_cases

        logger.info(f"Fetched total of {len(all_cases)} {case_type} cases from CommCare")
        return all_cases

    def fetch_cases_by_ids(self, case_ids: list[str], batch_size: int = 100) -> list[dict]:
        """
        Fetch multiple cases by their IDs in batches using comma-separated IDs.

        Uses GET /api/case/v2/{id1},{id2},.../ to fetch many cases per request.
        Batch size is limited to ~100 to stay within URL length limits.

        Args:
            case_ids: List of case IDs to fetch
            batch_size: Number of cases per request (default 100)

        Returns:
            List of case dictionaries

        Raises:
            ValueError: If OAuth token is not configured or expired
        """
        if not self.check_token_valid():
            raise ValueError(
                "CommCare OAuth not configured or expired. "
                "Please authorize CommCare access at /labs/commcare/initiate/"
            )

        if not case_ids:
            return []

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        all_cases = []
        total = len(case_ids)

        for i in range(0, total, batch_size):
            batch = case_ids[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size
            logger.info(f"Bulk-fetching case batch {batch_num}/{total_batches} " f"({len(batch)} cases) from CommCare")

            ids_param = ",".join(batch)
            url = f"{self.base_url}/a/{self.domain}/api/case/v2/{ids_param}/"

            try:
                # Follow pagination within this batch
                while url:
                    response = httpx.get(url, headers=headers, timeout=60.0)
                    response.raise_for_status()
                    data = response.json()

                    if isinstance(data, dict):
                        cases = data.get("cases", [])
                        all_cases.extend(cases)
                        url = data.get("next")  # follow pagination
                        if url and not self._validate_pagination_url(url):
                            url = None
                    elif isinstance(data, list):
                        all_cases.extend(data)
                        url = None
                    else:
                        url = None
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning(f"Batch {batch_num}: some cases not found")
                else:
                    logger.error(f"Bulk fetch batch {batch_num} failed: {e}")
            except httpx.TimeoutException:
                logger.warning(f"Timeout on bulk fetch batch {batch_num}")

        logger.info(f"Fetched {len(all_cases)}/{total} cases from CommCare")
        return all_cases

    def fetch_forms(
        self,
        xmlns: str | None = None,
        app_id: str | None = None,
        limit: int = 1000,
        received_on_start: str | None = None,
        received_on_end: str | None = None,
    ) -> list[dict]:
        """Fetch form submissions from CCHQ Form API v1.

        Uses /a/{domain}/api/form/v1/ with optional xmlns filter.
        Paginates via meta.next URLs.
        """
        if not self.check_token_valid():
            raise ValueError(
                "CommCare OAuth not configured or expired. "
                "Please authorize CommCare access at /labs/commcare/initiate/"
            )

        endpoint = f"{self.base_url}/a/{self.domain}/api/form/v1/"
        params = {"limit": limit}
        if xmlns:
            params["xmlns"] = xmlns
        if app_id:
            params["app_id"] = app_id
        if received_on_start:
            params["received_on_start"] = received_on_start
        if received_on_end:
            params["received_on_end"] = received_on_end

        headers = {"Authorization": f"Bearer {self.access_token}"}

        all_forms = []
        next_url = endpoint
        page = 0
        retried_after_refresh = False
        try:
            while next_url:
                page += 1
                logger.info(f"Fetching forms page {page} from {next_url}")

                response = httpx.get(
                    next_url,
                    params=params if next_url == endpoint else None,
                    headers=headers,
                    timeout=60.0,
                )

                # Auth-error retry-once-after-refresh path. Some CCHQ
                # tokens come back from refresh with reduced scope or get
                # de-authorized for a domain mid-session. Catch 401/403
                # specifically, attempt a refresh once, retry. If the
                # retry still fails, raise CCHQAuthError so the caller
                # surfaces "Authorize CommCare HQ" instead of pretending
                # the result was simply empty.
                if response.status_code in (401, 403):
                    if not retried_after_refresh:
                        retried_after_refresh = True
                        logger.warning(
                            f"CCHQ form fetch got {response.status_code} on page {page}; "
                            f"attempting token refresh + single retry"
                        )
                        if self._refresh_token():
                            headers = {"Authorization": f"Bearer {self.access_token}"}
                            page -= 1  # retry same page
                            continue
                    raise CCHQAuthError(
                        f"CommCare HQ rejected form fetch with HTTP {response.status_code} "
                        f"for domain {self.domain!r} after token refresh+retry. "
                        f"User needs to re-authorize CommCare access.",
                        status_code=response.status_code,
                        domain=self.domain,
                    )

                response.raise_for_status()
                data = response.json()
                forms = data.get("objects", [])
                all_forms.extend(forms)

                logger.info(f"Retrieved {len(forms)} forms (total so far: {len(all_forms)})")

                next_url = data.get("meta", {}).get("next")
                if next_url and not next_url.startswith("http"):
                    if next_url.startswith("?"):
                        # Query-params-only relative URL — prepend full endpoint path
                        next_url = f"{endpoint}{next_url}"
                    else:
                        # Path-based relative URL (e.g., /a/domain/api/...)
                        next_url = f"{self.base_url}{next_url}"
                if next_url and not self._validate_pagination_url(next_url):
                    break
        except CCHQAuthError:
            # Re-raise — auth errors must surface, not be swallowed.
            raise
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} fetching forms from CommCare (page {page}): {e}")
            return all_forms
        except httpx.RequestError as e:
            logger.error(f"Request error fetching forms from CommCare (page {page}): {e}")
            return all_forms

        logger.info(f"Fetched total of {len(all_forms)} forms from CommCare")
        return all_forms

    def get_form_xmlns(self, app_id: str, form_name: str = "Register Mother") -> str | None:
        """Look up a form's xmlns from the Application Structure API.

        Calls GET /a/{domain}/api/application/v1/{app_id}/ and walks
        modules[] -> forms[] matching by the form's multilingual name dict.

        Args:
            app_id: CommCare application ID
            form_name: Human-readable form name to search for (matched against
                       the values of each form's ``name`` dict, e.g. ``{"en": "Register Mother"}``)

        Returns:
            The xmlns string for the matching form, or None if not found.
        """
        if not self.check_token_valid():
            logger.warning("Cannot look up form xmlns: OAuth token invalid")
            return None

        url = f"{self.base_url}/a/{self.domain}/api/application/v1/{app_id}/"
        headers = {"Authorization": f"Bearer {self.access_token}"}

        try:
            response = httpx.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Application Structure API error for app {app_id}: {e.response.status_code}")
            return None
        except httpx.TimeoutException:
            logger.warning(f"Timeout fetching application structure for app {app_id}")
            return None

        app_data = response.json()

        for module in app_data.get("modules", []):
            for form in module.get("forms", []):
                name_dict = form.get("name", {})
                # name_dict is multilingual, e.g. {"en": "Register Mother"}
                if isinstance(name_dict, dict):
                    if form_name in name_dict.values():
                        xmlns = form.get("xmlns")
                        if xmlns:
                            logger.info(f"Discovered xmlns for '{form_name}': {xmlns}")
                            return xmlns
                elif isinstance(name_dict, str) and name_dict == form_name:
                    xmlns = form.get("xmlns")
                    if xmlns:
                        logger.info(f"Discovered xmlns for '{form_name}': {xmlns}")
                        return xmlns

        logger.warning(f"Form '{form_name}' not found in app {app_id}")
        return None

    def list_applications(self) -> list[dict]:
        """List all applications in the domain via Application API v1.

        Returns list of app summary dicts (each has 'id', 'name', etc.).
        Paginates through results using meta.next.
        """
        if not self.check_token_valid():
            logger.warning("Cannot list applications: OAuth token invalid")
            return []

        endpoint = f"{self.base_url}/a/{self.domain}/api/application/v1/"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {"limit": 100}

        all_apps = []
        next_url = endpoint
        page = 0
        while next_url:
            page += 1
            logger.info(f"Fetching applications page {page} from {next_url}")

            try:
                response = httpx.get(
                    next_url,
                    params=params if next_url == endpoint else None,
                    headers=headers,
                    timeout=30.0,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                logger.warning(f"Application API error: {e.response.status_code}")
                break
            except httpx.TimeoutException:
                logger.warning("Timeout fetching applications list")
                break

            data = response.json()
            apps = data.get("objects", [])
            all_apps.extend(apps)

            logger.info(f"Retrieved {len(apps)} applications (total so far: {len(all_apps)})")

            next_url = data.get("meta", {}).get("next")
            if next_url and not next_url.startswith("http"):
                if next_url.startswith("?"):
                    next_url = f"{endpoint}{next_url}"
                else:
                    next_url = f"{self.base_url}{next_url}"
            if next_url and not self._validate_pagination_url(next_url):
                break

        logger.info(f"Listed {len(all_apps)} applications in domain {self.domain}")
        return all_apps

    def discover_form_xmlns(self, form_name: str) -> str | None:
        """Search all apps in the domain for a form by name, return its xmlns.

        Useful when the form is in a different app than the deliver app.
        Calls list_applications(), then get_form_xmlns() for each app until found.
        """
        apps = self.list_applications()
        for app in apps:
            app_id = app.get("id")
            if app_id:
                xmlns = self.get_form_xmlns(app_id, form_name)
                if xmlns:
                    logger.info(f"Discovered xmlns for '{form_name}' in app {app_id}")
                    return xmlns
        logger.warning(f"Form '{form_name}' not found in any of {len(apps)} apps in domain {self.domain}")
        return None

    def fetch_case_by_id(self, case_id: str) -> dict | None:
        """
        Fetch a single case by ID.

        Args:
            case_id: CommCare case ID

        Returns:
            Case dictionary or None if not found
        """
        if not self.check_token_valid():
            raise ValueError("CommCare OAuth not configured or expired.")

        endpoint = f"{self.base_url}/a/{self.domain}/api/case/v2/{case_id}/"

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.get(endpoint, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
