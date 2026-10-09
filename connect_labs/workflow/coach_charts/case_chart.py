"""A case's picture: the type its case state names (``case_state.picture``), over datasets
Labs builds from the case's own row and visits, frozen like every coaching chart.

Datasets (every number in the picture is one of these):

``case_series``   one row per visit with a reading of ``picture.series``: ``t`` (days
                  since the first reading), ``w``, ``w_label``, ``first`` / ``last``,
                  ``flag`` (the highlighted reading) and ``flag_label``;
``case_segments`` the line as segments, ``dashed`` into and out of a highlighted reading;
``case_band``     the reference band from the first reading: ``picture.reference``'s
                  low and high per 1000 per day, compounded daily (``lo``, ``hi``,
                  ``mid``, and its label on the last row);
``case_bars``     the ``picture.bars`` series at the latest visits that recorded it
                  (``case_types.MAX_BARS``);
``case_text``     the panels' words and shapes, positioned in pixels; ``panel`` names
                  which part of the picture each belongs to (``case_types.LAYOUT``).

Every word is the registry's (the picture's texts, filled from the case's row with
``case_states.fill``). The caption is Labs' own and has no numbers.
"""

from __future__ import annotations

import datetime as dt
import math

from connect_labs.semantic import case_states as cs
from connect_labs.workflow.coach_charts import case_types, theme
from connect_labs.workflow.coach_charts.datasets import ChartError

VERSION = 2

CAPTIONS = {
    "series_vs_reference": "A chart of this case's {series} at each visit, against the {reference} band.",
    "series_highlight_step": "A chart of this case's {series} at each visit, with one reading circled to check.",
    "series_with_bars": (
        "A chart of this case's {series} at each visit against the {reference} band, "
        "with the {bars} at each visit beside it."
    ),
    "sign_card": "A card with what was recorded for this case, and what to do next.",
}
#: What each case_summary panel shows, in a caption (never a number).
PANEL_WORDS = {
    "visits": "its visits and when the next is due",
    "signs": "the danger signs recorded",
    "facts": "its milestones",
}


# ---------------------------------------------------------------------------
# Text layout (pixels): lines wrapped in the theme font
# ---------------------------------------------------------------------------


class _Panel:
    """One part of a picture's text, ``width`` px wide; its rows carry its ``name``."""

    def __init__(self, name: str, width: int):
        self.name, self.width = name, width
        self.rows: list[dict] = []
        self.y = 0

    def add(self, row: dict, *, first: bool = False):
        row = {**row, "panel": self.name}
        if first:
            self.rows.insert(0, row)
        else:
            self.rows.append(row)

    def text(self, text, *, x=0, size=22, bold=False, color=theme.INK, width=None, line=None, max_lines=6) -> int:
        from connect_labs.workflow.coach_charts.types import wrap

        line = line or round(size * 1.3)
        lines = wrap(text, size=size, width=width or (self.width - x), max_lines=max_lines)
        for ln in lines:
            self.add(
                {
                    "kind": "text",
                    "x": x,
                    "y": self.y + line / 2,
                    "text": ln,
                    "size": size,
                    "bold": bold,
                    "color": color,
                }
            )
            self.y += line
        return len(lines)

    def dot(self, *, x, y, diameter, color):
        self.add({"kind": "dot", "x": x, "y": y, "area": round(math.pi * (diameter / 2) ** 2), "color": color})

    def ctext(self, text, *, x, y, size=20, color="#ffffff"):
        self.add({"kind": "ctext", "x": x, "y": y, "text": text, "size": size, "color": color})

    def rect(self, *, x, y, x2, y2, color, opacity=1.0):
        self.add({"kind": "rect", "x": x, "y": y, "x2": x2, "y2": y2, "color": color, "opacity": opacity})

    def gap(self, n):
        self.y += n


def _numbered(panel: _Panel, items, *, color=theme.INDIGO, x=0, size=22, dot=34):
    for n, item in enumerate(items, 1):
        top = panel.y
        lines = panel.text(item, x=x + dot + 10, size=size)
        mid = top + round(size * 1.3) / 2
        panel.dot(x=x + dot / 2, y=mid, diameter=dot, color=color)
        panel.ctext(str(n), x=x + dot / 2, y=mid, size=20)
        panel.gap(8 if lines else 0)


def _checklist(panel: _Panel, title: str, items):
    if title:
        panel.text(title, size=22, bold=True, color=theme.DEEP_PURPLE)
        panel.gap(6)
    _numbered(panel, items, size=20, dot=28)


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


def _nice_step(span: float) -> int:
    for step in (1, 2, 5, 10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if span / step <= 5:
            return step
    return 10000


def _ticks(ts: list[int], max_t: int, kind: str, min_px: float = 80) -> list[int]:
    """Days to label on the x axis, never closer than ``min_px``; the last always."""
    lo, hi = case_types.plot_range(kind)
    px = lambda t: lo + (hi - lo) * t / max(max_t, 1)  # noqa: E731
    kept: list[int] = []
    for t in sorted(set(ts)):
        if not kept or px(t) - px(kept[-1]) >= min_px:
            kept.append(t)
    last = max(ts) if ts else 0
    if kept and kept[-1] != last:
        if px(last) - px(kept[-1]) < min_px:
            kept.pop()
        kept.append(last)
    return kept


def _flag_label_t(kind: str, t: int, max_t: int, label: str) -> float:
    """Where (in days) to centre the ringed reading's label: over the reading, slid
    sideways just enough to keep the whole label inside the plot."""
    from connect_labs.workflow.coach_charts.types import _text_width

    lo, hi = case_types.plot_range(kind)
    span = max(max_t, 1)
    half = _text_width(label, size=case_types.FLAG_SIZE, bold=True) / 2 + 4
    px = lo + (hi - lo) * t / span
    px = min(max(px, half), case_types.LAYOUT[kind]["plot"] - half)
    return round((px - lo) / (hi - lo) * span, 3)


def _band(w0: float, max_t: int, ref: dict) -> list[dict]:
    words = str(ref.get("label") or "").split()
    label_1, label_2 = (words[0], " ".join(words[1:])) if words else ("", "")
    low, high = float(ref.get("low") or 0), float(ref.get("high") or 0)
    rows = []
    for t in range(0, max_t + 1):
        lo, hi = w0 * (1 + low / 1000) ** t, w0 * (1 + high / 1000) ** t
        rows.append(
            {
                "t": t,
                "lo": round(lo),
                "hi": round(hi),
                "mid": round((lo + hi) / 2),
                "end": t == max_t,
                "label_1": label_1,
                "label_2": label_2,
            }
        )
    return rows


def _series_spec(series: list[dict], name: str | None) -> dict:
    return next((s for s in series if s["name"] == name), {"name": name or "", "label": name or ""})


def _num(v):
    return cs._as_number(v)


def build_datasets(
    state: dict, row: dict, visits: list[dict], series: list[dict], case_name: str = ""
) -> tuple[dict, dict]:
    """``(datasets, meta)`` for the case state's picture; meta is layout the spec needs."""
    pic = state.get("picture") or {}
    kind = pic.get("type")
    if kind == "case_summary":
        from connect_labs.workflow.coach_charts import case_summary

        return case_summary.build(state, row, visits, series, case_name)
    spec = _series_spec(series, pic.get("series"))
    meta: dict = {
        "kind": kind,
        "case_name": case_name or None,
        "title": cs.fill(pic.get("title") or state.get("label") or "", row),
        "unit": spec.get("unit") or "",
    }
    ds: dict = {"case_series": [], "case_segments": [], "case_band": [], "case_bars": [], "case_text": []}

    pts = [(cs._as_date(v.get("visit_date")), _num(v.get(spec["name"]))) for v in visits]
    pts = [(d, w) for d, w in pts if d is not None and w is not None]
    bars_spec = _series_spec(series, pic.get("bars")) if pic.get("bars") else None
    bar_pts = []
    if bars_spec:
        bar_pts = [(cs._as_date(v.get("visit_date")), _num(v.get(bars_spec["name"]))) for v in visits]
        bar_pts = [(d, h) for d, h in bar_pts if d is not None and h is not None]
    start = pts[0][0] if pts else (bar_pts[0][0] if bar_pts else dt.date.today())
    meta["start"] = (start.year, start.month, start.day)
    t_of = lambda d: (d - start).days  # noqa: E731
    max_t = max([t_of(d) for d, _ in pts] + [t_of(d) for d, _ in bar_pts] + [1])
    meta["max_t"] = max_t

    hl = pic.get("highlight") or {}
    flag_from = cs._as_date(row.get(hl.get("from"))) if hl else None
    flag_to = cs._as_date(row.get(hl.get("to"))) if hl else None
    fmt = lambda w: (cs.series_value(spec, w) or "") + (f" {meta['unit']}" if meta["unit"] else "")  # noqa: E731
    for n, (d, w) in enumerate(pts):
        ds["case_series"].append(
            {
                "t": t_of(d),
                "w": w,
                "w_label": fmt(w),
                "first": n == 0,
                "last": n == len(pts) - 1,
                "flag": flag_to is not None and d == flag_to,
                "flag_label": None,
            }
        )
    if flag_to is not None:
        idx = next((i for i in range(len(ds["case_series"]) - 1, -1, -1) if ds["case_series"][i]["flag"]), None)
        if idx is not None:
            target = ds["case_series"][idx]
            target["flag_label"] = cs.fill(hl.get("label") or "", row) or None
            target["flag_lx"] = _flag_label_t(kind, target["t"], max_t, target["flag_label"] or "")
    # Segments: dashed between the highlighted step's two ends (from -> to).
    for n in range(len(pts) - 1):
        a, b = ds["case_series"][n], ds["case_series"][n + 1]
        da, db = pts[n][0], pts[n + 1][0]
        dashed = flag_to is not None and (
            b["flag"] or a["flag"] or (flag_from is not None and flag_from <= da and db <= flag_to)
        )
        for p in (a, b):
            ds["case_segments"].append({"seg": n, "t": p["t"], "w": p["w"], "dashed": bool(dashed)})

    ref = pic.get("reference")
    if ref and pts:
        ds["case_band"] = _band(pts[0][1], max_t, ref)
    values = [p[1] for p in pts] + [r["lo"] for r in ds["case_band"]] + [r["hi"] for r in ds["case_band"]]
    values = values or [0]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.12, abs(hi) * 0.04, 1)
    step = _nice_step(hi - lo + 2 * pad)
    y_lo = math.floor((lo - pad) / step) * step
    if lo >= 0:
        y_lo = max(0, y_lo)
    y_hi = math.ceil((hi + pad) / step) * step
    meta["y_domain"] = [y_lo, y_hi]
    meta["y_ticks"] = list(range(int(y_lo), int(y_hi) + 1, step))[1:]
    meta["ticks"] = _ticks([r["t"] for r in ds["case_series"]] or [0], max_t, kind)

    bar_pts = bar_pts[-case_types.MAX_BARS :]
    for n, (d, h) in enumerate(bar_pts):
        last = n == len(bar_pts) - 1
        ds["case_bars"].append(
            {
                "t": t_of(d),
                "h": h,
                "h_label": (cs.series_value(bars_spec, h) or "")
                + (f" {bars_spec.get('unit')}" if bars_spec.get("unit") else ""),
                "date_label": cs.day_short(d),
                "tone": case_types.SUNSET if last else theme.CORNFLOWER,
            }
        )
    meta["skin_top"] = max([h for _, h in bar_pts] + [1]) * 1.3

    widths = case_types.LAYOUT[kind]["panels"] if kind in case_types.LAYOUT else {}
    panels = {name: _Panel(name, width) for name, width in widths.items()}
    if kind == "series_vs_reference":
        panel = panels["top"]
        badge = cs.fill(pic.get("badge") or "", row)
        gain = cs.fill(pic.get("gain_label") or "", row)
        if badge:
            panel.rect(x=0, y=0, x2=228, y2=case_types.BADGE_ROW, color=case_types.GREEN)
            panel.ctext(badge, x=114, y=case_types.BADGE_ROW / 2, size=26)
        if gain:
            head, _, tail = gain.partition(" since ")
            panel.add(
                {"kind": "text", "x": 250, "y": 18, "text": head, "size": 30, "bold": True, "color": case_types.GREEN}
            )
            if tail:
                panel.add(
                    {
                        "kind": "text",
                        "x": 250,
                        "y": 46,
                        "text": f"since {tail}",
                        "size": 20,
                        "bold": False,
                        "color": theme.MUTED,
                    }
                )
        panel.y = case_types.BADGE_ROW if (badge or gain) else 1
    elif kind == "series_highlight_step":
        if pic.get("checklist"):
            _checklist(panels["side"], cs.fill(pic.get("checklist_title") or "", row), pic["checklist"])
    elif kind == "series_with_bars":
        panels["bars"].text(
            cs.fill(pic.get("bars_title") or "", row) or (bars_spec or {}).get("label", ""),
            size=22,
            bold=True,
            color=theme.DEEP_PURPLE,
        )
    elif kind == "sign_card":
        _sign_card(panels, pic, row)
    meta["panel_height"] = {name: max(panel.y, 1) for name, panel in panels.items()}
    ds["case_text"] = [r for panel in panels.values() for r in panel.rows]
    return ds, meta


def _sign_card(panels: dict[str, _Panel], pic: dict, row: dict):
    """The badge and visit date across the top; the signs recorded on the left; why it
    matters and what to do on the right."""
    when = row.get(pic.get("date")) if pic.get("date") else None
    when_d = cs._as_date(when)
    listed = [s.strip() for s in str(row.get(pic.get("signs")) or "").split(",") if s.strip()]
    # A label may itself hold a comma ("pus in the eyes, skin or belly button"): rejoin
    # pieces until they name a known sign.
    known = pic.get("sign_text") or {}
    signs, buf = [], ""
    for piece in listed:
        buf = f"{buf}, {piece}" if buf else piece
        if buf in known or not any(k.startswith(buf) for k in known):
            signs.append(buf)
            buf = ""
    if buf:
        signs.append(buf)

    # Left: the badge, the visit, the signs. A lone sign says why it matters; several
    # are listed by name.
    left = panels["left"]
    badge = cs.fill(pic.get("badge") or "", row)
    if badge:
        from connect_labs.workflow.coach_charts.types import _font

        width = int(_font(22).getlength(badge)) + 44
        left.rect(x=0, y=0, x2=width, y2=44, color=case_types.SUNSET)
        left.ctext(badge, x=width / 2, y=22, size=22)
        left.gap(56)
    if when_d:
        left.text(f"Visit on {cs.day_long(when_d)}", size=22, bold=True)
        left.gap(10)
    shown = signs[:3]
    for sign in shown:
        text = known.get(sign) or {"title": sign[:1].upper() + sign[1:], "why": ""}
        at = left.y
        left.dot(x=13, y=at + 14, diameter=26, color=case_types.SUNSET)
        left.ctext("!", x=13, y=at + 14, size=20)
        left.text(text["title"], x=36, size=22, bold=True)
        if text.get("why") and len(signs) == 1:
            left.gap(2)
            left.text(text["why"], x=36, size=20, color=theme.MUTED)
        left.gap(6)
    if len(signs) > len(shown):
        left.text("Also recorded: " + ", ".join(signs[len(shown) :]), size=20, color=theme.MUTED)

    # Right: what to do; the card's own "why" above it when the picture has room.
    right = panels["right"]
    actions = _Panel("right", right.width)
    if pic.get("actions"):
        title = cs.fill(pic.get("actions_title") or "", row)
        if title:
            actions.text(title, size=22, bold=True, color=theme.DEEP_PURPLE)
            actions.gap(6)
        _numbered(actions, pic["actions"], color=case_types.SUNSET, size=20, dot=28)
    why = cs.fill(pic.get("why") or "", row)
    if why:
        trial = _Panel("right", right.width)
        trial.text(why, size=20, color=theme.INK)
        trial.gap(16)
        if trial.y + actions.y <= case_types.BODY:
            right.rows, right.y = trial.rows, trial.y
    for r in actions.rows:
        right.add({**r, "y": r["y"] + right.y, **({"y2": r["y2"] + right.y} if "y2" in r else {})})
    right.y += actions.y


def _summary_caption(pic: dict, series: list[dict]) -> str:
    parts = []
    for panel in pic.get("panels") or []:
        kind = panel.get("kind")
        if kind in ("series", "bars"):
            label = (_series_spec(series, panel.get("series")).get("label") or "readings").lower()
            parts.append(f"its {label}" + (" against the danger line" if panel.get("floor") else ""))
        elif kind == "list":
            title = str(panel.get("title") or "").strip()
            parts.append(title.lower() if title and not any(c.isdigit() for c in title) else "what to do")
        elif kind in PANEL_WORDS:
            parts.append(PANEL_WORDS[kind])
    shown = ", ".join(parts[:-1]) + (f" and {parts[-1]}" if len(parts) > 1 else parts[-1] if parts else "")
    return f"A summary of this case: {shown}." if shown else "A summary of this case."


def caption(state: dict, series: list[dict]) -> str:
    pic = state.get("picture") or {}
    if pic.get("type") == "case_summary":
        return _summary_caption(pic, series)
    words = {
        "series": (_series_spec(series, pic.get("series")).get("label") or "readings").lower(),
        "reference": str((pic.get("reference") or {}).get("label") or "reference"),
        "bars": (_series_spec(series, pic.get("bars")).get("label") or "readings").lower() if pic.get("bars") else "",
    }
    return CAPTIONS.get(pic.get("type"), "A picture of this case.").format(**words)


def build_case_chart(
    state: dict,
    row: dict,
    *,
    visits: list[dict],
    series: list[dict],
    case_name: str = "",
    others: list[tuple[str, str]] | None = None,
) -> dict:
    """The frozen chart of ``row`` in ``state``: ``{v, type, params, spec, datasets,
    caption, alt, notes}``. Raises ``ChartError`` when it cannot be drawn."""
    from connect_labs.workflow.coach_charts import chart as charts
    from connect_labs.workflow.coach_charts import render

    pic = state.get("picture") or {}
    kind = pic.get("type")
    if kind not in case_types.CASE_TYPES:
        raise ChartError("no_picture", f"case state {state.get('name')} names no picture type Labs draws")
    ds, meta = build_datasets(state, row, visits, series, case_name)
    if kind not in ("sign_card", "case_summary") and not ds["case_series"]:
        raise ChartError("no_readings", "this case has no readings to draw")
    spec = case_types.CASE_TYPES[kind](meta)
    text = caption(state, series)
    terms = charts.identity_terms(others or [], "")
    leaked = sorted(
        {t for s in [text, *charts._strings(spec), *charts._strings(ds)] for t in charts.identity_leaks(s, terms)}
    )
    if leaked:
        raise ChartError("peer_identity", "the picture's text names another worker")
    chart = {
        "v": VERSION,
        "type": kind,
        "params": {"case_state": state.get("name")},
        "spec": spec,
        "datasets": ds,
        "caption": text,
        "alt": text,
        "notes": [],
    }
    try:
        charts.png(chart)  # drawn now: a picture that cannot be drawn is refused at preview
    except render.RenderError as e:
        raise ChartError("unrenderable", e.message) from e
    return chart
