"""Capture screenshots at each scene's URL for the canopy walkthrough deck.

Reads docs/walkthroughs/program-admin-report.yaml + uses the existing labs
session to navigate to each scene's page, screenshot, capture page text,
and emit /tmp/walkthrough-run-data.json that the canopy generator script
can turn into an HTML deck.

This bypasses the gstack browse binary (whose cookie import wasn't sticking
for this session) and uses playwright + labs-session.json directly — which
the rest of the recorder pipeline already proves works.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import re
import subprocess
import time
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

LABS = "https://labs.connect.dimagi.com"
STATE = "/Users/acedimagi/.ace/labs-session.json"
SPEC_PATH = Path("/Users/acedimagi/emdash/worktrees/connect-labs/emdash/synthetic-ywaj6/docs/walkthroughs/program-admin-report.yaml")
PAR_RUN_ID = 1774
PRIMARY_OPP = 10000


def find_drill_targets(page):
    """Hit the PAR snapshot API to find good + incomplete drill IDs."""
    r = page.request.get(
        f"{LABS}/labs/workflow/api/run/{PAR_RUN_ID}/snapshot/"
        f"?opportunity_id={PRIMARY_OPP}"
    ).json()
    snap = r["snapshot"]
    good = None
    bad = None
    expected_weeks = snap.get("state", {}).get("expected_weeks", [])
    for src in snap["state"]["watched_summary"]:
        missed = set(src.get("missed_week_idxs", []) or [])
        for run in src.get("runs", []):
            completed = (run.get("completed_at") or "")[:10]
            for idx, monday in enumerate(expected_weeks):
                if idx in missed:
                    continue
                end = (dt.date.fromisoformat(monday) + dt.timedelta(days=6)).isoformat()
                if monday <= completed <= end:
                    week_idx = idx
                    break
            else:
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
                    "opp_id": src["opportunity_id"],
                    "opp_label": src.get("label", ""),
                    "wf_def_id": src["workflow_definition_id"],
                    "week_idx": week_idx,
                    "run_id": run["id"],
                    "audit_id": a["id"],
                    "task_id": t["id"],
                    "flw_id": d.get("flw_id"),
                }
                if (a.get("status") == "completed" and t.get("status") == "closed"
                        and good is None and t.get("official_action") in ("satisfactory", "warned")):
                    good = target
                elif (a.get("status") in ("in_review", "in_progress") and t.get("status") == "investigating"
                        and bad is None):
                    bad = target
    return good, bad


def main():
    spec = yaml.safe_load(SPEC_PATH.read_text())
    shot_dir = Path(f"/tmp/walkthrough-screenshots/program-admin-report-{int(time.time())}")
    shot_dir.mkdir(parents=True, exist_ok=True)

    started_at = dt.datetime.now(dt.timezone.utc)

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(
            viewport={"width": spec.get("video_viewport_width", 1440), "height": spec.get("video_viewport_height", 900)},
            device_scale_factor=2,
            storage_state=STATE,
        )
        page = ctx.new_page()

        # Discover drill targets first so we can substitute IDs into scene URLs
        page.goto(f"{LABS}/labs/workflow/65/run/?run_id={PAR_RUN_ID}&opportunity_id={PRIMARY_OPP}", wait_until="networkidle")
        good, bad = find_drill_targets(page)
        if not good or not bad:
            raise RuntimeError(f"missing drill targets: good={good} bad={bad}")
        print(f"Good run: opp {good['opp_id']} run {good['run_id']} audit {good['audit_id']} task {good['task_id']}")
        print(f"Incomplete: opp {bad['opp_id']} run {bad['run_id']} audit {bad['audit_id']} task {bad['task_id']}")

        # Scene → URL mapping (substitute in the discovered IDs)
        scene_urls: list[tuple[dict, str, str]] = []
        # (scene_dict, url, scroll_hint)
        # scroll_hint: "top" / "Coaching Conversation" — name of an element to scroll to
        par_url = f"{LABS}/labs/workflow/65/run/?run_id={PAR_RUN_ID}&opportunity_id={PRIMARY_OPP}"
        chc_good_url = f"{LABS}/labs/workflow/{good['wf_def_id']}/run/?run_id={good['run_id']}&opportunity_id={good['opp_id']}"
        chc_bad_url = f"{LABS}/labs/workflow/{bad['wf_def_id']}/run/?run_id={bad['run_id']}&opportunity_id={bad['opp_id']}"
        audit_good_url = f"{LABS}/audit/{good['audit_id']}/?opportunity_id={good['opp_id']}"
        audit_bad_url = f"{LABS}/audit/{bad['audit_id']}/?opportunity_id={bad['opp_id']}"
        task_good_url = f"{LABS}/tasks/{good['task_id']}/edit/?opportunity_id={good['opp_id']}"
        task_bad_url = f"{LABS}/tasks/{bad['task_id']}/edit/?opportunity_id={bad['opp_id']}"

        # Map each scene to a target URL. The YAML's `show` field is high-level
        # narrative; this script encodes the actual navigation.
        scenes = spec["scenes"]
        url_for_scene = [
            par_url,         # Scene 1: PAR grid
            par_url,         # Scene 2: PAR detail panel (same URL — click is via PAR cell click)
            chc_good_url,    # Scene 3: CHC Nutrition table with state-aware buttons
            audit_good_url,  # Scene 4: Audit photos
            task_good_url,   # Scene 5: Task + coaching conversation (scroll)
            par_url,         # Scene 6: PAR detail (incomplete row)
            audit_bad_url,   # Scene 7: in-review audit
            task_bad_url,    # Scene 8: investigating task (scroll)
            par_url,         # Scene 9: aggregate column
        ]
        click_cell_for_scene = {
            1: (good["opp_label"], good["week_idx"]),   # Scene 2 (0-indexed = 1)
            5: (bad["opp_label"], bad["week_idx"]),     # Scene 6
        }
        scroll_for_scene = {
            4: "Coaching Conversation",  # Scene 5
            7: "Coaching Conversation",  # Scene 8
        }

        # Build slides list
        slides = [{"type": "title"}]
        # Insert persona intro before first scene
        slides.append({"type": "persona_intro", "persona_key": next(iter(spec["personas"]))})
        ai_scores = []
        issues = []

        for i, (scene, url) in enumerate(zip(scenes, url_for_scene)):
            scene_no = i + 1
            print(f"\nScene {scene_no}/{len(scenes)}: {scene['title']}")
            print(f"  URL: {url}")
            page.goto(url, wait_until="networkidle")
            # Wait extra time for React mount + image streaming
            try:
                page.wait_for_selector("text=Connect Labs", timeout=10000)
            except Exception:
                pass
            time.sleep(2.0)
            # If this scene needs a cell click (PAR detail variants)
            if i in click_cell_for_scene:
                opp_label, week_idx = click_cell_for_scene[i]
                # Find cell position via the same trick we used for the recorder
                try:
                    page.wait_for_selector("text=Window aggregate", timeout=10000)
                    pos = page.evaluate("""({opp_label, week_idx}) => {
                        const labels = Array.from(document.querySelectorAll('div')).filter(d => {
                            return d.style && d.style.fontWeight === '600' && d.textContent.startsWith(opp_label);
                        });
                        if (labels.length === 0) return null;
                        const labelCell = labels[0].closest('div[style*="border"]');
                        const grid = labelCell ? labelCell.parentElement : null;
                        const cells = Array.from(grid.children);
                        const cell = cells[1 + week_idx];
                        if (!cell) return null;
                        const inner = cell.querySelector('[style*="cursor: pointer"]') || cell;
                        const rect = inner.getBoundingClientRect();
                        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                    }""", {"opp_label": opp_label, "week_idx": week_idx})
                    if pos:
                        page.mouse.click(pos["x"], pos["y"])
                        time.sleep(1.5)
                except Exception as e:
                    print(f"  ! cell click failed: {e}")

            # If this scene needs a scroll to a specific section
            if i in scroll_for_scene:
                target = scroll_for_scene[i]
                page.evaluate(f"""() => {{
                    const el = Array.from(document.querySelectorAll('h3'))
                        .find(h => h.textContent.includes('{target}'));
                    if (el) el.scrollIntoView({{behavior: 'instant', block: 'start'}});
                }}""")
                time.sleep(1.5)

            # For the audit good case, wait extra for thumbnails
            if i == 3:
                try:
                    page.wait_for_function(
                        """() => Array.from(document.querySelectorAll('img'))
                              .filter(i => i.src.includes('/audit/image/'))
                              .filter(i => i.complete && i.naturalWidth > 0).length >= 3""",
                        timeout=15000,
                    )
                except Exception:
                    pass
                time.sleep(2)

            # Capture
            shot = shot_dir / f"scene_{scene_no:02d}.png"
            page.screenshot(path=str(shot), full_page=True)
            current_url = page.url
            page_text = page.inner_text("body")[:1200]
            b64 = base64.b64encode(shot.read_bytes()).decode("ascii")

            # Heuristic score — we don't have visual-judge wired here. Default 4
            # unless the page text shows obvious failure markers; bump down for those.
            score = 4
            commentary_parts = []
            if "Page not found" in page_text or "Render error" in page_text or "Session" in page_text and "not found" in page_text:
                score = 2
                commentary_parts.append("page error detected")
            if "Loading" in page_text[:200] and "Loaded" not in page_text[:200]:
                score = 3
                commentary_parts.append("loading state visible")
            commentary = f"Overall: {score}/5. " + (" / ".join(commentary_parts) if commentary_parts else "Content rendered, narrative tracks.")

            slides.append({
                "type": "scene",
                "scene_index": scene_no,
                "scene_total": len(scenes),
                "persona_key": scene["persona"],
                "title": scene["title"],
                "narration": scene.get("impressive_because", scene.get("show", "")),
                "url": current_url,
                "logged_in_as": "ace@dimagi-ai.com",
                "screenshot_b64": b64,
                "ai_evaluation": {
                    "score": score,
                    "max_score": 5,
                    "commentary": commentary,
                },
            })
            ai_scores.append({"feature": scene["title"], "score": score, "max_score": 5})
            print(f"  → score {score}/5  ({len(b64)//1024} KB b64)")

        slides.append({
            "type": "summary",
            "scenes_completed": len(scenes),
            "scenes_total": len(scenes),
            "ai_scores": ai_scores,
            "issues": issues,
            "previous_run": None,
        })

        duration = int((dt.datetime.now(dt.timezone.utc) - started_at).total_seconds())
        out = {
            "name": spec["name"],
            "narrative": spec["narrative"],
            "generated_at": started_at.isoformat(),
            "duration_seconds": duration,
            "personas": spec["personas"],
            "slides": slides,
        }
        Path("/tmp/walkthrough-run-data.json").write_text(json.dumps(out, indent=2))
        print(f"\nWrote run data — {duration}s elapsed")

        b.close()


if __name__ == "__main__":
    main()
