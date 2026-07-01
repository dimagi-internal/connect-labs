"""
Scale Image Validation API Client.

Validates weight readings against scale images using ML vision.
Used for KMC (Kangaroo Mother Care) to verify that user-entered weight
readings match what's shown on digital scale photos.

API Details:
- Endpoint: https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/predict
- Auth: API key in x-api-key header
- Request: {"image": "<base64>", "reading": "XXXX"}
- Response: {"match": true/false}
"""

import base64
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class ScaleValidationError(Exception):
    """Exception raised for Scale Validation API errors."""

    pass


class ScaleValidationClient:
    """
    Client for the Scale Image Validation API.

    Validates that a user-entered weight reading matches what's shown
    in a scale image using ML vision analysis.

    Usage:
        with ScaleValidationClient() as client:
            result = client.validate_reading(image_bytes, "1535")
            if result["match"]:
                print("Weight matches!")
            else:
                print("Weight MISMATCH!")
    """

    def __init__(self, api_key: str | None = None):
        """
        Initialize client.

        Args:
            api_key: API key for authentication. Defaults to settings.SCALE_VALIDATION_API_KEY
        """
        self.api_key = api_key or getattr(settings, "SCALE_VALIDATION_API_KEY", "")
        self.base_url = getattr(
            settings,
            "SCALE_VALIDATION_API_URL",
            "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev",
        ).rstrip("/")
        self._client = None

    @property
    def http_client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                },
                timeout=60.0,
            )
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

    def validate_reading(self, image_bytes: bytes, reading: str) -> dict:
        """
        Validate a scale reading against an image.

        Args:
            image_bytes: Raw image bytes (JPEG/PNG)
            reading: 4-digit weight reading string (e.g., "1535")

        Returns:
            {"match": True/False}

        Raises:
            ScaleValidationError: On API errors or rate limiting
        """
        if not self.api_key:
            raise ScaleValidationError("SCALE_VALIDATION_API_KEY not configured")

        encoded_image = base64.b64encode(image_bytes).decode("utf-8")

        logger.debug(f"Validating scale reading: {reading} (image size: {len(image_bytes)} bytes)")

        try:
            response = self.http_client.post(
                f"{self.base_url}/predict",
                json={"image": encoded_image, "reading": reading},
            )

            if response.status_code == 429:
                raise ScaleValidationError("Rate limited - service busy or starting up. Try again later.")

            response.raise_for_status()
            result = response.json()

            logger.debug(f"Scale validation result: match={result.get('match')}")
            return result

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_data = e.response.json()
                error_detail = error_data.get("details", str(error_data))
            except Exception:
                error_detail = e.response.text
            logger.error(f"Scale validation API error: {error_detail}")
            raise ScaleValidationError(f"API error: {error_detail}") from e
        except httpx.HTTPError as e:
            logger.error(f"Scale validation connection error: {e}")
            raise ScaleValidationError(f"Connection error: {e}") from e

    def validate_reading_from_base64(self, base64_image: str, reading: str) -> dict:
        """
        Validate a scale reading from a base64-encoded image string.

        Args:
            base64_image: Base64-encoded image string
            reading: 4-digit weight reading string

        Returns:
            {"match": True/False}
        """
        image_bytes = base64.b64decode(base64_image)
        return self.validate_reading(image_bytes, reading)
