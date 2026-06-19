"""High-confidence browser smoke tests for the Campaign Utility Tool.

These exercise what unit tests structurally cannot: that the verbatim Babel-in-browser
React actually transpiles, mounts, fetches the bootstrap, and paints real data — and
that the auth gate redirects anonymous users. Excluded from the default suite (the
dir is in pyproject `--ignore`); run with the command in conftest.py.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


def test_anonymous_is_redirected_to_login(anon_page, live_server_url):
    anon_page.goto(f"{live_server_url}/campaign/")
    anon_page.wait_for_load_state("domcontentloaded")
    assert "/campaign/login" in anon_page.url


def test_login_page_renders(anon_page, live_server_url):
    resp = anon_page.goto(f"{live_server_url}/campaign/login/")
    assert resp.status == 200
    # The login page offers a CommCare sign-in affordance.
    assert anon_page.get_by_text(re.compile("commcare|sign in|log in", re.I)).first.is_visible()


def test_app_mounts_and_paints_overview(auth_page, live_server_url):
    auth_page.goto(f"{live_server_url}/campaign/")
    # The inline bootstrap blob is always in the served HTML.
    assert auth_page.locator("#campaign-bootstrap").count() == 1
    # React replaces the "Loading…" fallback in #root once mounted + data-loaded.
    root = auth_page.locator("#root")
    root.get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible", timeout=30_000)
    # Real seeded data is on the page: a funder short name + the currency unit.
    assert auth_page.get_by_text("Gavi").first.is_visible()
    assert auth_page.get_by_text(re.compile("₦")).first.is_visible()
    # No fatal render error fallback.
    assert auth_page.get_by_text(re.compile("Could not load campaign data", re.I)).count() == 0
