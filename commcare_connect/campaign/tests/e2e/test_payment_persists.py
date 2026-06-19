"""The one true end-to-end path: browser → CSRF(meta) → API → fraud guard → DB → reload.

This is the highest-value browser test — it's the exact path where the production-only
CSRF_USE_SESSIONS bug (PR #662) hid from unit tests. It approves a known clean,
not-yet-approved worker in the real UI and proves the new status survives a full page
reload (i.e. it was persisted server-side, not just held in optimistic client state).
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


def _open_payments(page, base):
    page.goto(f"{base}/campaign/")
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible")
    page.get_by_text(re.compile(r"^Workers$")).first.click()  # default sub-tab is Worker Payments
    page.wait_for_timeout(600)


def _row_for(page, wid):
    return page.get_by_role("row").filter(has_text=wid).first


def test_payment_approval_persists_across_reload(auth_page, live_server_url, approvable_worker):
    page = auth_page
    base = live_server_url
    wid = approvable_worker.worker_id

    _open_payments(page, base)
    _row_for(page, wid).click()  # open this worker's payment drawer

    approve = page.get_by_role("button", name=re.compile(r"approve.*queue", re.I)).first
    approve.wait_for(state="visible")
    approve.click()

    # Let the write round-trip (browser → CSRF meta → API → DB) settle.
    page.wait_for_timeout(1200)

    # Hard reload — status must come from the DB, not optimistic UI state.
    page.reload()
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible")
    page.get_by_text(re.compile(r"^Workers$")).first.click()
    page.wait_for_timeout(600)

    persisted = _row_for(page, wid)
    persisted.wait_for(state="visible")
    assert re.search(r"\bApproved\b", persisted.inner_text()), persisted.inner_text()
