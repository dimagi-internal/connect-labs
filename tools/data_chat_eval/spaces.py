"""Data space scenarios: a space made on synthetic data, driven through the page as ace.

A space scenario (``spaces:`` in a suite) walks one space's life, checking each step:

1. **create** -- ``POST /labs/data/api/spaces/`` from ace's own page session (CSRF), on the
   scenario's sources.
2. **open** -- ``/labs/data/space/<id>/`` draws the space (``#ex-page[data-space-id]``) and
   tells the panel (``canopyHost.pageState().filters.space_id``).
2b. **who can open it** -- ace made the space, so ace manages it: the space says so
   (``is_manager``, ``audience: holders``, ace among ``managers``), the rail shows the
   "Who can open" control, and ace can set it ``private`` (still opens, as its manager, on
   the page and through ``data_query``) and back to ``holders``.
3. **held to the space (MCP, ace's PAT)** -- ``data_query`` with ``space_id`` answers the
   ground truth; with another opportunity beside it is refused ``outside_space``; SQL naming
   an opportunity outside the space reads nothing.
4. **showQuery** -- the agent's page action (``window.LABSDATA_ACTIONS.showQuery``, exactly
   what canopy calls as ``page_showQuery``) opens a canvas tab, runs the query as the person,
   shows the truth, and the panel's state says what the canvas shows (``filters.canvas``).
5. **proposeSave + Save** -- the Save dialog is filled, nothing is saved until the click;
   after ``#ex-save-confirm`` the item is in ``#ex-space-items`` and the items API.
6. **the agent in the space** -- one chat turn in the space's panel; the canvas should switch.
6b. **saved conversation** -- the panel reports the conversation it has open (``{id,
   resource, private}``: canopy's id, begun on THIS space); "Save this conversation" keeps a
   pointer (session id + the label typed, nothing it said); after a reload the rail lists it,
   clicking it opens that conversation in the panel (canopy, as ace), and -- unlocked with
   ace's test vault key -- the agent's reply reads again.
7. **archive** -- ``POST .../archive/``; the space's page then answers 404.

Teammate and lock-out rules (a member's view, private hiding a non-manager) need a second
synthetic person; not covered yet -- the unit tests pin them (``labsdata/tests/test_spaces.py``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import checks
from .suite import LABS

API = f"{LABS}/labs/data/api"


@dataclass
class SpaceScenario:
    id: str
    title: str
    sources: dict
    sql: str
    truth_sql: str
    truth_column: str
    outside_opportunity: int
    ask: str = ""
    #: When set, the ask also asks the agent to propose saving the query under this name; the
    #: harness then clicks Save as the person and checks the item lands in the space.
    save_as: str = ""
    #: Step 6c: asked for a small dashboard, the agent drafts an artifact
    #: (page_showArtifactDraft) and proposes keeping it under ``artifact_as``.
    artifact_ask: str = ""
    artifact_as: str = ""
    covers: str = ""
    truth: dict = field(default_factory=dict)


def load(data: dict) -> list[SpaceScenario]:
    out = []
    for raw in data.get("spaces") or []:
        out.append(
            SpaceScenario(
                id=raw["id"],
                title=raw.get("title", raw["id"]),
                sources=dict(raw["sources"]),
                sql=raw["sql"],
                truth_sql=raw.get("truth_sql", raw["sql"]),
                truth_column=raw["truth_column"],
                outside_opportunity=int(raw["outside_opportunity"]),
                ask=raw.get("ask", ""),
                save_as=raw.get("save_as", ""),
                artifact_ask=raw.get("artifact_ask", ""),
                artifact_as=raw.get("artifact_as", ""),
                covers=raw.get("covers", ""),
            )
        )
    return out


class _Steps:
    def __init__(self):
        self.results: list[dict] = []

    def add(self, step: int, name: str, passed: bool, detail: str, route: str, gate: bool = True):
        self.results.append(
            {"turn": step, "name": name, "passed": bool(passed), "detail": detail[:500], "gate": gate, "route": route}
        )
        return passed


ROUTE_SPACE = "data spaces: connect_labs/labsdata/spaces.py + views.py (Space*View)"
ROUTE_MCP = "space-held data_* tools: connect_labs/mcp/tools/labsdata.py (_space / outside_space)"
ROUTE_PAGE = "the Data page's canvas and page actions: connect_labs/static/labsdata/data.js (ACTIONS)"
ROUTE_CONVERSATION = (
    "saved conversations: connect_labs/labsdata/spaces.py (save_conversation) + views.SpaceConversations*View"
    " + static/labsdata/data.js (Conversations card); the panel's report/openSession: canopy-web"
    " frontend/src/embed (hostLink, EmbedApp) and sdk panel.html"
)
ROUTE_ACL = "who can open a space: connect_labs/labsdata/spaces.py (AUDIENCES, set_audience) + views.SpaceAudienceView"


def run(sc: SpaceScenario, mcp, out_dir: Path, args) -> dict:
    from playwright.sync_api import sync_playwright

    from .driver import SESSION_FILE, PanelDriver, ScenarioCapture

    out_dir.mkdir(parents=True, exist_ok=True)
    steps = _Steps()
    entry = {
        "id": sc.id,
        "title": sc.title,
        "covers": sc.covers,
        "surface": "data_space",
        "url": f"{LABS}/labs/data/",
        "truth": {},
        "checks": steps.results,
        "turns": [],
        "agent": "labsdata",
    }
    opps = sc.sources.get("opportunity_ids") or []
    truth = mcp.query(sc.truth_sql, opps, max_rows=5)
    entry["truth"] = sc.truth = dict(zip(truth["columns"], truth["rows"][0])) if truth.get("rows") else {}
    want = sc.truth.get(sc.truth_column)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(storage_state=str(SESSION_FILE), viewport={"width": 1600, "height": 1000})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        space_id = None
        try:
            # 1. create
            page.goto(f"{LABS}/labs/data/", wait_until="load", timeout=120_000)
            name = f"eval {sc.id} {time.strftime('%Y%m%d-%H%M%S')}"
            made = _post(
                page, f"{API}/spaces/", {"name": name, "description": "data_chat_eval (synthetic)", **sc.sources}
            )
            space_id = ((made.get("body") or {}).get("space") or {}).get("id")
            if not steps.add(
                1, "create space", space_id, f"HTTP {made['status']}: {str(made['body'])[:300]}", ROUTE_SPACE
            ):
                return entry
            space_url = f"{LABS}/labs/data/space/{space_id}/"
            entry["url"] = space_url

            # 2. open
            page.goto(space_url, wait_until="load", timeout=120_000)
            page.wait_for_timeout(5000)
            drawn = page.evaluate(
                "() => { const e = document.querySelector('#ex-page'); return e ? e.dataset.spaceId : null }"
            )
            state = page.evaluate("() => window.canopyHost ? window.canopyHost.pageState() : null") or {}
            entry["page_state"] = state
            steps.add(
                2,
                "space page draws the space",
                str(drawn) == str(space_id),
                f"#ex-page data-space-id={drawn}",
                ROUTE_PAGE,
            )
            filt = (state.get("filters") or {}) if isinstance(state, dict) else {}
            steps.add(
                2,
                "panel told the space",
                str(filt.get("space_id")) == str(space_id),
                f"page state filters: {json.dumps(filt)[:200]}" if state else "no canopy panel on the space page",
                "the space page's panel state: connect_labs/labsdata/views.py (DataView canopy_panel filters)",
            )
            page.screenshot(path=str(out_dir / "step2-space.png"))
            _audience(page, steps, mcp, sc, space_id, space_url, want, out_dir)

            # 3. held to the space (MCP as ace)
            try:
                r = mcp.call("data_query", {"space_id": space_id, "sql": sc.truth_sql, "max_rows": 5})
                got = dict(zip(r["columns"], r["rows"][0])).get(sc.truth_column) if r.get("rows") else None
                steps.add(
                    3, "data_query(space_id) answers the truth", got == want, f"expected {want}, got {got}", ROUTE_MCP
                )
            except Exception as e:  # noqa: BLE001
                steps.add(3, "data_query(space_id) answers the truth", False, str(e), ROUTE_MCP)
            try:
                mcp.call(
                    "data_query", {"space_id": space_id, "opportunity_ids": [sc.outside_opportunity], "sql": sc.sql}
                )
                steps.add(
                    3, "outside selection refused", False, "data_query read another opportunity in a space", ROUTE_MCP
                )
            except Exception as e:  # noqa: BLE001
                steps.add(
                    3,
                    "outside selection refused",
                    "outside_space" in str(e) or "held to data space" in str(e),
                    str(e)[:200],
                    ROUTE_MCP,
                )
            try:
                r = mcp.call(
                    "data_query",
                    {
                        "space_id": space_id,
                        "sql": f"select count(*) as n from visits where opportunity_id = {sc.outside_opportunity}",
                    },
                )
                n = (r.get("rows") or [[None]])[0][0]
                steps.add(3, "SQL naming an outside opportunity reads nothing", n == 0, f"count = {n}", ROUTE_MCP)
            except Exception as e:  # noqa: BLE001
                steps.add(
                    3, "SQL naming an outside opportunity reads nothing", True, f"refused: {str(e)[:200]}", ROUTE_MCP
                )

            # 4. showQuery (the agent's page action, called as canopy would)
            title = "Eval: " + sc.id
            shown = _safe_eval(
                page, "args => window.LABSDATA_ACTIONS.showQuery(args)", {"sql": sc.truth_sql, "title": title}
            )
            page.wait_for_timeout(6000)
            steps.add(
                4,
                "showQuery acknowledged",
                isinstance(shown, dict) and shown.get("outcome") == "shown_to_person",
                json.dumps(shown)[:300],
                ROUTE_PAGE,
            )
            active = page.evaluate(
                "() => { const t = document.querySelector('.ex-tab[aria-selected=\"true\"]');"
                " return t ? t.textContent.trim() : null }"
            )
            steps.add(
                4,
                "canvas switched to the query",
                bool(active) and title in active,
                f"active tab: {active!r}",
                ROUTE_PAGE,
            )
            result_text = page.evaluate(
                "() => { const r = document.querySelector('#ex-panes') || document.querySelector('#ex-canvas');"
                " return r ? r.innerText : '' }"
            )
            if want is not None:
                nums = checks.numbers_in(result_text)
                steps.add(
                    4,
                    "canvas shows the truth",
                    float(want) in nums,
                    f"expected {want} in the canvas result",
                    ROUTE_PAGE,
                )
            state = page.evaluate("() => window.canopyHost ? window.canopyHost.pageState() : null") or {}
            canvas = ((state.get("filters") or {}).get("canvas")) if isinstance(state, dict) else None
            steps.add(
                4,
                "panel told what the canvas shows",
                bool(canvas) and title in str(canvas),
                f"filters.canvas = {canvas!r}",
                ROUTE_PAGE,
            )
            page.screenshot(path=str(out_dir / "step4-showquery.png"))

            # 5. proposeSave, then the person's click
            item = f"eval item {time.strftime('%H%M%S')}"
            proposed = _safe_eval(
                page, "args => window.LABSDATA_ACTIONS.proposeSave(args)", {"name": item, "sql": sc.sql}
            )
            page.wait_for_timeout(1500)
            steps.add(
                5,
                "proposeSave fills the dialog, saves nothing",
                isinstance(proposed, dict) and proposed.get("proposed") and proposed.get("saved") is False,
                json.dumps(proposed)[:300],
                ROUTE_PAGE,
            )
            filled = page.evaluate(
                "() => { const e = document.querySelector('#ex-save-name'); return e ? e.value : null }"
            )
            target = page.evaluate(
                "() => { const e = document.querySelector('#ex-save-target');"
                " return e ? (e.value || e.textContent) : null }"
            )
            steps.add(
                5, "dialog names the item", filled == item, f"#ex-save-name={filled!r}, target={target!r}", ROUTE_PAGE
            )
            page.screenshot(path=str(out_dir / "step5-dialog.png"))
            page.locator("#ex-save-confirm").click()
            page.wait_for_timeout(4000)
            rail = page.evaluate(
                "() => { const e = document.querySelector('#ex-space-items'); return e ? e.innerText : '' }"
            )
            steps.add(
                5, "item appears in the space's rail", item in rail, f"#ex-space-items: {rail[:200]!r}", ROUTE_PAGE
            )
            items = _get(page, f"{API}/spaces/{space_id}/items/")
            names = [i.get("name") for i in (items.get("body") or {}).get("items", [])]
            steps.add(5, "item saved in the space (API)", item in names, f"items: {names}", ROUTE_SPACE)
            page.screenshot(path=str(out_dir / "step5-saved.png"))

            # 6. the agent in the space
            if sc.ask:
                driver = PanelDriver(out_dir, turn_timeout=args.turn_timeout)
                cap = ScenarioCapture(url=space_url)
                sessions: list[str] = []
                page.on("request", lambda r: _note(r.url, sessions))
                try:
                    page.goto(space_url, wait_until="load", timeout=120_000)
                    driver._open(page, space_url, cap)
                    frame = driver._open_panel(page, cap)
                    before = ((page.evaluate("() => window.canopyHost.pageState()") or {}).get("filters") or {}).get(
                        "canvas"
                    )
                    turn = driver._turn(page, frame, sc.ask, 0, sessions, cap)
                    entry["turns"] = [_turn_dict(turn)]
                    if turn.panel_errors and any("vault key" in e for e in turn.panel_errors):
                        entry["blocked_agent"] = turn.panel_errors[0]
                        steps.add(
                            6,
                            "agent answers in the space",
                            False,
                            f"BLOCKED: {turn.panel_errors[0]}",
                            checks.blocked_route("vault key"),
                            gate=False,
                        )
                    else:
                        steps.add(
                            6,
                            "agent answered",
                            turn.finished and bool(turn.reply),
                            turn.reply[:200] or "no reply",
                            checks.ROUTES["finished"],
                        )
                        steps.add(
                            6,
                            "agent switched the canvas",
                            bool(turn.canvas_after) and turn.canvas_after != before,
                            f"canvas {before!r} -> {turn.canvas_after!r} (tab {turn.active_tab_after!r})",
                            checks.ROUTES["canvas"],
                        )
                        if sc.save_as:
                            _agent_save(page, steps, turn, sc, space_id, out_dir)
                        if turn.finished and cap.session_id:
                            _saved_conversation(
                                page, driver, steps, turn, cap.session_id, space_id, space_url, out_dir
                            )
                        if want is not None:
                            r = checks.run({"number": {"value": want}}, turn, {})
                            steps.add(6, "agent's answer has the truth", r.passed, r.detail, r.route, gate=False)
                except Exception as e:  # noqa: BLE001
                    steps.add(
                        6,
                        "agent answers in the space",
                        False,
                        f"{type(e).__name__}: {e}"[:300],
                        checks.ROUTES["blocked"],
                    )

            # 6c. the agent drafts an artifact
            if sc.artifact_ask:
                _agent_artifact(page, steps, sc, space_id, space_url, out_dir, args, entry)

            # 7. archive
            arch = _post(page, f"{API}/spaces/{space_id}/archive/", {})
            gone = page.request.get(space_url)
            steps.add(
                7,
                "archived space is gone",
                arch["status"] == 200 and gone.status == 404,
                f"archive HTTP {arch['status']}, then the page answers {gone.status}",
                ROUTE_SPACE,
            )
            space_id = None
        finally:
            if space_id:
                _post(page, f"{API}/spaces/{space_id}/archive/", {})  # never leave a test space behind
            browser.close()
    return entry


def _audience(page, steps: _Steps, mcp, sc: SpaceScenario, space_id, space_url: str, want, out_dir: Path) -> None:
    """Step 2b: ace, the space's creator, manages it -- sets it private and back."""
    got = _get(page, f"{API}/spaces/{space_id}/")
    space = (got.get("body") or {}).get("space") or {}
    managers = [m.get("username") for m in space.get("managers") or []]
    steps.add(
        2,
        "the creator manages the space (holders by default)",
        space.get("is_manager") is True and space.get("audience") == "holders" and len(managers) == 1,
        f"is_manager={space.get('is_manager')}, audience={space.get('audience')!r}, managers={managers}",
        ROUTE_ACL,
    )
    control = page.evaluate(
        "() => { const e = document.querySelector('#ex-audience');"
        " return e ? {hidden: e.hidden, value: e.value} : null }"
    )
    steps.add(
        2,
        "the rail offers 'Who can open' to its manager",
        bool(control) and control.get("hidden") is False and control.get("value") == "holders",
        f"#ex-audience: {control}",
        "the rail's access card: connect_labs/static/labsdata/data.js (renderAccess)",
    )
    private = _post(page, f"{API}/spaces/{space_id}/audience/", {"audience": "private"})
    after = ((private.get("body") or {}).get("space") or {}).get("audience")
    steps.add(
        2,
        "manager sets it private",
        private["status"] == 200 and after == "private",
        f"HTTP {private['status']}: {after!r}",
        ROUTE_ACL,
    )
    opened = page.request.get(space_url)
    steps.add(
        2, "a private space still opens for its manager", opened.status == 200, f"page {opened.status}", ROUTE_ACL
    )
    try:
        r = mcp.call("data_query", {"space_id": space_id, "sql": sc.truth_sql, "max_rows": 5})
        got_n = dict(zip(r["columns"], r["rows"][0])).get(sc.truth_column) if r.get("rows") else None
        steps.add(
            2,
            "data_query on the private space, as its manager",
            got_n == want,
            f"expected {want}, got {got_n}",
            ROUTE_ACL,
        )
    except Exception as e:  # noqa: BLE001
        steps.add(2, "data_query on the private space, as its manager", False, str(e)[:300], ROUTE_ACL)
    back = _post(page, f"{API}/spaces/{space_id}/audience/", {"audience": "holders"})
    after = ((back.get("body") or {}).get("space") or {}).get("audience")
    steps.add(
        2,
        "manager sets it back to holders",
        back["status"] == 200 and after == "holders",
        f"HTTP {back['status']}: {after!r}",
        ROUTE_ACL,
    )
    page.screenshot(path=str(out_dir / "step2b-audience.png"))


def _agent_save(page, steps: _Steps, turn, sc: SpaceScenario, space_id, out_dir: Path) -> None:
    """The agent proposed a save (page_proposeSave): the dialog is open; the person clicks Save."""
    dialog = turn.save_dialog or {}
    if not steps.add(
        6,
        "agent proposed the save",
        bool(dialog.get("open")),
        f"Save dialog open, name {dialog.get('name')!r}" if dialog.get("open") else "no Save dialog open",
        checks.ROUTES["canvas"],
    ):
        return
    page.locator("#ex-save-confirm").click()
    page.wait_for_timeout(4000)
    name = dialog.get("name") or ""
    rail = page.evaluate("() => { const e = document.querySelector('#ex-space-items'); return e ? e.innerText : '' }")
    items = _get(page, f"{API}/spaces/{space_id}/items/")
    names = [i.get("name") for i in (items.get("body") or {}).get("items", [])]
    steps.add(
        6,
        "the agent's proposal, saved by the person, is in the space",
        name in names and name in rail,
        f"saved {name!r}; items {names}",
        ROUTE_PAGE,
    )
    steps.add(
        6,
        "the agent named it as asked",
        sc.save_as.lower() in name.lower(),
        f"name {name!r}",
        checks.ROUTES["canvas"],
        gate=False,
    )
    page.screenshot(path=str(out_dir / "step6-agent-saved.png"))


ROUTE_ARTIFACT = (
    "artifacts: connect_labs/static/labsdata/data.js (showArtifactDraft, openArtifactTab) +"
    " components/workflow/sandbox/artifact.tsx; the agent's how-to: connect_labs/labsdata/guidance.py PAGE_ACTIONS"
)

_ARTIFACT_STATE_JS = """() => {
    const tab = document.querySelector('.ex-tab[aria-selected="true"]');
    const pane = [...document.querySelectorAll('#ex-panes > .ex-pane')].find((p) => !p.hidden);
    const frame = pane && pane.querySelector('iframe');
    const title = document.getElementById('ex-save-title');
    return {
        tab: tab ? tab.innerText : null,
        frame: frame ? {sandbox: frame.getAttribute('sandbox'), src: frame.getAttribute('src')} : null,
        waiting: !!(pane && pane.querySelector('[data-testid="artifact-draw"]')),
        dialog: !document.getElementById('ex-save-dialog').hidden,
        dialog_title: title ? title.textContent : null,
        name: document.getElementById('ex-save-name').value,
    };
}"""


def _artifact_frame_text(page) -> str:
    for f in page.frames:
        if "/labs/sandbox/render/" in f.url:
            try:
                text = f.inner_text("body", timeout=5000)
            except Exception:  # noqa: BLE001
                continue
            if text.strip():
                return text
    return ""


def _agent_artifact(page, steps: _Steps, sc: SpaceScenario, space_id, space_url: str, out_dir: Path, args, entry):
    """Step 6c: asked for a small dashboard, the agent drafts an artifact in the canvas
    (page_showArtifactDraft) -- drawn by the workflow sandbox -- and proposes keeping it; the
    person clicks Save; it is in the rail, and opens from it."""
    from .driver import PanelDriver, ScenarioCapture

    driver = PanelDriver(out_dir, turn_timeout=args.turn_timeout)
    cap = ScenarioCapture(url=space_url)
    sessions: list[str] = []
    page.on("request", lambda r: _note(r.url, sessions))
    try:
        page.goto(space_url, wait_until="load", timeout=120_000)
        driver._open(page, space_url, cap)
        frame = driver._open_panel(page, cap)
        # A fresh conversation, so this is its turn 0 (canopy's turns are counted per
        # conversation); step 6's own turn screenshots are kept under another name first.
        for shot in out_dir.glob("turn1-*.png"):
            shot.rename(out_dir / f"step6-{shot.name}")
        turn = driver._turn(page, frame, sc.artifact_ask, 0, sessions, cap)
        entry["turns"] = [*entry.get("turns", []), _turn_dict(turn)]
        steps.add(
            6,
            "agent answered the dashboard ask",
            turn.finished and bool(turn.reply),
            turn.reply[:200] or "no reply",
            checks.ROUTES["finished"],
        )
        state = page.evaluate(_ARTIFACT_STATE_JS) or {}
        if state.get("waiting"):
            page.locator('[data-testid="artifact-draw"]').first.click()
            page.wait_for_timeout(4000)
            state = page.evaluate(_ARTIFACT_STATE_JS) or {}
        fr = state.get("frame") or {}
        steps.add(
            6,
            "agent drafted an artifact in the canvas",
            "draft" in str(state.get("tab") or "").lower() and bool(fr),
            f"tab {state.get('tab')!r}, frame {fr}",
            ROUTE_ARTIFACT,
        )
        steps.add(
            6,
            "the draft draws in the workflow sandbox (opaque origin)",
            fr.get("sandbox") == "allow-scripts" and str(fr.get("src") or "").startswith("/labs/sandbox/render/"),
            f"iframe sandbox={fr.get('sandbox')!r} src={fr.get('src')!r}",
            ROUTE_ARTIFACT,
        )
        _wait_for(page, lambda: _artifact_frame_text(page), timeout_s=30)
        drawn = _artifact_frame_text(page)
        steps.add(6, "the draft drew something", bool(drawn.strip()), f"frame text: {drawn[:200]!r}", ROUTE_ARTIFACT)
        page.screenshot(path=str(out_dir / "step6c-draft.png"), full_page=True)
        if not steps.add(
            6,
            "agent proposed saving the artifact",
            bool(state.get("dialog")) and state.get("dialog_title") == "Save artifact",
            f"dialog open={state.get('dialog')}, title={state.get('dialog_title')!r}, name={state.get('name')!r}",
            ROUTE_ARTIFACT,
        ):
            return
        page.locator("#ex-save-confirm").click()
        page.wait_for_timeout(4000)
        got = _get(page, f"{API}/spaces/{space_id}/artifacts/")
        names = [a.get("name") for a in (got.get("body") or {}).get("artifacts", [])]
        rail = page.evaluate(
            "() => { const e = document.querySelector('#ex-artifacts'); return e ? e.innerText : '' }"
        )
        name = state.get("name") or ""
        steps.add(
            6,
            "the person's Save kept it in the space",
            name in names and name in rail,
            f"saved {name!r}; artifacts {names}",
            ROUTE_ARTIFACT,
        )
        steps.add(
            6,
            "the agent named it as asked",
            sc.artifact_as.lower() in name.lower(),
            f"name {name!r}",
            ROUTE_ARTIFACT,
            gate=False,
        )
        page.locator("#ex-artifacts button.open").first.click()
        page.wait_for_timeout(6000)
        opened = page.evaluate(_ARTIFACT_STATE_JS) or {}
        drawn = _artifact_frame_text(page)
        steps.add(
            6,
            "it opens from the rail, in the sandbox",
            (opened.get("frame") or {}).get("sandbox") == "allow-scripts" and bool(drawn.strip()),
            f"tab {opened.get('tab')!r}, frame {opened.get('frame')}",
            ROUTE_ARTIFACT,
        )
        page.screenshot(path=str(out_dir / "step6c-saved-artifact.png"), full_page=True)
    except Exception as e:  # noqa: BLE001
        steps.add(6, "agent drafts an artifact", False, f"{type(e).__name__}: {e}"[:300], checks.ROUTES["blocked"])


_PANEL_SESSION_JS = """() => {
    const h = window.canopyHost;
    if (!h) return null;
    if (typeof h.session === 'function') return h.session();
    return h.widget && typeof h.widget.currentSession === 'function' ? h.widget.currentSession() : null;
}"""


def _wait_for(page, fn, timeout_s: int = 30):
    deadline = time.time() + timeout_s
    value = None
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        page.wait_for_timeout(1000)
    return value


def _saved_conversation(page, driver, steps: _Steps, turn, session_id: str, space_id, space_url: str, out_dir) -> None:
    """Step 6b: save the conversation the panel has open, reload, open it from the rail, and
    read the agent's reply again (unlocked with ace's test vault key)."""
    from .driver import _needle

    want = f"labs-data-space://{space_id}"
    reported = _wait_for(page, lambda: page.evaluate(_PANEL_SESSION_JS)) or {}
    if not steps.add(
        6,
        "panel reports the open conversation, begun on this space",
        reported.get("id") == session_id and reported.get("resource") == want,
        f"reported {json.dumps(reported)[:200]}; expected id {session_id}, resource {want}",
        ROUTE_CONVERSATION,
    ):
        return
    steps.add(
        6,
        "the report carries a pointer, never words",
        set(reported) <= {"id", "resource", "private"},
        f"keys {sorted(reported)}",
        ROUTE_CONVERSATION,
    )
    # A modal left open (the agent's proposed Save, say) covers the rail: put it away first,
    # as a person would, without saving anything.
    if page.locator("#ex-save-dialog").is_visible():
        page.locator("#ex-save-cancel").click()
        page.wait_for_timeout(500)
    label = f"eval conversation {time.strftime('%H%M%S')}"
    page.locator("#ex-conversation-label").fill(label)
    page.locator("#ex-conversation-label").focus()
    enabled = _wait_for(page, lambda: page.locator("#ex-conversation-save").is_enabled(), 15)
    hint = page.evaluate("() => (document.querySelector('#ex-conversation-hint') || {}).textContent || ''")
    if not steps.add(6, "Save this conversation is offered", bool(enabled), f"hint: {hint!r}", ROUTE_CONVERSATION):
        return
    page.locator("#ex-conversation-save").click()
    page.wait_for_timeout(3000)
    listed = (_get(page, f"{API}/spaces/{space_id}/conversations/").get("body") or {}).get("conversations") or []
    mine = next((c for c in listed if c.get("session_id") == session_id), None)
    steps.add(
        6,
        "the pointer is saved in the space (API)",
        bool(mine) and mine.get("label") == label,
        f"conversations: {json.dumps(listed)[:300]}",
        ROUTE_CONVERSATION,
    )
    steps.add(
        6,
        "nothing of the conversation is stored",
        bool(mine) and set(mine) <= {"id", "session_id", "label", "saved_by", "created_at", "can_delete"},
        f"fields {sorted(mine or {})}",
        ROUTE_CONVERSATION,
    )
    page.screenshot(path=str(out_dir / "step6b-saved-conversation.png"))

    # Reload: a fresh page, the panel not yet on the conversation. Open it from the rail.
    page.goto(space_url, wait_until="load", timeout=120_000)
    page.wait_for_timeout(6000)
    row = page.locator("#ex-conversations [data-conversation-id]").filter(has_text=label)
    if not steps.add(
        6, "after a reload the rail lists it", row.count() == 1, f"rows {row.count()}", ROUTE_CONVERSATION
    ):
        return
    row.first.locator(".open").click()
    frame = _wait_for(
        page,
        lambda: (lambda f: f if f is not None and f.locator("[data-turn-index]").count() else None)(
            driver._chat_frame(page)
        ),
        60,
    )
    steps.add(
        6,
        "clicking it opens the conversation in the panel",
        frame is not None,
        "the panel shows the conversation" if frame is not None else "no conversation in the panel",
        ROUTE_CONVERSATION,
    )
    if frame is None:
        page.screenshot(path=str(out_dir / "step6b-open-failed.png"))
        return
    from . import vault

    unlocked = vault.unlock(frame, page)
    page.wait_for_timeout(4000)
    text = frame.evaluate("() => document.body.innerText")
    needle = _needle(turn.reply)
    steps.add(
        6,
        "opened from the rail, the agent's reply reads again",
        bool(needle) and needle in text,
        f"needle {needle!r}; unlock {unlocked}",
        ROUTE_CONVERSATION,
    )
    again = page.evaluate(_PANEL_SESSION_JS) or {}
    steps.add(
        6,
        "the panel reports the reopened conversation",
        again.get("id") == session_id,
        f"reported {json.dumps(again)[:200]}",
        ROUTE_CONVERSATION,
        gate=False,
    )
    page.screenshot(path=str(out_dir / "step6b-reopened.png"))


def _post(page, url: str, body: dict) -> dict:
    return page.evaluate(
        """async ([url, body]) => {
            const field = document.querySelector('[name=csrfmiddlewaretoken]');
            const r = await fetch(url, {method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': field ? field.value : ''},
                body: JSON.stringify(body)});
            let b = null; try { b = await r.json(); } catch (e) {}
            return {status: r.status, body: b};
        }""",
        [url, body],
    )


def _get(page, url: str) -> dict:
    return page.evaluate(
        """async (url) => { const r = await fetch(url, {credentials: 'same-origin'});
            let b = null; try { b = await r.json(); } catch (e) {} return {status: r.status, body: b}; }""",
        url,
    )


def _safe_eval(page, fn: str, arg):
    try:
        return page.evaluate(
            f"async (a) => {{ try {{ return await ({fn})(a) }} catch (e) {{ return {{error: String(e)}} }} }}", arg
        )
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:300]}


def _note(url: str, sessions: list[str]) -> None:
    from .driver import _note_session

    _note_session(url, sessions)


def _turn_dict(turn) -> dict:
    from dataclasses import asdict

    return json.loads(json.dumps(asdict(turn), default=str))
