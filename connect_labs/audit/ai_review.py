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


def run_single_ai_review_with_notes(
    agent,
    image_bytes: bytes,
    reading: str = "",
    metadata: dict | None = None,
) -> tuple[str, str]:
    """
    Like run_single_ai_review, but also returns a human-readable note pulled
    from the agent's own result details -- used by the synchronous
    AIReviewAPIView, which previously always sent back ai_notes="" and
    silently discarded whatever message the agent actually returned (e.g. an
    agent's badge_label/pass_label/reason). The async task path
    (_run_ai_review_on_sessions) already surfaces this via
    _combine_reviewer_results and is untouched by this function.

    Args:
        agent: AI review agent instance (e.g., ORSPackCheckAgent)
        image_bytes: Raw image bytes
        reading: The value to validate (e.g., weight reading from form)
        metadata: Optional metadata dict for context (visit_id, blob_id, etc.)

    Returns:
        (ai_result, ai_notes) -- ai_result is one of "match", "no_match", "error";
        ai_notes is the agent's own message, or "" if it didn't provide one.
    """
    from connect_labs.labs.ai_review_agents.types import ReviewContext

    context = ReviewContext(
        images={"scale": image_bytes},
        form_data={"reading": reading} if reading else {},
        metadata=metadata or {},
    )

    try:
        result = agent.review(context)
    except Exception as e:
        logger.warning(f"[AIReview] Agent review failed: {e}")
        return "error", str(e)

    note = result.details.get("badge_label") or result.details.get("pass_label") or result.details.get("reason") or ""

    if result.passed:
        return "match", note
    elif result.failed:
        return "no_match", note
    else:
        return "error", (result.errors[0] if result.errors else note)
