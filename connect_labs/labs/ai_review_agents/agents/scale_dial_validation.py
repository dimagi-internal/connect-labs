"""
Scale [Dial] Image Validation Agent.

Validates that a user-entered weight reading matches what's shown on an
*analog dial* scale photo, using ML vision. This is the dial-scale
counterpart to the digital Scale Image Validation agent: dial faces need a
different reading model, so this agent posts to the shared classifier's
``/interpret`` endpoint (the same one MUAC Reading Match uses) with the
``scale_dial_read`` task instead of the digital ``/predict`` path.

API Details:
- Endpoint: https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/interpret
- Auth: API key in x-api-key header (same key as scale validation service)
- Request: {"image": "<base64>", "task": "scale_dial_read", "reading": "1535", "tolerance": "strict"}
- Response: {"match": true/false, "processing_factor": <int>}
- tolerance: "strict" (default), "loose", or "wide".
"""

import base64

import httpx
from django.conf import settings

from connect_labs.labs.ai_review_agents.base import (
    GATEWAY_ERROR_MESSAGE,
    GATEWAY_NOT_CONFIGURED_MESSAGE,
    GATEWAY_RATE_LIMITED_MESSAGE,
    GATEWAY_UNREACHABLE_MESSAGE,
    AIReviewAgentError,
    BaseAIReviewAgent,
    post_with_retry,
)
from connect_labs.labs.ai_review_agents.registry import register
from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult


class ScaleDialValidationError(AIReviewAgentError):
    """Exception raised for Scale Dial Validation API errors."""

    pass


@register
class ScaleDialValidationAgent(BaseAIReviewAgent):
    """
    AI Review Agent for analog dial scale image validation.

    Validates that a user-entered weight reading matches what's shown on a
    dial-scale photo using ML vision analysis, via the shared classifier's
    ``scale_dial_read`` task.

    Required context:
        - images["scale"]: Raw image bytes (JPEG/PNG) of the dial scale
        - form_data["reading"]: weight reading string (e.g., "1535")

    Example:
        agent = ScaleDialValidationAgent()
        context = ReviewContext(
            images={"scale": image_bytes},
            form_data={"reading": "1535"}
        )
        result = agent.review(context)
        if result.passed:
            print("Weight matches!")
    """

    agent_id = "scale_dial_read"
    name = "Scale [Dial] Image Validation"
    description = "Validates weight readings against analog dial scale images using ML vision"
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
            "help": "Form field whose value is compared against the dial scale photo",
        }
    ]

    DEFAULT_API_URL = "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev"
    DEFAULT_TIMEOUT = 60.0
    DEFAULT_TOLERANCE = "strict"
    TASK = "scale_dial_read"

    def __init__(self):
        super().__init__()
        self._client: httpx.Client | None = None

    @property
    def api_key(self) -> str:
        """Get API key from settings — shared with scale validation service."""
        return getattr(settings, "SCALE_VALIDATION_API_KEY", "")

    @property
    def api_url(self) -> str:
        """Get API URL from settings."""
        return getattr(settings, "SCALE_VALIDATION_API_URL", self.DEFAULT_API_URL).rstrip("/")

    @property
    def tolerance(self) -> str:
        """How close a reading must be to be considered a match: strict, loose, wide."""
        return self.get_config("SCALE_DIAL_TOLERANCE", self.DEFAULT_TOLERANCE)

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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
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
        Validate a dial scale reading against an image.

        Args:
            context: ReviewContext with scale image and reading

        Returns:
            ReviewResult with match status
        """
        validation_errors = self.validate_context(context)
        if validation_errors:
            return ReviewResult.error("; ".join(validation_errors))

        if not self.api_key:
            return ReviewResult.error(GATEWAY_NOT_CONFIGURED_MESSAGE)

        # Get image - prefer "scale" key, fall back to first available
        image_bytes = context.get_image("scale")
        if image_bytes is None and context.images:
            image_bytes = next(iter(context.images.values()))

        reading = context.get_field("reading", "")
        tolerance = self.tolerance

        self.logger.debug(
            f"Validating dial scale reading: {reading} (tolerance={tolerance}, image size: {len(image_bytes)} bytes)"
        )

        try:
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            response = post_with_retry(
                self.http_client,
                f"{self.api_url}/interpret",
                json={"image": encoded_image, "task": self.TASK, "reading": reading, "tolerance": tolerance},
                logger=self.logger,
            )

            if response.status_code == 429:
                return ReviewResult.error(GATEWAY_RATE_LIMITED_MESSAGE)

            response.raise_for_status()
            result = response.json()

            match = result.get("match", False)
            processing_factor = result.get("processing_factor")
            self.logger.debug(f"Scale dial result: match={match}, processing_factor={processing_factor}")

            if match:
                return ReviewResult.success(
                    pass_label=f"Scale Match ({tolerance} tolerance)",
                    reading=reading,
                    tolerance=tolerance,
                    processing_factor=processing_factor,
                    api_response=result,
                )
            else:
                return ReviewResult.failure(
                    # Classifier-level (not per-visit) so
                    # AuditSessionRecord.get_assessment_stats() can tally
                    # ai_flags_by_label by this label — embedding the per-visit
                    # reading here would make every failing image unique. Same
                    # rationale as MUAC Reading Match; see muac_match.py.
                    badge_label=f"Scale Mismatch ({tolerance} tolerance)",
                    reading=reading,
                    tolerance=tolerance,
                    processing_factor=processing_factor,
                    api_response=result,
                )

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_data = e.response.json()
                error_detail = error_data.get("details", str(error_data))
            except Exception:
                error_detail = e.response.text
            self.logger.error(f"Scale dial API error: {error_detail}")
            return ReviewResult.error(GATEWAY_ERROR_MESSAGE)

        except httpx.HTTPError as e:
            self.logger.error(f"Scale dial connection error: {e}")
            return ReviewResult.error(GATEWAY_UNREACHABLE_MESSAGE)
