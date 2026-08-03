"""
Base class for AI Review Agents.

Provides the abstract interface and shared functionality that all
AI review agents must implement.
"""

import logging
import math
import random
import time
from abc import ABC, abstractmethod

from django.conf import settings

from connect_labs.labs.ai_review_agents.types import ReviewContext, ReviewResult


class AIReviewAgentError(Exception):
    """Base exception for AI review agent errors."""

    pass


# User-facing text for the shared classifier gateway's error paths (muac_overzoom,
# muac_match, scale_validation). Fixed vocabulary rather than the raw exception/API
# body: the review UI now renders ai_notes verbatim for ai_result="error" (previously
# it showed a generic "Error" regardless of what the agent returned), so a raw
# httpx.HTTPStatusError string or an unrecognized gateway response body could put
# internal detail -- hostnames, a framework traceback on a gateway 5xx, an unfamiliar
# JSON blob -- directly in front of an auditor. Each agent's except block still logs
# the full detail via self.logger; only the auditor-visible message is generic here.
# GATEWAY_RATE_LIMITED_MESSAGE is the one exception: it's actionable (retry later)
# and reveals nothing internal, so it stays specific.
GATEWAY_RATE_LIMITED_MESSAGE = "Rate limited - service busy or starting up. Retried, still unavailable."
GATEWAY_UNREACHABLE_MESSAGE = "Could not reach the AI classifier service. Try again."
GATEWAY_ERROR_MESSAGE = (
    "AI classifier service returned an error. Try again, or contact an administrator if this persists."
)
GATEWAY_UNEXPECTED_RESPONSE_MESSAGE = "AI classifier service returned an unexpected response."
GATEWAY_NOT_CONFIGURED_MESSAGE = "AI classifier service is not configured. Contact an administrator."


def post_with_retry(client, url, *, json, max_retries=3, backoff_seconds=2.0, logger=None):
    """POST with retry-on-429 (linear backoff: ~2s, ~4s, ~6s by default).

    The MUAC OverZoom / MUAC Match / Scale Validation classifiers share one
    gateway that returns 429 both when genuinely busy and during a cold
    start -- both conditions typically clear within seconds, so a short
    retry recovers automatically instead of permanently erroring the image
    (previously: a single 429 was treated as terminal, with no retry at all).

    Real production usage calls this from up to ~10 concurrently-reviewed
    images x up to ~4 reviewers per image (see tasks.py's
    _MAX_CONCURRENT_IMAGES_PER_SESSION / _MAX_REVIEWERS_PER_IMAGE) -- if the
    gateway is genuinely saturated, every one of those callers hits 429 at
    roughly the same moment. Two things keep the retry from making that
    worse: honoring the gateway's own ``Retry-After`` header when present
    (it knows its load better than a fixed guess), and jittering whichever
    wait is used so concurrent callers don't all wake and retry in lockstep.

    Returns the last response received, which may still be a 429 if every
    retry was exhausted -- callers check response.status_code exactly as
    they did before this helper existed.
    """
    response = None
    for attempt in range(max_retries + 1):
        response = client.post(url, json=json)
        if response.status_code != 429:
            return response
        if attempt < max_retries:
            retry_after = response.headers.get("Retry-After")
            wait = None
            if retry_after is not None:
                try:
                    parsed = float(retry_after)
                    # float() accepts "-1"/"nan"/"inf" without raising, but
                    # time.sleep() rejects negative/NaN outright and would
                    # block forever on inf -- a malformed or hostile header
                    # value must fall back to the computed backoff, not
                    # reach time.sleep() unvalidated.
                    if math.isfinite(parsed) and parsed >= 0:
                        wait = parsed
                except ValueError:
                    wait = None
            if wait is None:
                wait = backoff_seconds * (attempt + 1)
            wait *= random.uniform(0.75, 1.25)
            if logger:
                logger.warning(
                    f"Rate limited (429) on attempt {attempt + 1}/{max_retries + 1}, retrying in {wait:.1f}s"
                )
            time.sleep(wait)
    return response


class BaseAIReviewAgent(ABC):
    """
    Abstract base class for AI review agents.

    All AI review agents should inherit from this class and implement
    the required abstract methods.

    Class Attributes:
        agent_id: Unique identifier for this agent type (e.g., "scale_validation")
        name: Human-readable name
        description: Description of what this agent does
        result_actions: Dict mapping AI results to human review actions.
            Each action has: ai_result, human_result, button_label
            Example: {"pass_matched": {"ai_result": "match", "human_result": "pass", ...}}

    Example:
        class MyReviewAgent(BaseAIReviewAgent):
            agent_id = "my_review"
            name = "My Review Agent"
            description = "Reviews something"
            result_actions = {
                "pass_matched": {"ai_result": "match", "human_result": "pass", "button_label": "Pass"},
                "fail_unmatched": {"ai_result": "no_match", "human_result": "fail", "button_label": "Fail"},
            }

            def review(self, context: ReviewContext) -> ReviewResult:
                # Perform review logic
                return ReviewResult.success(match=True)
    """

    agent_id: str = ""
    name: str = ""
    description: str = ""
    result_actions: dict = {}
    # Declarative settings the creation wizard renders when this agent is chosen
    # for an image type. Each item: {key, label, type, required, help}.
    # type "form_field" renders a picker of the opportunity's form-field paths.
    config_fields: list[dict] = []

    def __init__(self):
        """Initialize the agent with logging."""
        self.logger = logging.getLogger(f"{__name__}.{self.agent_id}")
        self._validate_class_attrs()

    def _validate_class_attrs(self):
        """Validate required class attributes are set."""
        if not self.agent_id:
            raise ValueError(f"{self.__class__.__name__} must define 'agent_id'")
        if not self.name:
            raise ValueError(f"{self.__class__.__name__} must define 'name'")

    def get_config(self, key: str, default=None):
        """
        Get agent-specific configuration from Django settings.

        Looks for settings in the format: {AGENT_ID}_CONFIG or just the key directly.

        Args:
            key: Configuration key to look up
            default: Default value if not found

        Returns:
            Configuration value or default
        """
        # Try agent-specific config first
        agent_config_key = f"{self.agent_id.upper()}_CONFIG"
        agent_config = getattr(settings, agent_config_key, {})
        if key in agent_config:
            return agent_config[key]

        # Fall back to direct setting lookup
        return getattr(settings, key, default)

    @abstractmethod
    def review(self, context: ReviewContext) -> ReviewResult:
        """
        Perform the review.

        This is the main method that subclasses must implement.
        It should analyze the provided context and return a ReviewResult.

        Args:
            context: ReviewContext containing data to review

        Returns:
            ReviewResult with the outcome of the review

        Raises:
            AIReviewAgentError: If a recoverable error occurs during review
        """
        pass

    def validate_context(self, context: ReviewContext) -> list[str]:
        """
        Validate that the context has required data for this agent.

        Override in subclasses to add specific validation.

        Args:
            context: ReviewContext to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        return []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} agent_id='{self.agent_id}'>"
