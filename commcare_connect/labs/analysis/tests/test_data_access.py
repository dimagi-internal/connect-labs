"""Tests for analysis/data_access.py FLW fetching."""
import pytest
from django.core.cache import cache

from commcare_connect.labs.analysis.data_access import fetch_flw_names

# get_export_client queries SyntheticOpportunity to route real vs fixture reads,
# so these tests need DB access even though they mock HTTP.
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_fetch_flw_names_paginates_and_builds_mapping(httpx_mock, settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example.com"

    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_data/?page_size=2500",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_data/?last_id=2",
            "results": [
                {"username": "alice", "name": "Alice A", "last_active": "2026-04-01"},
                {"username": "bob", "name": "", "last_active": "2026-04-02"},
            ],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_data/?last_id=2",
        json={
            "next": None,
            "results": [{"username": "carol", "name": "Carol C"}],
        },
    )

    last_active: dict[str, str] = {}
    result = fetch_flw_names(
        access_token="t",
        opportunity_id=42,
        use_cache=False,
        last_active_out=last_active,
    )

    assert result == {"alice": "Alice A", "bob": "bob", "carol": "Carol C"}
    assert last_active == {"alice": "2026-04-01", "bob": "2026-04-02"}


def test_fetch_flw_names_raises_on_api_error(httpx_mock, settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example.com"
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_data/?page_size=2500",
        status_code=500,
    )

    with pytest.raises(RuntimeError, match="Connect export API error"):
        fetch_flw_names(access_token="t", opportunity_id=42, use_cache=False)
