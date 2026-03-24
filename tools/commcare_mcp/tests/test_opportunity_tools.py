"""Tests for opportunity-related MCP tools.

Tests get_opportunity_apps org_slug field and get_opportunity_url tool.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the commcare_mcp directory to path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture()
def sample_api_response():
    """Simulated Connect export API response for an opportunity."""
    return {
        "id": 874,
        "name": "Test Opportunity",
        "organization": "test-org",
        "learn_app": {
            "cc_domain": "test-domain",
            "cc_app_id": "learn-app-123",
            "name": "Learn App",
        },
        "deliver_app": {
            "cc_domain": "test-domain",
            "cc_app_id": "deliver-app-456",
            "name": "Deliver App",
        },
    }


@pytest.fixture()
def sample_api_response_no_org():
    """Simulated Connect export API response with no organization."""
    return {
        "id": 999,
        "name": "No Org Opportunity",
        "learn_app": None,
        "deliver_app": None,
    }


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the opportunity cache before each test."""
    import connect_client

    connect_client._opportunity_cache.clear()
    yield
    connect_client._opportunity_cache.clear()


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_mock_client(api_response):
    """Create a mock httpx.AsyncClient that returns the given response."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = api_response
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


class TestGetOpportunityApps:
    """Tests for get_opportunity_apps returning org_slug."""

    def test_includes_org_slug(self, sample_api_response):
        """get_opportunity_apps should include org_slug from API 'organization' field."""
        mock_client = _make_mock_client(sample_api_response)

        with (
            patch("connect_client._get_connect_token", return_value="fake-token"),
            patch("connect_client.httpx.AsyncClient", return_value=mock_client),
        ):
            import connect_client

            result = _run(connect_client.get_opportunity_apps(874))

        assert result["org_slug"] == "test-org"
        assert result["opportunity_id"] == 874
        assert result["opportunity_name"] == "Test Opportunity"
        assert result["learn_app"]["cc_domain"] == "test-domain"
        assert result["deliver_app"]["cc_app_id"] == "deliver-app-456"

    def test_org_slug_empty_when_missing(self, sample_api_response_no_org):
        """org_slug should be empty string when API response has no organization."""
        mock_client = _make_mock_client(sample_api_response_no_org)

        with (
            patch("connect_client._get_connect_token", return_value="fake-token"),
            patch("connect_client.httpx.AsyncClient", return_value=mock_client),
        ):
            import connect_client

            result = _run(connect_client.get_opportunity_apps(999))

        assert result["org_slug"] == ""


class TestGetOpportunityUrl:
    """Tests for get_opportunity_url MCP tool."""

    def test_returns_correct_url(self):
        """get_opportunity_url should build the correct Connect URL."""
        mock_opp = {
            "opportunity_id": 874,
            "opportunity_name": "Test Opportunity",
            "org_slug": "test-org",
            "learn_app": None,
            "deliver_app": None,
        }

        with patch("connect_client.get_opportunity_apps", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_opp

            import server

            importlib.reload(server)
            result = _run(server.get_opportunity_url(874))

        assert result["url"] == "https://connect.dimagi.com/a/test-org/opportunity/874/"
        assert result["opportunity_id"] == 874
        assert result["opportunity_name"] == "Test Opportunity"
        assert result["org_slug"] == "test-org"

    def test_error_when_no_org_slug(self):
        """get_opportunity_url should return error when no org_slug available."""
        mock_opp = {
            "opportunity_id": 999,
            "opportunity_name": "No Org",
            "org_slug": "",
            "learn_app": None,
            "deliver_app": None,
        }

        with patch("connect_client.get_opportunity_apps", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_opp

            import server

            importlib.reload(server)
            result = _run(server.get_opportunity_url(999))

        assert "error" in result
        assert "no organization slug" in result["error"]

    def test_uses_custom_connect_url(self):
        """get_opportunity_url should use CONNECT_URL from connect_client."""
        mock_opp = {
            "opportunity_id": 874,
            "opportunity_name": "Test",
            "org_slug": "custom-org",
            "learn_app": None,
            "deliver_app": None,
        }

        with (
            patch("connect_client.get_opportunity_apps", new_callable=AsyncMock) as mock_get,
            patch("connect_client.CONNECT_URL", "https://staging.connect.dimagi.com"),
        ):
            mock_get.return_value = mock_opp

            import server

            importlib.reload(server)
            result = _run(server.get_opportunity_url(874))

        assert result["url"] == "https://staging.connect.dimagi.com/a/custom-org/opportunity/874/"
