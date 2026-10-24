"""Deterministic checks on one turn. Each returns ``Result(name, passed, detail, gate, route)``.

A check in the suite is a one-key mapping, ``{<kind>: <args>}``; ``gate: false`` beside the
kind makes it advisory (reported, never failing the scenario). Values may be references to
the scenario's ground truth (``total.n``), resolved by ``suite.value``.

Kinds:

* ``number``: ``{value, tolerance (abs, default 0) | rel_tolerance, scale, any_of: [...], in}``
  -- the reply (or a card, ``in: reply|card|either``, default either) states the number.
  ``scale: 100`` compares a fraction to a percentage in the text.
* ``text``: ``{any_of: [...], in}`` -- one of these strings appears (case-insensitive).
* ``card``: ``{min: 1, chart: true|false, sane_headers: true}`` -- a Labs results card was
  drawn this turn (and, optionally, a chart; plain-word column headers).
* ``tool``: ``{any_of: [data_query, ...]}`` -- the agent called one of these (canopy's record).
* ``no_rows_repeated``: true -- with a card drawn, the reply does not repeat the rows as a table
  (labs guidance rule 10).
* ``asks_or_states``: true -- the reply asks a question or states the definition it assumed.
* ``no_errors``: true -- no panel error, the turn finished, and no tool call failed.
  ``{allow_refused: true}``: a tool call labs REFUSED (not held, outside the selection or
  space, a table not allowed) is the expected answer, not an error. Named in a turn's
  checks, it replaces the implicit one.
* ``refuses``: true -- the reply says plainly it will not (or cannot) do what was asked.
* ``reads_none_of``: ``{opportunities: [...]}`` -- no data tool call that SUCCEEDED named one of
  these opportunities (canopy's record of the inputs): the agent kept to the page's selection.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from .suite import value

#: Where a failure of each kind is most likely fixed (the report's findings route on it).
ROUTES = {
    "number": "the answer: labsdata/guidance.py ANSWERING_RULES + data_* tool descriptions "
    "(connect_labs/mcp/tools/labsdata.py); the agent's own skill (ace's labs capability; "
    "labsdata agent: dimagi-internal/labsdata skills/answer-data/SKILL.md)",
    "text": "the answer: labsdata/guidance.py + the agent's skill",
    "card": "a results card: guidance.py rule 10/15 and data_query's description; the card itself "
    "is connect_labs/mcp/ui/data_result.html",
    "card_chart": "the chart: guidance.py rule 15 (display) / labsdata/display.py / the card's auto-chart "
    "in connect_labs/mcp/ui/data_result.html",
    "card_headers": "column labels: guidance.py rule 15 (display labels) / connect_labs/mcp/ui/data_result.html",
    "tool": "tool choice: data_* tool descriptions (connect_labs/mcp/tools/labsdata.py) and the agent's skill",
    "no_rows_repeated": "guidance.py rule 10 (with a card, give the takeaway, not the rows) and the agent's skill",
    "asks_or_states": "guidance.py rule 9 (ask when ambiguous) and the agent's skill",
    "reads_none_of": "what a page's agent may read: data_* under a page grant are held to the page's selection "
    "(labsdata/page_holds.py, recorded at the mint by labs/canopy_token.py); if a read got through, the hold "
    "is missing or too wide there. The agent's side: guidance.py PAGE_RULES and the agent's skill",
    "refuses": "what the agent may read from a page: canopy's page grant (labs canopy.py SCOPE_TOOLS), "
    "labsdata/scope.py, and the agent's guidance (guidance.py / the agent's skill)",
    "no_errors": "the failing tool (connect_labs/mcp/tools/) or the panel/page (canopy widget, labs canopy_panel.py)",
    "finished": "canopy runner/turn (canopy-web) -- the turn never ended",
    "blocked": "setup: canopy identity / Labs Admin › Canopy agents / the page's canopy_panel",
}

#: A blocked conversation, by what the panel or page said.
BLOCKED_ROUTES = (
    (
        "vault key",
        "canopy-web: the Data page's agent runs on sensitive-data runners, so its conversation is sealed to "
        "its people and needs a canopy account with a vault key (apps/canopy_sessions/conversation_keys.py, "
        "CannotHold). The visitor's canopy account has none: set one up for it (passkey + vault key at "
        "canopy.dimagi.com/settings#vault-key), or let a keyless test visitor hold a sensitive conversation",
    ),
    (
        "not allowed sensitive data",
        "labs: the workflow's opportunities are not all GENERATED synthetic data "
        "(labs/synthetic/provenance.py), so its agent needs 'Sensitive data' ticked in Labs Admin › Canopy agents",
    ),
    ("labs session has expired", "harness setup: run the ace plugin's /ace:labs-login"),
)


def blocked_route(reason: str) -> str:
    for needle, route in BLOCKED_ROUTES:
        if needle in (reason or "").lower():
            return route
    return ROUTES["blocked"]


_NUM = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
#: Dates and times are not quantities: "2026-10-15" must not read as 2,026, 10 and 15.
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?\S*)?\b|\b\d{1,2}:\d{2}\b")


@dataclass
class Result:
    name: str
    passed: bool
    detail: str
    gate: bool = True
    route: str = ""


def numbers_in(text: str) -> list[float]:
    text = _DATE.sub(" ", text or "")
    return [float(m.group(0).replace(",", "")) for m in _NUM.finditer(text)]


def _haystack(turn, where: str) -> str:
    # A card's cells, one per line: a collapsed card's innerText runs its cells together.
    cards = "\n".join("\n".join(c.get("cells") or []) + "\n" + c.get("text", "") for c in turn.cards)
    return {"reply": turn.reply, "card": cards}.get(where, f"{turn.reply}\n{cards}")


def run(spec: dict, turn, truth: dict) -> Result:
    spec = dict(spec)
    gate = bool(spec.pop("gate", True))
    route = spec.pop("route", "")
    if len(spec) != 1:
        raise ValueError(f"a check is one kind: {spec}")
    kind, args = next(iter(spec.items()))
    fn = KINDS.get(kind)
    if fn is None:
        raise ValueError(f"unknown check kind {kind}")
    res = fn(args if isinstance(args, dict) else {"value": args}, turn, truth)
    if isinstance(args, dict):
        gate = bool(args.get("gate", gate))
        route = args.get("route", route)
    res.gate = gate
    res.route = route or res.route or ROUTES.get(kind, "")
    return res


def check_number(args, turn, truth) -> Result:
    wanted = [value(v, truth) for v in (args.get("any_of") or [args.get("value")])]
    scale = float(args.get("scale", 1))
    text = _haystack(turn, args.get("in", "either"))
    found = numbers_in(text)
    label = args.get("label") or "/".join(str(v) for v in (args.get("any_of") or [args.get("value")]))
    for w in wanted:
        if w is None:
            return Result(f"number {label}", False, "ground truth missing for this check")
        target = float(w) * scale
        tol = float(args.get("tolerance", 0))
        if "rel_tolerance" in args:
            tol = max(tol, abs(target) * float(args["rel_tolerance"]))
        if tol == 0 and scale == 1 and float(target).is_integer():
            tol = 0.5  # an exact count; tolerate nothing but the decimal point
        hit = [n for n in found if abs(n - target) <= tol]
        if hit:
            return Result(f"number {label}", True, f"expected {_fmt(target)}, found {_fmt(hit[0])}")
    want = ", ".join(_fmt(float(w) * scale) for w in wanted if w is not None)
    return Result(f"number {label}", False, f"expected {want}; the text names {_some(found)}")


def _spellings(option: str) -> list[str]:
    """An ISO date also as people write it: 24 July 2026, July 24, 2026, 24 Jul."""
    try:
        d = datetime.date.fromisoformat(option)
    except ValueError:
        return [option]
    return [option, f"{d.day} {d:%B %Y}", f"{d:%B} {d.day}, {d.year}", f"{d.day} {d:%b}", f"{d:%b} {d.day}"]


def check_text(args, turn, truth) -> Result:
    options = [s for v in args.get("any_of") or [args.get("value")] for s in _spellings(str(value(v, truth)))]
    text = _haystack(turn, args.get("in", "either")).lower()
    for o in options:
        if o.lower() in text:
            return Result(f"text {options[0]}", True, f"found {o!r}")
    return Result(f"text {options[0]}", False, f"none of {options} in the {args.get('in', 'reply or card')}")


def check_card(args, turn, truth) -> Result:
    cards = turn.cards
    need = int(args.get("min", 1))
    if len(cards) < need:
        return Result("card drawn", False, f"{len(cards)} Labs card(s) this turn, wanted {need}")
    if args.get("chart"):
        charted = [c for c in cards if c.get("svg", 0) > 0 or c.get("canvas", 0) > 0]
        if not charted:
            hidden = [c for c in cards if c.get("chart_on_click")]
            why = (
                f"the card has a chart, but opens on its {hidden[0].get('active_tab') or 'Table'} tab"
                if hidden
                else "a card, but no chart in it"
            )
            return Result("card chart", False, why, route=ROUTES["card_chart"])
    if args.get("sane_headers", True):
        raw = [h for c in cards for h in c.get("headers", []) if _raw_header(h)]
        if raw:
            return Result(
                "card headers", False, f"raw column names in the card: {raw[:6]}", route=ROUTES["card_headers"]
            )
    return Result("card drawn", True, f"{len(cards)} card(s); headers {[c.get('headers') for c in cards][:2]}")


_FUNC = re.compile(r"^[a-z_]+\(")


def _raw_header(h: str) -> bool:
    """A column name straight from SQL (``approved_visits``, ``count(*)``, ``?column?``), not a label."""
    h = h.strip().rstrip("↑↓↕ ").strip()
    return bool(h) and ("_" in h or bool(_FUNC.match(h)) or h.startswith("?"))


def check_tool(args, turn, truth) -> Result:
    wanted = args.get("any_of") or [args.get("value")]
    called = [c["short"] for c in turn.tool_calls]
    if turn.source == "panel" and not called:
        return Result(f"tool {wanted[0]}", True, "canopy's record unavailable (sensitive or no PAT); not checked")
    hit = [c for c in called if c in wanted]
    return Result(
        f"tool {'/'.join(wanted)}",
        bool(hit),
        f"called {sorted(set(called))}" if called else "called no tools",
    )


_TABLE = re.compile(r"^\s*\|.*\|\s*$|^\s*\|?\s*:?-{3,}", re.M)


def check_no_rows_repeated(args, turn, truth) -> Result:
    if not turn.cards:
        return Result("no rows repeated", True, "no card this turn")
    table_lines = [ln for ln in turn.reply.splitlines() if _TABLE.match(ln)]
    if len(table_lines) >= 3:
        return Result("no rows repeated", False, f"reply repeats the card's rows as a {len(table_lines)}-line table")
    return Result("no rows repeated", True, "takeaway only")


_DEFINE = re.compile(
    r"\b(assum|defin|I took|I counted|I treated|which do you mean|do you mean|counted as|what it counts|"
    r"the closest (?:one|indicator|measure)|if (?:by \S+ )?you mean|depending on what you mean)",
    re.I,
)


def check_asks_or_states(args, turn, truth) -> Result:
    reply = turn.reply or ""
    asks = "?" in reply
    states = bool(_DEFINE.search(reply))
    return Result(
        "asks or states assumption",
        asks or states,
        "asks a question" if asks else ("states its definition" if states else "neither asks nor states a definition"),
    )


def check_no_errors(args, turn, truth) -> Result:
    problems = []
    if not turn.finished:
        problems.append(f"turn did not finish in {turn.seconds}s")
    problems += [f"panel: {e}" for e in turn.panel_errors]
    failed = [c for c in turn.tool_calls if c.get("is_error")]
    if isinstance(args, dict) and args.get("allow_refused"):
        failed = [c for c in failed if not _REFUSAL.search(c.get("result") or "")]
    problems += [f"tool {c['short']} failed: {c['result'][:160]}" for c in failed]
    gate_fail = (not turn.finished) or bool(turn.panel_errors)
    res = Result("no errors", not problems, "; ".join(problems) or "clean")
    if failed and not gate_fail:
        res.detail = "recovered from: " + res.detail  # a failed tool the agent got past is still worth fixing
    return res


#: A labs refusal in a tool result: the call was outside what the caller may read.
_REFUSAL = re.compile(
    r"not_held|outside_space|outside_page|table_not_allowed|schema_not_allowed|PERMISSION_DENIED|do not have access"
)
_REFUSES = re.compile(
    r"\b(can(?:no|')t|unable to|not able to|won't|will not|don't have access|do not have access|no access|"
    r"not part of|isn't part of|is not part of|outside (?:this|the)|only (?:reads|covers|has|includes|sees)|"
    r"not (?:included|available|allowed))",
    re.I,
)


def check_refuses(args, turn, truth) -> Result:
    m = _REFUSES.search(turn.reply or "")
    return Result(
        "refuses cleanly",
        bool(m),
        f"says {m.group(0)!r}" if m else "the reply does not say it will not (or cannot) read it",
        route=ROUTES["refuses"],
    )


_DATA_TOOLS = ("data_query", "data_describe", "data_visits", "data_validate", "explorer_query", "explorer_describe")


def check_reads_none_of(args, turn, truth) -> Result:
    banned = {int(o) for o in args.get("opportunities") or []}
    read = []
    for c in turn.tool_calls:
        if c.get("is_error") or not any(c.get("short", "").startswith(t) for t in _DATA_TOOLS):
            continue
        given = c.get("input") or {}
        if isinstance(given, str):
            try:
                import json

                given = json.loads(given)
            except ValueError:
                given = {}
        ids = given.get("opportunity_ids") or []
        ids = ids if isinstance(ids, list) else [ids]
        hit = sorted(banned & {int(i) for i in ids if str(i).lstrip("-").isdigit()})
        if hit:
            read.append(f"{c['short']} read {hit}")
    return Result(
        "kept to the page's selection",
        not read,
        "; ".join(read) or "no data call read an outside opportunity",
        route=ROUTES["reads_none_of"],
    )


KINDS = {
    "reads_none_of": check_reads_none_of,
    "refuses": check_refuses,
    "number": check_number,
    "text": check_text,
    "card": check_card,
    "tool": check_tool,
    "no_rows_repeated": check_no_rows_repeated,
    "asks_or_states": check_asks_or_states,
    "no_errors": check_no_errors,
}


def _fmt(n: float) -> str:
    return f"{int(n):,}" if float(n).is_integer() else f"{n:,.3f}".rstrip("0").rstrip(".")


def _some(found: list[float]) -> str:
    if not found:
        return "no numbers"
    uniq = list(dict.fromkeys(found))
    return ", ".join(_fmt(n) for n in uniq[:12]) + (" ..." if len(uniq) > 12 else "")
