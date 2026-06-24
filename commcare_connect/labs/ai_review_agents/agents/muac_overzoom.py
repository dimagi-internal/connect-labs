"""
MUAC OverZoom Classification Agent.

Classifies MUAC (Mid-Upper Arm Circumference) photos as overzoomed using a CNN
classifier trained to identify images where the zoom is so extreme that
context is lost (child's arm / body not visible around the tape).

Overzoomed images are pre-tagged as 'fail' before human review because they
cannot be meaningfully audited for evidence of a proper measurement.

API Details:
- Endpoint: https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/classify
- Auth: API key in x-api-key header (same key as scale validation service)
- Request: {"image": "<base64>", "task": "muac_overzoom"}
- Response: {"result": "pass"|"fail"}
  - "fail" = image is overzoomed (insufficient context) → tag as fail
  - "pass" = image has adequate context → tag as pass / leave for human review
"""

import base64

import httpx
from django.conf import settings

from commcare_connect.labs.ai_review_agents.base import AIReviewAgentError, BaseAIReviewAgent
from commcare_connect.labs.ai_review_agents.registry import register
from commcare_connect.labs.ai_review_agents.types import ReviewContext, ReviewResult


class MUACOverzoomError(AIReviewAgentError):
    """Exception raised for MUAC OverZoom API errors."""

    pass


@register
class MUACOverzoomAgent(BaseAIReviewAgent):
    """
    AI Review Agent for MUAC photo overzoom classification.

    Detects MUAC photos that are zoomed in so tightly that the child's
    arm/body context is not visible, making the image unverifiable as
    a genuine measurement.

    Unlike the scale validation agent, no numeric reading is required —
    classification is purely image-based. Images flagged as overzoomed
    are automatically pre-tagged as 'fail' before human review.

    Required context:
        - Any image in context.images (first image used if "muac" key absent)

    Example:
        agent = MUACOverzoomAgent()
        context = ReviewContext(images={"muac": image_bytes})
        result = agent.review(context)
        if result.failed:
            print("Image is overzoomed - pre-tagging as fail")
    """

    agent_id = "muac_overzoom"
    name = "MUAC OverZoom"
    description = "Flags MUAC photos that are excessively zoomed in, removing context needed for verification"

    # No weight reading needed — classification is image-only
    requires_reading = False

    # AI failures (overzoomed) are automatically pre-tagged as human 'fail'
    auto_apply_result = True

    result_actions = {
        "fail_overzoomed": {
            "ai_result": "no_match",
            "human_result": "fail",
            "button_label": "Fail all Overzoomed",
        },
    }

    DEFAULT_API_URL = "https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev"
    DEFAULT_TIMEOUT = 60.0

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
        """Validate that context has at least one image."""
        errors = []
        if not context.images:
            errors.append("Missing MUAC image")
        return errors

    def review(self, context: ReviewContext) -> ReviewResult:
        """
        Classify whether the MUAC image is overzoomed.

        Args:
            context: ReviewContext with MUAC image (no reading required)

        Returns:
            ReviewResult.failure() if overzoomed (pre-tags as fail)
            ReviewResult.success() if adequate context
            ReviewResult.error() on API failure
        """
        validation_errors = self.validate_context(context)
        if validation_errors:
            return ReviewResult.error("; ".join(validation_errors))

        if not self.api_key:
            return ReviewResult.error("SCALE_VALIDATION_API_KEY not configured")

        # Prefer "muac" key; fall back to first available image
        image_bytes = context.get_image("muac")
        if image_bytes is None and context.images:
            image_bytes = next(iter(context.images.values()))

        self.logger.debug(f"Classifying MUAC image for overzoom (size: {len(image_bytes)} bytes)")

        try:
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            response = self.http_client.post(
                f"{self.api_url}/classify",
                json={"image": encoded_image, "task": "muac_overzoom"},
            )

            if response.status_code == 429:
                return ReviewResult.error("Rate limited - service busy or starting up. Try again later.")

            response.raise_for_status()
            result = response.json()

            api_result = result.get("result", "")

            if api_result == "fail":
                self.logger.debug("MUAC overzoom: image classified as overzoomed (fail)")
                return ReviewResult.failure(api_response=result)
            elif api_result == "pass":
                self.logger.debug("MUAC overzoom: image has adequate context (pass)")
                return ReviewResult.success(api_response=result)
            else:
                # Fallback: handle match:true/false style responses from the same gateway
                match = result.get("match")
                if match is True:
                    return ReviewResult.success(api_response=result)
                elif match is False:
                    return ReviewResult.failure(api_response=result)
                self.logger.warning(f"Unexpected MUAC overzoom API response: {result}")
                return ReviewResult.error(f"Unexpected API response format: {result}")

        except httpx.HTTPStatusError as e:
            error_detail = ""
            try:
                error_data = e.response.json()
                error_detail = error_data.get("details", str(error_data))
            except Exception:
                error_detail = e.response.text
            self.logger.error(f"MUAC overzoom API error: {error_detail}")
            return ReviewResult.error(f"API error: {error_detail}")

        except httpx.HTTPError as e:
            self.logger.error(f"MUAC overzoom connection error: {e}")
            return ReviewResult.error(f"Connection error: {e}")
