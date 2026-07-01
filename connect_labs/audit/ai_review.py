"""
Shared AI review utilities for audit assessments.

Provides common functionality used by both synchronous API endpoints
and asynchronous Celery tasks for running AI review agents.
"""

import logging

logger = logging.getLogger(__name__)


def run_single_ai_review(
    agent,
    image_bytes: bytes,
    reading: str = "",
    metadata: dict | None = None,
) -> str:
    """
    Run AI review on a single image and return the result.

    This is the shared utility for running AI review that both the
    synchronous API (AIReviewAPIView) and async task (_run_ai_review_on_sessions)
    can use.

    Args:
        agent: AI review agent instance (e.g., ScaleValidationAgent)
        image_bytes: Raw image bytes
        reading: The value to validate (e.g., weight reading from form)
        metadata: Optional metadata dict for context (visit_id, blob_id, etc.)

    Returns:
        ai_result: One of "match", "no_match", or "error"
    """
    from connect_labs.labs.ai_review_agents.types import ReviewContext

    context = ReviewContext(
        images={"scale": image_bytes},
        form_data={"reading": reading} if reading else {},
        metadata=metadata or {},
    )

    try:
        result = agent.review(context)

        if result.passed:
            return "match"
        elif result.failed:
            return "no_match"
        else:
            return "error"
    except Exception as e:
        logger.warning(f"[AIReview] Agent review failed: {e}")
        return "error"
