"""Every gateway agent must classify WHY a call failed, not just that it did.

The auditor-facing text stays deliberately generic (see base.py), so
``ReviewResult.details["error_kind"]`` is the only machine-readable cause a run
summary can tally. Before it existed, an "errors=160" run summary was equally
consistent with a dead backend, a saturated one, and a misconfigured API key.

The timeout case is called out separately from the connect-failure case because
it was previously reported as "Could not reach the AI classifier service" --
inaccurate (the gateway accepted the request and never answered) and, since it
costs a full client timeout of wall clock per image, the single most important
failure to be able to name.
"""
import httpx
import pytest
from django.test import override_settings

from connect_labs.labs.ai_review_agents.agents.muac_match import MUACMatchAgent
from connect_labs.labs.ai_review_agents.agents.muac_overzoom import MUACOverzoomAgent
from connect_labs.labs.ai_review_agents.agents.scale_dial_validation import ScaleDialValidationAgent
from connect_labs.labs.ai_review_agents.agents.scale_validation import ScaleValidationAgent
from connect_labs.labs.ai_review_agents.base import (
    ERROR_KIND_GATEWAY_ERROR,
    ERROR_KIND_NOT_CONFIGURED,
    ERROR_KIND_RATE_LIMITED,
    ERROR_KIND_TIMEOUT,
    ERROR_KIND_UNREACHABLE,
    GATEWAY_TIMEOUT_MESSAGE,
)
from connect_labs.labs.ai_review_agents.types import ReviewContext

AGENTS = [ScaleValidationAgent, MUACMatchAgent, MUACOverzoomAgent, ScaleDialValidationAgent]


class _Client:
    """Minimal stand-in for the agent's httpx.Client."""

    def __init__(self, *, raises=None, status=200, payload=None):
        self._raises = raises
        self._status = status
        self._payload = payload if payload is not None else {}

    def post(self, url, json=None):
        if self._raises is not None:
            raise self._raises
        request = httpx.Request("POST", url)
        return httpx.Response(self._status, json=self._payload, request=request)


def _context():
    # Every agent takes its image from whichever key it prefers, falling back to
    # the first available -- one context satisfies all four.
    return ReviewContext(images={"scale": b"\xff\xd8jpeg", "muac": b"\xff\xd8jpeg"}, form_data={"reading": "1535"})


def _review_with(agent_cls, client):
    agent = agent_cls()
    agent._client = client
    return agent.review(_context())


@pytest.mark.parametrize("agent_cls", AGENTS, ids=lambda c: c.agent_id)
@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_read_timeout_is_reported_as_a_timeout(agent_cls):
    result = _review_with(agent_cls, _Client(raises=httpx.ReadTimeout("The read operation timed out")))

    assert result.status.value == "error"
    assert result.details["error_kind"] == ERROR_KIND_TIMEOUT
    assert result.errors == [GATEWAY_TIMEOUT_MESSAGE]


@pytest.mark.parametrize("agent_cls", AGENTS, ids=lambda c: c.agent_id)
@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_connect_error_stays_unreachable(agent_cls):
    """The timeout branch is ordered ahead of the generic httpx.HTTPError branch
    it subclasses -- this pins that it did not swallow genuine connect failures
    on the way past."""
    result = _review_with(agent_cls, _Client(raises=httpx.ConnectError("no route")))

    assert result.details["error_kind"] == ERROR_KIND_UNREACHABLE


@pytest.mark.parametrize("agent_cls", AGENTS, ids=lambda c: c.agent_id)
@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_server_error_is_reported_as_a_gateway_error(agent_cls):
    result = _review_with(agent_cls, _Client(status=500, payload={"details": "boom"}))

    assert result.details["error_kind"] == ERROR_KIND_GATEWAY_ERROR


@pytest.mark.parametrize("agent_cls", AGENTS, ids=lambda c: c.agent_id)
@override_settings(SCALE_VALIDATION_API_KEY="test-key")
def test_exhausted_rate_limit_is_reported_as_rate_limited(agent_cls, monkeypatch):
    from connect_labs.labs.ai_review_agents import base

    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    result = _review_with(agent_cls, _Client(status=429))

    assert result.details["error_kind"] == ERROR_KIND_RATE_LIMITED


@pytest.mark.parametrize("agent_cls", AGENTS, ids=lambda c: c.agent_id)
@override_settings(SCALE_VALIDATION_API_KEY="")
def test_missing_api_key_is_reported_as_not_configured(agent_cls):
    result = _review_with(agent_cls, _Client())

    assert result.details["error_kind"] == ERROR_KIND_NOT_CONFIGURED
