"""Record the manager-flow prepend: 10 scenes covering Northern Wk4
in_progress → bulk Mark No Issue → Create Audit on jumoke_n → audit page →
back to review → Create Task with Coaching → task page with open OCS
conversation. Output: drill_manager_prepend.webm.

After this records cleanly the recorder for the existing 9-scene PAR
drill-through (record_real_labs.py) runs second; the two webms are
concatenated to produce the final video.
"""

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

LABS = "https://labs.connect.dimagi.com"
STATE = "/Users/acedimagi/.ace/labs-session.json"
OUT_DIR = Path("/tmp/par_preview/video_manager")
OUT_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOTS = Path("/tmp/par_preview/manager_snapshot_manifest.json")

CURSOR_JS = Path("/tmp/par_preview/cursor_overlay.js").read_text() if Path("/tmp/par_preview/cursor_overlay.js").exists() else ""


def discover_run_ids(req, par_run_id):
    """Resolve the Wk4 in_progress run id + opp/workflow_def from env or
    direct lookup. PAR snapshots only include completed runs, so we can't
    find the in_progress one via PAR — caller must pass WK4_RUN_ID."""
    wk4_run_id = int(os.environ["WK4_RUN_ID"])
    return {
        "wk4_run_id": wk4_run_id,
        "opp_id": int(os.environ.get("OPP_ID", "10000")),
        "workflow_def_id": int(os.environ.get("WORKFLOW_DEF_ID", "1506")),
    }


def snapshot_dump(page, key, captures):
    captures[key] = page.inner_text("body")
    Path(SNAPSHOTS).write_text(json.dumps(captures, indent=2))


def wait_for_text(page, text, timeout_ms=15000):
    page.wait_for_function(
        "(t) => document.body && document.body.innerText.includes(t)",
        arg=text,
        timeout=timeout_ms,
    )


def main():
    par_run_id = int(os.environ.get("PAR_RUN_ID", "2125"))
    flw_username = os.environ.get("FLW_USERNAME", "jumoke_n")

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- Pre-warm + discovery (NOT recorded) ----------
        warm = browser.new_context(viewport={"width": 1440, "height": 900}, storage_state=STATE)
        warm_page = warm.new_page()
        warm_page.goto(f"{LABS}/labs/workflow/", wait_until="networkidle")
        ids = discover_run_ids(warm_page.request, par_run_id)
        print(f"PAR run {par_run_id} | Wk4 in_progress run {ids['wk4_run_id']} | opp {ids['opp_id']} | flw {flw_username}")
        warm.close()

        wk4_url = f"{LABS}/labs/workflow/{ids['workflow_def_id']}/run/?run_id={ids['wk4_run_id']}&opportunity_id={ids['opp_id']}"

        # ---------- Recording ----------
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            storage_state=STATE,
            record_video_dir=str(OUT_DIR),
            record_video_size={"width": 1440, "height": 900},
        )
        # Auto-accept native confirms (Mark all No Issue uses window.confirm).
        captures: dict = {}
        page = ctx.new_page()
        if CURSOR_JS:
            ctx.add_init_script(CURSOR_JS)

        def accept_dialog(dialog):
            print(f"  dialog: {dialog.message[:80]!r}")
            dialog.accept()

        page.on("dialog", accept_dialog)
        # Capture page console + errors for debugging
        console_log: list[str] = []
        page.on("console", lambda m: console_log.append(f"[{m.type}] {m.text[:200]}"))
        page.on("pageerror", lambda e: console_log.append(f"[pageerror] {e}"[:200]))

        # --- Scene 0: Arrive at Wk4 in_progress weekly review
        print("Scene 0: Wk4 in_progress weekly review")
        page.goto(wk4_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        snapshot_dump(page, "wk4_in_progress", captures)

        # --- Scene 1: Click "Mark all No Issue" header action
        print("Scene 1: Bulk Mark all No Issue")
        page.click("button:has-text('Mark all No Issue')")
        # The bulk-mark fires 9 parallel decision POSTs then window.location.reload().
        # Wait for the reload + remount: page text must include 9 "Confirmed
        # No Issue" rows AND the workflow runner must have re-mounted.
        page.wait_for_function(
            "() => (document.body.innerText.match(/Confirmed No Issue/g) || []).length >= 9",
            timeout=30000,
        )
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(1500)
        snapshot_dump(page, "after_bulk_mark", captures)

        # --- Scene 2: Scroll to the flagged FLW and Create Audit
        print(f"Scene 2: Click Create Audit for {flw_username}")
        # After bulk-mark + reload, give the render code an extra beat to
        # remount so the click handler is wired before we scroll.
        page.wait_for_selector(f"text={flw_username}", timeout=15000)
        page.wait_for_timeout(800)
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); if (row) row.scrollIntoView({block: 'center'}); }",
            flw_username,
        )
        page.wait_for_timeout(1200)
        # Verify the button is there + click. Capture row state for debug.
        pre_click = page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); if (!row) return 'no row'; const btns = [...row.querySelectorAll('button, a')].map(b => b.innerText.trim()); return btns; }",
            flw_username,
        )
        print(f"  pre-click row buttons: {pre_click}")
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); const btn = row && [...row.querySelectorAll('button')].find(b => b.innerText.trim() === 'Create Audit'); if (btn) btn.click(); }",
            flw_username,
        )
        # Page navigates to /audit/<id>/?opportunity_id=...
        page.wait_for_url("**/audit/**", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2500)
        snapshot_dump(page, "audit_pass_clean", captures)
        audit_url = page.url

        # --- Scene 3: Scroll through the audit page to show all 5 pass images
        print("Scene 3: Audit detail — 5 good-pool pass images")
        height = page.evaluate("() => document.documentElement.scrollHeight")
        viewport_h = page.evaluate("() => window.innerHeight")
        distance = max(0, height - viewport_h)
        if distance > 50:
            page.evaluate(
                """([dist]) => new Promise(res => {
                    const start = performance.now();
                    const dur = Math.min(5000, dist * 1.5);
                    function step(t) {
                        const r = Math.min(1, (t - start) / dur);
                        const eased = r < 0.5 ? 4*r*r*r : 1 - Math.pow(-2*r + 2, 3)/2;
                        window.scrollTo(0, dist * eased);
                        if (r < 1) requestAnimationFrame(step); else res();
                    }
                    requestAnimationFrame(step);
                })""",
                [distance],
            )
        page.wait_for_timeout(1500)

        # --- Scene 4: Navigate back to Wk4 review
        print("Scene 4: Back to Wk4 weekly review")
        page.goto(wk4_url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        snapshot_dump(page, "back_after_audit", captures)

        # --- Scene 5: Click Create Task with Coaching for the FLW
        print(f"Scene 5: Create Task with Coaching for {flw_username}")
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); if (row) row.scrollIntoView({block: 'center'}); }",
            flw_username,
        )
        page.wait_for_timeout(1200)
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); const btn = row && [...row.querySelectorAll('button')].find(b => b.innerText.includes('Create Task with Coaching')); if (btn) btn.click(); }",
            flw_username,
        )
        # Two API calls fire, then page reloads.
        page.wait_for_load_state("networkidle", timeout=45000)
        page.wait_for_timeout(2500)
        snapshot_dump(page, "after_create_task", captures)

        # --- Scene 6: Click the new task link → task detail with OCS conversation
        print("Scene 6: Click View Task → OCS coaching conversation")
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); if (row) row.scrollIntoView({block: 'center'}); }",
            flw_username,
        )
        page.wait_for_timeout(800)
        page.evaluate(
            "(uname) => { const row = [...document.querySelectorAll('tr')].find(r => r.innerText.includes(uname)); const a = row && [...row.querySelectorAll('a')].find(x => x.innerText.includes('View task')); if (a) a.click(); }",
            flw_username,
        )
        page.wait_for_url("**/tasks/**", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2500)
        # Try to scroll to the conversation block.
        page.evaluate(
            "() => { const els = [...document.querySelectorAll('*')].filter(e => e.innerText && e.innerText.includes('MUAC tape')); if (els[0]) els[0].scrollIntoView({block: 'center'}); }"
        )
        page.wait_for_timeout(2500)
        snapshot_dump(page, "task_ocs_conversation", captures)

        print(f"\nConsole log ({len(console_log)} entries, last 20):")
        for line in console_log[-20:]:
            print(f"  {line}")

        ctx.close()
        browser.close()

        webms = list(OUT_DIR.glob("*.webm"))
        webms.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        latest = webms[0] if webms else None
        print(f"\nRecorded manager-flow prepend: {latest}")
        print(f"Snapshot manifest: {SNAPSHOTS}")


if __name__ == "__main__":
    main()
