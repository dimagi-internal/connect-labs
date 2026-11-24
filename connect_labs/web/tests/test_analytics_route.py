"""Umami gets the matched URL pattern, never the request path.

Labs paths carry frontline-worker usernames and record ids
(``/labs/p/user/<username>/<page>/``); what the analytics config hands
the browser must be the urlconf's template, so those never reach Umami.
"""

from django.test import RequestFactory, override_settings
from django.urls import resolve

from connect_labs.web.context_processors import analytics_context, analytics_route


def _request(path):
    request = RequestFactory().get(path + "?username=flw_jane&opportunity_id=812")
    request.resolver_match = resolve(path)
    return request


def test_route_is_the_pattern_with_named_placeholders():
    request = _request("/labs/p/user/flw_jane/demo/")
    assert analytics_route(request) == "/labs/p/user/{username}/{page}/"


def test_route_never_carries_the_identifier_or_query():
    request = _request("/labs/p/user/flw_jane/W-12345/")
    route = analytics_route(request)
    assert route == "/labs/p/user/{username}/{page}/"
    assert "W-12345" not in route and "flw_jane" not in route and "?" not in route


def test_unresolved_request_gives_none():
    request = RequestFactory().get("/nowhere/jane/")
    assert analytics_route(request) is None


@override_settings(UMAMI_HOST_URL="https://labs.example/umami", UMAMI_WEBSITE_ID="w-1")
def test_analytics_context_exposes_route():
    from django.contrib.auth.models import AnonymousUser

    request = _request("/labs/p/user/flw_jane/demo/")
    request.user = AnonymousUser()
    cfg = analytics_context(request)["ANALYTICS_VARS_JSON"]
    assert cfg["route"] == "/labs/p/user/{username}/{page}/"
    assert cfg["username"] is None
