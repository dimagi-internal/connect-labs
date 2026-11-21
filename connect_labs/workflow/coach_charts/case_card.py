"""The CASE CARD: one case as a card-html message (Connect's rich card), for a phone.

The ``case_summary`` picture (``case_summary.py``) is a 1200 x 900 PNG: right on a laptop,
small on a phone. The card carries the same case -- the case state's title, the case's
name, the banner of facts and the registry's panels, in the registry's order -- as one
self-contained ``index.html`` that the Connect app draws in a sandboxed webview, a column
280-480 px wide, as tall as its content. Plus what a picture cannot say: who to call
about this case.

Two halves, so what was approved is what is sent:

* ``card_data`` -- built at preview, beside the chart, and FROZEN in it (``chart["card"]``):
  every word the registry's, every number the case's, read once from the run;
* ``render_html`` -- draws a frozen card. Deterministic, no network, no script: the
  card-html sandbox blocks the network and links anyway, and a card with no script
  cannot hang the chat (a hung card takes every card on screen down with it).

The card-html contract (PersonalID's guide): ``index.html`` required; fluid width; height
from content (no ``vh``, no ``height: 100%``, no ``position: fixed``, ``body{margin:0}``);
its own background and ``color-scheme: light``; no links (``tel:`` does nothing inside a
card -- the number is shown, and the conversation's own text carries the tap-to-call
link, ``call_markdown``); taps only.

The contact: the case's own number (the registry's ``case_call`` names the columns,
first set wins) else the programme's fallback (the coaching action's
``supervisor_phone``), else none. A phone number is case-level data; it is only ever
read on what the action already reads, and on synthetic opportunities it is invented.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from typing import Any

from connect_labs.semantic import case_states as cs
from connect_labs.workflow.coach_charts import case_summary as summary

VERSION = 2
FILENAME = "index.html"

# The palette of Design D (``case_summary``): one blue for every chart; red and green
# only in a status pill.
INK, ACCENT, MUTED, HAIRLINE, FAINT = summary.INK, summary.ACCENT, summary.MUTED, summary.HAIRLINE, summary.FAINT
BAND, DUE, LATE, LATE_TEXT = summary.BAND, summary.DUE, summary.LATE, summary.LATE_TEXT
PILL = summary.PILL
TONE = {"urgent": ("#fde2de", "#b42318"), "concern": ("#fff1d6", "#8a5300"), "check": ("#e3e8ff", "#3843d0")}
TONE["celebrate"] = ("#e6f5e5", "#1b6b5f")

#: The panel kinds a card draws (every ``case_summary`` kind).
KINDS = ("series", "bars", "visits", "signs", "list", "facts")

#: The default columns a case's own number is read from, first set wins.
DEFAULT_CALL_COLUMNS = ("mother_phone",)
_PHONE = re.compile(r"^\+?[0-9][0-9 ()\-]{5,22}$")


# ---------------------------------------------------------------------------
# The frozen card
# ---------------------------------------------------------------------------


def clean_phone(value: Any) -> str | None:
    """A dialable number, or None: digits with an optional leading +, spaces, dashes
    and brackets dropped."""
    text = str(value or "").strip()
    if not text or not _PHONE.match(text):
        return None
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) < 6:
        return None
    return ("+" if text.startswith("+") else "") + digits


def contact(row: dict, choices: list | None = None, *, fallback: dict | None = None) -> dict | None:
    """``{"label", "number", "who"}``: who to call about this case. ``choices`` is the
    registry's ``case_call`` (``case_states.case_call``): ``{column, label}`` each, the
    first whose column holds a number wins; without one, the case's ``mother_phone``.
    Else ``fallback`` (``{"label", "number"}`` from the coaching action), else None."""
    if choices is None:
        choices = [{"column": c, "label": "Call the mother"} for c in DEFAULT_CALL_COLUMNS]
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        number = clean_phone(row.get(choice.get("column")))
        if number:
            label = cs.fill_if_set(choice.get("label"), row) or "Call"
            return {"label": label, "number": number, "who": "case"}
    if fallback:
        number = clean_phone(fallback.get("number"))
        if number:
            return {"label": str(fallback.get("label") or "Call your supervisor"), "number": number, "who": "fallback"}
    return None


def call_markdown(card: dict) -> str:
    """The conversation's tap-to-call line for a card: a Markdown ``tel:`` link, which the
    chat may make tappable (a card itself cannot)."""
    call = (card or {}).get("call")
    if not call:
        return ""
    return f"[{call['label']}: {call['number']}](tel:{call['number']})"


def _series_points(visits: list[dict], name: str) -> list[list]:
    return [[day.isoformat(), value] for day, value in summary._readings(visits, name)]


def _series_panel(panel: dict, row: dict, visits: list[dict], by_name: dict) -> dict:
    spec = summary._spec(panel, by_name)
    pts = summary._readings(visits, spec["name"])
    status = summary.series_status(panel, pts, spec, row)
    hl = panel.get("highlight") or {}
    ref, floor = panel.get("reference") or None, panel.get("floor") or None
    out: dict[str, Any] = {
        "kind": "series",
        "title": cs.fill(panel.get("title") or spec.get("label") or "", row),
        "status": list(status) if status else None,
        "unit": spec.get("unit") or "",
        "points": [[day.isoformat(), v, summary._label(spec, v, unit=False)] for day, v in pts],
        "last": summary._label(spec, pts[-1][1], unit=True) if pts else None,
    }
    if ref:
        out["reference"] = {
            "low": float(ref.get("low") or 0),
            "high": float(ref.get("high") or 0),
            "label": str(ref.get("label") or ""),
        }
    if floor and cs._as_number(floor.get("value")) is not None:
        value = cs._as_number(floor.get("value"))
        out["floor"] = {
            "value": value,
            "label": str(floor.get("label") or ""),
            "shown": summary._label(spec, value, unit=True),
        }
    flag_to = cs._as_date(row.get(hl.get("to"))) if hl else None
    flag_from = cs._as_date(row.get(hl.get("from"))) if hl else None
    if flag_to:
        out["highlight"] = {
            "from": flag_from.isoformat() if flag_from else None,
            "to": flag_to.isoformat(),
            "label": cs.fill_if_set(hl.get("label"), row) or "",
        }
    out["series"] = spec["name"]
    photo = panel.get("photo") if isinstance(panel.get("photo"), dict) else None
    if photo:
        # ``{value}`` in the ask is the flagged reading, as the app holds it.
        flagged = next((summary._label(spec, v, unit=True) for d, v in pts if d == flag_to), "")
        ask = photo.get("ask")
        if isinstance(ask, str):
            ask = ask.replace("{value}", flagged.replace(" ", "\u00a0"))  # "3,922 g" stays on one line
        out["photo"] = {
            "label": str(photo.get("label") or "Photo"),
            "ask": (cs.fill_if_set(ask, row) or "") if ask else "",
        }
    return out


def _visit_details(visits: list[dict], series: list[dict], primary: dict | None, photos: dict) -> list[dict]:
    """Each visit day, oldest first: its readings (every numeric ``case_series`` the
    briefing reads), the change in the primary series since the reading before, and the
    day's photo. A day with two rows (the registration and a visit) is one day, its
    later readings winning, as in the series."""
    shown = [s for s in series if s.get("brief") is not False and s.get("format") != "text"]
    days: dict[dt.date, dict] = {}
    for v in visits:
        day = cs._as_date(v.get("visit_date"))
        if day is None:
            continue
        d = days.setdefault(day, {"d": day.isoformat(), "values": {}, "photo": None})
        for s in shown:
            num = cs._as_number(v.get(s["name"]))
            if num is not None:
                d["values"][s["name"]] = [str(s.get("label") or s["name"]), summary._label(s, num, unit=True), num]
        d["photo"] = d["photo"] or photos.get(str(v.get("visit_id")))
    out, prev = [], None
    spec = next((s for s in series if primary and s["name"] == primary.get("series")), None)
    for day in sorted(days):
        d = days[day]
        main = d["values"].pop(spec["name"], None) if spec else None
        d["main"] = main[1] if main else None
        if main and prev:
            delta, gap = main[2] - prev[1], (day - prev[0]).days
            sign = "+" if delta > 0 else "-" if delta < 0 else "±"
            d["change"] = f"{sign}{summary._label(spec, abs(delta), unit=True)} in {gap} day{'s' if gap != 1 else ''}"
        if main:
            prev = (day, main[2])
        d["values"] = [[label, text] for label, text, _n in d["values"].values()]
        out.append(d)
    return out


def _visits_panel(panel: dict, row: dict, visits: list[dict], today: dt.date | None) -> dict:
    dated = sorted({day for day in (cs._as_date(v.get("visit_date")) for v in visits) if day})
    due_from, due_to = summary.due_window(panel, row, visits)
    status = summary.visits_status(panel, row, today, visits)
    return {
        "kind": "visits",
        "title": cs.fill(panel.get("title") or "Visits", row),
        "status": list(status) if status else None,
        "visits": [day.isoformat() for day in dated],
        "due": summary._window_words(due_from, due_to) or None,
        "overdue": summary.overdue_days(panel, row, today, visits),
        "today": today.isoformat() if today else None,
    }


def _signs_panel(panel: dict, row: dict) -> dict:
    known = panel.get("sign_text") or {}
    by_title = {(v or {}).get("title"): v for v in known.values() if isinstance(v, dict)}
    when = cs._as_date(row.get(panel.get("date"))) if panel.get("date") else None
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    return {
        "kind": "signs",
        "title": cs.fill(panel.get("title") or "", row),
        "status": ["bad", badge] if badge else None,
        "when": cs.day_short(when) if when else None,
        "signs": [
            {"title": t, "why": str((by_title.get(t) or {}).get("why") or "")}
            for t in summary._split_signs(panel, row)
        ],
    }


def _words_panel(panel: dict, row: dict, *, key: str, tone: str) -> dict:
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    details = list(panel.get("details") or [])
    pairs = [
        (t, cs.fill_if_set(details[i], row) if i < len(details) and details[i] else "")
        for i, t in enumerate(cs.fill_if_set(x, row) for x in panel.get(key) or [])
    ]
    out = {
        "kind": panel.get("kind"),
        "title": cs.fill(panel.get("title") or "", row),
        "status": [tone, badge] if badge else None,
        "items": [t for t, _d in pairs if t],
    }
    if any(d for t, d in pairs if t):
        out["details"] = [d or "" for t, d in pairs if t]
        out["hint"] = cs.fill_if_set(panel.get("hint"), row) or ""
        out["done"] = cs.fill_if_set(panel.get("done"), row) or ""
    return out


def card_data(
    state: dict,
    row: dict,
    visits: list[dict],
    series: list[dict],
    case_name: str = "",
    *,
    as_of: Any = None,
    call: list | None = None,
    fallback_contact: dict | None = None,
    photos: dict | None = None,
) -> dict:
    """The frozen card of ``row`` in ``state`` as of ``as_of``: the ``case_summary``
    picture's content, panel by panel, with every number resolved, and who to call."""
    pic = state.get("picture") or {}
    panels = [p for p in (pic.get("panels") or []) if isinstance(p, dict)]
    by_name = {s["name"]: s for s in series}
    today = summary._today(as_of, row, panels, visits)
    out_panels = []
    for panel in panels:
        kind = panel.get("kind")
        if kind in ("series", "bars"):
            out_panels.append(_series_panel(panel, row, visits, by_name))
        elif kind == "visits":
            out_panels.append(_visits_panel(panel, row, visits, today))
        elif kind == "signs":
            out_panels.append(_signs_panel(panel, row))
        elif kind == "list":
            out_panels.append(_words_panel(panel, row, key="items", tone="good"))
        elif kind == "facts":
            out_panels.append(_words_panel(panel, row, key="lines", tone="good"))
    primary = next((p for p in out_panels if p.get("kind") == "series" and p.get("points")), None)
    days = _visit_details(visits, series, primary, photos or {})
    return {
        "v": VERSION,
        "case_state": state.get("name"),
        "tone": state.get("tone") or "check",
        "title": cs.fill(pic.get("title") or state.get("label") or "", row),
        "label": str(state.get("label") or ""),
        "case_name": case_name or "",
        "banner": [c for c in (cs.fill_if_set(chip, row) for chip in pic.get("banner") or []) if c],
        # The worker reads this: the picture's own ``lead`` in their words, else the facts.
        "facts": (cs.fill_if_set(pic["lead"], row) if pic.get("lead") else None) or cs.facts(state, row),
        "panels": out_panels,
        "as_of": today.isoformat() if today else None,
        "call": contact(row, call, fallback=fallback_contact),
        # The visits a worker taps through: readings, change and photo, day by day.
        "visits_detail": days,
        "next_step": cs.fill_if_set(pic.get("next_step"), row) or "",
    }


# ---------------------------------------------------------------------------
# Drawing it
# ---------------------------------------------------------------------------

E = html.escape

#: The series plot's viewBox (it scales to the card's width).
PW, PH = 320.0, 150.0
PL, PR, PT, PB = 22.0, 60.0, 20.0, 24.0


def _day(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _short(day: dt.date) -> str:
    return cs.day_short(day)


def _pill(status) -> str:
    if not status or not status[1]:
        return ""
    bg, fg = PILL.get(status[0], PILL["bad"])
    return f'<span class="pill" style="background:{bg};color:{fg}">{E(str(status[1]))}</span>'


def _wrap(text: str, width: float, char_px: float = 6.2) -> list[str]:
    """``text`` in lines no wider than ``width`` px at the chart's small type (about
    ``char_px`` a character), breaking only between words."""
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width / char_px:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines or [text]


def _series_svg(p: dict, *, tap: str | None = None) -> str:
    """The series as an inline SVG. With ``tap`` (the day shown first), each reading is
    a tap target that opens its visit, and a ring marks the visit open."""
    pts = [(_day(d), v, label) for d, v, label in p.get("points") or [] if _day(d) is not None]
    if not pts:
        return '<p class="muted">not recorded</p>'
    start, end = pts[0][0], pts[-1][0]
    span = max((end - start).days, 1)
    ref, floor = p.get("reference"), p.get("floor")
    band = []
    if ref:
        w0 = pts[0][1]
        band = [(t, w0 * (1 + ref["low"] / 1000) ** t, w0 * (1 + ref["high"] / 1000) ** t) for t in range(0, span + 1)]
    values = [v for _, v, _ in pts] + [b[2] for b in band] + [b[1] for b in band]
    if floor:
        values += [floor["value"], 0.0]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def X(day: dt.date) -> float:
        if len(pts) == 1:
            return (PL + PW - PR) / 2
        return PL + (PW - PR - PL) * (day - start).days / span

    def Y(v: float) -> float:
        return PT + (PH - PB - PT) * (hi - v) / (hi - lo)

    parts = [f'<svg viewBox="0 0 {PW:.0f} {PH:.0f}" role="img" aria-label="{E(p["title"])}">']
    parts.append(
        f'<line x1="{PL}" y1="{PH - PB}" x2="{PW - PR + 8}" y2="{PH - PB}" stroke="{HAIRLINE}" stroke-width="1"/>'
    )
    if band:
        x_of = lambda t: X(start + dt.timedelta(days=t))  # noqa: E731
        upper = " ".join(f"{x_of(t):.1f},{Y(b):.1f}" for t, _a, b in band)
        lower = " ".join(f"{x_of(t):.1f},{Y(a):.1f}" for t, a, _b in reversed(band))
        parts.append(f'<polygon points="{upper} {lower}" fill="{BAND}"/>')
        if ref.get("label"):
            # Right of the band's end, in the plot's right margin: a label wider than the
            # margin wraps onto lines of a word or two, so it is never cut off at the edge.
            t = band[-1]
            x, mid = X(end) + 6, (Y(t[1]) + Y(t[2])) / 2
            lines = _wrap(str(ref["label"]), PW - x)
            height = (len(lines) - 1) * 13
            top = mid + 4 - height / 2
            # Clear of the last reading and its value label (drawn just above the point):
            # beside the band's middle when that is free, else past whichever edge of the
            # band is away from the point.
            last_y = Y(pts[-1][1])
            if top - 12 < last_y + 6 and top + height > last_y - 22:
                if last_y >= mid:
                    top = Y(t[2]) - 6 - height
                else:
                    top = Y(t[1]) + 14
            spans = "".join(
                f'<tspan x="{x:.1f}" y="{top + i * 13:.1f}">{E(line)}</tspan>' for i, line in enumerate(lines)
            )
            parts.append(f'<text class="t s" fill="{ACCENT}">{spans}</text>')
    if floor:
        fy = Y(floor["value"])
        parts.append(
            f'<line x1="{PL}" y1="{fy:.1f}" x2="{PW - PR + 4}" y2="{fy:.1f}" stroke="{MUTED}" '
            'stroke-width="1.5" stroke-dasharray="5 4"/>'
        )
        parts.append(
            f'<text x="{PW - PR + 8:.1f}" y="{fy - 2:.1f}" class="t s" fill="{MUTED}">{E(floor["label"])}</text>'
            f'<text x="{PW - PR + 8:.1f}" y="{fy + 11:.1f}" class="t s" fill="{MUTED}">{E(floor["shown"])}</text>'
        )
    hl = p.get("highlight") or {}
    flag_to, flag_from = _day(hl.get("to")), _day(hl.get("from"))
    for n in range(len(pts) - 1):
        a, b = pts[n], pts[n + 1]
        dashed = bool(flag_to) and (
            b[0] == flag_to or a[0] == flag_to or (flag_from is not None and flag_from <= a[0] and b[0] <= flag_to)
        )
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        parts.append(
            f'<line x1="{X(a[0]):.1f}" y1="{Y(a[1]):.1f}" x2="{X(b[0]):.1f}" y2="{Y(b[1]):.1f}" '
            f'stroke="{ACCENT}" stroke-width="2.5" stroke-linecap="round"{dash}/>'
        )
    for n, (day, v, label) in enumerate(pts):
        last = n == len(pts) - 1
        parts.append(f'<circle cx="{X(day):.1f}" cy="{Y(v):.1f}" r="{4 if last else 3}" fill="{ACCENT}"/>')
        if flag_to and day == flag_to:
            parts.append(
                f'<circle cx="{X(day):.1f}" cy="{Y(v):.1f}" r="9" fill="none" stroke="{LATE_TEXT}" stroke-width="2"/>'
            )
        if tap:
            on = " on" if day.isoformat() == tap else ""
            parts.append(
                f'<circle class="ring{on}" data-ring="{day.isoformat()}" cx="{X(day):.1f}" cy="{Y(v):.1f}" r="12" '
                f'fill="none" stroke="{INK}" stroke-width="2.5"/>'
                f'<circle class="hit" data-pt="{day.isoformat()}" cx="{X(day):.1f}" cy="{Y(v):.1f}" r="16" '
                'fill="#fff" fill-opacity="0"/>'
            )
    # Value labels: the first and the last always, the others where they fit.
    shown_x: list[float] = []
    for n, (day, v, label) in enumerate(pts):
        x = X(day)
        last = n == len(pts) - 1
        if not last and n != 0 and any(abs(x - sx) < 34 for sx in shown_x):
            continue
        if not last and pts and abs(x - X(pts[-1][0])) < 34:
            continue
        shown_x.append(x)
        text = p.get("last") if last else label
        cls = "t b" if last else "t"
        anchor = "start" if (n == 0 and len(pts) > 1) else "middle"
        lx = x - 4 if anchor == "start" else x
        ringed = bool(flag_to) and day == flag_to
        parts.append(
            f'<text x="{lx:.1f}" y="{Y(v) - (15 if ringed else 8):.1f}" text-anchor="{anchor}" class="{cls}">'
            f'{E(text or "")}</text>'
        )
    # Dates along the bottom: first and last, and between where they fit.
    dx: list[float] = []
    for n, (day, _v, _l) in enumerate(pts):
        x = X(day)
        last = n == len(pts) - 1
        if not last and any(abs(x - s) < 40 for s in dx):
            continue
        if not last and abs(x - X(pts[-1][0])) < 40:
            continue
        dx.append(x)
        anchor = "start" if (n == 0 and len(pts) > 1) else "end" if (last and len(pts) > 1) else "middle"
        lx = x - 4 if anchor == "start" else x + 4 if anchor == "end" else x
        parts.append(
            f'<text x="{lx:.1f}" y="{PH - 8:.1f}" text-anchor="{anchor}" class="t s" fill="{MUTED}">'
            f"{E(_short(day))}</text>"
        )
    parts.append("</svg>")
    out = "".join(parts)
    status_text = str((p.get("status") or [None, None])[1] or "")
    if hl.get("label") and hl["label"] != status_text:
        out = f'<p class="flag">{E(hl["label"])}</p>' + out
    return out


def _visits_html(p: dict) -> str:
    days = [d for d in (_day(x) for x in p.get("visits") or []) if d]
    if not days:
        return '<p class="muted">no visits recorded</p>'
    items = "".join(f"<li>{E(_short(d))}</li>" for d in days[-6:])
    more = f'<p class="muted">and {len(days) - 6} earlier</p>' if len(days) > 6 else ""
    next_line = ""
    if p.get("due"):
        late = p.get("overdue") or 0
        cls = "late" if late else "due"
        words = f"Next visit {p['due']}" + (f" · {late} day{'s' if late != 1 else ''} late" if late else "")
        next_line = f'<p class="{cls}">{E(words)}</p>'
    return f'{next_line}<ol class="visits">{items}</ol>{more}'


def _signs_html(p: dict) -> str:
    when = f'<p class="muted">recorded {E(p["when"])}</p>' if p.get("when") else ""
    rows = "".join(
        f'<li><b>{E(s["title"])}</b>'
        + (f'<span class="muted"> {E(s["why"])}</span>' if s.get("why") else "")
        + "</li>"
        for s in p.get("signs") or []
    )
    return f'{when}<ul class="signs">{rows}</ul>'


def _items_html(p: dict, *, numbered: bool) -> str:
    tag = "ol" if numbered else "ul"
    rows = "".join(f"<li>{E(t)}</li>" for t in p.get("items") or [])
    return f'<{tag} class="items">{rows}</{tag}>'


def _visit_panes(card: dict, p: dict, first: str) -> str:
    """Under the primary chart: a chip per visit day and, per day, what was written,
    the change since the reading before, the day's photo and the other readings. Every
    day is in the page; the script only shows one (``first`` with no script)."""
    days = card.get("visits_detail") or []
    flag = (p.get("highlight") or {}).get("to")
    photo = p.get("photo") or {}
    chips, panes = [], []
    for d in days:
        day = _day(d["d"])
        if day is None:
            continue
        bad = d["d"] == flag
        pressed = "true" if d["d"] == first else "false"
        chips.append(
            f'<button type="button" class="chip{" bad" if bad else ""}" data-tab="{d["d"]}" aria-pressed="{pressed}">'
            f'{E(_short(day))}{" !" if bad else ""}</button>'
        )
        if d.get("photo"):
            pic = (
                f'<button type="button" class="ph" aria-label="{E(photo.get("label") or "Photo")}, {E(_short(day))}">'
                f'<img src="{E(d["photo"])}" alt=""><span>tap to enlarge</span></button>'
            )
        else:
            pic = f'<div class="ph none">No {E((photo.get("label") or "photo").lower())} at this visit</div>'
        change = ""
        if d.get("change"):
            change = f'<span class="chg{" bad" if bad else ""}">{E(d["change"])}</span>'
        ask = ""
        if bad and d.get("photo") and photo.get("ask"):
            ask = f'<div class="ask"><b>Look at the photo</b>{E(photo["ask"])}</div>'
        rest = " · ".join(f"{label} {text}" for label, text in d.get("values") or [])
        main = d.get("main")
        panes.append(
            f'<div class="pane" data-pane="{d["d"]}"{"" if d["d"] == first else " hidden"}><div class="row">'
            + (pic if photo else "")
            + '<div class="fx">'
            f'<div class="when">{E(_short(day))} · in the app</div>'
            + (f'<div class="big">{E(main)}</div>' if main else '<div class="big none">not recorded</div>')
            + change
            + ask
            + (f'<div class="rest">{E(rest)}</div>' if rest else "")
            + "</div></div></div>"
        )
    if not panes:
        return ""
    return f'<div class="chips2" role="tablist">{"".join(chips)}</div>' + "".join(panes)


def _checklist_html(p: dict) -> str:
    details = p.get("details") or []
    rows = "".join(
        '<button type="button" class="chk" aria-pressed="false"><span class="box"></span><span class="tx">'
        f"<b>{E(t)}</b>"
        + (f"<small>{E(details[i])}</small>" if i < len(details) and details[i] else "")
        + "</span></button>"
        for i, t in enumerate(p.get("items") or [])
    )
    n = len(p.get("items") or [])
    return f'<div class="bar"><i style="width:0%"></i></div>' f'<p class="muted prog" data-n="{n}">0 of {n} done' + (
        f" · {E(p['hint'])}" if p.get("hint") else ""
    ) + "</p>" + rows + (f'<p class="donemsg" hidden>{E(p["done"])}</p>' if p.get("done") else "")


def _panel_html(p: dict, card: dict | None = None, primary: bool = False) -> str:
    kind = p.get("kind")
    if kind == "series" and primary and (card or {}).get("visits_detail"):
        days = [d["d"] for d in card["visits_detail"]]
        flag = (p.get("highlight") or {}).get("to")
        first = flag if flag in days else days[-1]
        body = _series_svg(p, tap=first) + _visit_panes(card, p, first)
    elif kind == "list" and p.get("details"):
        body = _checklist_html(p)
    elif kind == "series":
        body = _series_svg(p)
    elif kind == "visits":
        body = _visits_html(p)
    elif kind == "signs":
        body = _signs_html(p)
    elif kind == "list":
        body = _items_html(p, numbered=True)
    else:
        body = _items_html(p, numbered=False)
    return (
        f'<section><div class="head"><h2>{E(p.get("title") or "")}</h2>{_pill(p.get("status"))}</div>{body}</section>'
    )


CSS = (
    ":root{color-scheme:light}"
    "html,body{margin:0;padding:0;background:#fff;color:" + INK + ";overflow-wrap:anywhere;"
    "font:15px/1.4 -apple-system,'Segoe UI',Roboto,'Work Sans',sans-serif;-webkit-text-size-adjust:100%}"
    ".card{padding:14px 14px 10px}"
    ".tone{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;"
    "padding:3px 9px;border-radius:12px;margin-bottom:6px;max-width:100%;box-sizing:border-box}"
    "h1{font-size:21px;line-height:1.2;margin:0 0 2px}"
    ".name{font-size:15px;font-weight:600;margin:0 0 6px}"
    ".chips{display:flex;flex-wrap:wrap;gap:4px 6px;margin:0 0 8px;padding:0;list-style:none}"
    ".chips li{font-size:13px;color:" + MUTED + ";background:#f1f5f9;border-radius:6px;padding:2px 7px}"
    ".facts{font-size:14px;margin:0 0 10px}"
    ".call{border:1.5px solid " + ACCENT + ";border-radius:10px;padding:8px 10px;margin:0 0 4px}"
    ".call .who{font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:" + ACCENT + "}"
    ".call .num{font-size:20px;font-weight:700;letter-spacing:.02em}"
    ".call .how{font-size:12px;color:" + MUTED + "}"
    "section{border-top:1px solid " + HAIRLINE + ";padding:10px 0 8px}"
    ".head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px}"
    "h2{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:" + ACCENT + ";margin:0}"
    ".pill{font-size:12px;font-weight:600;border-radius:999px;padding:2px 8px;white-space:nowrap}"
    "svg{display:block;width:100%;height:auto;max-width:440px;overflow:visible}"
    "svg text{paint-order:stroke;stroke:#fff;stroke-width:3px;stroke-linejoin:round}"
    "svg .t{font-size:12px;font-weight:600;fill:" + INK + "}svg .s{font-size:11px;font-weight:400}"
    "svg .b{font-size:13px;font-weight:700}"
    ".flag{font-size:13px;font-weight:600;margin:0 0 2px}"
    ".muted{color:" + MUTED + ";font-size:13px;margin:2px 0}"
    "ol,ul{margin:4px 0;padding-left:20px}li{margin:2px 0}"
    ".visits{display:flex;flex-wrap:wrap;gap:4px 12px;list-style:none;padding:0}"
    ".visits li{font-size:13px;color:" + MUTED + "}"
    ".due{font-size:14px;margin:2px 0 4px}"
    ".late{font-size:14px;font-weight:600;color:" + LATE_TEXT + ";background:" + LATE + ";"
    "border-radius:6px;padding:3px 7px;margin:2px 0 4px;display:inline-block}"
    ".signs b{display:block}"
    ".foot{font-size:11px;color:" + FAINT + ";margin:6px 0 0}"
    "[hidden]{display:none!important}"
    "button{font:inherit;color:inherit;-webkit-tap-highlight-color:transparent}"
    "svg .ring{display:none}svg .ring.on{display:inline}svg .hit{cursor:pointer}"
    ".chips2{display:flex;gap:5px;overflow-x:auto;margin:8px 0 10px;padding-bottom:2px}"
    ".chip{flex:none;border:1.5px solid " + HAIRLINE + ";background:#fff;border-radius:999px;"
    "padding:5px 9px;font-size:13px;cursor:pointer}"
    ".chip[aria-pressed=true]{background:" + INK + ";border-color:" + INK + ";color:#fff}"
    ".chip.bad{border-color:" + LATE_TEXT + ";color:" + LATE_TEXT + "}"
    ".chip.bad[aria-pressed=true]{background:" + LATE_TEXT + ";color:#fff}"
    ".row{display:flex;gap:12px;align-items:flex-start}"
    ".ph{flex:0 0 40%;position:relative;border:0;padding:0;border-radius:12px;overflow:hidden;"
    "background:#0b1020;cursor:zoom-in}"
    ".ph img{width:100%;aspect-ratio:3/4;object-fit:cover;display:block}"
    ".ph span{position:absolute;right:6px;bottom:6px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;"
    "border-radius:6px;padding:2px 6px}"
    ".ph.none{display:flex;align-items:center;justify-content:center;text-align:center;color:#cbd5e1;"
    "font-size:12px;padding:8px;box-sizing:border-box;cursor:default;aspect-ratio:3/4}"
    ".pane.zoom .row{flex-direction:column}.pane.zoom .ph{flex-basis:auto;width:100%;cursor:zoom-out}"
    ".pane.zoom .ph img{aspect-ratio:auto;object-fit:contain}"
    ".fx{flex:1;min-width:0}"
    ".when{font-size:12.5px;color:" + MUTED + "}"
    ".big{font-size:28px;font-weight:800;line-height:1.15;margin:2px 0 4px}.big.none{font-size:16px;color:"
    + MUTED
    + "}"
    ".chg{display:inline-block;font-size:13.5px;font-weight:700;border-radius:8px;padding:3px 8px;"
    "background:#e6f5e5;color:#1b6b5f}.chg.bad{background:" + LATE + ";color:" + LATE_TEXT + "}"
    ".ask{margin:8px 0 0;background:#fff8e6;border:1.5px solid #f3d27a;border-radius:10px;"
    "padding:8px 10px;font-size:14px}"
    ".ask b{display:block;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;"
    "color:#8a5300;margin-bottom:2px}"
    ".rest{font-size:13px;color:" + MUTED + ";margin-top:8px}"
    ".bar{height:6px;border-radius:3px;background:#eef1f7;overflow:hidden;margin:4px 0}"
    ".bar i{display:block;height:6px;background:#1b6b5f;transition:width .25s}"
    ".chk{display:flex;gap:10px;align-items:flex-start;width:100%;text-align:left;background:none;border:0;"
    "border-bottom:1px solid " + HAIRLINE + ";padding:10px 0;cursor:pointer}"
    ".chk .box{flex:none;width:24px;height:24px;border:2px solid #cbd5e1;border-radius:7px;margin-top:1px;"
    "box-sizing:border-box;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800}"
    ".chk[aria-pressed=true] .box{background:#1b6b5f;border-color:#1b6b5f}"
    ".chk[aria-pressed=true] .box:after{content:'\\2713'}"
    ".chk[aria-pressed=true] b{text-decoration:line-through;color:" + MUTED + "}"
    ".chk .tx b{display:block;font-size:15px}.chk .tx small{display:block;font-size:13px;color:" + MUTED + "}"
    ".donemsg{font-weight:700;color:#1b6b5f;margin:8px 0 0}"
    ".next{background:" + INK + ";color:#fff;border-radius:12px;padding:12px 14px;margin:12px 0 4px}"
    ".next b{display:block;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase;"
    "opacity:.75;margin-bottom:2px}"
)

#: The card's one script: taps only. Every visit, checklist line and photo is already in
#: the page; this shows one visit, ticks a line, enlarges a photo. No network, no
#: storage, nothing the card-html sandbox refuses; with no script the card still reads.
SCRIPT = (
    "(function(){var d=document;function all(q){return Array.prototype.slice.call(d.querySelectorAll(q))}"
    "function pick(day){all('[data-pane]').forEach(function(e){e.hidden=e.getAttribute('data-pane')!==day});"
    "all('[data-tab]').forEach(function(e){e.setAttribute('aria-pressed',String(e.getAttribute('data-tab')===day))});"
    "all('.ring').forEach(function(e){var on=e.getAttribute('data-ring')===day;"
    "e.setAttribute('class',on?'ring on':'ring')});chip(day)}"
    "function chip(day){var t=d.querySelector('[data-tab=\"'+day+'\"]'),c=t&&t.parentNode;"
    "if(c&&c.scrollWidth>c.clientWidth)c.scrollLeft=Math.max(0,t.offsetLeft-c.offsetLeft-8)}"
    "var open=d.querySelector('[data-tab][aria-pressed=true]');if(open)chip(open.getAttribute('data-tab'));"
    "all('[data-tab]').forEach(function(e){e.addEventListener('click',function(){pick(e.getAttribute('data-tab'))})});"
    "all('[data-pt]').forEach(function(e){e.addEventListener('click',function(){pick(e.getAttribute('data-pt'))})});"
    "all('.ph').forEach(function(e){if(e.tagName!=='BUTTON')return;e.addEventListener('click',function(){"
    "var p=e.parentNode.parentNode;p.classList.toggle('zoom');"
    "e.querySelector('span').textContent=p.classList.contains('zoom')?'tap to shrink':'tap to enlarge'})});"
    "var prog=d.querySelector('.prog');function count(){if(!prog)return;var c=all('.chk'),n=c.filter(function(e)"
    "{return e.getAttribute('aria-pressed')==='true'}).length;var bar=d.querySelector('.bar i');"
    "if(bar)bar.style.width=(c.length?100*n/c.length:0)+'%';prog.firstChild.nodeValue=n+' of '+c.length+' done'"
    "+(function(t){var i=t.indexOf(' · ');return i>=0?t.slice(i):''})(prog.firstChild.nodeValue);"
    "var m=d.querySelector('.donemsg');if(m)m.hidden=n!==c.length}"
    "all('.chk').forEach(function(e){e.addEventListener('click',function(){"
    "e.setAttribute('aria-pressed',String(e.getAttribute('aria-pressed')!=='true'));count()})})})();"
)


def render_html(card: dict) -> str:
    """The card's ``index.html``: one file, inline style, no external resource, and
    (for a card with visits to tap through) one small inline script. Every string is
    escaped."""
    bg, fg = TONE.get(card.get("tone") or "", TONE["check"])
    chips = "".join(f"<li>{E(c)}</li>" for c in card.get("banner") or [])
    call = card.get("call")
    call_html = ""
    if call:
        call_html = (
            '<div class="call">'
            f'<div class="who">{E(call["label"])}</div>'
            f'<div class="num">{E(call["number"])}</div>'
            '<div class="how">Tap the number in the message below to call.</div>'
            "</div>"
        )
    primary = next((p for p in card.get("panels") or [] if p.get("kind") == "series" and p.get("points")), None)
    as_of = _day(card.get("as_of"))
    foot = f'<p class="foot">As of {E(_short(as_of))}</p>' if as_of else ""
    body = (
        '<div class="card">'
        f'<span class="tone" style="background:{bg};color:{fg}">{E(card.get("label") or "")}</span>'
        f'<h1>{E(card.get("title") or "")}</h1>'
        + (f'<p class="name">{E(card["case_name"])}</p>' if card.get("case_name") else "")
        + (f'<ul class="chips">{chips}</ul>' if chips else "")
        + (f'<p class="facts">{E(card["facts"])}</p>' if card.get("facts") else "")
        + call_html
        + "".join(_panel_html(p, card, p is primary) for p in card.get("panels") or [])
        + (f'<div class="next"><b>Your next step</b>{E(card["next_step"])}</div>' if card.get("next_step") else "")
        + foot
        + "</div>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{E(card.get('title') or 'Case')}</title><style>{CSS}</style></head>"
        f"<body>{body}" + (f"<script>{SCRIPT}</script>" if card.get("visits_detail") else "") + "</body></html>"
    )
