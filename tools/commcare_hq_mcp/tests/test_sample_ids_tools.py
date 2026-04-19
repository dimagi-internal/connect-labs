"""Tests for the get_sample_ids MCP tool."""

from __future__ import annotations

import asyncio
import concurrent.futures
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

# Add the commcare_mcp package to sys.path so imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sample_ids_tools import get_sample_ids  # noqa: E402


def _run(coro):
    """Run an async coroutine synchronously, safe when event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If loop is already running (e.g. Playwright left one), use a new thread
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


def _make_response(json_data, status_code=200):
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("GET", "https://example.com"),
    )


def test_get_sample_ids_returns_all_categories():
    """get_sample_ids returns funds, solicitations, and programs."""
    org_data = {
        "programs": [
            {"id": 42, "name": "CHC Nigeria"},
            {"id": 68, "name": "MBW Kenya"},
        ],
        "organizations": [],
        "opportunities": [],
    }
    solicitation_records = [
        {"id": 101, "data": {"title": "CHC EOI Round 2"}},
        {"id": 102, "data": {"title": "MBW RFP Phase 1"}},
    ]
    fund_records = [
        {"id": 201, "data": {"name": "ECF", "funder_slug": "ecf"}},
        {"id": 202, "data": {"name": "GiveWell", "funder_slug": "givewell"}},
    ]

    async def mock_get(url, **kwargs):
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        params = kwargs.get("params", {})
        if params.get("type") == "solicitation":
            return _make_response(solicitation_records)
        if params.get("type") == "fund":
            return _make_response(fund_records)
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            result = _run(get_sample_ids())

    assert "funds" in result
    assert "solicitations" in result
    assert "programs" in result

    assert len(result["programs"]) == 2
    assert result["programs"][0] == {"id": 42, "name": "CHC Nigeria"}
    assert result["programs"][1] == {"id": 68, "name": "MBW Kenya"}

    assert len(result["solicitations"]) == 2
    assert result["solicitations"][0] == {"id": 101, "name": "CHC EOI Round 2"}
    assert result["solicitations"][1] == {"id": 102, "name": "MBW RFP Phase 1"}

    assert len(result["funds"]) == 2
    assert result["funds"][0] == {"id": 201, "name": "ECF"}
    assert result["funds"][1] == {"id": 202, "name": "GiveWell"}


def test_get_sample_ids_limits_to_five():
    """Results are capped at 5 per category."""
    org_data = {
        "programs": [{"id": i, "name": f"Program {i}"} for i in range(10)],
        "organizations": [],
        "opportunities": [],
    }

    async def mock_get(url, **kwargs):
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            result = _run(get_sample_ids())

    assert len(result["programs"]) == 5


def test_get_sample_ids_handles_empty_data():
    """Returns empty lists when no data is available."""
    org_data = {"programs": [], "organizations": [], "opportunities": []}

    async def mock_get(url, **kwargs):
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            result = _run(get_sample_ids())

    assert result == {"funds": [], "solicitations": [], "programs": []}


def test_get_sample_ids_uses_program_id_for_scoping():
    """Solicitation and fund queries use the first program_id for API scoping."""
    org_data = {
        "programs": [{"id": 42, "name": "CHC Nigeria"}],
        "organizations": [],
        "opportunities": [],
    }

    captured_params: list[dict] = []

    async def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        captured_params.append(dict(params))
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            _run(get_sample_ids())

    # The solicitation and fund queries should include program_id=42
    sol_params = [p for p in captured_params if p.get("type") == "solicitation"]
    fund_params = [p for p in captured_params if p.get("type") == "fund"]

    assert len(sol_params) == 1
    assert sol_params[0]["program_id"] == "42"
    assert len(fund_params) == 1
    assert fund_params[0]["program_id"] == "42"


def test_get_sample_ids_fallback_names():
    """Uses fallback name fields when 'title'/'name' is missing."""
    org_data = {
        "programs": [{"id": 1, "slug": "chc-slug"}],
        "organizations": [],
        "opportunities": [],
    }
    solicitation_records = [
        {"id": 10, "data": {}},
    ]
    fund_records = [
        {"id": 20, "data": {"funder_slug": "ecf"}},
    ]

    async def mock_get(url, **kwargs):
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        params = kwargs.get("params", {})
        if params.get("type") == "solicitation":
            return _make_response(solicitation_records)
        if params.get("type") == "fund":
            return _make_response(fund_records)
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            result = _run(get_sample_ids())

    assert result["programs"][0] == {"id": 1, "name": "chc-slug"}
    assert result["solicitations"][0] == {"id": 10, "name": "Solicitation 10"}
    assert result["funds"][0] == {"id": 20, "name": "ecf"}


def test_get_sample_ids_partial_api_failure():
    """Gracefully handles partial API failures."""
    org_data = {
        "programs": [{"id": 42, "name": "CHC Nigeria"}],
        "organizations": [],
        "opportunities": [],
    }

    async def mock_get(url, **kwargs):
        if "opp_org_program_list" in str(url):
            return _make_response(org_data)
        params = kwargs.get("params", {})
        if params.get("type") == "solicitation":
            return _make_response([{"id": 101, "data": {"title": "Test Sol"}}])
        if params.get("type") == "fund":
            raise httpx.ConnectError("Connection refused")
        return _make_response([])

    with patch("sample_ids_tools._get_connect_token", return_value="fake-token"):
        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = MockClient.return_value.__aenter__.return_value
            mock_instance.get = mock_get

            result = _run(get_sample_ids())

    assert len(result["programs"]) == 1
    assert len(result["solicitations"]) == 1
    assert result["funds"] == []
