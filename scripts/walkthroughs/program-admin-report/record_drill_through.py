"""Record the program-admin-report drill-through against real labs prod
with a visible synthetic cursor so screen-to-screen motion reads as motion.

Story arc:
  Grid → Southern Wk 1 detail → Audit #500 (real MUAC photos)
       → Task #501 (✓ Closed · warned badge)
       → Southern Wk 3 detail → Audit #518 (in-review, 3 pending)
       → Task #519 (Investigating dropdown + Close Task button)
       → back to aggregate
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

LABS = "https://labs.connect.dimagi.com"
STATE = "/Users/acedimagi/.ace/labs-session.json"
PAR_DEFINITION_ID = 65
PRIMARY_OPP = 10000
OUT = Path("/tmp/par_preview/video")
OUT.mkdir(exist_ok=True)


def find_par_drill_targets(api_get, par_run_id):
    """From a known PAR run id, walk its snapshot to find drill targets:

      - A "good run" audit: a completed AuditSession whose decision's task is
        closed with action='warned' (matches the closed_warned archetype).
      - An "incomplete run" audit: an in_review AuditSession whose decision's
        task is still in 'investigating' status (matches the investigating
        archetype).

    Returns a dict with the run_id + the two audit/task pairs.
    """
    snap_resp = api_get(
        f"{LABS}/labs/workflow/api/run/{par_run_id}/snapshot/"
        f"?opportunity_id={PRIMARY_OPP}"
    ).json()
    summary = snap_resp.get("snapshot", {}).get("state", {}).get("watched_summary", [])

    # The "good run" pick prefers a closed_satisfactory task (cleanest visual:
    # all 5 photos pass, audit overall_result=pass), then falls back to
    # closed_warned (which has fail thumbnails). The "incomplete run" pick is
    # the first in_review audit with an investigating task.
    satisfactory = None    # dict with opp_label, week_idx, run_id, audit_id, task_id
    warned = None
    incomplete = None
    # Map opp_id → (label, list_of_week_isos_in_order) by walking the snapshot.
    # The grid is rendered in summary order; weeks come from expected_weeks
    # in state, but each opp may have its own missed_week_idxs.
    expected_weeks = snap_resp.get("snapshot", {}).get("state", {}).get("expected_weeks", [])
    for src in summary:
        opp_label = src.get("label", "")
        missed = set(src.get("missed_week_idxs", []) or [])
        # Build a mapping of week_idx → run for this opp (so we can name the
        # cell to click)
        # Match each run to its expected week by completed_at date prefix.
        run_to_week_idx = {}
        for run in src.get("runs", []):
            completed_date = (run.get("completed_at") or "")[:10]
            for idx, monday in enumerate(expected_weeks):
                if idx in missed:
                    continue
                # Run is in this week if completed_at is within monday..+6d
                if completed_date >= monday:
                    # Check if it's the same week (not later)
                    from datetime import date, timedelta
                    end = (date.fromisoformat(monday) + timedelta(days=6)).isoformat()
                    if completed_date <= end:
                        run_to_week_idx[run["id"]] = idx
                        break
        for run in src.get("runs", []):
            week_idx = run_to_week_idx.get(run["id"])
            if week_idx is None:
                continue
            for d in run.get("decisions", []):
                if d.get("decision_type") != "action_taken":
                    continue
                audits = d.get("audit_outcomes", []) or []
                tasks = d.get("task_outcomes", []) or []
                if not audits or not tasks:
                    continue
                a, t = audits[0], tasks[0]
                target = {
                    "opp_label": opp_label.split()[0] if opp_label else "Opp",
                    "week_idx": week_idx,
                    "run_id": run["id"],
                    "audit_id": a["id"],
                    "task_id": t["id"],
                    "flw_id": d.get("flw_id"),
                }
                if (
                    a.get("status") == "completed"
                    and t.get("status") == "closed"
                ):
                    if satisfactory is None and t.get("official_action") == "satisfactory":
                        satisfactory = target
                    elif warned is None and t.get("official_action") == "warned":
                        warned = target
                elif (
                    incomplete is None
                    and a.get("status") in ("in_review", "in_progress")
                    and t.get("status") == "investigating"
                ):
                    incomplete = target
    good = satisfactory or warned
    if not good or not incomplete:
        raise RuntimeError(
            f"could not find a good + incomplete pair (good={good}, incomplete={incomplete})"
        )
    return {"par_run_id": par_run_id, "good": good, "incomplete": incomplete}

CURSOR_JS = Path("/tmp/par_preview/cursor_overlay.js").read_text()


def slow_move(page, x, y, steps=40):
    """Mouse move with enough steps that the cursor overlay can animate it."""
    page.mouse.move(x, y, steps=steps)


def find_cell_position(page, opp_label, week_idx):
    return page.evaluate("""({opp_label, week_idx}) => {
        const labels = Array.from(document.querySelectorAll('div')).filter(d => {
            return d.style && d.style.fontWeight === '600' && d.textContent.startsWith(opp_label);
        });
        if (labels.length === 0) return null;
        const labelCell = labels[0].closest('div[style*="border"]');
        const grid = labelCell ? labelCell.parentElement : null;
        if (!grid) return null;
        const cells = Array.from(grid.children);
        const cell = cells[1 + week_idx];
        if (!cell) return null;
        const inner = cell.querySelector('[style*="cursor: pointer"]') || cell;
        const rect = inner.getBoundingClientRect();
        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
    }""", {"opp_label": opp_label, "week_idx": week_idx})


def click_cell(page, opp_label, week_idx, pre_dwell=0.4):
    pos = find_cell_position(page, opp_label, week_idx)
    if not pos:
        print(f"  ! no cell {opp_label} wk{week_idx}")
        return
    slow_move(page, pos["x"], pos["y"])
    time.sleep(pre_dwell)
    page.mouse.click(pos["x"], pos["y"])
    time.sleep(0.5)


def click_text(page, text, timeout=4000, post_wait_selector=None, post_wait_timeout=10000):
    locator = page.locator(f"text={text}").first
    locator.wait_for(state="visible", timeout=timeout)
    box = locator.bounding_box()
    if not box:
        return False
    slow_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    time.sleep(0.4)
    locator.click()
    page.wait_for_load_state("networkidle")
    if post_wait_selector:
        try:
            page.wait_for_selector(post_wait_selector, timeout=post_wait_timeout)
        except Exception as e:
            print(f"  ! post-click wait for {post_wait_selector!r} failed: {e}")
    time.sleep(0.5)
    return True


def main():
    import os
    par_run_id = int(os.environ.get("PAR_RUN_ID", "1774"))

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- Discovery + pre-warm context (NOT recorded) ----------
        # Resolve drill IDs and visit each target page once so its async
        # data (snapshot JSON, bulk-assessment images from GDrive) lands in
        # the labs in-process cache. The cursor overlay is unnecessary here
        # since this context is never captured.
        warm_ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            storage_state=STATE,
        )
        warm_page = warm_ctx.new_page()
        warm_page.goto(f"{LABS}/labs/workflow/", wait_until="networkidle")
        targets = find_par_drill_targets(warm_page.request.get, par_run_id)
        good = targets["good"]
        bad = targets["incomplete"]
        par_url = (
            f"{LABS}/labs/workflow/65/run/"
            f"?run_id={par_run_id}&opportunity_id={PRIMARY_OPP}"
        )
        print(f"PAR run {par_run_id}")
        print(f"  good:       {good['opp_label']} Wk{good['week_idx']+1} flw={good['flw_id']}  audit #{good['audit_id']}, task #{good['task_id']}")
        print(f"  incomplete: {bad['opp_label']} Wk{bad['week_idx']+1} flw={bad['flw_id']}  audit #{bad['audit_id']}, task #{bad['task_id']}")

        # Pre-warm: visit each page so labs caches them. The bulk-assessment
        # endpoint streams JPGs from GDrive on first hit; cache once here so
        # the recorded second hit is instant.
        good_audit_url = f"{LABS}/audit/{good['audit_id']}/?opportunity_id=10001"
        bad_audit_url = f"{LABS}/audit/{bad['audit_id']}/?opportunity_id=10001"
        good_task_url = f"{LABS}/tasks/{good['task_id']}/edit/?opportunity_id=10000"
        bad_task_url = f"{LABS}/tasks/{bad['task_id']}/edit/?opportunity_id=10001"

        # Fix opp scope on tasks: good_task belongs to good['opp_label'] opp
        # (Northern = 10000, Southern = 10001). The PAR detail panel link uses
        # the watched source's opportunity_id, so reconstruct it here.
        good_opp = 10000 if good["opp_label"].startswith("Northern") else 10001
        bad_opp = 10000 if bad["opp_label"].startswith("Northern") else 10001
        good_audit_url = f"{LABS}/audit/{good['audit_id']}/?opportunity_id={good_opp}"
        bad_audit_url = f"{LABS}/audit/{bad['audit_id']}/?opportunity_id={bad_opp}"
        good_task_url = f"{LABS}/tasks/{good['task_id']}/edit/?opportunity_id={good_opp}"
        bad_task_url = f"{LABS}/tasks/{bad['task_id']}/edit/?opportunity_id={bad_opp}"

        print("Pre-warming target pages...")
        for url in (par_url, good_audit_url, bad_audit_url, good_task_url, bad_task_url):
            warm_page.goto(url, wait_until="networkidle")
            time.sleep(0.5)
            # For audit pages, also wait for image thumbnails so GDrive caches.
            if "/audit/" in url:
                try:
                    warm_page.wait_for_function(
                        """() => Array.from(document.querySelectorAll('img'))
                              .filter(i => i.src.includes('/audit/image/'))
                              .filter(i => i.complete && i.naturalWidth > 0).length >= 1""",
                        timeout=15000,
                    )
                except Exception:
                    pass

        warm_ctx.close()
        print("  pre-warm done\n")

        # ---------- Recording context ----------
        ctx = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            storage_state=STATE,
            record_video_dir=str(OUT),
            record_video_size={"width": 1440, "height": 900},
        )
        ctx.add_init_script(CURSOR_JS)

        page = ctx.new_page()
        page.goto(par_url, wait_until="networkidle")
        page.wait_for_selector("text=Program Admin Report", timeout=10000)
        # Wait for React to mount before any cells are clickable.
        page.wait_for_selector("text=Window aggregate", timeout=15000)
        # Park the cursor visibly on screen at start
        slow_move(page, 50, 60, steps=20)
        time.sleep(1.0)

        # In-record QA — snapshot what was visible at each scene boundary
        # so verify_video.py can assert we actually drilled where we meant
        # to. Write incrementally so a mid-recording crash still leaves us
        # with verifiable partial data.
        manifest_path = Path("/tmp/par_preview/scene_snapshots.json")
        scene_snapshots: dict[str, str] = {}
        import json as _json
        manifest_path.write_text("{}")  # initialize

        def snap(key: str):
            try:
                scene_snapshots[key] = page.inner_text("body")
            except Exception as e:
                scene_snapshots[key] = f"<<snapshot failed: {e}>>"
            manifest_path.write_text(_json.dumps(scene_snapshots, indent=2))

        # Scene 1: grid overview
        time.sleep(2.0)
        snap("par_grid")

        # Scene 2: open the "good run" cell → inline detail
        click_cell(page, good["opp_label"], good["week_idx"])
        time.sleep(3.0)
        snap("par_detail_good")

        # Scene 2b: drill through "Open the run" to the underlying CHC
        # Nutrition weekly review. The flagged FLW row has "View audit" +
        # "View task" buttons; the rest show "Confirmed OK".
        # (The post_wait selector matches the *workflow definition name*,
        # which varies per clone — match a robust element instead.)
        click_text(page, "Open the run", post_wait_selector="text=FLW-Level Analysis")
        # Wait for synthetic rows to render
        try:
            page.wait_for_function(
                """() => document.querySelectorAll('tbody tr').length >= 5""",
                timeout=12000,
            )
        except Exception as e:
            print(f"  ! CHC table didn't populate: {e}")
        # Scroll to the flagged row + park cursor near it. Single 2.5s dwell
        # is enough — the page itself doesn't change during cursor moves so
        # extending dwell just feels like a stuck screen.
        page.evaluate("""() => {
            const flagged = Array.from(document.querySelectorAll('tr')).find(
                tr => tr.textContent.includes('View audit'));
            if (flagged) flagged.scrollIntoView({behavior: 'smooth', block: 'center'});
        }""")
        # Move cursor onto the View audit button so the viewer knows what
        # we're about to click.
        view_audit = page.locator("text=View audit").first
        if view_audit.count() > 0:
            box = view_audit.bounding_box()
            if box:
                slow_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
        time.sleep(2.5)

        snap("chc_table_state_aware")

        # Click View audit to drill straight into the audit page from here.
        if view_audit.count() > 0:
            view_audit.click()
            page.wait_for_load_state("networkidle")
            try:
                page.wait_for_selector("text=Showing 5 assessment(s)", timeout=10000)
            except Exception:
                pass

        # Scene 3: dwell on the audit page (already navigated via the View
        # audit click in Scene 2b above) — wait for thumbnails to decode.
        try:
            page.wait_for_function(
                """() => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    const audit = imgs.filter(i => i.src.includes('/audit/image/'));
                    return audit.length >= 3 && audit.filter(i => i.complete && i.naturalWidth > 0).length >= 3;
                }""",
                timeout=15000,
            )
        except Exception as e:
            print(f"  ! good audit images partial: {e}")
        time.sleep(4.0)
        snap("audit_good_run")

        # Scene 4: back to the CHC Nutrition table, then click "View task #N"
        # to drill into the synthetic OCS coaching conversation.
        page.go_back(wait_until="networkidle")
        try:
            page.wait_for_function(
                """() => document.querySelectorAll('tbody tr').length >= 5""",
                timeout=8000,
            )
        except Exception:
            pass
        time.sleep(0.6)
        view_task = page.locator("text=View task").first
        if view_task.count() > 0:
            box = view_task.bounding_box()
            if box:
                slow_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)
                time.sleep(0.4)
            view_task.click()
            page.wait_for_load_state("networkidle")
            try:
                page.wait_for_selector("text=Closed", timeout=8000)
            except Exception:
                pass
        else:
            # Fallback: use the PAR drill path
            page.go_back(wait_until="networkidle")
            time.sleep(0.6)
            click_cell(page, good["opp_label"], good["week_idx"])
            time.sleep(0.6)
            click_text(page, f"Task #{good['task_id']}", post_wait_selector="text=Closed")
        # Brief dwell on Task Details (Closed badge + resolution)
        time.sleep(2.5)
        # Scroll down to reveal the synthetic OCS Coaching Conversation panel.
        # Smooth so a viewer can pause if they want to read.
        page.evaluate("""() => {
            const el = Array.from(document.querySelectorAll('h3'))
                .find(h => h.textContent.includes('Coaching Conversation'));
            if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
        }""")
        time.sleep(7.5)
        # Slow scroll-down through the messages so longer transcripts read.
        page.evaluate("""() => window.scrollBy({top: 350, left: 0, behavior: 'smooth'})""")
        time.sleep(3.5)
        snap("task_good_run")

        # Scene 5: back to PAR, jump to the incomplete-run cell.
        # We're currently on the task page (after View task drill). Navigate
        # straight back to the PAR rather than chaining go_back()s, which is
        # fragile after a long drill chain.
        page.goto(par_url, wait_until="networkidle")
        page.wait_for_selector("text=Window aggregate", timeout=10000)
        time.sleep(0.6)
        click_cell(page, bad["opp_label"], bad["week_idx"])
        time.sleep(2.5)
        snap("par_detail_incomplete")

        # Scene 6: in_review audit — wait for Save Progress + any thumbnails
        # In the in_review case, some photos may still be "pending" with
        # placeholder cards (no <img> tag yet), so we don't insist on a full
        # 5 — just wait for at least 2 visible thumbnails.
        click_text(page, f"Audit #{bad['audit_id']}", post_wait_selector="text=Save Progress")
        try:
            page.wait_for_function(
                """() => {
                    const imgs = Array.from(document.querySelectorAll('img'));
                    const audit = imgs.filter(i => i.src.includes('/audit/image/'));
                    return audit.length >= 2 && audit.filter(i => i.complete && i.naturalWidth > 0).length >= 2;
                }""",
                timeout=12000,
            )
        except Exception as e:
            print(f"  ! in_review audit images not fully loaded: {e}")
        time.sleep(4.0)
        snap("audit_in_review")

        # Scene 7: back, drill into the investigating task.
        # Same robustness fix as Scene 5: re-navigate to PAR + re-open the
        # detail panel rather than chaining go_back()s.
        page.goto(par_url, wait_until="networkidle")
        page.wait_for_selector("text=Window aggregate", timeout=10000)
        time.sleep(0.6)
        click_cell(page, bad["opp_label"], bad["week_idx"])
        time.sleep(0.6)
        click_text(page, f"Task #{bad['task_id']}", post_wait_selector="text=Close Task")
        # Dwell on Task Details (Investigating dropdown + Close Task button)
        time.sleep(2.5)
        # Scroll down to the in-progress coaching conversation
        page.evaluate("""() => {
            const el = Array.from(document.querySelectorAll('h3'))
                .find(h => h.textContent.includes('Coaching Conversation'));
            if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
        }""")
        time.sleep(6.0)
        snap("task_investigating")

        # Scene 8: back to PAR aggregate linger
        page.go_back(wait_until="networkidle")
        time.sleep(0.6)
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        # Move cursor to point at the aggregate column
        slow_move(page, 1310, 240)
        time.sleep(3.0)

        page.close()
        ctx.close()
        browser.close()

    vids = list(OUT.glob("*.webm"))
    print(f"\nRecorded {len(vids)} videos:")
    for v in vids:
        print(f"  {v} ({v.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
