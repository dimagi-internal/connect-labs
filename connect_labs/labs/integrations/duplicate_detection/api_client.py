"""
Duplicate Detection API client.

Detects images sharing duplicated visual features (e.g. the same article of
clothing reappearing across supposedly-different visits) across a batch of
world-readable image URLs. Deployed behind the same Connect Scale
verification service gateway as scale_validation/muac_overzoom (same API key
and host) -- see connect_labs.labs.integrations.scale_validation.api_client.

API Details:
- Endpoint: https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/detect_duplicates
- Auth: API key in x-api-key header (same key as scale validation service)
- Request: {"images": [{"id": "<blob_id>", "url": "<world-readable url>"}, ...]}
- Response: {"groups": [[id, id, ...], ...]} -- ids NOT in any group had no
  detected duplication; an id can appear in more than one group.
"""

import httpx
from django.conf import settings


class DuplicateDetectionError(Exception):
    """Exception raised for Duplicate Detection API errors."""

    pass


class DuplicateDetectionClient:
    """Client for the Duplicate Detection API.

    Usage:
        with DuplicateDetectionClient() as client:
            result = client.detect_duplicates([{"id": "blob1", "url": "https://..."}])
            for group in result["groups"]:
                print("Duplicate set:", group)
    """

    DEFAULT_API_URL = "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev"
    DEFAULT_TIMEOUT = 60.0

    def __init__(self):
        self._client: httpx.Client | None = None

    @property
    def api_key(self) -> str:
        """Get API key from settings -- shared with scale validation service."""
        return getattr(settings, "SCALE_VALIDATION_API_KEY", "")

    @property
    def api_url(self) -> str:
        return getattr(settings, "SCALE_VALIDATION_API_URL", self.DEFAULT_API_URL).rstrip("/")

    @property
    def http_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers={"Content-Type": "application/json", "x-api-key": self.api_key},
                timeout=self.DEFAULT_TIMEOUT,
            )
        return self._client

    def close(self):
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def detect_duplicates(self, images: list[dict]) -> dict:
        """
        Check a batch of images for duplicated visual features.

        Args:
            images: [{"id": str, "url": str}, ...] -- world-readable image URLs.

        Returns:
            {"groups": [[id, id, ...], ...]}

        Raises:
            DuplicateDetectionError: On missing config, API errors, or rate limiting.
        """
        if not images:
            return {"groups": []}

        if not self.api_key:
            raise DuplicateDetectionError("SCALE_VALIDATION_API_KEY not configured")

        try:
            response = self.http_client.post(f"{self.api_url}/detect_duplicates", json={"images": images})

            if response.status_code == 429:
                raise DuplicateDetectionError("Rate limited - service busy or starting up. Try again later.")

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_data = e.response.json()
                error_detail = error_data.get("details", str(error_data))
            except Exception:
                error_detail = e.response.text
            raise DuplicateDetectionError(f"API error: {error_detail}") from e
        except httpx.HTTPError as e:
            raise DuplicateDetectionError(f"Connection error: {e}") from e
