"""
Types for AI Review Agents.

Provides flexible data structures for review input/output that can accommodate
various review types (image validation, form data review, etc.).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReviewStatus(Enum):
    """Status of a review result."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class ReviewContext:
    """
    Input context for an AI review agent.

    Flexible container that can hold various types of data depending
    on what the specific agent needs to perform its review.

    Attributes:
        images: Dict of image name -> bytes for image-based reviews
        form_data: Dict of form field data
        metadata: Additional context (opportunity_id, record_id, etc.)
    """

    images: dict[str, bytes] = field(default_factory=dict)
    form_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_image(self, key: str) -> bytes | None:
        """Get image bytes by key."""
        return self.images.get(key)

    def get_field(self, key: str, default: Any = None) -> Any:
        """Get form data field by key."""
        return self.form_data.get(key, default)


@dataclass
class ReviewResult:
    """
    Result of an AI review.

    Standardized output that all review agents return.

    Attributes:
        status: Overall review status (passed, failed, error, skipped)
        confidence: Confidence score 0.0-1.0 (if applicable)
        details: Agent-specific result details
        errors: List of error messages if any occurred
    """

    status: ReviewStatus
    confidence: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Whether the review passed."""
        return self.status == ReviewStatus.PASSED

    @property
    def failed(self) -> bool:
        """Whether the review failed."""
        return self.status == ReviewStatus.FAILED

    @classmethod
    def success(cls, confidence: float | None = None, **details) -> "ReviewResult":
        """Create a successful review result."""
        return cls(status=ReviewStatus.PASSED, confidence=confidence, details=details)

    @classmethod
    def failure(cls, confidence: float | None = None, **details) -> "ReviewResult":
        """Create a failed review result."""
        return cls(status=ReviewStatus.FAILED, confidence=confidence, details=details)

    @classmethod
    def error(cls, message: str, **details) -> "ReviewResult":
        """Create an error review result."""
        return cls(status=ReviewStatus.ERROR, errors=[message], details=details)

    @classmethod
    def skipped(cls, reason: str, **details) -> "ReviewResult":
        """Create a skipped review result."""
        return cls(status=ReviewStatus.SKIPPED, details={"reason": reason, **details})
