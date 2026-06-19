"""Plan 4 end-to-end: syncing an activity to CommCare persists across a reload.

Mirrors the live-verified Plan 4 flow (activity sync count moved and stuck). Each
unsynced activity row shows a "Sync" button; syncing one removes its button. We assert
the count of Sync buttons drops by one and stays dropped after a full reload — i.e. the
`synced` flag was written server-side, not just toggled in client state.
"""
from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


def _open_activity(page, base):
    page.goto(f"{base}/campaign/")
    page.locator("#root").get_by_text(re.compile("Measles", re.I)).first.wait_for(state="visible", timeout=30_000)
    page.get_by_text(re.compile(r"^Activity$")).first.click()
    page.get_by_text("Activity Details").first.wait_for(state="visible")
    page.wait_for_timeout(500)


def _sync_buttons(page):
    # The per-row control is a <button> whose text is exactly "Sync" (already-synced
    # rows show a non-button "Synced" indicator). Filter by role to avoid matching
    # stray "Sync" text (e.g. a column header).
    return page.get_by_role("button").filter(has_text=re.compile(r"^Sync$"))


def test_activity_sync_persists_across_reload(auth_page, live_server_url):
    page = auth_page
    base = live_server_url

    _open_activity(page, base)
    before = _sync_buttons(page).count()
    assert before >= 1, "seed should include at least one unsynced activity"

    _sync_buttons(page).first.click()
    page.wait_for_timeout(1200)  # let the write round-trip

    page.reload()
    _open_activity(page, base)
    after = _sync_buttons(page).count()
    assert after == before - 1, f"expected one fewer unsynced activity after reload (before={before}, after={after})"
