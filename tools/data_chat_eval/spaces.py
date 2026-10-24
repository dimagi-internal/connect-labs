"""Data space scenarios: a space made on synthetic data, driven through the page as ace.

A space scenario (``spaces:`` in a suite) walks one space's life, checking each step:

1. **create** -- ``POST /labs/data/api/spaces/`` from ace's own page session (CSRF), on the
   scenario's sources.
2. **open** -- ``/labs/data/space/<id>/`` draws the space (``#ex-page[data-space-id]``) and
   tells the panel (``canopyHost.pageState().filters.space_id``).
3. **held to the space (MCP, ace's PAT)** -- ``data_query`` with ``space_id`` answers the
   ground truth; with another opportunity beside it is refused ``outside_space``; SQL naming
   an opportunity outside the space reads nothing.
4. **showQuery** -- the agent's page action (``window.LABSDATA_ACTIONS.showQuery``, exactly
   what canopy calls as ``page_showQuery``) opens a canvas tab, runs the query as the person,
   shows the truth, and the panel's state says what the canvas shows (``filters.canvas``).
5. **proposeSave + Save** -- the Save dialog is filled, nothing is saved until the click;
   after ``#ex-save-confirm`` the item is in ``#ex-space-items`` and the items API.
6. **the agent in the space** -- one chat turn in the space's panel; the canvas should switch.
7. **archive** -- ``POST .../archive/``; the space's page then answers 404.

Teammate and lock-out rules need a second synthetic person; not covered yet.
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
