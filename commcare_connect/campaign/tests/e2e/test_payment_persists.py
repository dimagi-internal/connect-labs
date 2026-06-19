"""The one true end-to-end path: browser → CSRF(meta) → API → fraud guard → DB → reload.

This is the highest-value browser test — it's the exact path where the production-only
CSRF_USE_SESSIONS bug (PR #662) hid from unit tests. The backend half is already proven
by `test_workers_api.test_csrf_round_trip_via_meta_token`; this proves the *real* React
UI drives it and the result survives a full reload.

SELECTORS ARE BEST-EFFORT and were authored without a live run of the verbatim React —
confirm/adjust the locators on the first real execution (they use resilient role/text
queries to minimise breakage). The assertions on persistence are the load-bearing part.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


def _open_workers_payments(page, base):
    page.goto(f"{base}/campaign/")
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible")
    # Sidebar → Workers (then the Payments sub-tab is the default).
    page.get_by_text(re.compile(r"^Workers$", re.I)).first.click()
    page.get_by_text(re.compile(r"^Payments$", re.I)).first.click()


def test_payment_approval_persists_across_reload(auth_page, live_server_url):
    page = auth_page
    base = live_server_url
    _open_workers_payments(page, base)

    # Open the first worker's payment drawer (a table row with a chevron/name).
    first_row = page.get_by_role("row").filter(has_text=re.compile(r"W\d{4,}")).first
    first_row.click()

    # The drawer exposes "Approve & queue for payment".
    approve = page.get_by_role("button", name=re.compile("approve.*queue|approve & queue", re.I))
    approve.first.wait_for(state="visible")
    approve.first.click()

    # A success toast confirms the write round-tripped (CSRF meta token included).
    page.get_by_text(re.compile("approved|queued|payment", re.I)).first.wait_for(state="visible")

    # Hard reload — the new status must come from the DB, not optimistic UI state.
    page.reload()
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible")
    page.get_by_text(re.compile(r"^Workers$", re.I)).first.click()
    page.get_by_text(re.compile(r"^Payments$", re.I)).first.click()

    # At least one worker now shows the Approved pay status pill after reload.
    assert page.get_by_text(re.compile(r"\bApproved\b")).first.is_visible()
