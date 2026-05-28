"""Playwright recording helpers shared by every walkthrough.

The previous two recorders each reinvented:
  - Playwright context setup with cursor overlay
  - Dialog auto-accept (for the ``window.confirm`` bulk-mark prompts)
  - Console + pageerror capture
  - A snapshot manifest writer (``snap()``)
  - ``slow_move``, ``click_text`` with post-wait selector, ``wait_for_text``

This module consolidates them so a new walkthrough writes just its
scene sequence.

Usage::

    from _lib.recorder import RecorderSession, slow_move, click_text, snap

    with RecorderSession(
        out_dir=Path("/tmp/my_walkthrough/video"),
        manifest_path=Path("/tmp/my_walkthrough/scenes.json"),
    ) as rec:
        # rec.page is the recording page (with cursor overlay).
        # rec.warm_page is the pre-warm page (no cursor, no video).
        rec.page.goto(...)
        snap(rec, "scene_1")
        click_text(rec.page, "Mark all No Issue", ...)

The warm context is optional — pass ``prewarm=False`` if you don't need
the GDrive image cache primed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from . import config

CURSOR_OVERLAY_JS = (Path(__file__).resolve().parent / "cursor_overlay.js").read_text()

DEFAULT_VIEWPORT = {"width": 1440, "height": 900}


class RecorderSession:
    """Playwright recording session: pre-warm + record contexts + helpers.

    Lifecycle:

      with RecorderSession(out_dir=...) as rec:
          rec.warm_page.goto(...)    # optional: pre-warm caches
          rec.page.goto(...)         # recording page; gets cursor + video

    On exit, contexts close (which flushes the .webm) and the browser
    quits. ``rec.video_paths`` is populated with the resulting webm paths.
    """

    def __init__(
        self,
        *,
        out_dir: Path,
        manifest_path: Path | None = None,
        viewport: dict | None = None,
        prewarm: bool = True,
        accept_dialogs: bool = True,
        with_cursor: bool = True,
        record: bool = True,
        defer_record: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.manifest_path = Path(manifest_path) if manifest_path else None
        self.viewport = viewport or DEFAULT_VIEWPORT
        self.prewarm = prewarm
        self.accept_dialogs = accept_dialogs
        self.with_cursor = with_cursor
        self.record = record
        # When True, the video context is NOT created in __enter__; the
        # caller invokes start_recording() after any pre-record setup so the
        # clip doesn't open on a blank screen. See start_recording().
        self.defer_record = defer_record
        self._storage_state: str | None = None

        self._pw = None
        self.browser = None
        self.warm_context = None
        self.warm_page = None
        self.context = None
        self.page = None
        self.console_log: list[str] = []
        self.scene_snapshots: dict[str, str] = {}
        self.video_paths: list[Path] = []

    # ------------------------------------------------------------------ #
    # context manager
    # ------------------------------------------------------------------ #

    def __enter__(self) -> RecorderSession:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.manifest_path:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text("{}")

        storage_state = str(config.require_session_file())

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()

        if self.prewarm:
            self.warm_context = self.browser.new_context(
                viewport=self.viewport,
                device_scale_factor=2,
                storage_state=storage_state,
            )
            self.warm_page = self.warm_context.new_page()

        self._storage_state = storage_state
        if not self.defer_record:
            self.start_recording()
        return self

    def start_recording(self) -> Page:
        """Create the recorded (video) context + page and return the page.

        Split out of ``__enter__`` so a caller that needs to do slow setup
        first — discovery, server-side cache pre-warming — can run that work
        BEFORE the video starts. Playwright begins capturing the moment a
        context with ``record_video_dir`` is created, so any navigation that
        happens before this call would otherwise be recorded as a blank
        ``about:blank`` screen at the head of the clip. Pass
        ``defer_record=True`` and call this once you're ready for the first
        real scene. Idempotent-ish: calling twice returns the existing page.
        """
        if self.page is not None:
            return self.page

        record_kwargs: dict[str, Any] = {}
        if self.record:
            record_kwargs = {
                "record_video_dir": str(self.out_dir),
                "record_video_size": dict(self.viewport),
            }
        self.context = self.browser.new_context(
            viewport=self.viewport,
            device_scale_factor=2,
            storage_state=self._storage_state,
            **record_kwargs,
        )
        if self.with_cursor:
            self.context.add_init_script(CURSOR_OVERLAY_JS)

        self.page = self.context.new_page()

        if self.accept_dialogs:

            def _accept(dialog):
                print(f"  dialog: {dialog.message[:80]!r}")
                dialog.accept()

            self.page.on("dialog", _accept)

        self.page.on(
            "console",
            lambda m: self.console_log.append(f"[{m.type}] {m.text[:200]}"),
        )
        self.page.on(
            "pageerror",
            lambda e: self.console_log.append(f"[pageerror] {str(e)[:200]}"),
        )
        return self.page

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.context:
                self.context.close()
            if self.warm_context:
                self.warm_context.close()
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

        # Collect any webms Playwright produced.
        if self.record:
            self.video_paths = sorted(self.out_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if self.console_log:
            tail = self.console_log[-20:]
            print(f"\nConsole log ({len(self.console_log)} entries, last {len(tail)}):")
            for line in tail:
                print(f"  {line}")
        if self.video_paths:
            print(f"\nRecorded video(s) in {self.out_dir}:")
            for v in self.video_paths:
                print(f"  {v} ({v.stat().st_size / 1024:.0f} KB)")


# ---------------------------------------------------------------------- #
# Snapshot manifest
# ---------------------------------------------------------------------- #


def snap(session: RecorderSession, key: str) -> None:
    """Record the visible page text at this scene boundary.

    Writes incrementally so a mid-recording crash still leaves verifiable
    partial data. Reads via session.scene_snapshots + session.manifest_path.
    """
    page = session.page
    try:
        session.scene_snapshots[key] = page.inner_text("body") if page else ""
    except Exception as e:
        session.scene_snapshots[key] = f"<<snapshot failed: {e}>>"
    if session.manifest_path:
        session.manifest_path.write_text(json.dumps(session.scene_snapshots, indent=2))


# ---------------------------------------------------------------------- #
# Page navigation primitives (tolerant of slow labs prod)
# ---------------------------------------------------------------------- #


def goto_and_settle(
    page: Page,
    url: str,
    *,
    timeout: int = 30_000,
    wait_for_selector: str | None = None,
    settle_seconds: float = 1.5,
) -> None:
    """Navigate to ``url`` and wait for the page to be meaningfully ready,
    without depending on ``networkidle`` (which doesn't settle on labs
    because of PAR snapshot polling + bulk-assessment image streaming).

    The contract:

    1. ``page.goto(url, wait_until="domcontentloaded")`` — HTML is parsed
       and the document is ready for selector queries.
    2. ``page.wait_for_load_state("load", timeout=10_000)`` — best-effort
       wait for window.load. Tolerates timeout (some labs pages never
       fully fire load because of long-poll connections).
    3. Optional ``wait_for_selector`` for the page's content marker, e.g.
       ``"text=FLW Name"`` on a chc_nutrition page. Use to pin the
       recorder to the moment when the meaningful content is on screen.
    4. ``page.wait_for_timeout(settle_seconds * 1000)`` so the React app
       has a beat to hydrate.

    Returns silently on success. Raises only if the initial ``goto`` or
    the ``wait_for_selector`` exceed their timeouts — never on the
    tolerant ``load`` wait.

    Use this instead of ``page.goto(url, wait_until='networkidle')`` —
    the latter regularly hangs the recorder past its 30s timeout on slow
    labs days, even when the page is visibly fully rendered.
    """
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_load_state("load", timeout=10_000)
    except Exception:
        # Networkidle / load can hang forever on pages with long-poll or
        # streaming endpoints (PAR snapshot SSE, GDrive image streams).
        # The recorder doesn't need them fully idle — just visible.
        pass
    if wait_for_selector:
        page.wait_for_selector(wait_for_selector, timeout=timeout)
    if settle_seconds > 0:
        page.wait_for_timeout(int(settle_seconds * 1000))


def wait_for_content(
    page: Page,
    selector: str,
    *,
    timeout: int = 15_000,
    settle_seconds: float = 0.5,
) -> None:
    """Wait for a specific content marker to appear, then settle briefly.

    Wraps ``page.wait_for_selector`` with a follow-up settle pause so the
    recording captures the content rendered rather than a half-hydrated
    intermediate state. Use after a click that triggers in-page rerender.
    """
    page.wait_for_selector(selector, timeout=timeout)
    if settle_seconds > 0:
        page.wait_for_timeout(int(settle_seconds * 1000))


# ---------------------------------------------------------------------- #
# Page interaction primitives
# ---------------------------------------------------------------------- #


def slow_move(page: Page, x: float, y: float, steps: int = 40) -> None:
    """Mouse move with enough steps that the cursor overlay can animate it."""
    page.mouse.move(x, y, steps=steps)


def wait_for_text(page: Page, text: str, timeout_ms: int = 15_000) -> None:
    page.wait_for_function(
        "(t) => document.body && document.body.innerText.includes(t)",
        arg=text,
        timeout=timeout_ms,
    )


def click_text(
    page: Page,
    text: str,
    *,
    timeout_ms: int = 4_000,
    post_wait_selector: str | None = None,
    post_wait_timeout_ms: int = 10_000,
    pre_dwell_s: float = 0.4,
) -> bool:
    """Locate text, slow-move cursor onto it, click, wait for the next page state.

    Returns True on successful click + post-wait (or no post-wait), False
    if the text wasn't there. Catches the post_wait_selector timeout and
    logs it rather than raising — the caller's scene snapshot will record
    what actually rendered.
    """
    locator = page.locator(f"text={text}").first
    locator.wait_for(state="visible", timeout=timeout_ms)
    box = locator.bounding_box()
    if not box:
        return False
    slow_move(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(int(pre_dwell_s * 1000))
    locator.click()
    page.wait_for_load_state("networkidle")
    if post_wait_selector:
        try:
            page.wait_for_selector(post_wait_selector, timeout=post_wait_timeout_ms)
        except Exception as e:
            print(f"  ! post-click wait for {post_wait_selector!r} failed: {e}")
    return True


def smooth_scroll_to_text(page: Page, text: str, *, header_tag: str = "h3") -> bool:
    """Smooth-scroll the page so the element containing ``text`` is in view.

    Used to reveal "Coaching Conversation" panels below the fold. Returns
    True if an element was found and scrolled to.
    """
    return bool(
        page.evaluate(
            """({text, tag}) => {
                const el = Array.from(document.querySelectorAll(tag))
                    .find(h => h.textContent.includes(text));
                if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); return true; }
                return false;
            }""",
            {"text": text, "tag": header_tag},
        )
    )


def scroll_through_page(page: Page, *, max_duration_ms: int = 5_000) -> None:
    """Eased top-to-bottom scroll. Used to show off an audit page's full
    set of thumbnails without jump-cutting."""
    height = page.evaluate("() => document.documentElement.scrollHeight")
    viewport_h = page.evaluate("() => window.innerHeight")
    distance = max(0, height - viewport_h)
    if distance <= 50:
        return
    page.evaluate(
        """([dist, maxDur]) => new Promise(res => {
            const start = performance.now();
            const dur = Math.min(maxDur, dist * 1.5);
            function step(t) {
                const r = Math.min(1, (t - start) / dur);
                const eased = r < 0.5 ? 4*r*r*r : 1 - Math.pow(-2*r + 2, 3)/2;
                window.scrollTo(0, dist * eased);
                if (r < 1) requestAnimationFrame(step); else res();
            }
            requestAnimationFrame(step);
        })""",
        [distance, max_duration_ms],
    )


def wait_for_row_count(page: Page, *, at_least: int, timeout_ms: int = 12_000) -> bool:
    """Wait until ``tbody tr`` count reaches ``at_least``. Used after async
    table renders. Returns False on timeout (caller can scene-snap to log
    what was there)."""
    try:
        page.wait_for_function(
            "(n) => document.querySelectorAll('tbody tr').length >= n",
            arg=at_least,
            timeout=timeout_ms,
        )
        return True
    except Exception as e:
        print(f"  ! table didn't reach {at_least} rows: {e}")
        return False


def wait_for_audit_images(page: Page, *, at_least: int, timeout_ms: int = 15_000) -> bool:
    """Wait for at least ``at_least`` ``/audit/image/`` thumbnails to decode.

    The bulk-assessment view streams JPGs from GDrive; without this wait,
    a recording captures the spinner state. Returns False on timeout.
    """
    try:
        page.wait_for_function(
            """(n) => {
                const imgs = Array.from(document.querySelectorAll('img'))
                    .filter(i => i.src.includes('/audit/image/'));
                return imgs.length >= n
                    && imgs.filter(i => i.complete && i.naturalWidth > 0).length >= n;
            }""",
            arg=at_least,
            timeout=timeout_ms,
        )
        return True
    except Exception as e:
        print(f"  ! audit images not fully loaded ({at_least}): {e}")
        return False


# ---------------------------------------------------------------------- #
# Row helpers — table-of-FLWs operations the recorders share
# ---------------------------------------------------------------------- #


def scroll_row_into_view(page: Page, username: str) -> None:
    page.evaluate(
        "(uname) => { const row = [...document.querySelectorAll('tr')]"
        ".find(r => r.innerText.includes(uname));"
        " if (row) row.scrollIntoView({block: 'center'}); }",
        username,
    )


def click_row_button(page: Page, username: str, button_text: str) -> bool:
    """Find the row containing ``username`` and click the button whose label
    matches ``button_text`` (exact-text match preferred, falls back to
    ``includes``).

    Returns True if the click was issued, False if nothing matched. Caller
    is responsible for waiting on the resulting navigation/network.
    """
    clicked = page.evaluate(
        """([uname, label]) => {
            const row = [...document.querySelectorAll('tr')]
                .find(r => r.innerText.includes(uname));
            if (!row) return false;
            const btns = [...row.querySelectorAll('button, a')];
            const exact = btns.find(b => b.innerText.trim() === label);
            const fuzzy = btns.find(b => b.innerText.includes(label));
            const target = exact || fuzzy;
            if (!target) return false;
            target.click();
            return true;
        }""",
        [username, button_text],
    )
    return bool(clicked)


def row_button_labels(page: Page, username: str) -> list[str]:
    """Return the button + link labels visible on ``username``'s row.

    Useful for debugging "Create Audit isn't there" without a recorder
    rerun — print before the click and the labels show whether the
    previous run already created the audit.
    """
    return page.evaluate(
        """(uname) => {
            const row = [...document.querySelectorAll('tr')]
                .find(r => r.innerText.includes(uname));
            if (!row) return [];
            return [...row.querySelectorAll('button, a')]
                .map(b => b.innerText.trim());
        }""",
        username,
    )


def click_menu_item(page: Page, item_text: str, *, timeout_ms: int = 5_000) -> bool:
    """Click an item inside the currently-open MenuButton dropdown.

    The chc_nutrition (and any future flag-aware) Actions cell uses split
    buttons: clicking the trigger opens a popover of quick actions, then
    the caller picks one. This helper waits for the popover to render
    (any button whose text matches ``item_text``), then clicks it.

    The popover panel is rendered with ``absolute z-20`` and no stable
    test id, so we match on visible button text. Highlighted (flag-
    context-aware) items always carry the same text the catalog declares,
    so e.g. ``click_menu_item(page, "Audit low-MUAC visits")`` is the
    canonical way to fire the sam_low/mam_low quick action.

    Returns True if a click was issued, False if the item never appeared.
    Caller waits on the resulting navigation/network themselves.
    """
    import time as _time

    deadline = _time.time() + timeout_ms / 1000
    while _time.time() < deadline:
        clicked = page.evaluate(
            """(label) => {
                const items = [...document.querySelectorAll('div.absolute.z-20 button')];
                const exact = items.find(b => b.innerText.trim() === label);
                const fuzzy = items.find(b => b.innerText.includes(label));
                const target = exact || fuzzy;
                if (!target || target.disabled) return false;
                target.click();
                return true;
            }""",
            item_text,
        )
        if clicked:
            return True
        page.wait_for_timeout(150)
    return False


def dwell_on_menu_item(page: Page, item_text: str, *, timeout_ms: int = 5_000) -> bool:
    """Glide the synthetic cursor onto an open-menu item and rest on it.

    Used right after opening a MenuButton dropdown so the recording shows
    the cursor travelling to the chosen option (reads as deliberate) and
    pausing on it before the click. Does NOT click — call
    ``click_menu_item`` afterwards. Returns True if the item was found and
    the cursor moved, False otherwise (caller can still attempt the click).
    """
    import time as _time

    deadline = _time.time() + timeout_ms / 1000
    box = None
    while _time.time() < deadline:
        box = page.evaluate(
            """(label) => {
                const items = [...document.querySelectorAll('div.absolute.z-20 button')];
                const exact = items.find(b => b.innerText.trim() === label);
                const fuzzy = items.find(b => b.innerText.includes(label));
                const target = exact || fuzzy;
                if (!target) return null;
                const r = target.getBoundingClientRect();
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }""",
            item_text,
        )
        if box:
            break
        page.wait_for_timeout(150)
    if not box:
        return False
    slow_move(page, box["x"], box["y"], steps=22)
    return True
