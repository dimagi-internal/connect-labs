"""
MUAC Reading Match Agent.

Validates that a user-entered MUAC (Mid-Upper Arm Circumference) reading
matches what's shown in the accompanying tape photo, using ML vision.
Independent of (and can run alongside) the MUAC OverZoom framing check —
this validates the *reading*, not the photo's framing.

API Details:
- Endpoint: https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/interpret
- Auth: API key in x-api-key header (same key as scale validation service)
- Request: {"image": "<base64>", "task": "muac_match", "reading": "14.6", "tolerance": "strict"}
- Response: {"match": true/false, "processing_factor": <int>}
- tolerance: "strict" (default, 98% accurate), "loose" (99.8% accurate, fewer
  real positives caught), or "wide" (no false matches, low predictive power).
"""

import base64

import httpx
from django.conf import settings

from connect_labs.labs.ai_review_agents.base import AIReviewAgentError, BaseAIReviewAgent
from connect_labs.labs.ai_review_agents.registry import register
from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult


class MUACMatchError(AIReviewAgentError):
    """Exception raised for MUAC Match API errors."""

    pass


@register
class MUACMatchAgent(BaseAIReviewAgent):
    """
    AI Review Agent for MUAC reading validation.

    Validates that a user-entered MUAC reading (cm) matches what's shown
    in the MUAC tape photo using ML vision analysis.

    Required context:
        - images["muac"]: Raw image bytes (JPEG/PNG) of the MUAC tape
        - form_data["reading"]: MUAC reading string (e.g., "14.6")

    Example:
        agent = MUACMatchAgent()
        context = ReviewContext(
            images={"muac": image_bytes},
            form_data={"reading": "14.6"}
        )
        result = agent.review(context)
        if result.passed:
            print("Reading matches!")
    """

    agent_id = "muac_match"
    name = "MUAC Reading Match"
    description = "Validates MUAC readings against tape photos using ML vision"
    result_actions = {
        "pass_matched": {
            "ai_result": "match",
            "human_result": "pass",
            "button_label": "Pass all Matched",
        },
        "fail_unmatched": {
            "ai_result": "no_match",
            "human_result": "fail",
            "button_label": "Fail all Mismatched",
        },
    }
    config_fields = [
        {
            "key": "comparison_field",
            "label": "Manual MUAC Reading",
            "type": "form_field",
            "required": True,
            "help": "Form field whose value is compared against the MUAC tape photo",
        }
    ]

    DEFAULT_API_URL = "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev"
    DEFAULT_TIMEOUT = 60.0
    DEFAULT_TOLERANCE = "strict"

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
        return self.get_config("MUAC_MATCH_TOLERANCE", self.DEFAULT_TOLERANCE)

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
        """Validate that context has required MUAC image and reading."""
        errors = []

        if "muac" not in context.images and not context.images:
            errors.append("Missing MUAC image (images['muac'] or any image)")

        if "reading" not in context.form_data:
            errors.append("Missing MUAC reading (form_data['reading'])")

        return errors

    def review(self, context: ReviewContext) -> ReviewResult:
        """
        Validate a MUAC reading against a tape photo.

        Args:
            context: ReviewContext with MUAC image and reading

        Returns:
            ReviewResult with match status
        """
        validation_errors = self.validate_context(context)
        if validation_errors:
            return ReviewResult.error("; ".join(validation_errors))

        if not self.api_key:
            return ReviewResult.error("SCALE_VALIDATION_API_KEY not configured")

        # Get image - prefer "muac" key, fall back to first available
        image_bytes = context.get_image("muac")
        if image_bytes is None and context.images:
            image_bytes = next(iter(context.images.values()))

        reading = context.get_field("reading", "")
        tolerance = self.tolerance

        self.logger.debug(
            f"Validating MUAC reading: {reading} (tolerance={tolerance}, image size: {len(image_bytes)} bytes)"
        )

        try:
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            response = self.http_client.post(
                f"{self.api_url}/interpret",
                json={"image": encoded_image, "task": "muac_match", "reading": reading, "tolerance": tolerance},
            )

            if response.status_code == 429:
                return ReviewResult.error("Rate limited - service busy or starting up. Try again later.")

            response.raise_for_status()
            result = response.json()

            match = result.get("match", False)
            processing_factor = result.get("processing_factor")
            self.logger.debug(f"MUAC match result: match={match}, processing_factor={processing_factor}")

            if match:
                return ReviewResult.success(
                    pass_label=f"MUAC Match ({tolerance} tolerance)",
                    reading=reading,
                    tolerance=tolerance,
                    processing_factor=processing_factor,
                    api_response=result,
                )
            else:
                return ReviewResult.failure(
                    # Deliberately classifier-level, not per-visit: this string
                    # is both the assessment's ai_notes AND the key
                    # AuditSessionRecord.get_assessment_stats() tallies
                    # ai_flags_by_label by (see connect_labs/audit/models.py).
                    # Embedding the per-visit reading here would make every
                    # failing image's label unique, defeating that tally. The
                    # actual reading is already visible in the review UI's
                    # "MUAC Reading" related-fields box (see
                    # weekly_dual_track_audit.py's MUAC_MATCH_REVIEWER config).
                    badge_label=f"MUAC Mismatch ({tolerance} tolerance)",
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
            self.logger.error(f"MUAC match API error: {error_detail}")
            return ReviewResult.error(f"API error: {error_detail}")

        except httpx.HTTPError as e:
            self.logger.error(f"MUAC match connection error: {e}")
            return ReviewResult.error(f"Connection error: {e}")
