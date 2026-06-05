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
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from walkthroughs._lib import config as wcfg  # noqa: E402
from walkthroughs._lib.recorder import RecorderSession, goto_and_settle, slow_move, snap  # noqa: E402

HERO = "text=Survey round"


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


def _cursor_to(page, label_text: str):
    """Glide the cursor to a map layer checkbox; return its <input> locator."""
    loc = page.locator(f"label:has-text('{label_text}')").first
    try:
        box = loc.bounding_box()
    except Exception:
        box = None
    if box:
        slow_move(page, box["x"] + 10, box["y"] + box["height"] / 2, steps=30)
        page.wait_for_timeout(300)
    return loc.locator("input[type=checkbox]")


def _toggle(page, label_text: str) -> None:
    """Click a map layer checkbox off, pause, then back on (starts on)."""
    cb = _cursor_to(page, label_text)
    cb.click()  # off — the layer disappears
    page.wait_for_timeout(1400)
    cb.click()  # back on
    page.wait_for_timeout(900)


def _reveal(page, label_text: str) -> None:
    """Click a map layer checkbox on and dwell (starts off)."""
    cb = _cursor_to(page, label_text)
    cb.click()  # on — the layer appears
    page.wait_for_timeout(1600)


def _click_round(page, label: str) -> None:
    """Glide to and click a round-selector chip (R1..R6) so the KPIs re-drive."""
    btn = page.locator(f"button:has-text('{label}')").first
    try:
        box = btn.bounding_box()
    except Exception:
        box = None
    if box:
        slow_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=22)
        page.wait_for_timeout(220)
    try:
        btn.click()
    except Exception:
        pass


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
        # Pre-warm: load once (primes Mapbox + render) on the no-video page.
        goto_and_settle(rec.warm_page, url, wait_for_selector=HERO, settle_seconds=2.0)
        page = rec.start_recording()
        # The video records from here; the renderer shows a "Loading renderer…"
        # spinner until Babel transpiles + the hero appears. The webm timeline
        # runs slower than wall-clock, so trim the measured load time PLUS a
        # cushion (floor 5s) to be sure the clip opens on content, not spinner.
        t0 = time.monotonic()
        goto_and_settle(page, url, wait_for_selector=HERO, settle_seconds=2.5)
        trim_s = max(time.monotonic() - t0 + 1.0, 5.0)

        # Scene 1 — the hero: self-reported vs independently-verified (the two
        # numbers + the dumbbell). Park the cursor off the hero so the opening
        # frame reads finished, not mid-interaction.
        slow_move(page, 1180, 600, steps=20)
        snap(rec, "hero")
        page.wait_for_timeout(2000)

        # Scene 2 — the survey-quality KPIs (the data-quality strip).
        _scroll_to(page, "data quality", settle_ms=1400)
        snap(rec, "kpis")

        # Scene 3 — drill into the back-check: the side-by-side table where an
        # independent re-survey is compared field-by-field, discordances in red.
        _scroll_to(page, "Independent back-check", settle_ms=1600)
        snap(rec, "backcheck")
        _scroll_to(page, "showing", settle_ms=1500)  # the comparison table itself
        snap(rec, "backcheck-table")

        # Scene 4 — drill across cycles: click the selector and watch the KPIs
        # re-drive cycle to cycle (different program ward each time).
        _scroll_to(page, "Survey round", settle_ms=1000)
        for r in ("R1", "R6"):
            _click_round(page, r)
            page.wait_for_timeout(1300)
        snap(rec, "rounds")

        # Scene 5 — the six-cycle trend.
        _scroll_to(page, "bi-monthly", settle_ms=1400)
        snap(rec, "trend")

        # Scene 6 — THE moving map: land on it, then step the round selector so
        # the map FLIES to each cycle's two real wards (the rotating-wards
        # highlight). Click the chips via JS (no auto-scroll) so the map stays in
        # frame while it re-fits to a new program/comparison pair each click.
        _scroll_to(page, "Program service delivery", settle_ms=1700)
        page.wait_for_selector(".mapboxgl-canvas", timeout=20_000)
        try:
            page.wait_for_function(
                "window.ConnectMap && document.querySelector('.mapboxgl-canvas')",
                timeout=10_000,
            )
        except Exception:
            pass
        page.wait_for_timeout(2600)
        snap(rec, "map")

        def _click_round_no_scroll(label):
            page.evaluate(
                "(lbl)=>{var b=[].slice.call(document.querySelectorAll('button'))"
                ".find(x=>x.textContent.trim()===lbl); if(b) b.click();}",
                label,
            )

        for r in ("R1", "R2", "R3", "R4", "R5", "R6"):
            _click_round_no_scroll(r)
            page.wait_for_timeout(1900)  # let the map fly + settle on the new wards
        snap(rec, "map-rotating")
        page.wait_for_timeout(1200)

    webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not webms:
        sys.exit("no .webm produced")
    src = webms[-1]
    out = HERE / "verified_monitoring.mp4"
    print(f"webm: {src.name} ({src.stat().st_size // 1024} KB) → {out.name}")
    # Keep the full Connect UI in frame (the surrounding shell shows this is
    # running inside CommCare Connect); only trim the measured renderer-load
    # spinner off the front.
    print(f"trimming {trim_s:.1f}s of renderer-load spinner")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-ss",
            f"{trim_s:.2f}",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
