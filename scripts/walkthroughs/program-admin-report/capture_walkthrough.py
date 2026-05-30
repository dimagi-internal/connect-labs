"""Capture per-scene screenshots for the canopy walkthrough deck.

Reads ``docs/walkthroughs/program-admin-report.yaml`` (the single source
of truth for scene order + narration) and produces
``/tmp/walkthrough-run-data.json`` that canopy's ``generate_presentation.py``
turns into an HTML deck.

Each scene declares a ``target`` keyword; this script maps targets to
URLs (built from the IDs discovered in the PAR snapshot) + optional
post-load interactions. The YAML and Python stay in sync because the
mapping is a single dict here.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.discovery import find_drill_targets  # noqa: E402
from walkthroughs._lib.grid import find_cell_position  # noqa: E402
from walkthroughs._lib.recorder import RecorderSession, goto_and_settle  # noqa: E402

HERE = Path(__file__).resolve().parent
SPEC_PATH = REPO_ROOT / "docs" / "walkthroughs" / "program-admin-report.yaml"
PAR_DEFINITION_ID = 65


def build_target_handlers(par_url: str, good: dict, bad: dict) -> dict[str, dict]:
    """Map each YAML ``target`` keyword to its URL + post-load actions.

    ``post`` is a list of dicts of the form ``{action: ..., **args}`` —
    handled by ``apply_post_action`` below. Add new target types here.
    """
    LABS = wcfg.LABS_BASE_URL
    chc_good_url = (
        f"{LABS}/labs/workflow/{good['wf_def_id']}" f"/run/?run_id={good['run_id']}&opportunity_id={good['opp_id']}"
    )
    audit_good_url = f"{LABS}/audit/{good['audit_id']}/?opportunity_id={good['opp_id']}"
    audit_bad_url = f"{LABS}/audit/{bad['audit_id']}/?opportunity_id={bad['opp_id']}"
    task_good_url = f"{LABS}/tasks/{good['task_id']}/edit/?opportunity_id={good['opp_id']}"
    task_bad_url = f"{LABS}/tasks/{bad['task_id']}/edit/?opportunity_id={bad['opp_id']}"

    def click_cell(opp_label: str, week_idx: int):
        return {"action": "click_cell", "opp_label": opp_label, "week_idx": week_idx}

    def scroll_to(target: str):
        return {"action": "scroll_to", "text": target}

    def wait_audit():
        return {"action": "wait_audit_images", "at_least": 3}

    return {
        "par_grid": {
            "url": par_url,
            "post": [],
        },
        "par_detail_good": {
            "url": par_url,
            "post": [click_cell(good["opp_label"], good["week_idx"])],
        },
        "par_detail_incomplete": {
            "url": par_url,
            "post": [click_cell(bad["opp_label"], bad["week_idx"])],
        },
        "par_aggregate": {
            "url": par_url,
            "post": [],
        },
        "chc_good": {
            "url": chc_good_url,
            "post": [],
        },
        "audit_good": {
            "url": audit_good_url,
            "post": [wait_audit()],
        },
        "audit_incomplete": {
            "url": audit_bad_url,
            "post": [{"action": "wait_audit_images", "at_least": 2}],
        },
        "task_good": {
            "url": task_good_url,
            "post": [scroll_to("Coaching Conversation")],
        },
        "task_incomplete": {
            "url": task_bad_url,
            "post": [scroll_to("Coaching Conversation")],
        },
    }


def apply_post_action(page, action: dict) -> None:
    kind = action["action"]
    if kind == "click_cell":
        page.wait_for_selector("text=Window aggregate", timeout=10_000)
        pos = find_cell_position(page, action["opp_label"], action["week_idx"])
        if pos:
            page.mouse.click(pos["x"], pos["y"])
            time.sleep(1.5)
        else:
            print(f"  ! click_cell failed: no cell {action['opp_label']} wk{action['week_idx']}")
    elif kind == "scroll_to":
        page.evaluate(
            """({text}) => {
                const el = Array.from(document.querySelectorAll('h3'))
                    .find(h => h.textContent.includes(text));
                if (el) el.scrollIntoView({behavior: 'instant', block: 'start'});
            }""",
            {"text": action["text"]},
        )
        time.sleep(1.5)
    elif kind == "wait_audit_images":
        try:
            page.wait_for_function(
                """(n) => {
                    const imgs = Array.from(document.querySelectorAll('img'))
                        .filter(i => i.src.includes('/audit/image/'));
                    return imgs.length >= n
                        && imgs.filter(i => i.complete && i.naturalWidth > 0).length >= n;
                }""",
                arg=int(action["at_least"]),
                timeout=15_000,
            )
        except Exception as e:
            print(f"  ! wait_audit_images({action['at_least']}) timed out: {e}")
        time.sleep(2)
    else:
        print(f"  ! unknown post action {kind!r}")


def main() -> int:
    ids = wcfg.read_run_ids(HERE, required=["par_run_id", "opp_id"])
    par_run_id = int(ids["par_run_id"])
    primary_opp = int(ids["opp_id"])
    par_url = (
        f"{wcfg.LABS_BASE_URL}/labs/workflow/{PAR_DEFINITION_ID}"
        f"/run/?run_id={par_run_id}&opportunity_id={primary_opp}"
    )

    spec = yaml.safe_load(SPEC_PATH.read_text())
    shot_dir = Path(f"/tmp/walkthrough-screenshots/program-admin-report-{int(time.time())}")
    shot_dir.mkdir(parents=True, exist_ok=True)
    started_at = dt.datetime.now(dt.timezone.utc)

    viewport = {
        "width": spec.get("video_viewport_width", 1440),
        "height": spec.get("video_viewport_height", 900),
    }

    with RecorderSession(
        out_dir=Path("/tmp/walkthrough-capture-noop"),  # we won't record
        manifest_path=None,
        viewport=viewport,
        prewarm=False,
        accept_dialogs=False,
        with_cursor=False,
        record=False,
    ) as rec:
        page = rec.page
        goto_and_settle(page, par_url, timeout=30_000, settle_seconds=0)
        targets = find_drill_targets(
            page.request.get,
            par_run_id,
            labs_base_url=wcfg.LABS_BASE_URL,
            primary_opp_id=primary_opp,
        )
        good = targets["good"]
        bad = targets["incomplete"]
        print(f"Good run: opp {good['opp_id']} run {good['run_id']} audit {good['audit_id']} task {good['task_id']}")
        print(f"Incomplete: opp {bad['opp_id']} run {bad['run_id']} audit {bad['audit_id']} task {bad['task_id']}")

        target_handlers = build_target_handlers(par_url, good, bad)
        scenes = spec["scenes"]

        slides = [
            {"type": "title"},
            {"type": "persona_intro", "persona_key": next(iter(spec["personas"]))},
        ]
        ai_scores: list[dict] = []
        issues: list[dict] = []

        for i, scene in enumerate(scenes, start=1):
            target_key = scene["target"]
            handler = target_handlers.get(target_key)
            if not handler:
                raise SystemExit(f"Scene {i}: unknown target {target_key!r}")
            print(f"\nScene {i}/{len(scenes)} ({target_key}): {scene['title']}")
            print(f"  URL: {handler['url']}")
            goto_and_settle(
                page,
                handler["url"],
                timeout=30_000,
                wait_for_selector="text=Connect Labs",
                settle_seconds=2.0,
            )
            for action in handler["post"]:
                apply_post_action(page, action)

            shot = shot_dir / f"scene_{i:02d}.png"
            page.screenshot(path=str(shot), full_page=True)
            current_url = page.url
            page_text = page.inner_text("body")[:1200]
            b64 = base64.b64encode(shot.read_bytes()).decode("ascii")

            score, commentary_parts = _heuristic_score(page_text)
            commentary = f"Overall: {score}/5. " + (
                " / ".join(commentary_parts) if commentary_parts else "Content rendered, narrative tracks."
            )

            slides.append(
                {
                    "type": "scene",
                    "scene_index": i,
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
                }
            )
            ai_scores.append({"feature": scene["title"], "score": score, "max_score": 5})
            print(f"  → score {score}/5  ({len(b64)//1024} KB b64)")

        slides.append(
            {
                "type": "summary",
                "scenes_completed": len(scenes),
                "scenes_total": len(scenes),
                "ai_scores": ai_scores,
                "issues": issues,
                "previous_run": None,
            }
        )

        duration = int((dt.datetime.now(dt.timezone.utc) - started_at).total_seconds())
        out_data = {
            "name": spec["name"],
            "narrative": spec["narrative"],
            "generated_at": started_at.isoformat(),
            "duration_seconds": duration,
            "personas": spec["personas"],
            "slides": slides,
        }
        Path("/tmp/walkthrough-run-data.json").write_text(json.dumps(out_data, indent=2))
        print(f"\nWrote /tmp/walkthrough-run-data.json — {duration}s elapsed")
        return 0


def _heuristic_score(page_text: str) -> tuple[int, list[str]]:
    """Cheap-and-cheerful page-quality heuristic until visual-judge is wired."""
    parts: list[str] = []
    score = 4
    if "Page not found" in page_text or "Render error" in page_text:
        score = 2
        parts.append("page error detected")
    elif "Session" in page_text[:400] and "not found" in page_text[:400]:
        score = 2
        parts.append("session error detected")
    elif "Loading" in page_text[:200] and "Loaded" not in page_text[:200]:
        score = 3
        parts.append("loading state visible")
    return score, parts


if __name__ == "__main__":
    sys.exit(main())
