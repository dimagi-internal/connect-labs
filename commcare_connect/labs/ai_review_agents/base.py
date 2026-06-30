"""
Base class for AI Review Agents.

Provides the abstract interface and shared functionality that all
AI review agents must implement.
"""

import logging
from abc import ABC, abstractmethod

from django.conf import settings

from commcare_connect.labs.ai_review_agents.types import ReviewContext, ReviewResult


class AIReviewAgentError(Exception):
    """Base exception for AI review agent errors."""

    pass


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
