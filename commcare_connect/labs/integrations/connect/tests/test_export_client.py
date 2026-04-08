"""Tests for the v2 paginated JSON export client."""
import pytest

from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError


@pytest.fixture
def client():
    return ExportAPIClient(
        base_url="https://connect.example.com",
        access_token="test-token-abc",
    )


def test_paginate_sends_version_header_and_bearer_auth(client, httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        json={"next": None, "results": [{"id": 1, "username": "alice"}]},
    )

    pages = list(client.paginate("/export/opportunity/42/user_visits/"))

    assert pages == [[{"id": 1, "username": "alice"}]]
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer test-token-abc"
    assert request.headers["Accept"] == "application/json; version=2.0"


def test_paginate_follows_next_url_until_null(client, httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
            "results": [{"id": 1}],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=2",
            "results": [{"id": 2}],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?last_id=2",
        json={"next": None, "results": [{"id": 3}]},
    )

    pages = list(client.paginate("/export/opportunity/42/user_visits/"))

    assert pages == [[{"id": 1}], [{"id": 2}], [{"id": 3}]]


def test_paginate_passes_initial_query_params(client, httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?images=true",
        json={"next": None, "results": []},
    )

    list(client.paginate("/export/opportunity/42/user_visits/", params={"images": "true"}))

    request = httpx_mock.get_request()
    assert "images=true" in str(request.url)


def test_paginate_does_not_append_params_to_next_url(client, httpx_mock):
    """The server's `next` URL already contains all preserved params; we must NOT
    re-append our initial params or we'll get duplicates."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?images=true",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?images=true&last_id=1",
            "results": [{"id": 1}],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?images=true&last_id=1",
        json={"next": None, "results": []},
    )

    list(client.paginate("/export/opportunity/42/user_visits/", params={"images": "true"}))

    second_request = httpx_mock.get_requests()[1]
    # Each param appears exactly once
    assert str(second_request.url).count("images=true") == 1
    assert str(second_request.url).count("last_id=1") == 1


def test_fetch_all_materializes_pages_into_one_list(client, httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
            "results": [{"id": 1}, {"id": 2}],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
        json={"next": None, "results": [{"id": 3}]},
    )

    rows = client.fetch_all("/export/opportunity/42/user_visits/")

    assert rows == [{"id": 1}, {"id": 2}, {"id": 3}]


def test_paginate_raises_export_api_error_on_http_error(client, httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        status_code=404,
    )

    with pytest.raises(ExportAPIError, match="404"):
        list(client.paginate("/export/opportunity/42/user_visits/"))


def test_paginate_strips_trailing_slash_from_base_url():
    client = ExportAPIClient(
        base_url="https://connect.example.com/",  # trailing slash
        access_token="t",
    )
    assert client.base_url == "https://connect.example.com"


def test_paginate_supports_absolute_path_or_endpoint(client, httpx_mock):
    """Either `/export/...` or `export/...` should resolve to the same URL."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        json={"next": None, "results": []},
    )
    list(client.paginate("export/opportunity/42/user_visits/"))


def test_context_manager_closes_underlying_client():
    with ExportAPIClient(
        base_url="https://connect.example.com",
        access_token="t",
    ) as c:
        assert c.http_client is not None
    # After exit, the client is closed; calling .get() on a closed httpx.Client raises
    import httpx  # noqa: F401

    with pytest.raises(RuntimeError):
        c.http_client.get("https://example.com")
