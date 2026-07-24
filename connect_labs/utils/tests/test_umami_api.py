from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from connect_labs.utils import umami_api


@pytest.fixture(autouse=True)
def configured(settings):
    settings.UMAMI_HOST_URL = "https://labs.example.com/umami"
    settings.UMAMI_WEBSITE_ID = "site-123"
    settings.UMAMI_ADMIN_USERNAME = "admin"
    settings.UMAMI_ADMIN_PASSWORD = "pw"
    cache.delete(umami_api.TOKEN_CACHE_KEY)
    yield
    cache.delete(umami_api.TOKEN_CACHE_KEY)


def _response(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    return r


def test_not_configured_raises(settings):
    settings.UMAMI_ADMIN_PASSWORD = ""
    assert not umami_api.is_configured()
    with pytest.raises(umami_api.UmamiAPIError):
        umami_api.website_stats(0, 1)


def test_login_and_stats():
    with patch("connect_labs.utils.umami_api.httpx.post", return_value=_response(200, {"token": "tok"})) as post:
        with patch("connect_labs.utils.umami_api.httpx.get", return_value=_response(200, {"pageviews": 5})) as get:
            data = umami_api.website_stats(0, 1)
    assert data == {"pageviews": 5}
    assert post.call_args.kwargs["json"]["username"] == "admin"
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_reauth_on_401():
    cache.set(umami_api.TOKEN_CACHE_KEY, "stale")
    with patch("connect_labs.utils.umami_api.httpx.post", return_value=_response(200, {"token": "fresh"})):
        with patch(
            "connect_labs.utils.umami_api.httpx.get",
            side_effect=[_response(401), _response(200, [{"x": "/labs/", "y": 9}])],
        ):
            data = umami_api.metrics("url", 0, 1)
    assert data[0]["y"] == 9


def test_active_visitors_handles_both_shapes():
    with patch("connect_labs.utils.umami_api.httpx.post", return_value=_response(200, {"token": "tok"})):
        with patch("connect_labs.utils.umami_api.httpx.get", return_value=_response(200, {"visitors": 3})):
            assert umami_api.active_visitors() == 3
        cache.set(umami_api.TOKEN_CACHE_KEY, "tok")
        with patch("connect_labs.utils.umami_api.httpx.get", return_value=_response(200, [{"x": 2}])):
            assert umami_api.active_visitors() == 2
