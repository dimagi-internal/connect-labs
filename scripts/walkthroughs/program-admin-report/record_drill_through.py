"""Record the program-admin-report drill-through against real labs prod
with a visible synthetic cursor.

Story arc:
  PAR grid → good-run cell → CHC Nutrition table (state-aware buttons)
          → audit photos (good run)
          → task page w/ closed OCS coaching transcript
          → incomplete-run cell → in_review audit
          → investigating task w/ in-progress coaching
          → back to aggregate

All Playwright primitives + the PAR snapshot walker + the grid cell
clicker live in ``scripts/walkthroughs/_lib/``; this file is just the
scene sequence.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.discovery import find_drill_targets  # noqa: E402
from walkthroughs._lib.grid import click_cell  # noqa: E402
from walkthroughs._lib.recorder import (  # noqa: E402
    RecorderSession,
    click_text,
    goto_and_settle,
    slow_move,
    smooth_scroll_to_text,
    snap,
    wait_for_audit_images,
    wait_for_row_count,
)

HERE = Path(__file__).resolve().parent
PAR_DEFINITION_ID = 65
OUT_DIR = Path("/tmp/par_preview/video")
MANIFEST = Path("/tmp/par_preview/scene_snapshots.json")


def main() -> None:
    ids = wcfg.read_run_ids(HERE, required=["par_run_id", "opp_id"])
    par_run_id = int(ids["par_run_id"])
    primary_opp = int(ids["opp_id"])
    par_url = (
        f"{wcfg.LABS_BASE_URL}/labs/workflow/{PAR_DEFINITION_ID}"
        f"/run/?run_id={par_run_id}&opportunity_id={primary_opp}"
    )

    with RecorderSession(
        out_dir=OUT_DIR,
        manifest_path=MANIFEST,
        prewarm=True,
        # The drill-through doesn't need confirm() dialogs (no bulk mark),
        # but accepting is harmless and matches the warm-then-record convention.
    ) as rec:
        warm = rec.warm_page
        page = rec.page

        # ---------- Discovery (NOT recorded) ----------
        goto_and_settle(warm, f"{wcfg.LABS_BASE_URL}/labs/workflow/", timeout=30_000, settle_seconds=0)
        targets = find_drill_targets(
            warm.request.get,
            par_run_id,
            labs_base_url=wcfg.LABS_BASE_URL,
            primary_opp_id=primary_opp,
        )
        good = targets["good"]
        bad = targets["incomplete"]
        print(f"PAR run {par_run_id}")
        print(
            f"  good:       {good['opp_label']} Wk{good['week_idx']+1} "
            f"flw={good['flw_id']}  audit #{good['audit_id']}, task #{good['task_id']}"
        )
        print(
            f"  incomplete: {bad['opp_label']} Wk{bad['week_idx']+1} "
            f"flw={bad['flw_id']}  audit #{bad['audit_id']}, task #{bad['task_id']}"
        )

        good_audit_url = f"{wcfg.LABS_BASE_URL}/audit/{good['audit_id']}/?opportunity_id={good['opp_id']}"
        bad_audit_url = f"{wcfg.LABS_BASE_URL}/audit/{bad['audit_id']}/?opportunity_id={bad['opp_id']}"
        good_task_url = f"{wcfg.LABS_BASE_URL}/tasks/{good['task_id']}/edit/" f"?opportunity_id={good['opp_id']}"
        bad_task_url = f"{wcfg.LABS_BASE_URL}/tasks/{bad['task_id']}/edit/" f"?opportunity_id={bad['opp_id']}"

        # Pre-warm: visit each page so labs caches the bulk-assessment
        # JPGs from GDrive on first hit; the recorded second hit is instant.
        # Use goto_and_settle — labs has background polling that prevents
        # networkidle from ever firing on the PAR page.
        print("Pre-warming target pages...")
        for url in (par_url, good_audit_url, bad_audit_url, good_task_url, bad_task_url):
            try:
                goto_and_settle(warm, url, timeout=30_000, settle_seconds=0.5)
            except Exception as e:
                print(f"  ! pre-warm {url}: {e}")
                continue
            if "/audit/" in url:
                wait_for_audit_images(warm, at_least=1, timeout_ms=15_000)
        print("  pre-warm done\n")

        # ---------- Recording ----------
        goto_and_settle(
            page,
            par_url,
            timeout=60_000,
            wait_for_selector="text=Window aggregate",
            settle_seconds=0,
        )
        slow_move(page, 50, 60, steps=20)
        time.sleep(1.0)

        # Scene 1: grid overview.
        time.sleep(2.0)
        snap(rec, "par_grid")

        # Scene 2: open the "good run" cell → inline detail.
        click_cell(page, good["opp_label"], good["week_idx"])
        time.sleep(3.0)
        snap(rec, "par_detail_good")

        # Scene 2b: drill through "Open the run" → CHC Nutrition table.
        click_text(page, "Open the run", post_wait_selector="text=FLW-Level Analysis")
        wait_for_row_count(page, at_least=5, timeout_ms=12_000)
        # Scroll to the flagged row + park the cursor on View Audit.
        # The chc_nutrition Actions cell now flips per-row to "View Audit"
        # (title-case) when the row already has an audit — that's what we
        # want to click here for the drill-through scenario. PR #289
        # added the state-aware flip via view.auditsFor(); pre-#289 builds
        # rendered the lowercase "View audit" button.
        page.evaluate(
            """() => {
                const flagged = Array.from(document.querySelectorAll('tr'))
                    .find(tr => tr.textContent.includes('View Audit'));
                if (flagged) flagged.scrollIntoView({behavior: 'smooth', block: 'center'});
            }"""
        )
        view_audit = page.locator("text=View Audit").first
        if view_audit.count() > 0:
            box = view_audit.bounding_box()
            if box:
                slow_move(
                    page,
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                    steps=25,
                )
        time.sleep(2.5)
        snap(rec, "chc_table_state_aware")

        # Click View audit to drill into the audit page.
        if view_audit.count() > 0:
            view_audit.click()
            # No networkidle wait — bulk-assessment streams GDrive JPGs and
            # never lets the network settle. The selector wait IS the signal.
            try:
                page.wait_for_selector("text=Showing 5 assessment(s)", timeout=10_000)
            except Exception:
                pass

        # Scene 3: dwell on the audit page; wait for thumbnails.
        wait_for_audit_images(page, at_least=3, timeout_ms=15_000)
        time.sleep(4.0)
        snap(rec, "audit_good_run")

        # Scene 4: back to CHC Nutrition table, click View Task → coaching transcript.
        # Same state-aware flip — when the row has an existing task, the
        # Actions cell renders "View Task" (title-case) in place of the
        # "Create Task ▾" menu (post PR #289).
        page.go_back(wait_until="domcontentloaded")
        wait_for_row_count(page, at_least=5, timeout_ms=8_000)
        time.sleep(0.6)
        view_task = page.locator("text=View Task").first
        if view_task.count() > 0:
            box = view_task.bounding_box()
            if box:
                slow_move(
                    page,
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                    steps=25,
                )
                time.sleep(0.4)
            view_task.click()
            try:
                page.wait_for_selector("text=Closed", timeout=8_000)
            except Exception:
                pass
        else:
            # Fallback: re-drill via PAR detail panel.
            page.go_back(wait_until="domcontentloaded")
            time.sleep(0.6)
            click_cell(page, good["opp_label"], good["week_idx"])
            time.sleep(0.6)
            click_text(
                page,
                f"Task #{good['task_id']}",
                post_wait_selector="text=Closed",
            )
        time.sleep(2.5)
        smooth_scroll_to_text(page, "Coaching Conversation")
        time.sleep(7.5)
        page.evaluate("() => window.scrollBy({top: 350, left: 0, behavior: 'smooth'})")
        time.sleep(3.5)
        snap(rec, "task_good_run")

        # Scene 5: back to PAR, jump to incomplete-run cell.
        goto_and_settle(
            page,
            par_url,
            timeout=30_000,
            wait_for_selector="text=Window aggregate",
            settle_seconds=0.6,
        )
        click_cell(page, bad["opp_label"], bad["week_idx"])
        time.sleep(2.5)
        snap(rec, "par_detail_incomplete")

        # Scene 6: in_review audit — at least 2 visible thumbnails (some
        # may still be pending placeholder cards in this state).
        click_text(
            page,
            f"Audit #{bad['audit_id']}",
            post_wait_selector="text=Save Progress",
        )
        wait_for_audit_images(page, at_least=2, timeout_ms=12_000)
        time.sleep(4.0)
        snap(rec, "audit_in_review")

        # Scene 7: back, drill into the investigating task.
        goto_and_settle(
            page,
            par_url,
            timeout=30_000,
            wait_for_selector="text=Window aggregate",
            settle_seconds=0.6,
        )
        click_cell(page, bad["opp_label"], bad["week_idx"])
        time.sleep(0.6)
        click_text(
            page,
            f"Task #{bad['task_id']}",
            post_wait_selector="text=Close Task",
        )
        time.sleep(2.5)
        smooth_scroll_to_text(page, "Coaching Conversation")
        time.sleep(6.0)
        snap(rec, "task_investigating")

        # Scene 8: back to PAR aggregate linger.
        page.go_back(wait_until="domcontentloaded")
        time.sleep(0.6)
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        slow_move(page, 1310, 240)
        time.sleep(3.0)


if __name__ == "__main__":
    main()
