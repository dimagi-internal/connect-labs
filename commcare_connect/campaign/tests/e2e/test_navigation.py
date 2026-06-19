"""Every main tab mounts, paints its expected content, and throws no JS errors.

Catches the failure mode unit tests can't: a verbatim-React tab that fails to transpile
or references a missing serializer key (blank/throwing UI). Built tabs assert real
content (Reporting was built in Plan 5, Training Hub in Plan 6).
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e

# nav label -> a stable string that proves the tab rendered
TABS = [
    ("Overview", "Campaign progress"),
    ("Workers", "Worker Payments"),
    ("Activity", "Activity Details"),
    ("Reporting & Monitoring", "Cumulative enrollment"),  # Plan 5 built this tab
    ("System Administration", "User Management"),
    ("Training Hub", "Training videos and learning materials"),  # Plan 6 built this tab
]


def _wait_app(page, base):
    page.goto(f"{base}/campaign/")
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible", timeout=30_000)


@pytest.mark.parametrize("label,expected", TABS, ids=[t[0] for t in TABS])
def test_tab_renders_without_errors(auth_page, live_server_url, label, expected):
    page = auth_page
    page_errors = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    _wait_app(page, live_server_url)

    page.get_by_text(re.compile(rf"^{re.escape(label)}$")).first.click()
    page.get_by_text(expected).first.wait_for(state="visible", timeout=15_000)

    assert page.get_by_text(re.compile("Could not load campaign data", re.I)).count() == 0
    assert not page_errors, f"{label} raised JS errors: {page_errors}"
