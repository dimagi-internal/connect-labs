"""The ``case_summary`` picture: one case at a glance, like the summary page of a patient
record. A title (the case state's), the case's name, a banner of facts about it, and up
to four panels in a 2x2 grid -- each a kind the registry chooses and binds to its own
columns and visit series (``semantic/case_states.PANEL_KINDS``):

``series``  one visit series as a line: against a reference band from its first
            reading, a floor (a danger line), a ringed step, a star at the latest;
``bars``    one visit series as bars, the latest few visits;
``visits``  the visits as a timeline -- on time or late, the danger-sign check, a
            referral -- and the next visit's window, flagged when it is overdue;
``list``    a titled numbered list (a checklist, what to do, next steps);
``signs``   the labels a case column lists, under a badge;
``facts``   a badge and a few lines worded from the case (milestones).

Everything is laid out here, in pixels, and drawn by one layered Vega-Lite view over
the ``case_text`` dataset (``spec``). Laying out by hand is what keeps a dense
picture legible at phone width: every text is the theme's size or larger, and what
does not fit is LEFT OUT (a chip, a line, a label) rather than drawn smaller.

The frame is 600 x 450 CSS px (4:3): landscape, so Connect's messenger sizes the
bubble by its width (#2413), and a little taller than the 3:2 charts because four
panels and a banner need the height. Every word is the registry's, every number the
case's; ``case_points`` carries the series values each panel draws.
"""

from __future__ import annotations

import math
from typing import Any

from connect_labs.semantic import case_states as cs
from connect_labs.workflow.coach_charts import theme

W = theme.WIDTH - 2 * theme.PADDING  # 536
H = 450 - 2 * theme.PADDING  # 386: the frame is 600 x 450
GAP = 12
GRID_TOP = 108
CARD_W = (W - GAP) // 2
CARD_H = (H - GRID_TOP - 12) // 2
INSET = 12
TEXT = theme.MIN_FONT_SIZE  # 20: the smallest any text is drawn

GREEN = theme.BAND_COLOURS["green"]
SUNSET = theme.BAND_COLOURS["red"]
MARIGOLD = theme.BAND_COLOURS["yellow"]
TONES = {"indigo": theme.INDIGO, "sunset": SUNSET, "green": GREEN}

STAR = (
    "M0,-1L0.2245,-0.309L0.951,-0.309L0.363,0.118L0.588,0.809L0,0.382L-0.588,0.809"
    "L-0.363,0.118L-0.951,-0.309L-0.2245,-0.309Z"
)


def _width(text: str, size: int = TEXT, bold: bool = False) -> float:
    from connect_labs.workflow.coach_charts.types import _text_width

    return _text_width(text, size=size, bold=bold)


def _fit(text: str, width: float, size: int = TEXT, bold: bool = False) -> str:
    """``text`` cut to ``width`` with an ellipsis (never a smaller font)."""
    text = str(text or "")
    if _width(text, size, bold) <= width:
        return text
    while text and _width(text + "…", size, bold) > width:
        text = text[:-1]
    return text.rstrip(" ,;·") + "…"


class Draw:
    """Shapes and text in pixels, as rows of the ``case_text`` dataset."""

    def __init__(self):
        self.rows: list[dict] = []
        self._seg = 0

    def rect(self, x, y, x2, y2, color, *, opacity=1.0, shape="card"):
        # shape: card (8 px corners), pill (fully round), bar (round top only), plain
        self.rows.append(
            {"kind": f"rect_{shape}", "x": x, "y": y, "x2": x2, "y2": y2, "color": color, "opacity": opacity}
        )

    def text(self, x, y, text, *, size=TEXT, bold=False, color=theme.INK, align="left"):
        if text:
            self.rows.append(
                {
                    "kind": f"text_{align}_{'b' if bold else 'r'}",
                    "x": x,
                    "y": y,
                    "text": str(text),
                    "size": size,
                    "color": color,
                }
            )

    def dot(self, x, y, diameter, color, *, ring=False, stroke=4):
        self.rows.append(
            {
                "kind": "ring" if ring else "dot",
                "x": x,
                "y": y,
                "area": round(math.pi * (diameter / 2) ** 2),
                "color": color,
                "stroke": stroke,
            }
        )

    def star(self, x, y, size, color):
        self.rows.append({"kind": "star", "x": x, "y": y, "area": size, "color": color})

    def line(self, points, color, *, width=4, dashed=False):
        self._seg += 1
        for o, (x, y) in enumerate(points):
            self.rows.append(
                {"kind": "line", "seg": self._seg, "o": o, "x": x, "y": y, "color": color, "w": width, "dash": dashed}
            )

    def area(self, points, color, *, opacity=0.18):
        """``points``: (x, y_low, y_high), left to right."""
        self._seg += 1
        for o, (x, y, y2) in enumerate(points):
            self.rows.append(
                {
                    "kind": "area",
                    "seg": self._seg,
                    "o": o,
                    "x": x,
                    "y": y,
                    "y2": y2,
                    "color": color,
                    "opacity": opacity,
                }
            )

    def check(self, x, y, color="#ffffff"):
        """A tick, centred on (x, y), drawn over dots."""
        self._seg += 1
        for o, (px, py) in enumerate([(x - 5, y), (x - 1.5, y + 4), (x + 5.5, y - 4.5)]):
            self.rows.append({"kind": "tick", "seg": self._seg, "o": o, "x": px, "y": py, "color": color})


# ---------------------------------------------------------------------------
# Building the picture
# ---------------------------------------------------------------------------


def build(state: dict, row: dict, visits: list[dict], series: list[dict], case_name: str = "") -> tuple[dict, dict]:
    """``(datasets, meta)`` for a ``case_summary`` picture of ``row``."""
    pic = state.get("picture") or {}
    d = Draw()
    points: list[dict] = []

    title = cs.fill(pic.get("title") or state.get("label") or "", row)
    d.text(
        0,
        20,
        _fit(title, W, size=theme.TITLE_SIZE, bold=True),
        size=theme.TITLE_SIZE,
        bold=True,
        color=theme.DEEP_PURPLE,
    )
    if case_name:
        d.text(0, 58, _fit(case_name, W, size=24, bold=True), size=24, bold=True)
    _banner(d, [cs.fill_if_set(chip, row) for chip in pic.get("banner") or []])

    by_name = {s["name"]: s for s in series}
    for i, panel in enumerate((pic.get("panels") or [])[:4]):
        x = 0 if i % 2 == 0 else CARD_W + GAP
        y = GRID_TOP + (0 if i < 2 else CARD_H + 12)
        card = _Card(d, x, y)
        kind = panel.get("kind")
        if kind == "series":
            points += _series_panel(card, panel, row, visits, by_name, i)
        elif kind == "bars":
            points += _bars_panel(card, panel, row, visits, by_name, i)
        elif kind == "visits":
            _visits_panel(card, panel, row, visits, by_name)
        elif kind == "list":
            _list_panel(card, panel, row)
        elif kind == "signs":
            _signs_panel(card, panel, row)
        elif kind == "facts":
            _facts_panel(card, panel, row)
    meta = {"kind": "case_summary", "case_name": case_name or None, "title": title}
    return {"case_text": d.rows, "case_points": points}, meta


def _banner(d: Draw, chips: list[str | None]):
    """The banner under the name: the case's facts in one line, `` · `` between them.
    A fact that does not fit is left out (the registry lists them most useful first)."""
    shown: list[str] = []
    for chip in chips:
        if chip and _width(" · ".join([*shown, chip])) <= W:
            shown.append(chip)
    d.text(0, 88, " · ".join(shown), color=theme.MUTED)


class _Card:
    def __init__(self, d: Draw, x: float, y: float):
        self.d, self.x, self.y = d, x, y
        self.x0, self.x1 = x + INSET, x + CARD_W - INSET
        self.y0, self.y1 = y + 42, y + CARD_H - 10
        d.rect(x, y, x + CARD_W, y + CARD_H, theme.RULE, opacity=0.45)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def title(
        self, text: str, color=theme.DEEP_PURPLE, *, suffix: str = "", suffix_color=theme.MUTED, suffix_bold=False
    ) -> float:
        """The card's title, and an optional suffix after it when it fits; returns the
        x after them."""
        text = _fit(text, self.width, bold=True)
        self.d.text(self.x0, self.y + 20, text, bold=True, color=color)
        end = self.x0 + _width(text, bold=True)
        if suffix and end + 8 + _width(suffix, bold=suffix_bold) <= self.x1:
            self.d.text(end + 8, self.y + 20, suffix, bold=suffix_bold, color=suffix_color)
            end += 8 + _width(suffix, bold=suffix_bold)
        return end

    def pill(self, x, cy, text, color, *, align="left") -> float:
        """A filled pill with white bold text; returns its width."""
        w = _width(text, bold=True) + 20
        x = x - w if align == "right" else x
        self.d.rect(x, cy - 15, x + w, cy + 15, color, shape="pill")
        self.d.text(x + w / 2, cy, text, bold=True, color="#ffffff", align="center")
        return w


def _readings(visits: list[dict], name: str) -> list[tuple[Any, float]]:
    """A series' readings in date order, one per day (a day's last reading: the
    registration and the first visit are often the same day)."""
    by_day: dict = {}
    for v in visits:
        day, value = cs._as_date(v.get("visit_date")), cs._as_number(v.get(name))
        if day is not None and value is not None:
            by_day[day] = value
    return sorted(by_day.items())


def _first_that_fits(template: Any, row: dict, width: float) -> str | None:
    """The first choice of ``template`` whose columns are set AND whose words fit
    ``width`` -- a shorter wording before none at all."""
    if not template:
        return None
    choices = template if isinstance(template, list) else [template]
    texts = [cs.fill_if_set([c] if isinstance(c, dict) else c, row) for c in choices]
    texts = [t for t in texts if t]
    return next((t for t in texts if _width(t) <= width), _fit(texts[0], width) if texts else None)


def _label(spec: dict, value: float) -> str:
    shown = cs.series_value(spec, value) or ""
    unit = spec.get("unit") or ""
    return f"{shown} {unit}" if unit and unit.isalpha() else f"{shown}{unit}" if unit else shown


def _series_panel(card: _Card, panel: dict, row: dict, visits: list[dict], by_name: dict, idx: int) -> list[dict]:
    d = card.d
    spec = by_name.get(panel.get("series")) or {"name": panel.get("series"), "label": panel.get("series") or ""}
    pts = _readings(visits, spec["name"])
    title = cs.fill(panel.get("title") or spec.get("label") or "", row)
    ref, floor, hl = panel.get("reference"), panel.get("floor"), panel.get("highlight")
    flag_label = cs.fill_if_set(hl.get("label"), row) if hl else None
    flag_day = cs._as_date(row.get(hl.get("to"))) if hl else None
    flag_from = cs._as_date(row.get(hl.get("from"))) if hl else None
    # The title, and after it the key to the band or the danger line when it fits there
    # (else inside the plot). The question about a ringed reading IS the title.
    key, key_color = (str(ref.get("label") or ""), GREEN) if ref else (str((floor or {}).get("label") or ""), SUNSET)
    if flag_label:
        card.title(flag_label, SUNSET)
        key_in_title = False
    else:
        key_in_title = bool(key) and card.title(title, suffix=key, suffix_color=key_color) > card.x0 + _width(
            _fit(title, card.width, bold=True), bold=True
        )
    if not pts:
        d.text(card.x0, card.y0 + 20, "not recorded", color=theme.MUTED)
        return []

    note = _first_that_fits(panel.get("note"), row, card.width)
    every_value = floor is not None and len(pts) <= 5  # a few readings against a line: label each
    last_text = _label(spec, pts[-1][1])
    right_room = 6 if every_value else _width(last_text, bold=True) + 14
    if panel.get("star"):
        right_room = max(right_room, 22)
    px0, px1 = card.x0 + 8, card.x1 - right_room
    top = card.y0 + (22 if every_value or panel.get("star") or flag_label else 6)
    bottom = card.y1 - 26

    start = pts[0][0]
    span = max((pts[-1][0] - start).days, 1)
    band: list[tuple[float, float, float]] = []
    values = [v for _, v in pts]
    if ref:
        lo_r, hi_r = float(ref.get("low") or 0), float(ref.get("high") or 0)
        for t in range(0, span + 1):
            band.append((t, pts[0][1] * (1 + lo_r / 1000) ** t, pts[0][1] * (1 + hi_r / 1000) ** t))
        values += [b[1] for b in band] + [b[2] for b in band]
    if floor is not None:
        values += [0.0, float(floor["value"])]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.1, abs(hi) * 0.02, 1)
    lo, hi = (0.0 if floor is not None else lo - pad), hi + pad

    def X(day) -> float:
        return px0 + (px1 - px0) * (day - start).days / span

    def Y(v) -> float:
        return bottom - (bottom - top) * (v - lo) / (hi - lo)

    if band:
        d.area([(px0 + (px1 - px0) * t / span, Y(a), Y(b)) for t, a, b in band], GREEN)
        if key and not key_in_title:
            # Top left of the plot, where a band from the first reading never reaches.
            d.text(card.x0, top + 4, _fit(key, card.width * 0.6), color=GREEN)
    if floor is not None:
        fy = Y(float(floor["value"]))
        d.line([(card.x0, fy), (card.x1, fy)], SUNSET, width=2, dashed=True)
        if key and not key_in_title:
            d.text(card.x1, fy + 13, _fit(key, card.width), color=SUNSET, align="right")
    xy = [(X(day), Y(v)) for day, v in pts]
    for n in range(len(xy) - 1):
        dashed = bool(flag_day) and (
            pts[n + 1][0] == flag_day
            or pts[n][0] == flag_day
            or (flag_from is not None and flag_from <= pts[n][0] and pts[n + 1][0] <= flag_day)
        )
        d.line([xy[n], xy[n + 1]], theme.INDIGO, width=4, dashed=dashed)
    for x, y in xy:
        d.dot(x, y, 12, theme.INDIGO)
    if flag_day:
        for (day, _v), (x, y) in zip(pts, xy):
            if day == flag_day:
                d.dot(x, y, 34, SUNSET, ring=True)
    if panel.get("star"):
        d.star(xy[-1][0], xy[-1][1], 700, MARIGOLD)
    if every_value:
        for (day, v), (x, y) in zip(pts, xy):
            d.text(x, y - 16, _label(spec, v), bold=True, color=theme.INDIGO, align="center")
    else:
        d.text(xy[-1][0] + (18 if panel.get("star") else 10), xy[-1][1], last_text, bold=True, color=theme.INDIGO)
    # The bottom row: what the feeding is (the note), else the first and last dates.
    if note:
        d.text(card.x0, card.y1 - 10, _fit(note, card.width), color=theme.MUTED)
    else:
        d.text(card.x0, card.y1 - 10, cs.day_short(pts[0][0]), color=theme.MUTED)
        if len(pts) > 1:
            d.text(card.x1, card.y1 - 10, cs.day_short(pts[-1][0]), color=theme.MUTED, align="right")
    return [{"panel": idx, "series": spec["name"], "date": day.isoformat(), "value": v} for day, v in pts]


def _bars_panel(card: _Card, panel: dict, row: dict, visits: list[dict], by_name: dict, idx: int) -> list[dict]:
    d = card.d
    spec = by_name.get(panel.get("series")) or {"name": panel.get("series"), "label": panel.get("series") or ""}
    card.title(cs.fill(panel.get("title") or spec.get("label") or "", row))
    pts = _readings(visits, spec["name"])[-int(panel.get("last") or 3) :]
    if not pts:
        d.text(card.x0, card.y0 + 20, "not recorded", color=theme.MUTED)
        return []
    slot = card.width / len(pts)
    top_v = max(v for _, v in pts) or 1
    base, top = card.y1 - 26, card.y0 + 20
    for n, (day, v) in enumerate(pts):
        cx = card.x0 + slot * (n + 0.5)
        bw = min(slot * 0.55, 48)
        bh = max((base - top) * v / top_v, 3)
        falling = n == len(pts) - 1 and n > 0 and v < pts[n - 1][1]
        color = SUNSET if falling else theme.CORNFLOWER
        d.rect(cx - bw / 2, base - bh, cx + bw / 2, base, color, shape="bar")
        d.text(cx, base - bh - 13, _label(spec, v), bold=True, color=color, align="center")
        d.text(cx, card.y1 - 10, cs.day_short(day), color=theme.MUTED, align="center")
    return [{"panel": idx, "series": spec["name"], "date": day.isoformat(), "value": v} for day, v in pts]


def _matches(value: Any, word: str | None) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() == str(word or "").strip().lower()


def _visits_panel(card: _Card, panel: dict, row: dict, visits: list[dict], by_name: dict):
    d = card.d
    title_end = card.title(cs.fill(panel.get("title") or "Visits", row))
    overdue = cs._as_number(row.get(panel["overdue_days"])) if panel.get("overdue_days") else None
    days = int(round(overdue)) if overdue and overdue > 0 else 0
    if days:
        wordings = [f"{days} day{'s' if days != 1 else ''} overdue", f"{days} d overdue", "OVERDUE"]
        text = next((t for t in wordings if title_end + 8 + _width(t, bold=True) + 20 <= card.x1), "OVERDUE")
        card.pill(card.x1, card.y + 20, text, SUNSET, align="right")
    dated = [(cs._as_date(v.get("visit_date")), v) for v in visits]
    dated = [(day, v) for day, v in dated if day is not None]
    due_from = cs._as_date(row.get(panel.get("due_from"))) if panel.get("due_from") else None
    due_to = cs._as_date(row.get(panel.get("due_to"))) if panel.get("due_to") else None
    if not dated:
        d.text(card.x0, card.y0 + 20, "no visits recorded", color=theme.MUTED)
        return
    start = dated[0][0]
    end = max([dated[-1][0]] + [x for x in (due_from, due_to) if x])
    span = max((end - start).days, 1)
    x0, x1 = card.x0 + 12, card.x1 - 12
    cy = card.y0 + 10

    def X(day) -> float:
        return x0 + (x1 - x0) * (day - start).days / span

    late = SUNSET if days else theme.NEUTRAL
    d.line([(X(start), cy), (X(dated[-1][0]), cy)], theme.NEUTRAL, width=3)
    if due_from and due_to:
        d.line([(X(dated[-1][0]), cy), (X(due_from), cy)], late, width=3, dashed=True)
        d.rect(X(due_from) - 4, cy - 9, X(due_to) + 4, cy + 9, late, opacity=0.3, shape="pill")
    on_time = panel.get("on_time")
    for day, v in dated:
        timing = _matches(v.get(panel.get("timeliness")), on_time) if panel.get("timeliness") else None
        color = theme.NEUTRAL if timing is None else GREEN if timing else MARIGOLD
        danger = _matches(v.get(panel.get("danger")), panel.get("danger_yes")) if panel.get("danger") else None
        referred = _matches(v.get(panel.get("referred")), panel.get("referred_yes")) if panel.get("referred") else None
        x = X(day)
        if referred:
            d.dot(x, cy, 32, theme.INDIGO, ring=True, stroke=3)
        d.dot(x, cy, 22, SUNSET if danger else color)
        if danger:
            d.text(x, cy, "!", bold=True, color="#ffffff", align="center")
        elif danger is False:
            d.check(x, cy)
    # The key: what the colours mean, then when the next visit is due.
    kx = card.x0
    for label, color in (("on time", GREEN), ("late", MARIGOLD), ("sign", SUNSET)):
        w = 18 + _width(label)
        if kx + w > card.x1:
            break
        d.dot(kx + 6, card.y0 + 36, 12, color)
        d.text(kx + 16, card.y0 + 36, label, color=theme.MUTED)
        kx += w + 14
    if due_from and due_to:
        window = (
            f"{due_from.day}–{cs.day_short(due_to)}"
            if due_from.month == due_to.month
            else f"{cs.day_short(due_from)}–{cs.day_short(due_to)}"
        )
        wordings = [f"Was due {window}, {days} days ago", f"Was due {window}"] if days else [f"Next due {window}"]
        text = next((t for t in wordings if _width(t, bold=bool(days)) <= card.width), wordings[-1])
        d.text(
            card.x0,
            card.y1 - 10,
            _fit(text, card.width, bold=bool(days)),
            color=SUNSET if days else theme.INK,
            bold=bool(days),
        )
    else:
        _vitals(card, panel, dated[-1][1], by_name)


def _vitals(card: _Card, panel: dict, last: dict, by_name: dict):
    """The last visit's vitals, green inside the panel's limits and red outside -- as
    many as fit on one line."""
    x = card.x0
    for v in panel.get("vitals") or []:
        spec = by_name.get(v.get("series")) or {}
        value = cs._as_number(last.get(v.get("series")))
        if value is None:
            continue
        text = f"{v.get('label')} {_label(spec, value)}"
        w = _width(text)
        if x + w > card.x1:
            break
        ok = (v.get("low") is None or value >= v["low"]) and (v.get("high") is None or value <= v["high"])
        card.d.text(x, card.y1 - 10, text, color=GREEN if ok else SUNSET, bold=not ok)
        x += w + 12


def _numbered(card: _Card, items: list[str], color: str, top: float):
    y = top
    for n, item in enumerate(items, 1):
        if y > card.y1 - 8:
            break
        card.d.dot(card.x0 + 12, y, 24, color)
        card.d.text(card.x0 + 12, y, str(n), bold=True, color="#ffffff", align="center")
        card.d.text(card.x0 + 30, y, _fit(item, card.width - 30))
        y += 27


def _list_panel(card: _Card, panel: dict, row: dict):
    card.title(cs.fill(panel.get("title") or "", row))
    items = [t for t in (cs.fill_if_set(i, row) for i in panel.get("items") or []) if t]
    _numbered(card, items, TONES.get(panel.get("tone") or "indigo", theme.INDIGO), card.y0 + 4)


def _signs_panel(card: _Card, panel: dict, row: dict):
    d = card.d
    when = cs._as_date(row.get(panel.get("date"))) if panel.get("date") else None
    card.title(cs.fill(panel.get("title") or "", row), suffix=cs.day_short(when) if when else "")
    known = panel.get("sign_text") or {}
    listed = [s.strip() for s in str(row.get(panel.get("signs")) or "").split(",") if s.strip()]
    # A label may itself hold a comma ("pus in the eyes, skin or belly button"): rejoin
    # pieces until they name a known sign.
    signs, buf = [], ""
    for piece in listed:
        buf = f"{buf}, {piece}" if buf else piece
        if buf in known or not any(k.startswith(buf) for k in known):
            signs.append(buf)
            buf = ""
    if buf:
        signs.append(buf)
    y = card.y0 + 4
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    if badge:
        card.pill(card.x0, y, badge, SUNSET)
        y += 34
    from connect_labs.workflow.coach_charts.types import wrap

    rows = int((card.y1 - y) // 26) + 1  # lines the card has room for
    titles = [(known.get(sg) or {}).get("title") or sg[:1].upper() + sg[1:] for sg in signs]
    # Each sign on one line; a sign may take a second line while the rest still fit.
    lines = [[_fit(t, card.width - 28, bold=True)] for t in titles]
    if len(titles) > rows:
        lines = lines[: rows - 1]
    spare = rows - len(lines) - (1 if len(lines) < len(titles) else 0)
    for i, t in enumerate(titles[: len(lines)]):
        wrapped = wrap(t, size=TEXT, width=int((card.width - 28) / 1.08), max_lines=2)
        if spare > 0 and len(wrapped) == 2:
            lines[i] = wrapped
            spare -= 1
    for sign_lines in lines:
        d.dot(card.x0 + 10, y, 20, SUNSET)
        d.text(card.x0 + 10, y, "!", bold=True, color="#ffffff", align="center")
        for ln in sign_lines:
            d.text(card.x0 + 28, y, ln, bold=True)
            y += 26
        y += 2
    if len(lines) < len(titles):
        d.text(card.x0, y, _fit(f"and {len(titles) - len(lines)} more", card.width), color=theme.MUTED)


def _facts_panel(card: _Card, panel: dict, row: dict):
    card.title(cs.fill(panel.get("title") or "", row))
    y = card.y0 + 4
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    if badge:
        card.pill(card.x0, y, badge, GREEN)
        y += 34
    for n, line in enumerate(t for t in (cs.fill_if_set(x, row) for x in panel.get("lines") or []) if t):
        if y > card.y1 - 8:
            break
        card.d.text(card.x0, y, _fit(line, card.width), bold=n == 0, color=GREEN if n == 0 else theme.INK)
        y += 27


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


def _px(field: str) -> dict:
    return {"field": field, "type": "quantitative", "scale": None}


def _colour() -> dict:
    return {"field": "color", "type": "nominal", "scale": None}


def spec(meta: dict) -> dict:
    """One layered view, ``W`` x ``H``, drawing every row of ``case_text``."""
    xy = {"x": _px("x"), "y": _px("y")}
    rects = [
        {
            "transform": [{"filter": f"datum.kind == 'rect_{shape}'"}],
            "mark": {"type": "rect", **corners},
            "encoding": {**xy, "x2": _px("x2"), "y2": _px("y2"), "color": _colour(), "opacity": _px("opacity")},
        }
        for shape, corners in (
            ("card", {"cornerRadius": theme.RADIUS}),
            ("pill", {"cornerRadius": 15}),
            ("bar", {"cornerRadiusTopLeft": 6, "cornerRadiusTopRight": 6}),
            ("plain", {}),
        )
    ]
    texts = [
        {
            "transform": [{"filter": f"datum.kind == 'text_{align}_{weight}'"}],
            "mark": {
                "type": "text",
                "align": align,
                "baseline": "middle",
                **({"fontWeight": 700} if weight == "b" else {}),
            },
            "encoding": {**xy, "text": {"field": "text", "type": "nominal"}, "size": _px("size"), "color": _colour()},
        }
        for align in ("left", "center", "right")
        for weight in ("r", "b")
    ]
    layers = [
        *rects,
        {
            "transform": [{"filter": "datum.kind == 'area'"}],
            "mark": {"type": "area", "interpolate": "linear"},
            "encoding": {
                **xy,
                "y2": _px("y2"),
                # No `order` here: on an area it is the STACK order, and it splits the
                # band into one sliver per row. The rows go left to right already.
                "detail": {"field": "seg", "type": "nominal"},
                "color": _colour(),
                "opacity": _px("opacity"),
            },
        },
        {
            "transform": [{"filter": "datum.kind == 'line'"}],
            "mark": {"type": "line", "strokeCap": "round"},
            "encoding": {
                **xy,
                "detail": {"field": "seg", "type": "nominal"},
                "order": {"field": "o", "type": "quantitative"},
                "color": _colour(),
                "strokeWidth": _px("w"),
                "strokeDash": {
                    "field": "dash",
                    "type": "nominal",
                    "scale": {"domain": [False, True], "range": [[1, 0], [7, 6]]},
                    "legend": None,
                },
            },
        },
        {
            "transform": [{"filter": "datum.kind == 'ring'"}],
            "mark": {"type": "point", "filled": False, "opacity": 1},
            "encoding": {**xy, "size": _px("area"), "color": _colour(), "strokeWidth": _px("stroke")},
        },
        {
            "transform": [{"filter": "datum.kind == 'dot'"}],
            "mark": {"type": "point", "filled": True, "shape": "circle", "opacity": 1},
            "encoding": {**xy, "size": _px("area"), "color": _colour()},
        },
        {
            "transform": [{"filter": "datum.kind == 'tick'"}],
            "mark": {"type": "line", "strokeWidth": 3, "strokeCap": "round", "strokeJoin": "round"},
            "encoding": {
                **xy,
                "detail": {"field": "seg", "type": "nominal"},
                "order": {"field": "o", "type": "quantitative"},
                "color": _colour(),
            },
        },
        {
            "transform": [{"filter": "datum.kind == 'star'"}],
            "mark": {
                "type": "point",
                "filled": True,
                "shape": STAR,
                "opacity": 1,
                "stroke": "#ffffff",
                "strokeWidth": 2,
            },
            "encoding": {**xy, "size": _px("area"), "color": _colour()},
        },
        *texts,
    ]
    return {"data": {"name": "case_text"}, "width": W, "height": H, "layer": layers}
