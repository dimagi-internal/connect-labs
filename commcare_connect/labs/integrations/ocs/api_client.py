"""
Open Chat Studio API Client.

Provides OAuth-based access to OCS APIs for listing experiments/bots and triggering conversations.
Uses the OCS OAuth token stored in request.session["ocs_oauth"].
"""

import logging

import httpx
from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class OCSAPIError(Exception):
    """Exception raised for OCS API errors."""

    pass


class OCSDataAccess:
    """
    Access OCS APIs using session OAuth.

    Uses the OCS OAuth token stored in request.session["ocs_oauth"].
    """

    def __init__(self, request: HttpRequest):
        """
        Initialize OCS data access.

        Args:
            request: HttpRequest with ocs_oauth in session
        """
        self.request = request

        # Get OCS OAuth token from session
        self.ocs_oauth = request.session.get("ocs_oauth", {})
        self.access_token = self.ocs_oauth.get("access_token")
        self.base_url = getattr(settings, "OCS_URL", "https://www.openchatstudio.com").rstrip("/")

        self._client = None

    @property
    def http_client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            self._client = httpx.Client(headers=headers, timeout=30.0)
        return self._client

    def close(self):
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close client."""
        self.close()

    def check_token_valid(self) -> bool:
        """
        Check if OCS OAuth token is configured and not expired.

        If the access token has expired and a refresh_token is available,
        attempts a silent refresh before reporting invalid. Mirrors the
        CCHQ client pattern.

        Returns:
            True if a usable token is in session (after refresh if needed),
            False otherwise.
        """
        from django.utils import timezone

        if not self.access_token:
            return False

        expires_at = self.ocs_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            logger.info("OCS OAuth token expired, attempting refresh...")
            if self._refresh_token():
                logger.info("Successfully refreshed OCS OAuth token")
                return True
            logger.warning(f"OCS OAuth token expired at {expires_at} and refresh failed")
            return False

        return True

    def _refresh_token(self) -> bool:
        """
        Attempt to refresh the OCS OAuth token using the stored refresh token.

        Updates both the instance state and the session so the new token persists.

        Returns:
            True if refresh succeeded, False otherwise
        """
        refresh_token = self.ocs_oauth.get("refresh_token")
        if not refresh_token:
            logger.debug("No refresh token available for OCS OAuth")
            return False

        client_id = getattr(settings, "OCS_OAUTH_CLIENT_ID", "")
        client_secret = getattr(settings, "OCS_OAUTH_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            logger.warning("OCS OAuth client credentials not configured for token refresh")
            return False

        try:
            from django.utils import timezone

            response = httpx.post(
                f"{self.base_url}/o/token/",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                logger.warning(f"OCS token refresh failed: {response.status_code} - {response.text}")
                return False

            token_data = response.json()
            new_oauth = {
                "access_token": token_data["access_token"],
                "refresh_token": token_data.get("refresh_token", refresh_token),
                "expires_at": timezone.now().timestamp() + token_data.get("expires_in", 3600),
                "token_type": token_data.get("token_type", "Bearer"),
                "scope": token_data.get("scope", self.ocs_oauth.get("scope", "")),
            }

            self.access_token = new_oauth["access_token"]
            self.ocs_oauth = new_oauth

            self.request.session["ocs_oauth"] = new_oauth
            if hasattr(self.request.session, "modified"):
                self.request.session.modified = True

            # Invalidate cached HTTP client so next call uses the new token
            if self._client is not None:
                self._client.close()
                self._client = None

            return True
        except Exception as e:
            logger.warning(f"OCS token refresh error: {e}")
            return False

    def list_experiments(self) -> list[dict]:
        """
        List all experiments (chatbots) available to the authenticated user.

        Returns:
            List of experiment dictionaries with id, name, version_number, versions

        Raises:
            OCSAPIError: If token is invalid or API request fails
        """
        if not self.check_token_valid():
            raise OCSAPIError(
                "OCS OAuth not configured or expired. " "Please authorize OCS access at /labs/ocs/initiate/"
            )

        url = f"{self.base_url}/api/experiments/"
        all_experiments = []

        logger.info(f"Fetching experiments from OCS: {url}")

        # Paginate through results
        page = 0
        while url:
            page += 1
            logger.debug(f"Fetching page {page} from {url}")

            response = self.http_client.get(url)
            response.raise_for_status()

            data = response.json()

            # Handle paginated response
            if isinstance(data, dict):
                experiments = data.get("results", [])
                url = data.get("next")
            else:
                # Non-paginated response
                experiments = data
                url = None

            all_experiments.extend(experiments)
            logger.debug(f"Retrieved {len(experiments)} experiments (total so far: {len(all_experiments)})")

        logger.info(f"Fetched total of {len(all_experiments)} experiments from OCS")
        return all_experiments

    def get_experiment(self, experiment_id: str) -> dict | None:
        """
        Get a single experiment by ID.

        Args:
            experiment_id: Experiment public ID (UUID)

        Returns:
            Experiment dictionary or None if not found
        """
        if not self.check_token_valid():
            raise OCSAPIError("OCS OAuth not configured or expired.")

        url = f"{self.base_url}/api/experiments/{experiment_id}/"

        try:
            response = self.http_client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise OCSAPIError(f"Failed to fetch experiment: {e}") from e

    def trigger_bot(
        self,
        identifier: str,
        platform: str,
        experiment_id: str,
        prompt_text: str,
        start_new_session: bool = False,
        session_data: dict | None = None,
        participant_data: dict | None = None,
    ) -> dict:
        """
        Trigger an OCS bot conversation using OAuth.

        Args:
            identifier: Unique identifier for the participant
            platform: Channel platform (e.g., 'commcare_connect', 'whatsapp')
            experiment_id: OCS Experiment ID (UUID)
            prompt_text: Instructions for the bot
            start_new_session: Whether to start a new session
            session_data: Custom data to store with the session
            participant_data: Custom data to store with the participant

        Returns:
            Response data from OCS

        Raises:
            OCSAPIError: If token is invalid or API request fails
        """
        if not self.check_token_valid():
            raise OCSAPIError("OCS OAuth not configured or expired.")

        url = f"{self.base_url}/api/trigger_bot"

        payload = {
            "identifier": identifier,
            "platform": platform,
            "experiment": experiment_id,
            "prompt_text": prompt_text,
            "start_new_session": start_new_session,
        }

        if session_data:
            payload["session_data"] = session_data

        if participant_data:
            payload["participant_data"] = participant_data

        try:
            response = self.http_client.post(url, json=payload)
            response.raise_for_status()

            try:
                return response.json() if response.text else {}
            except ValueError:
                return {}

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_detail = e.response.json()
            except ValueError:
                error_detail = e.response.text
            raise OCSAPIError(f"Failed to trigger bot: {error_detail}") from e
        except httpx.HTTPError as e:
            raise OCSAPIError(f"Failed to trigger bot: {e}") from e

    def get_session(self, session_id: str) -> dict | None:
        """
        Get session details including messages.

        Args:
            session_id: OCS session external ID (UUID)

        Returns:
            Session dictionary with messages or None if not found
        """
        if not self.check_token_valid():
            raise OCSAPIError("OCS OAuth not configured or expired.")

        url = f"{self.base_url}/api/sessions/{session_id}/"

        try:
            response = self.http_client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise OCSAPIError(f"Failed to fetch session: {e}") from e

    def list_sessions(
        self,
        experiment_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        List sessions, optionally filtered by experiment.

        Args:
            experiment_id: Optional experiment ID to filter by
            limit: Maximum number of sessions to return

        Returns:
            List of session dictionaries
        """
        if not self.check_token_valid():
            raise OCSAPIError("OCS OAuth not configured or expired.")

        url = f"{self.base_url}/api/sessions/"
        params = {
            "ordering": "-created_at",
            "page_size": limit,
        }

        if experiment_id:
            params["experiment"] = experiment_id

        try:
            response = self.http_client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except httpx.HTTPError as e:
            raise OCSAPIError(f"Failed to fetch sessions: {e}") from e
