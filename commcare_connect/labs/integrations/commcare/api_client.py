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


class CommCareDataAccess:
    """
    Fetch cases from CommCare Case API v2 using session OAuth.

    Uses the CommCare OAuth token stored in request.session["commcare_oauth"].
    """

    def __init__(self, request: HttpRequest, domain: str):
        """
        Initialize CommCare data access.

        Args:
            request: HttpRequest with commcare_oauth in session
            domain: CommCare domain to query
        """
        self.request = request
        self.domain = domain

        # Get CommCare OAuth token from session
        self.commcare_oauth = request.session.get("commcare_oauth", {})
        self.access_token = self.commcare_oauth.get("access_token")
        self.base_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")

        if not self.access_token:
            logger.warning("No CommCare OAuth token found in session")

    def check_token_valid(self) -> bool:
        """
        Check if CommCare OAuth token is configured and not expired.

        If the token is expired, attempts automatic refresh using the stored
        refresh token before returning False.

        Returns:
            True if token is valid (or was successfully refreshed), False otherwise
        """
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
