"""Umami gets the matched URL pattern, never the request path.

Labs paths carry frontline-worker usernames and record ids
(``/campaign/api/users/<username>/role/``); what the analytics config hands
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
    request = _request("/campaign/api/users/flw_jane/role/")
    assert analytics_route(request) == "/campaign/api/users/{username}/role/"


def test_route_never_carries_the_identifier_or_query():
    request = _request("/campaign/api/kyc/W-12345/status/")
    route = analytics_route(request)
    assert route == "/campaign/api/kyc/{worker_id}/status/"
    assert "W-12345" not in route and "flw_jane" not in route and "?" not in route


def test_unresolved_request_gives_none():
    request = RequestFactory().get("/nowhere/jane/")
    assert analytics_route(request) is None


@override_settings(UMAMI_HOST_URL="https://labs.example/umami", UMAMI_WEBSITE_ID="w-1")
def test_analytics_context_exposes_route():
    from django.contrib.auth.models import AnonymousUser

    request = _request("/campaign/api/users/flw_jane/role/")
    request.user = AnonymousUser()
    cfg = analytics_context(request)["ANALYTICS_VARS_JSON"]
    assert cfg["route"] == "/campaign/api/users/{username}/role/"
    assert cfg["username"] is None
