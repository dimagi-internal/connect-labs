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
from urllib.parse import urlparse

import httpx
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
# Split out of GATEWAY_UNREACHABLE_MESSAGE: a read timeout is not "could not
# reach" -- the request was accepted and the gateway simply never answered
# within DEFAULT_TIMEOUT. That distinction is the difference between "the
# service is down" and "the service is overloaded/slow", and it is by far the
# most common classifier failure in production, so it gets its own vocabulary
# instead of being filed under an inaccurate one. Like the rate-limit message
# it is actionable and leaks nothing internal.
GATEWAY_TIMEOUT_MESSAGE = "AI classifier service timed out. Try again."
GATEWAY_ERROR_MESSAGE = (
    "AI classifier service returned an error. Try again, or contact an administrator if this persists."
)
GATEWAY_UNEXPECTED_RESPONSE_MESSAGE = "AI classifier service returned an unexpected response."
GATEWAY_NOT_CONFIGURED_MESSAGE = "AI classifier service is not configured. Contact an administrator."

# Machine-readable cause for an "error" ReviewResult, carried in
# ReviewResult.details["error_kind"].
#
# The user-facing GATEWAY_* strings above are deliberately generic, which makes
# them useless for triage: "errors=160" in a run summary could be 160 dead
# backends, 160 timeouts, or 160 misconfigurations, and telling them apart used
# to mean cross-referencing separate per-agent log lines that carry no blob or
# session id. These kinds are tallied per run (see _run_ai_review_on_sessions)
# so the summary line itself says which failure happened and how often.
ERROR_KIND_TIMEOUT = "timeout"
ERROR_KIND_UNREACHABLE = "unreachable"
ERROR_KIND_GATEWAY_ERROR = "gateway_error"
ERROR_KIND_UNEXPECTED_RESPONSE = "unexpected_response"
ERROR_KIND_RATE_LIMITED = "rate_limited"
ERROR_KIND_NOT_CONFIGURED = "not_configured"
ERROR_KIND_INVALID_CONTEXT = "invalid_context"
ERROR_KIND_AGENT_EXCEPTION = "agent_exception"
ERROR_KIND_UNKNOWN = "unknown"


def _is_timeout(exc) -> bool:
    """Whether a transport exception is a timeout rather than a genuine
    can't-connect.

    Primarily an isinstance check on httpx.TimeoutException. The string fallback
    covers the case the production logs actually show -- "The read operation
    timed out", which originates in the socket/ssl layer and can surface wrapped
    in a non-httpx exception type depending on where it is raised.
    """
    if isinstance(exc, httpx.TimeoutException):
        return True
    return "timed out" in str(exc).lower()


def _log_classifier_call(logger, agent_id, url, *, outcome, status, attempts, elapsed_ms, attempt_ms, detail=""):
    """Emit the one-line-per-classifier-call record.

    This is the only place any classifier call latency is recorded. Without it
    per-call latency is unobservable: the pool width is known but the time each
    call actually takes is not, so "the run was slow" can only be divided by an
    assumed concurrency rather than measured. Fields are `key=value` so a log
    query can average `elapsed_ms` or group by `outcome` without regex-parsing
    prose.
    """
    if logger is None:
        return
    endpoint = urlparse(url).path or url
    logger.info(
        "[classifier] agent=%s endpoint=%s outcome=%s status=%s attempts=%d elapsed_ms=%d attempt_ms=%d%s",
        agent_id or "unknown",
        endpoint,
        outcome,
        status,
        attempts,
        elapsed_ms,
        attempt_ms,
        f" detail={detail}" if detail else "",
    )


def post_with_retry(client, url, *, json, max_retries=3, backoff_seconds=2.0, logger=None, agent_id=""):
    """POST with retry-on-429 and retry-on-unreachable (linear backoff: ~2s,
    ~4s, ~6s by default).

    The MUAC OverZoom / MUAC Match / Scale Validation / Scale Dial classifiers
    share one gateway that returns 429 both when genuinely busy and during a
    cold start -- both conditions typically clear within seconds, so a short
    retry recovers automatically instead of permanently erroring the image
    (previously: a single 429 was treated as terminal, with no retry at all).

    A fast connection failure (refused, DNS failure, reset -- ERROR_KIND_UNREACHABLE,
    an httpx.TransportError that _is_timeout says is NOT a timeout) gets the same
    retry treatment: it is often the same cold-start/overload condition surfacing
    as a dropped connection instead of a 429 response. A TIMEOUT is deliberately
    excluded from this retry: it already costs a full client timeout of wall-clock
    per attempt (the dominant real-world failure -- see _is_timeout), so retrying
    it would multiply that cost by max_retries+1 for the single most expensive
    failure mode. A timeout still gets exactly one attempt, same as before.

    Real production usage calls this from up to ~10 concurrently-reviewed
    images x up to ~4 reviewers per image (see tasks.py's
    _MAX_CONCURRENT_IMAGES_PER_SESSION / _MAX_REVIEWERS_PER_IMAGE) -- if the
    gateway is genuinely saturated, every one of those callers hits 429 (or an
    unreachable failure) at roughly the same moment. Two things keep the retry
    from making that worse: honoring the gateway's own ``Retry-After`` header
    when present for 429s (there's no equivalent header for a connection
    failure, so those always use the computed backoff), and jittering
    whichever wait is used so concurrent callers don't all wake and retry in
    lockstep.

    Returns the last response received, which may still be a 429 if every
    retry was exhausted -- callers check response.status_code exactly as they
    did before this helper existed. Re-raises the last exception if every
    retry was exhausted on the unreachable path (or immediately, for a
    timeout or any other exception), since there's no response object to
    return.

    Every call is timed and logged exactly once per attempt via
    _log_classifier_call -- including the failure paths, which re-raise
    afterwards so each agent's own except blocks behave exactly as before.
    This is the single choke point all four gateway agents share, so
    instrumenting it here covers all of them without four copies of the same
    timing code.
    """
    response = None
    started = time.monotonic()
    attempt_started = started
    for attempt in range(max_retries + 1):
        attempt_started = time.monotonic()
        try:
            response = client.post(url, json=json)
        except Exception as exc:
            now = time.monotonic()
            # A timeout is the dominant real-world failure and costs a full
            # client timeout of wall-clock per image -- naming it in the log
            # (rather than lumping every transport fault under one label) is
            # what makes "the run was slow because calls hung" visible.
            is_timeout = _is_timeout(exc)
            outcome = ERROR_KIND_TIMEOUT if is_timeout else ERROR_KIND_UNREACHABLE
            _log_classifier_call(
                logger,
                agent_id,
                url,
                outcome=outcome,
                status="-",
                attempts=attempt + 1,
                elapsed_ms=int((now - started) * 1000),
                attempt_ms=int((now - attempt_started) * 1000),
                detail=type(exc).__name__,
            )
            # Only a fast connection failure is worth retrying -- see the
            # timeout-cost rationale above. Anything else (a timeout, or a
            # non-transport exception) raises immediately, same as before.
            retryable = isinstance(exc, httpx.TransportError) and not is_timeout
            if not retryable or attempt >= max_retries:
                raise
            wait = backoff_seconds * (attempt + 1)
            wait *= random.uniform(0.75, 1.25)
            if logger:
                logger.warning(
                    f"Unreachable ({exc!r}) on attempt {attempt + 1}/{max_retries + 1}, retrying in {wait:.1f}s"
                )
            time.sleep(wait)
            continue
        if response.status_code != 429:
            now = time.monotonic()
            status = response.status_code
            outcome = "ok" if 200 <= status < 300 else ERROR_KIND_GATEWAY_ERROR
            _log_classifier_call(
                logger,
                agent_id,
                url,
                outcome=outcome,
                status=status,
                attempts=attempt + 1,
                elapsed_ms=int((now - started) * 1000),
                attempt_ms=int((now - attempt_started) * 1000),
            )
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
    # Every retry exhausted and still 429.
    now = time.monotonic()
    _log_classifier_call(
        logger,
        agent_id,
        url,
        outcome=ERROR_KIND_RATE_LIMITED,
        status=429,
        attempts=max_retries + 1,
        elapsed_ms=int((now - started) * 1000),
        attempt_ms=int((now - attempt_started) * 1000),
    )
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
