"""Host-contract tests: the supply app must coexist with the labs host.

Mirrors the campaign app's host-integration approach (see campaign PR #661
lesson): any upstream OAuth-session middleware in the host project must skip
this app's path prefix, or host auth reconciliation will destroy supply
sessions.
"""
import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_ping_unauthenticated():
    resp = Client().get("/supply/ping/")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_labs_oauth_middleware_skips_supply():
    from connect_labs.labs import oauth_session

    assert any(p.rstrip("/") == "/supply" for p in oauth_session._SKIP_PATH_PREFIXES)
