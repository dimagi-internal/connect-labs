"""Record the Verified Monitoring (N1) dashboard walkthrough video.

A single-page motion tour of the live dashboard: the hero coverage tiles,
the six-round trend, then the two-ward map where the layer toggles are
exercised on camera (service-delivery points and survey pins switched off and
back on) so the map's interactivity is visible — the reason this demo is shown
in motion rather than a static frame. Closes on the self-report-vs-independent
panel.

Reads the run id written by ``regenerate.py`` (``.run_ids.json``); falls back to
``--run-id``. Produces a ``.webm`` under ``video/`` and converts it to
``verified_monitoring.mp4`` with ffmpeg.

Usage::

    python scripts/walkthroughs/verified-monitoring/regenerate.py     # seed first
    python scripts/walkthroughs/verified-monitoring/record_dashboard.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.recorder import RecorderSession, goto_and_settle, slow_move, snap  # noqa: E402

HERO = "text=Verified vitamin-A coverage"


def _scroll_to(page, text: str, *, settle_ms: int = 1200) -> bool:
    """Smooth-scroll so the element whose text contains ``text`` is centered."""
    found = bool(
        page.evaluate(
            """(t) => {
                const els = Array.from(document.querySelectorAll('div,span,h1,h2,h3'));
                const el = els.reverse().find(e => e.textContent && e.textContent.includes(t)
                    && e.children.length <= 6);
                if (el) { el.scrollIntoView({behavior:'smooth', block:'center'}); return true; }
                return false;
            }""",
            text,
        )
    )
    page.wait_for_timeout(settle_ms)
    return found


def _toggle(page, label_text: str) -> None:
    """Move the cursor to a map layer checkbox and click it (off), pause, on."""
    loc = page.locator(f"label:has-text('{label_text}')").first
    try:
        box = loc.bounding_box()
    except Exception:
        box = None
    if box:
        slow_move(page, box["x"] + 10, box["y"] + box["height"] / 2, steps=30)
        page.wait_for_timeout(300)
    cb = loc.locator("input[type=checkbox]")
    cb.click()  # off — the layer disappears
    page.wait_for_timeout(1400)
    cb.click()  # back on
    page.wait_for_timeout(900)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=None)
    args = ap.parse_args()

    run_id = args.run_id
    if run_id is None:
        ids = json.loads(wcfg.run_ids_path(HERE).read_text()) if wcfg.run_ids_path(HERE).exists() else {}
        run_id = ids.get("run_id")
    if not run_id:
        sys.exit("No run id: run regenerate.py first, or pass --run-id")

    opp, wf = 10008, 3699
    url = f"{wcfg.LABS_BASE_URL}/labs/workflow/{wf}/run/?opportunity_id={opp}&run_id={run_id}"
    video_dir = HERE / "video"
    manifest = HERE / "scenes.json"
    print(f"recording {url}")

    with RecorderSession(out_dir=video_dir, manifest_path=manifest, prewarm=True, defer_record=True) as rec:
        # Pre-warm: load once (primes Leaflet CDN + render) on the no-video page.
        goto_and_settle(rec.warm_page, url, wait_for_selector=HERO, settle_seconds=2.0)
        page = rec.start_recording()
        goto_and_settle(page, url, wait_for_selector=HERO, settle_seconds=2.5)

        # Scene 1 — the hero coverage tiles.
        snap(rec, "hero")
        slow_move(page, 360, 320, steps=30)
        page.wait_for_timeout(1800)

        # Scene 2 — the six-round trend.
        _scroll_to(page, "Coverage across", settle_ms=1600)
        snap(rec, "trend")

        # Scene 3 — the two-ward map: exercise the layer toggles on camera.
        _scroll_to(page, "Two adjacent wards", settle_ms=1800)
        page.wait_for_selector(".leaflet-container", timeout=15_000)
        page.wait_for_timeout(1200)
        snap(rec, "map")
        _toggle(page, "service delivery")
        _toggle(page, "survey pins")
        page.wait_for_timeout(800)

        # Scene 4 — close on self-report vs independently verified.
        _scroll_to(page, "Self-reported vs independently verified", settle_ms=1600)
        snap(rec, "verify")
        page.wait_for_timeout(1600)

    webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not webms:
        sys.exit("no .webm produced")
    src = webms[-1]
    out = HERE / "verified_monitoring.mp4"
    print(f"webm: {src.name} ({src.stat().st_size // 1024} KB) → {out.name}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
