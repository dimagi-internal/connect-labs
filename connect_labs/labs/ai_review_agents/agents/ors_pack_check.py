"""
ORS Pack Check Agent.

Verifies a photographed pack is the ORASURE Kit -- a Dimagi-branded pediatric
zinc sulphate + oral rehydration salt (ORS) supplement package used in the
CHC PRE-RCT program (Nigeria) -- using a vision-capable LLM prompted with an
explicit, inspectable rule set rather than classical image-processing. A
single reference photo isn't enough data to reliably tune a classical CV
template/color matcher against real-world lighting/angle/occlusion variation;
an LLM given the same explicit rules a human reviewer would use generalizes
better from one example.

API Details:
- Provider: Anthropic Messages API, called directly and synchronously via the
  `anthropic` SDK (same package already used by automation/update_docs.py and
  automation/regenerate_confluence.py) -- no pydantic-ai, no streaming.
- Auth: ANTHROPIC_API_KEY is already provisioned as a real AWS Secrets Manager
  secret and wired into both the web and worker ECS task definitions
  (deploy/task-definitions/{web,worker}.json) for pydantic-ai's own agents in
  connect_labs/ai/agents/. The `anthropic` SDK reads it from the process
  environment automatically -- no new secret or settings wiring needed.
"""

import base64
import json

from connect_labs.labs.ai_review_agents.base import AIReviewAgentError, BaseAIReviewAgent
from connect_labs.labs.ai_review_agents.registry import register
from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult


class ORSPackCheckError(AIReviewAgentError):
    """Exception raised for ORS pack check errors."""

    pass


# Explicit, inspectable identification rules -- written from directly reviewing
# the reference ORASURE Kit photo plus ~10 real field-submitted photos
# (2026-07-30, CHC PRE-RCT EHA opportunity). Kept as a module constant so the
# rule text is easy to find and revise without touching the request-building
# code below.
ORASURE_IDENTIFICATION_RULES = """
You are checking whether a field photo shows the ORASURE Kit -- a Dimagi-branded pediatric
zinc sulphate + oral rehydration salt (ORS) supplement package.

Genuine ORASURE Kit boxes have ALL of these identifying features:
- A mostly orange/peach gradient background on the front panel.
- A bold blue "ORASURE" wordmark near the top, usually followed by a smaller "KIT" label.
- Text near the top mentioning "10 dispersible zinc sulphate tablets" and
  "2 Pouches of Oral rehydration salt of 20.5gm each" (may be partially visible or angled).
- A photo of an infant's face inset in a white rounded-rectangle frame, roughly centered.
- A black curved swoosh/band running across the panel, usually behind or above the baby photo.
- Often (not always visible, depending on crop/angle) a red or orange starburst badge reading
  "NEW IMPROVED WHO FORMULA" near the baby photo.
- A "dimagi" wordmark near the bottom of the panel.

Field conditions to expect and NOT penalize:
- The box is usually held in a hand (any skin tone), often outdoors, at an angle or tilt.
- Fingers may partially cover some text or the corner of the box.
- Lighting varies -- direct sun, glare, shade, or slight blur from a moving hand.
- The box may be cropped so only part of the front panel is visible.
None of the above alone are reasons to fail a photo -- judge the PRODUCT identity, not photo quality.

Fail (no_match) ONLY when:
- The item shown is clearly a DIFFERENT product or brand -- e.g. a different wordmark/brand name,
  a different color scheme entirely, no baby-photo inset, or packaging that doesn't match the
  description above in a way field conditions can't explain.
- No product packaging is visible at all -- e.g. an empty hand, an unrelated object, or the image
  is blurred/obscured beyond any packaging being recognizable.

This is a SUGGESTION for a human reviewer to confirm, not a final verdict -- be genuinely
calibrated rather than defaulting to the extremes. Use a lower confidence_percent when the photo
is ambiguous, heavily obscured, or you're guessing; use a high one only when the identifying
features are clearly visible.

Respond with ONLY a JSON object, no other text, no markdown code fence:
{"match": true or false, "confidence_percent": <integer 0-100>, "reason": "<one short sentence>"}
""".strip()


@register
class ORSPackCheckAgent(BaseAIReviewAgent):
    """
    AI Review Agent for ORS (ORASURE Kit) pack identity verification.

    Classifies whether a photographed pack is genuinely the ORASURE Kit
    product, using a vision LLM prompted with an explicit rule set (see
    ORASURE_IDENTIFICATION_RULES) rather than a trained classifier -- cheap
    to build and revise from a handful of reference photos, at the cost of
    being LLM-call-latency-bound rather than a fast local model.

    No numeric reading required -- classification is purely image-based.

    Required context:
        - Any image in context.images (first image used if "photo" key absent)

    Example:
        agent = ORSPackCheckAgent()
        context = ReviewContext(images={"photo": image_bytes})
        result = agent.review(context)
        if result.failed:
            print("Not the ORASURE Kit:", result.details.get("reason"))
    """

    agent_id = "ors_pack_check"
    name = "ORS Pack Check"
    description = (
        "Verifies the photographed pack is the ORASURE zinc/ORS kit, not a different product or no product at all"
    )

    # No manual reading needed -- classification is purely image-based.
    requires_reading = False

    result_actions = {
        "fail_wrong_product": {
            "ai_result": "no_match",
            "human_result": "fail",
            "button_label": "Fail all Wrong/Missing Product",
        },
    }

    DEFAULT_MODEL = "claude-sonnet-4-6"
    DEFAULT_MAX_TOKENS = 200

    @property
    def model(self) -> str:
        """Model id -- overridable via Django settings (ORS_PACK_CHECK_CONFIG['ORS_PACK_CHECK_MODEL'])."""
        return self.get_config("ORS_PACK_CHECK_MODEL", self.DEFAULT_MODEL)

    def validate_context(self, context: ReviewContext) -> list[str]:
        """Validate that context has an image to classify."""
        errors = []
        if not context.images:
            errors.append("Missing photo (images dict is empty)")
        return errors

    def review(self, context: ReviewContext) -> ReviewResult:
        """
        Classify whether the photographed pack is the ORASURE Kit.

        Args:
            context: ReviewContext with the submitted photo

        Returns:
            ReviewResult with match status
        """
        validation_errors = self.validate_context(context)
        if validation_errors:
            return ReviewResult.error("; ".join(validation_errors))

        # Get image -- prefer "photo" key, fall back to first available
        image_bytes = context.get_image("photo")
        if image_bytes is None and context.images:
            image_bytes = next(iter(context.images.values()))

        try:
            import anthropic

            # No api_key kwarg -- the SDK reads ANTHROPIC_API_KEY from the
            # process environment, same as automation/update_docs.py.
            client = anthropic.Anthropic()
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            response = client.messages.create(
                model=self.model,
                max_tokens=self.DEFAULT_MAX_TOKENS,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": encoded_image,
                                },
                            },
                            {"type": "text", "text": ORASURE_IDENTIFICATION_RULES},
                        ],
                    }
                ],
            )

            raw_text = response.content[0].text.strip()
            parsed = self._parse_response(raw_text)

            match = bool(parsed.get("match"))
            # Stored as an actual 0.0-1.0 float on ReviewResult.confidence (not a
            # string label) so any consumer can use it directly -- e.g. as a %.
            confidence_percent = max(0, min(100, int(parsed.get("confidence_percent", 0))))
            confidence = confidence_percent / 100.0
            reason = parsed.get("reason", "")

            # Deliberately soft, suggestion-only wording -- this is a lighter-touch
            # check than the MUAC agents (which call a dedicated, validated ML
            # classifier), not an equally authoritative verdict. A human always
            # confirms; these labels say so explicitly rather than asserting.
            if match:
                return ReviewResult.success(
                    confidence,
                    pass_label=f"Likely ORS pack ({confidence_percent}%) — please confirm",
                    confidence_percent=confidence_percent,
                    reason=reason,
                    raw_response=raw_text,
                )
            else:
                return ReviewResult.failure(
                    confidence,
                    badge_label=f"Possibly not ORS pack ({confidence_percent}%) — please confirm",
                    confidence_percent=confidence_percent,
                    reason=reason,
                    raw_response=raw_text,
                )

        except json.JSONDecodeError as e:
            self.logger.error(f"ORS pack check: failed to parse model response: {e}")
            return ReviewResult.error(f"Could not parse model response: {e}")
        except Exception as e:
            self.logger.error(f"ORS pack check API error: {e}")
            return ReviewResult.error(f"API error: {e}")

    @staticmethod
    def _parse_response(raw_text: str) -> dict:
        """Parse the model's JSON reply, defensively stripping a markdown code fence if present."""
        text = raw_text
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        return json.loads(text)
