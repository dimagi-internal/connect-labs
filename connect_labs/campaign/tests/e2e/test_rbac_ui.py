"""RBAC reflected in the real UI navigation.

The server is the true gate (covered exhaustively in test_endpoint_rbac.py); this
confirms the show/hide layer (`perms.js` via `app.jsx`'s ROLE_DISPLAY bridge) actually
hides admin-only nav from non-admins in a live browser — the layer whose drift the
contract test (test_rbac_contract.py) guards.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


def _wait_app(page, base):
    page.goto(f"{base}/campaign/")
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible", timeout=30_000)


def test_admin_sees_system_administration(page_as, live_server_url):
    page = page_as("campaign_admin")
    _wait_app(page, live_server_url)
    assert page.get_by_text(re.compile(r"^System Administration$")).count() >= 1


def test_reporting_user_cannot_see_system_administration(page_as, live_server_url):
    page = page_as("reporting_user")
    _wait_app(page, live_server_url)
    # Admin-only nav is hidden...
    assert page.get_by_text(re.compile(r"^System Administration$")).count() == 0
    # ...but view-level + public nav remain.
    assert page.get_by_text(re.compile(r"^Workers$")).count() >= 1
    assert page.get_by_text(re.compile(r"^Training Hub$")).count() >= 1


def test_payment_admin_cannot_see_system_administration(page_as, live_server_url):
    page = page_as("payment_admin")
    _wait_app(page, live_server_url)
    assert page.get_by_text(re.compile(r"^System Administration$")).count() == 0
