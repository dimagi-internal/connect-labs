"""
Scale Image Validation Agent.

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

import httpx
from django.conf import settings

from connect_labs.labs.ai_review_agents.base import AIReviewAgentError, BaseAIReviewAgent
from connect_labs.labs.ai_review_agents.registry import register
from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult


class ScaleValidationError(AIReviewAgentError):
    """Exception raised for Scale Validation API errors."""

    pass


@register
class ScaleValidationAgent(BaseAIReviewAgent):
    """
    AI Review Agent for scale image validation.

    Validates that a user-entered weight reading matches what's shown
    in a scale image using ML vision analysis.

    Required context:
        - images["scale"]: Raw image bytes (JPEG/PNG) of the scale
        - form_data["reading"]: 4-digit weight reading string (e.g., "1535")

    Example:
        agent = ScaleValidationAgent()
        context = ReviewContext(
            images={"scale": image_bytes},
            form_data={"reading": "1535"}
        )
        result = agent.review(context)
        if result.passed:
            print("Weight matches!")
    """

    agent_id = "scale_validation"
    name = "Scale Image Validation"
    description = "Validates weight readings against scale images using ML vision"
    result_actions = {
        "pass_matched": {
            "ai_result": "match",
            "human_result": "pass",
            "button_label": "Pass all Matched",
        },
        "fail_unmatched": {
            "ai_result": "no_match",
            "human_result": "fail",
            "button_label": "Fail all Unmatched",
        },
    }
    config_fields = [
        {
            "key": "comparison_field",
            "label": "Manual Scale Value",
            "type": "form_field",
            "required": True,
            "help": "Form field whose value is compared against the scale photo",
        }
    ]

    DEFAULT_API_URL = "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev"
    DEFAULT_TIMEOUT = 60.0

    def __init__(self):
        super().__init__()
        self._client: httpx.Client | None = None

    @property
    def api_key(self) -> str:
        """Get API key from settings."""
        return getattr(settings, "SCALE_VALIDATION_API_KEY", "")

    @property
    def api_url(self) -> str:
        """Get API URL from settings."""
        return getattr(settings, "SCALE_VALIDATION_API_URL", self.DEFAULT_API_URL).rstrip("/")

    @property
    def http_client(self) -> httpx.Client:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.Client(
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                },
                timeout=self.DEFAULT_TIMEOUT,
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

    def validate_context(self, context: ReviewContext) -> list[str]:
        """Validate that context has required scale image and reading."""
        errors = []

        if "scale" not in context.images and not context.images:
            errors.append("Missing scale image (images['scale'] or any image)")

        if "reading" not in context.form_data:
            errors.append("Missing weight reading (form_data['reading'])")

        return errors

    def review(self, context: ReviewContext) -> ReviewResult:
        """
        Validate a scale reading against an image.

        Args:
            context: ReviewContext with scale image and reading

        Returns:
            ReviewResult with match status
        """
        # Validate context
        validation_errors = self.validate_context(context)
        if validation_errors:
            return ReviewResult.error("; ".join(validation_errors))

        # Check API key
        if not self.api_key:
            return ReviewResult.error("SCALE_VALIDATION_API_KEY not configured")

        # Get image - prefer "scale" key, fall back to first available
        image_bytes = context.get_image("scale")
        if image_bytes is None and context.images:
            image_bytes = next(iter(context.images.values()))

        reading = context.get_field("reading", "")

        self.logger.debug(f"Validating scale reading: {reading} (image size: {len(image_bytes)} bytes)")

        try:
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            response = self.http_client.post(
                f"{self.api_url}/predict",
                json={"image": encoded_image, "reading": reading},
            )

            if response.status_code == 429:
                return ReviewResult.error("Rate limited - service busy or starting up. Try again later.")

            response.raise_for_status()
            result = response.json()

            match = result.get("match", False)
            self.logger.debug(f"Scale validation result: match={match}")

            if match:
                return ReviewResult.success(match=True, api_response=result)
            else:
                return ReviewResult.failure(match=False, api_response=result)

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_data = e.response.json()
                error_detail = error_data.get("details", str(error_data))
            except Exception:
                error_detail = e.response.text
            self.logger.error(f"Scale validation API error: {error_detail}")
            return ReviewResult.error(f"API error: {error_detail}")

        except httpx.HTTPError as e:
            self.logger.error(f"Scale validation connection error: {e}")
            return ReviewResult.error(f"Connection error: {e}")

    def validate_reading(self, image_bytes: bytes, reading: str) -> dict:
        """
        Validate a scale reading against an image (legacy method).

        This method provides backward compatibility with the old
        ScaleValidationClient interface.

        Args:
            image_bytes: Raw image bytes (JPEG/PNG)
            reading: 4-digit weight reading string (e.g., "1535")

        Returns:
            {"match": True/False}

        Raises:
            ScaleValidationError: On API errors or rate limiting
        """
        context = ReviewContext(
            images={"scale": image_bytes},
            form_data={"reading": reading},
        )

        result = self.review(context)

        if result.status.value == "error":
            raise ScaleValidationError(result.errors[0] if result.errors else "Unknown error")

        return {"match": result.passed}

    def validate_reading_from_base64(self, base64_image: str, reading: str) -> dict:
        """
        Validate a scale reading from a base64-encoded image string (legacy method).

        Args:
            base64_image: Base64-encoded image string
            reading: 4-digit weight reading string

        Returns:
            {"match": True/False}
        """
        image_bytes = base64.b64decode(base64_image)
        return self.validate_reading(image_bytes, reading)


# Convenience aliases for backward compatibility
ScaleValidationClient = ScaleValidationAgent
