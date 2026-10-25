"""Drive a labs page's canopy panel in a headless browser, signed in as ace.

One scenario = one fresh browser context (ace's labs cookies from the ``ace:labs-login``
session file), one page load, one NEW conversation. For each turn: type into the panel's
composer, press Enter, wait for the turn to end, then capture the reply, the result cards
(MCP Apps views from Labs, each in its own sandboxed frame), screenshots of the page and
the panel, and the conversation as canopy recorded it.

The end of a turn comes from canopy's session API (``turn_status``) when ace's canopy PAT
is available, else from the panel (no ``stop`` button, and the text has stopped changing).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import canopy_api, vault

SESSION_FILE = Path.home() / ".ace" / "labs-session.json"
CHAT_FRAME = "canopy.dimagi.com/embed/chat"
_SEND = re.compile(r"/api/canopy-sessions/([0-9a-f-]{36})/send")
#: Words in the panel that mean something went wrong around the agent, not in its answer.
PANEL_ERRORS = (
    "needs a canopy account with a vault key",
    "no agent is available here",
    "something went wrong",
    "could not reach",
    "couldn't reach",
    "token error",
    "turn failed",
    "runner is offline",
    "no runner",
    "request failed",
)


class Blocked(Exception):
    """The chat could not be had at all (no panel, refused conversation). Not the agent's fault."""


@dataclass
class TurnCapture:
    say: str
    reply: str = ""
    panel_text: str = ""
    cards: list[dict] = field(default_factory=list)  # new cards this turn
    tool_calls: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    finished: bool = False
    status: dict = field(default_factory=dict)
    panel_errors: list[str] = field(default_factory=list)
    screenshots: dict = field(default_factory=dict)
    source: str = "api"  # where reply/tool calls came from: api | panel
    # The Data page's canvas (filters.canvas in the panel's page state) before and after the turn.
    canvas_before: str | None = None
    canvas_after: str | None = None
    active_tab_after: str | None = None
    save_dialog: dict = field(default_factory=dict)
    unlock: dict = field(default_factory=dict)  # a sealed banner seen this turn, and the unlock


@dataclass
class ScenarioCapture:
    url: str = ""
    final_url: str = ""
    session_id: str = ""
    agent: str = ""
    page_state: dict | None = None
    blocked: str = ""
    turns: list[TurnCapture] = field(default_factory=list)
    transcript: dict | None = None
    console_errors: list[str] = field(default_factory=list)
    sensitive: bool = False
    # A sealed conversation reopened after a reload: banner, unlock, and whether the last reply reads again.
    history: dict = field(default_factory=dict)


class PanelDriver:
    def __init__(self, out_dir: Path, *, headed: bool = False, turn_timeout: int = 480, canopy=None):
        self.out_dir = out_dir
        self.headed = headed
        self.turn_timeout = turn_timeout
        self.canopy = canopy or canopy_api.CanopyAPI()

    def run(self, page_url: str, turns: list[str]) -> ScenarioCapture:
        from playwright.sync_api import sync_playwright

        cap = ScenarioCapture(url=page_url)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not self.headed)
            ctx = browser.new_context(storage_state=str(SESSION_FILE), viewport={"width": 1600, "height": 1000})
            page = ctx.new_page()
            sessions: list[str] = []
            page.on("request", lambda r: _note_session(r.url, sessions))
            page.on("console", lambda m: cap.console_errors.append(m.text[:300]) if m.type == "error" else None)
            try:
                self._open(page, page_url, cap)
                frame = self._open_panel(page, cap)
                for i, text in enumerate(turns):
                    turn = self._turn(page, frame, text, i, sessions, cap)
                    cap.turns.append(turn)
                    if turn.panel_errors and any("vault key" in e for e in turn.panel_errors):
                        cap.blocked = turn.panel_errors[0]
                        break
                if cap.session_id:
                    cap.transcript = self.canopy.session(cap.session_id)
                    cap.sensitive = bool((cap.transcript or {}).get("sensitive"))
                if cap.sensitive and cap.turns and not cap.blocked:
                    cap.history = self._history(page, cap)
            except Blocked as b:
                cap.blocked = str(b)
                page.screenshot(path=str(self.out_dir / "blocked.png"))
            finally:
                browser.close()
        return cap

    # -- steps ---------------------------------------------------------------

    def _open(self, page, url: str, cap: ScenarioCapture) -> None:
        page.goto(url, wait_until="load", timeout=120_000)
        cap.final_url = page.url
        if "/labs/login" in page.url or "/accounts/login" in page.url:
            raise Blocked("ace's labs session has expired: run the ace plugin's /ace:labs-login")
        page.wait_for_timeout(5000)
        if not page.locator("#canopy-page-state").count():
            why = page.evaluate(
                "() => { const m = document.body.innerText.match(/not shown on this page:[^\\n]*/);"
                " return m ? m[0] : null }"
            )
            raise Blocked(why or "the page drew no canopy panel (no #canopy-page-state)")
        options = page.evaluate(
            "() => { const e = document.getElementById('canopy-panel-options');"
            " return e ? JSON.parse(e.textContent) : null }"
        )
        cap.agent = (options or {}).get("agent") or "(default agent)"
        cap.page_state = page.evaluate("() => window.canopyHost ? window.canopyHost.pageState() : null")

    def _open_panel(self, page, cap: ScenarioCapture):
        frame = self._chat_frame(page)
        if frame is None or not frame.locator("textarea").first.is_visible():
            launcher = page.get_by_text("Ask an agent")
            if not launcher.count():
                raise Blocked("no 'Ask an agent' launcher on the page")
            launcher.first.click()
            deadline = time.time() + 30
            while time.time() < deadline:
                frame = self._chat_frame(page)
                if frame is not None and frame.locator("textarea").count():
                    break
                page.wait_for_timeout(1000)
        if frame is None or not frame.locator("textarea").count():
            raise Blocked("the canopy panel opened no composer")
        new_chat = frame.get_by_role("button", name="New chat")
        if new_chat.count() and frame.locator("[data-turn-index]").count():
            new_chat.first.click()  # never continue an earlier conversation
            page.wait_for_timeout(2000)
        return frame

    def _chat_frame(self, page):
        for f in page.frames:
            if CHAT_FRAME in f.url:
                return f
        return None

    def _turn(self, page, frame, text: str, index: int, sessions: list[str], cap: ScenarioCapture) -> TurnCapture:
        turn = TurnCapture(say=text)
        turn.canvas_before = _canvas(page).get("canvas")
        cards_before = len(self._card_frames(page))
        box = frame.locator("textarea").first
        box.fill(text)
        box.press("Enter")
        t0 = time.time()
        last_text, stable, retried = "", 0, False
        while time.time() - t0 < self.turn_timeout:
            page.wait_for_timeout(5000)
            if sessions and not cap.session_id:
                cap.session_id = sessions[-1]
            panel = _safe(lambda: frame.evaluate("() => document.body.innerText"), "")
            errors = [e for e in PANEL_ERRORS if e in panel.lower()]
            if errors and "vault key" in errors[0]:
                turn.panel_errors = [_line_with(panel, errors[0])]
                break
            if "request failed" in errors and not cap.session_id:
                # canopy refused to start the conversation (e.g. a 502). A person would press
                # send again; do that once, and record it either way.
                turn.panel_errors.append(_line_with(panel, "request failed"))
                if retried:
                    break
                retried = True
                if box.input_value().strip():
                    box.press("Enter")
                else:
                    box.fill(text)
                    box.press("Enter")
                continue
            if cap.session_id and self.canopy.enabled:
                session = self.canopy.session(cap.session_id)
                if session and canopy_api.turn_finished(session, text, index):
                    turn.finished, turn.status = True, session.get("turn_status") or {}
                    break
            else:
                running = frame.get_by_role("button", name="stop").count() > 0
                stable = stable + 1 if (panel == last_text and not running) else 0
                last_text = panel
                if stable >= 3 and len(panel) > 0:
                    # Quiet is the end of a turn only if a conversation exists to have one.
                    turn.finished, turn.source = bool(cap.session_id), "panel"
                    break
        turn.seconds = round(time.time() - t0, 1)
        page.wait_for_timeout(6000)  # let cards fetch their rows and draw
        turn.panel_text = _safe(lambda: frame.evaluate("() => document.body.innerText"), "")
        turn.panel_errors = list(
            dict.fromkeys(
                turn.panel_errors
                + [_line_with(turn.panel_text, e) for e in PANEL_ERRORS if e in turn.panel_text.lower()]
            )
        )
        if not cap.session_id:
            turn.finished = False
            turn.panel_errors.append("no conversation was created (canopy never received the message)")
        turn.cards = [self._card(f) for f in self._card_frames(page)[cards_before:]]
        if cap.session_id and self.canopy.enabled:
            session = self.canopy.session(cap.session_id) or {}
            sliced = canopy_api.turn_slice(session, text, index)
            turn.reply = canopy_api.reply_text(sliced)
            turn.tool_calls = canopy_api.tool_calls(sliced)
            if not turn.status:
                turn.status = session.get("turn_status") or {}
        if not turn.reply and cap.session_id:
            turn.reply, turn.source = _panel_reply(turn.panel_text, text), "panel"
        turn.unlock = vault.unlock(frame, page)  # live replies need none; a banner here is a finding
        state = _canvas(page)
        turn.canvas_after, turn.active_tab_after, turn.save_dialog = (
            state.get("canvas"),
            state.get("tab"),
            state.get("dialog") or {},
        )
        turn.screenshots = self._shots(page, frame, index)
        return turn

    def _history(self, page, cap: ScenarioCapture) -> dict:
        """Reload, reopen the panel, and read the sealed conversation back: does the panel
        offer it again, does ace's vault key unlock it, and does the last reply read again."""
        out = {"reopened": False, "banner": False, "unlocked": False, "reply_visible": False, "error": ""}
        try:
            page.reload(wait_until="load", timeout=120_000)
            page.wait_for_timeout(6000)
            frame = self._chat_frame(page)
            if frame is None or not frame.locator("textarea").first.is_visible():
                page.get_by_text("Ask an agent").first.click()
                page.wait_for_timeout(6000)
                frame = self._chat_frame(page)
            if frame is None:
                out["error"] = "no panel after reload"
                return out
            if not frame.locator("[data-turn-index]").count():
                # Not restored: pick it from the panel's list of earlier conversations. A private
                # one is listed as "Private conversation" (its title is sealed too), newest first.
                listed = frame.get_by_role("button", name="All conversations")
                if listed.count():
                    listed.first.click()
                    page.wait_for_timeout(2000)
                title = cap.turns[0].say[:40]
                for label in (title, "Private conversation"):
                    hit = frame.get_by_text(label, exact=False)
                    if hit.count():
                        out["picked_from_list"] = label
                        hit.first.click()
                        page.wait_for_timeout(6000)
                        break
            out["reopened"] = frame.locator("[data-turn-index]").count() > 0
            page.screenshot(path=str(self.out_dir / "history-locked.png"))
            unlocked = vault.unlock(frame, page)
            out.update(banner=unlocked["banner"], unlocked=unlocked["unlocked"], error=unlocked["error"])
            page.wait_for_timeout(4000)
            text = frame.evaluate("() => document.body.innerText")
            needle = _needle(cap.turns[-1].reply)
            out["reply_visible"] = bool(needle) and needle in text
            page.screenshot(path=str(self.out_dir / "history-unlocked.png"))
        except Exception as e:  # noqa: BLE001
            out["error"] = vault.scrub(f"{type(e).__name__}: {e}")[:300]
        return out

    def _card_frames(self, page):
        """Each Labs view's innermost frame (the app's own document), in page order."""
        return [
            f for f in page.frames if f.url == "about:srcdoc" and f.parent_frame and "mcp-apps" in f.parent_frame.url
        ]

    def _card(self, f) -> dict:
        """What a Labs card shows: its text and cells, headers, tabs, and whether a chart is
        drawn -- or would be, one click on its Chart tab away (then it is clicked back)."""
        info = _safe(lambda: f.evaluate(_CARD_JS), {}) or {}
        if not (info.get("svg") or info.get("canvas")) and "Chart" in (info.get("tabs") or []):
            info["chart_on_click"] = bool(_safe(lambda: f.evaluate(_CHART_ON_CLICK_JS), 0))
        return info

    def _shots(self, page, frame, index: int) -> dict:
        shots = {}
        page_path = self.out_dir / f"turn{index + 1}-page.png"
        page.screenshot(path=str(page_path))
        shots["page"] = page_path.name
        try:
            panel = page.locator(f"iframe[src*='{CHAT_FRAME.split('/', 1)[1]}']").first
            panel_path = self.out_dir / f"turn{index + 1}-panel.png"
            panel.screenshot(path=str(panel_path))
            shots["panel"] = panel_path.name
            cards = frame.locator("section[data-testid=app-view]")
            for i in range(cards.count()):
                card = cards.nth(i)
                if card.locator("[data-testid=app-view-body]").get_attribute("hidden") is not None:
                    continue  # a collapsed earlier card
                card_path = self.out_dir / f"turn{index + 1}-card{i + 1}.png"
                card.scroll_into_view_if_needed(timeout=5000)
                card.screenshot(path=str(card_path))
                shots.setdefault("cards", []).append(card_path.name)
        except Exception as e:  # noqa: BLE001 -- a missing screenshot is noted, never fatal
            shots["error"] = str(e)[:200]
        return shots


def _canvas(page) -> dict:
    """The Data page's canvas as the panel is told it, its active tab, and the Save dialog."""
    return (
        _safe(
            lambda: page.evaluate("""() => {
                    const st = window.canopyHost ? window.canopyHost.pageState() : null;
                    const tab = document.querySelector('.ex-tab[aria-selected="true"]');
                    const dlg = document.querySelector('#ex-save-dialog');
                    const name = document.querySelector('#ex-save-name');
                    // The dialog is position: fixed, so its offsetParent is always null: read
                    // whether it is shown from `hidden` and its computed display instead.
                    const open = !!dlg && (dlg.open || (!dlg.hidden && getComputedStyle(dlg).display !== 'none'));
                    return {
                        canvas: st && st.filters ? (st.filters.canvas || null) : null,
                        tab: tab ? tab.textContent.trim() : null,
                        dialog: {open: open, name: name ? name.value : null},
                    };
                }"""),
            {},
        )
        or {}
    )


def _needle(reply: str) -> str:
    """A stretch of the reply's first line that will appear verbatim in the panel."""
    for line in (reply or "").splitlines():
        words = line.replace("*", "").replace("`", "").split()
        if len(words) >= 4:
            return " ".join(words[:6])
    return ""


_CARD_JS = """() => ({
    text: (document.body.innerText || '').slice(0, 6000),
    cells: Array.from(document.querySelectorAll('th, td')).slice(0, 600).map(c => c.textContent.trim()),
    headers: Array.from(document.querySelectorAll('th')).map(t => t.textContent.trim()),
    tabs: Array.from(document.querySelectorAll('[data-tab]')).map(b => b.textContent.trim()),
    active_tab: (document.querySelector('[data-tab][aria-selected="true"]') || {}).textContent || '',
    svg: document.querySelectorAll('svg').length,
    canvas: document.querySelectorAll('canvas').length,
    rows: document.querySelectorAll('tbody tr').length,
})"""


_CHART_ON_CLICK_JS = """() => new Promise(done => {
    const tab = document.querySelector('[data-tab="chart"]');
    const back = document.querySelector('[data-tab][aria-selected="true"]');
    if (!tab) return done(0);
    tab.click();
    setTimeout(() => {
        const n = document.querySelectorAll('svg').length + document.querySelectorAll('canvas').length;
        if (back) document.querySelector('[data-tab="' + back.dataset.tab + '"]').click();
        done(n);
    }, 800);
})"""


def _note_session(url: str, sessions: list[str]) -> None:
    m = _SEND.search(url)
    if m and m.group(1) not in sessions:
        sessions.append(m.group(1))


def _safe(fn, default):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _line_with(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line.lower():
            return line.strip()[:300]
    return needle


def _panel_reply(panel_text: str, said: str) -> str:
    """The panel's words after our message (fallback when canopy's API cannot be read)."""
    i = panel_text.rfind(said)
    tail = panel_text[i + len(said) :] if i >= 0 else panel_text
    for chrome in ("attach\nsend", "attach\nstop\nsend", "\nsend"):
        if tail.endswith(chrome):
            tail = tail[: -len(chrome)]
    return tail.strip()


def dump(cap: ScenarioCapture) -> dict:
    from dataclasses import asdict

    return json.loads(json.dumps(asdict(cap), default=str))
