"""The ``case_summary`` picture: one case at a glance, like the summary page of a patient
record. A title (the case state's), the case's name, a banner of facts about it, a rule,
and up to four panels in a 2x2 grid -- each a kind the registry chooses and binds to its
own columns and visit series (``semantic/case_states.PANEL_KINDS``), in the order the
registry lists them (top row first: the registry puts the problems there).

``series``  one visit series as a line: against a reference band from its first
            reading, a floor or target line, a ringed step;
``bars``    drawn as ``series`` (a line, like every series);
``visits``  the visits as a dated timeline up to the run date ("today"): the next
            visit's due window, and the late stretch after it when it is overdue;
``list``    a titled numbered list (a checklist, what to do, next steps);
``signs``   the labels a case column lists;
``facts``   a few lines worded from the case (milestones).

The look (Design D, approved 2026-10-11): a white canvas; panels with no card behind them,
separated only by hairlines; each panel headed by its title -- small, letter-spaced,
upper case, indigo -- and a STATUS PILL. All charts are one blue; a pill is the ONLY
place red or green appears. The series panels share one frame: the same dates at the
same x along the bottom. A pill's status is worked out from the panel's own config
(``status``), never from what the programme is about.

Everything is laid out here in PNG pixels of the 1200 x 900 picture (``S`` = 0.5 turns
them into the 600 x 450 CSS px Vega-Lite draws at scale 2), and drawn by one layered
Vega-Lite view over the ``case_text`` dataset (``spec``). Every word is the registry's,
every number the case's; ``case_points`` carries the series values each panel draws.
"""

from __future__ import annotations

import datetime as dt
import functools
import math
from typing import Any

from connect_labs.semantic import case_states as cs

#: PNG px -> CSS px (the picture is drawn at scale 2).
S = 0.5

#: The canvas, in PNG px: 1200 x 900, padded 40 top, 44 at the sides and 28 at the foot.
CANVAS_W, CANVAS_H = 1200, 900
PAD_TOP, PAD_SIDE, PAD_BOTTOM = 40, 44, 28
W = CANVAS_W - 2 * PAD_SIDE  # 1112
H = CANVAS_H - PAD_TOP - PAD_BOTTOM  # 832

# The palette of Design D.
INK = "#16006d"
ACCENT = "#3843d0"
MUTED = "#475569"
HAIRLINE = "#e2e8f0"
FAINT = "#94a3b8"
BAND = "#e3e8ff"
DUE = "#f1f5f9"
LATE = "#fdece8"
LATE_TEXT = "#b42318"
WHITE = "#ffffff"
PILL = {"bad": ("#fde2de", "#b42318"), "good": ("#e6f5e5", "#1b6b5f")}

# Type sizes (PNG px).
TITLE_SIZE, NAME_SIZE, BANNER_SIZE = 58, 32, 24
PANEL_TITLE_SIZE, PILL_SIZE = 20, 20
VALUE_SIZE, LAST_SIZE, AXIS_SIZE = 20, 22, 18
LETTER_SPACING = 2

# The header and the grid.
NAME_Y = TITLE_SIZE + 6
BANNER_Y = NAME_Y + round(NAME_SIZE * 1.17) + 6
GRID_TOP = BANNER_Y + round(BANNER_SIZE * 1.17) + 18
RULE_W = 2
COL_W = W / 2
ROW_H = (H - GRID_TOP - RULE_W) / 2
CELL_PAD_TOP, CELL_GUTTER = 16, 28
PILL_H = round(PILL_SIZE * 1.17) + 10
PILL_PAD = 16
#: A panel's plot (the design's SVG): its size and where it sits under the header.
PLOT_W, PLOT_H = 520, 250
PLOT_TOP = CELL_PAD_TOP + PILL_H + 6

# Inside a plot (plot coordinates, PNG px).
X_FIRST, X_LAST = 60, 420  # the first and last date of the shared frame
VISITS_X_LAST = 460  # the visits timeline runs a little further, to today
BASE_X0, BASE_X1 = 40, 440
BASELINE_Y = 210
DATE_Y = 234  # the middle of a date label (its baseline is at 240)
TARGET_LABEL_X = 450


@functools.lru_cache(maxsize=32)
def _font(size: float, semibold: bool):
    from PIL import ImageFont

    from connect_labs.workflow.coach_charts.render import FONTS_DIR

    name = "WorkSans-SemiBold.ttf" if semibold else "WorkSans-Regular.ttf"
    return ImageFont.truetype(str(FONTS_DIR / name), size)


def _width(text: str, size: float, weight: int = 400) -> float:
    """``text``'s width in PNG px in Work Sans (700 is drawn about 3% wider than 600)."""
    return _font(size, weight >= 600).getlength(str(text or "")) * (1.03 if weight >= 700 else 1.0)


def _fit(text: str, width: float, size: float, weight: int = 400) -> str:
    """``text`` cut to ``width`` with an ellipsis (never a smaller font)."""
    text = str(text or "")
    if _width(text, size, weight) <= width:
        return text
    while text and _width(text + "…", size, weight) > width:
        text = text[:-1]
    return text.rstrip(" ,;·") + "…"


WEIGHTS = {400: "r", 600: "s", 700: "b"}
#: The stroke widths a line may have (PNG px): a hairline, the target line and today's
#: tick, a series.
STROKES = (2, 3, 5)


class Draw:
    """Shapes and text in PNG px, as rows of the ``case_text`` dataset (in CSS px)."""

    def __init__(self):
        self.rows: list[dict] = []
        self._seg = 0

    def rect(self, x, y, x2, y2, color, *, shape="plain"):
        # shape: plain, or pill (corners fully round at the pill's height)
        self.rows.append({"kind": f"rect_{shape}", "x": x * S, "y": y * S, "x2": x2 * S, "y2": y2 * S, "color": color})

    def text(self, x, y, text, *, size, weight=400, color=INK, align="left"):
        """``text`` with its middle at ``y``."""
        if text:
            self.rows.append(
                {
                    "kind": f"text_{align}_{WEIGHTS[weight]}",
                    "x": x * S,
                    "y": y * S,
                    "text": str(text),
                    "size": size * S,
                    "color": color,
                }
            )

    def spaced(self, x, y, text, *, size, weight=700, color=ACCENT, spacing=LETTER_SPACING) -> float:
        """``text`` letter-spaced (Vega has no letter spacing: one row per letter);
        returns the x after it."""
        for ch in str(text):
            if not ch.isspace():
                self.text(x, y, ch, size=size, weight=weight, color=color)
            x += _width(ch, size, weight) + spacing
        return x

    def dot(self, x, y, radius, color, *, ring=False, stroke=3):
        self.rows.append(
            {
                "kind": "ring" if ring else "dot",
                "x": x * S,
                "y": y * S,
                "area": round(math.pi * (radius * S) ** 2, 1),
                "color": color,
                "stroke": stroke * S,
            }
        )

    def line(self, points, color, *, width=5, dashed=False):
        if width not in STROKES:
            raise ValueError(f"a line is {' or '.join(map(str, STROKES))} px wide")
        self._seg += 1
        for o, (x, y) in enumerate(points):
            self.rows.append(
                {
                    "kind": "line",
                    "seg": self._seg,
                    "o": o,
                    "x": x * S,
                    "y": y * S,
                    "color": color,
                    "w": width * S,
                    "dash": dashed,
                }
            )

    def area(self, points, color):
        """``points``: (x, y_low, y_high), left to right."""
        self._seg += 1
        for o, (x, y, y2) in enumerate(points):
            self.rows.append(
                {"kind": "area", "seg": self._seg, "o": o, "x": x * S, "y": y * S, "y2": y2 * S, "color": color}
            )


# ---------------------------------------------------------------------------
# Building the picture
# ---------------------------------------------------------------------------


def build(
    state: dict,
    row: dict,
    visits: list[dict],
    series: list[dict],
    case_name: str = "",
    as_of: Any = None,
) -> tuple[dict, dict]:
    """``(datasets, meta)`` for a ``case_summary`` picture of ``row`` as of ``as_of``
    (the run date: "today" on the visits timeline)."""
    pic = state.get("picture") or {}
    d = Draw()
    points: list[dict] = []

    title = cs.fill(pic.get("title") or state.get("label") or "", row)
    d.text(0, TITLE_SIZE / 2, _fit(title, W, TITLE_SIZE, 700), size=TITLE_SIZE, weight=700)
    if case_name:
        d.text(0, NAME_Y + NAME_SIZE * 0.58, _fit(case_name, W, NAME_SIZE, 600), size=NAME_SIZE, weight=600)
    _banner(d, [cs.fill_if_set(chip, row) for chip in pic.get("banner") or []])

    # The grid: a rule under the header, hairlines between the panels, no cards.
    d.rect(0, GRID_TOP, W, GRID_TOP + RULE_W, INK)
    top = GRID_TOP + RULE_W
    d.rect(COL_W - 1, top, COL_W, H, HAIRLINE)
    d.rect(0, top + ROW_H - 1, W, top + ROW_H, HAIRLINE)

    panels = [p for p in (pic.get("panels") or [])[:4] if isinstance(p, dict)]
    by_name = {s["name"]: s for s in series}
    frame = _shared_frame(panels, visits, by_name)
    today = _today(as_of, row, panels, visits)
    for i, panel in enumerate(panels):
        left = i % 2 == 0
        cell = _Cell(
            d,
            x=0 if left else COL_W + CELL_GUTTER,
            y=top + (0 if i < 2 else ROW_H),
            right_edge=(COL_W - CELL_GUTTER) if left else W,
        )
        kind = panel.get("kind")
        if kind in ("series", "bars"):
            points += _series_panel(cell, panel, row, visits, by_name, frame, i)
        elif kind == "visits":
            _visits_panel(cell, panel, row, visits, by_name, today)
        elif kind == "list":
            _list_panel(cell, panel, row)
        elif kind == "signs":
            _signs_panel(cell, panel, row)
        elif kind == "facts":
            _facts_panel(cell, panel, row)
    meta = {"kind": "case_summary", "case_name": case_name or None, "title": title}
    return {"case_text": d.rows, "case_points": points}, meta


def _banner(d: Draw, chips: list[str | None]):
    """The banner under the name: the case's facts in one line, `` · `` between them.
    A fact that does not fit is left out (the registry lists them most useful first)."""
    shown: list[str] = []
    for chip in chips:
        if chip and _width(" · ".join([*shown, chip]), BANNER_SIZE) <= W:
            shown.append(chip)
    d.text(0, BANNER_Y + BANNER_SIZE * 0.58, " · ".join(shown), size=BANNER_SIZE, color=MUTED)


class _Cell:
    """One panel of the grid: its header (title and pill) and its plot area."""

    def __init__(self, d: Draw, *, x: float, y: float, right_edge: float):
        self.d, self.x, self.y, self.x1 = d, x, y + 0, right_edge
        self.header_y = y + CELL_PAD_TOP + PILL_H / 2
        self.plot_x, self.plot_y = x, y + PLOT_TOP
        self.pill_w = 0.0

    @property
    def width(self) -> float:
        return self.x1 - self.x

    def header(self, title: str, status: tuple[str, str] | None = None):
        """The title (upper case, letter-spaced, indigo) on the left, and the status pill
        -- ``(tone, text)``, tone ``bad`` or ``good`` -- on the right."""
        title_room = self.width
        if status:
            tone, text = status
            text = _fit(text, self.width * 0.62, PILL_SIZE, 700)
            w = _width(text, PILL_SIZE, 700) + 2 * PILL_PAD
            bg, fg = PILL[tone]
            self.d.rect(self.x1 - w, self.header_y - PILL_H / 2, self.x1, self.header_y + PILL_H / 2, bg, shape="pill")
            self.d.text(self.x1 - w / 2, self.header_y, text, size=PILL_SIZE, weight=700, color=fg, align="center")
            title_room -= w + 12
        upper = str(title or "").upper()
        while upper and _spaced_width(upper) > title_room:
            upper = upper[:-1]
        if upper != str(title or "").upper():
            upper = upper.rstrip() + "…"
        self.d.spaced(self.x, self.header_y, upper, size=PANEL_TITLE_SIZE)

    def at(self, x: float, y: float) -> tuple[float, float]:
        """Plot coordinates -> picture coordinates."""
        return self.plot_x + x, self.plot_y + y


def _spaced_width(text: str) -> float:
    return sum(_width(ch, PANEL_TITLE_SIZE, 700) + LETTER_SPACING for ch in text)


# ---------------------------------------------------------------------------
# Series: one frame shared by every series panel
# ---------------------------------------------------------------------------


def _readings(visits: list[dict], name: str) -> list[tuple[dt.date, float]]:
    """A series' readings in date order, one per day (a day's last reading: the
    registration and the first visit are often the same day)."""
    by_day: dict = {}
    for v in visits:
        day, value = cs._as_date(v.get("visit_date")), cs._as_number(v.get(name))
        if day is not None and value is not None:
            by_day[day] = value
    return sorted(by_day.items())


def _shared_frame(panels: list[dict], visits: list[dict], by_name: dict) -> list[dt.date]:
    """The dates every series panel draws along its bottom: each day any of them has a
    reading. The same date is at the same x in every series panel."""
    days: set[dt.date] = set()
    for panel in panels:
        if panel.get("kind") in ("series", "bars"):
            days |= {day for day, _ in _readings(visits, _spec(panel, by_name)["name"])}
    return sorted(days)


def _spec(panel: dict, by_name: dict) -> dict:
    return by_name.get(panel.get("series")) or {"name": panel.get("series"), "label": panel.get("series") or ""}


def _frame_x(frame: list[dt.date], x_last: float = X_LAST):
    start = frame[0] if frame else None
    span = max((frame[-1] - frame[0]).days, 1) if frame else 1

    def X(day: dt.date) -> float:
        if start is None or len(frame) == 1:
            return (X_FIRST + x_last) / 2
        return X_FIRST + (x_last - X_FIRST) * (day - start).days / span

    return X


def _label(spec: dict, value: float, *, unit: bool) -> str:
    shown = cs.series_value(spec, value) or ""
    u = spec.get("unit") or ""
    if not unit or not u:
        return shown
    return f"{shown} {u}" if u.isalpha() else f"{shown}{u}"


def growth_rate(pts: list[tuple[dt.date, float]]) -> float | None:
    """The measured rate from the first reading to the last, per 1000 of the first
    reading per day (g/kg/day for a weight in grams) -- the units of a reference band."""
    if len(pts) < 2 or not pts[0][1]:
        return None
    days = (pts[-1][0] - pts[0][0]).days
    if days <= 0:
        return None
    return (pts[-1][1] - pts[0][1]) / pts[0][1] / days * 1000


def _rate_unit(spec: dict) -> str:
    u = spec.get("unit") or ""
    return f"{u}/k{u}/day" if u.isalpha() else "per 1000 a day"


def _status_text(panel: dict, tone: str, default: str, **words) -> str:
    text = str(((panel.get("status") or {}).get(tone)) or default)
    for k, v in words.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def series_status(panel: dict, pts: list[tuple[dt.date, float]], spec: dict, row: dict) -> tuple[str, str] | None:
    """A series panel's pill, ``(tone, text)``, from its config alone:

    * a ringed step (``highlight``) -- bad, its own label;
    * a reference band -- bad when the measured rate is below ``reference.low``
      ("Behind · {rate} g/kg/day"), else good ("On target");
    * a floor or target -- bad when the last reading is below it ("Below target"),
      else good ("On target");
    * none of these -- no pill.

    ``status: {bad, good}`` in the panel replaces either wording; ``{rate}`` and
    ``{unit}`` in it are filled."""
    hl = panel.get("highlight")
    if hl:
        label = cs.fill_if_set(hl.get("label"), row)
        if label:
            return ("bad", _status_text(panel, "bad", label))
    if not pts:
        return None
    ref, floor = panel.get("reference"), panel.get("floor")
    if ref:
        rate = growth_rate(pts)
        if rate is None:
            return None
        low = cs._as_number(ref.get("low"))
        shown = f"{rate:.1f}"
        if low is not None and rate < low:
            unit = _rate_unit(spec)
            return ("bad", _status_text(panel, "bad", f"Behind · {shown} {unit}", rate=shown, unit=unit))
        return ("good", _status_text(panel, "good", "On target", rate=shown, unit=_rate_unit(spec)))
    if floor is not None:
        value = cs._as_number(floor.get("value"))
        if value is None:
            return None
        if pts[-1][1] < value:
            return ("bad", _status_text(panel, "bad", "Below target"))
        return ("good", _status_text(panel, "good", "On target"))
    return None


def _thin(xs: list[tuple[float, float]], keep_last: bool = True) -> list[int]:
    """Indexes of ``(x, width)`` labels, centred at x, that can be drawn without
    overlapping, left to right -- always the last."""
    kept: list[int] = []
    for i, (x, w) in enumerate(xs):
        if kept:
            px, pw = xs[kept[-1]]
            if x - w / 2 < px + pw / 2 + 8:
                if keep_last and i == len(xs) - 1:
                    kept.pop()
                    while kept and x - w / 2 < xs[kept[-1]][0] + xs[kept[-1]][1] / 2 + 8:
                        kept.pop()
                else:
                    continue
        kept.append(i)
    return kept


def _dates(cell: _Cell, frame: list[dt.date], X):
    d = cell.d
    labels = [(X(day), _width(cs.day_short(day), AXIS_SIZE)) for day in frame]
    for i in _thin(labels):
        d.text(*cell.at(X(frame[i]), DATE_Y), cs.day_short(frame[i]), size=AXIS_SIZE, color=MUTED, align="center")


def _series_panel(
    cell: _Cell, panel: dict, row: dict, visits: list[dict], by_name: dict, frame: list[dt.date], idx: int
) -> list[dict]:
    d = cell.d
    spec = _spec(panel, by_name)
    pts = _readings(visits, spec["name"])
    title = cs.fill(panel.get("title") or spec.get("label") or "", row)
    cell.header(title, series_status(panel, pts, spec, row))
    d.line([cell.at(BASE_X0, BASELINE_Y), cell.at(BASE_X1, BASELINE_Y)], HAIRLINE, width=2)
    if not pts:
        d.text(*cell.at(X_FIRST, 120), "not recorded", size=AXIS_SIZE, color=MUTED)
        return []
    X = _frame_x(frame)
    ref, floor, hl = panel.get("reference"), panel.get("floor"), panel.get("highlight")
    floor_v = cs._as_number((floor or {}).get("value")) if floor else None

    # The vertical scale: from zero to above the line for a series with a floor (the
    # line and the readings in proportion), else the readings (and the band).
    values = [v for _, v in pts]
    band: list[tuple[float, float, float]] = []
    if ref:
        lo_r, hi_r = float(ref.get("low") or 0), float(ref.get("high") or 0)
        span = max((pts[-1][0] - pts[0][0]).days, 1)
        for t in range(0, span + 1):
            band.append((t, pts[0][1] * (1 + lo_r / 1000) ** t, pts[0][1] * (1 + hi_r / 1000) ** t))
        values += [b[2] for b in band]
    if floor_v is not None:
        lo, hi = 0.0, max(values + [floor_v]) or 1.0
        y_top, y_bottom = 50.0, 200.0
    else:
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        y_top, y_bottom = (12.0, 180.0) if ref else (60.0, 180.0)

    def Y(v: float) -> float:
        return y_bottom - (y_bottom - y_top) * (v - lo) / (hi - lo)

    if band:
        first_x, last_x = X(pts[0][0]), X(pts[-1][0])
        bx = lambda t: first_x + (last_x - first_x) * t / max(len(band) - 1, 1)  # noqa: E731
        d.area([(*cell.at(bx(t), Y(a)), cell.at(0, Y(b))[1]) for t, a, b in band], BAND)
        key = str(ref.get("label") or "")
        if key:
            # Two-thirds along, just above the band's upper edge.
            kx = first_x + (last_x - first_x) * 2 / 3
            t = round((len(band) - 1) * 2 / 3)
            d.text(
                *cell.at(kx, Y(band[t][2]) - 14),
                _fit(key, 300, AXIS_SIZE, 600),
                size=AXIS_SIZE,
                weight=600,
                color=ACCENT,
                align="right",
            )
    if floor_v is not None:
        fy = Y(floor_v)
        d.line([cell.at(BASE_X0, fy), cell.at(BASE_X1, fy)], MUTED, width=3, dashed=True)
        key = str(floor.get("label") or "")
        if key:
            d.text(
                *cell.at(TARGET_LABEL_X, fy), _fit(key, 70, AXIS_SIZE, 600), size=AXIS_SIZE, weight=600, color=MUTED
            )
        d.text(
            *cell.at(TARGET_LABEL_X, fy + 22),
            _label(spec, floor_v, unit=True),
            size=AXIS_SIZE,
            weight=600,
            color=MUTED,
        )

    flag_day = cs._as_date(row.get(hl.get("to"))) if hl else None
    flag_from = cs._as_date(row.get(hl.get("from"))) if hl else None
    xy = [(X(day), Y(v)) for day, v in pts]
    for n in range(len(xy) - 1):
        dashed = bool(flag_day) and (
            pts[n + 1][0] == flag_day
            or pts[n][0] == flag_day
            or (flag_from is not None and flag_from <= pts[n][0] and pts[n + 1][0] <= flag_day)
        )
        d.line([cell.at(*xy[n]), cell.at(*xy[n + 1])], ACCENT, width=5, dashed=dashed)
    for n, (x, y) in enumerate(xy):
        d.dot(*cell.at(x, y), 9 if n == len(xy) - 1 else 7, ACCENT)
        if flag_day and pts[n][0] == flag_day:
            d.dot(*cell.at(x, y), 17, ACCENT, ring=True)

    # Each value above its point (the last bold, with its unit); with a band above the
    # line, the last goes under its point instead.
    labels = []
    for n, (day, v) in enumerate(pts):
        last = n == len(pts) - 1
        text = _label(spec, v, unit=last)
        labels.append((xy[n][0], _width(text, LAST_SIZE if last else VALUE_SIZE, 700 if last else 600), text, last))
    for i in _thin([(x, w) for x, w, _, _ in labels]):
        x, _w, text, last = labels[i]
        y = xy[i][1]
        if last:
            ly = y + 30 if ref else y - 26
            d.text(*cell.at(x, ly), text, size=LAST_SIZE, weight=700, align="center")
        else:
            d.text(*cell.at(x, y - 22), text, size=VALUE_SIZE, weight=600, align="center")
    # A date of the frame this series has no reading on.
    have = {day for day, _ in pts}
    for day in frame:
        if day not in have:
            d.text(*cell.at(X(day), 116), "–", size=AXIS_SIZE, color=FAINT, align="center")
    _dates(cell, frame, X)
    return [{"panel": idx, "series": spec["name"], "date": day.isoformat(), "value": v} for day, v in pts]


# ---------------------------------------------------------------------------
# Visits: a dated timeline to today
# ---------------------------------------------------------------------------


def _today(as_of: Any, row: dict, panels: list[dict], visits: list[dict]) -> dt.date | None:
    """The run date. Without one: the end of the due window plus the days overdue the
    registry worked out, else the last visit."""
    day = cs._as_date(as_of) if as_of is not None else None
    if day:
        return day
    for p in panels:
        if p.get("kind") == "visits" and p.get("due_to") and p.get("overdue_days"):
            due_to = cs._as_date(row.get(p["due_to"]))
            over = cs._as_number(row.get(p["overdue_days"]))
            if due_to and over is not None and over > 0:
                return due_to + dt.timedelta(days=int(round(over)))
    dated = [cs._as_date(v.get("visit_date")) for v in visits]
    dated = [x for x in dated if x]
    return max(dated) if dated else None


def overdue_days(panel: dict, row: dict, today: dt.date | None) -> int:
    """Days past the due window's end: the registry's own column when it names one,
    else the run date less the window's end."""
    if panel.get("overdue_days"):
        n = cs._as_number(row.get(panel["overdue_days"]))
        if n is not None:
            return max(int(round(n)), 0)
    due_to = cs._as_date(row.get(panel.get("due_to"))) if panel.get("due_to") else None
    if due_to and today:
        return max((today - due_to).days, 0)
    return 0


def visits_status(panel: dict, row: dict, today: dt.date | None) -> tuple[str, str] | None:
    """The visits pill: bad when the run date is past the due window's end ("{n} days
    overdue"), good when a window is set and not passed ("On schedule"); ``status:
    {bad, good}`` replaces either (``{days}`` is filled)."""
    has_window = bool(panel.get("due_to") or panel.get("overdue_days"))
    if not has_window:
        return None
    days = overdue_days(panel, row, today)
    if days:
        default = f"{days} day{'s' if days != 1 else ''} overdue"
        return ("bad", _status_text(panel, "bad", default, days=days))
    return ("good", _status_text(panel, "good", "On schedule", days=0))


def _window_words(due_from: dt.date | None, due_to: dt.date | None) -> str:
    if due_from and due_to and due_from != due_to:
        if due_from.month == due_to.month:
            return f"due {due_from.day}–{cs.day_short(due_to)}"
        return f"due {cs.day_short(due_from)}–{cs.day_short(due_to)}"
    one = due_to or due_from
    return f"due {cs.day_short(one)}" if one else ""


def _visits_panel(cell: _Cell, panel: dict, row: dict, visits: list[dict], by_name: dict, today: dt.date | None):
    d = cell.d
    cell.header(cs.fill(panel.get("title") or "Visits", row), visits_status(panel, row, today))
    dated = sorted({day for day in (cs._as_date(v.get("visit_date")) for v in visits) if day})
    last_by_day = {cs._as_date(v.get("visit_date")): v for v in visits}
    due_from = cs._as_date(row.get(panel.get("due_from"))) if panel.get("due_from") else None
    due_to = cs._as_date(row.get(panel.get("due_to"))) if panel.get("due_to") else None
    d.line([cell.at(BASE_X0, BASELINE_Y), cell.at(470, BASELINE_Y)], HAIRLINE, width=2)
    if not dated:
        d.text(*cell.at(X_FIRST, 120), "no visits recorded", size=AXIS_SIZE, color=MUTED)
        return
    start = dated[0]
    end = max([dated[-1]] + [x for x in (due_from, due_to, today) if x])
    span = max((end - start).days, 1)

    def X(day: dt.date) -> float:
        return X_FIRST + (VISITS_X_LAST - X_FIRST) * (day - start).days / span

    overdue = overdue_days(panel, row, today)
    top_labels: list[tuple[float, float, str, str, int]] = []  # x, width, text, colour, weight
    if overdue and due_to and today and today > due_to:
        x0, x1 = X(due_to), X(today)
        d.rect(*cell.at(x0, 120), *cell.at(x1, BASELINE_Y), LATE)
        lw = _width("late visit", AXIS_SIZE)
        # Centred in the late stretch; a stretch too narrow for it has it end just left
        # of today's tick instead (over the light bands, above the visits' dates).
        lx = (x0 + x1) / 2 if x1 - x0 >= lw + 8 else max(x1 - 8 - lw / 2, lw / 2)
        d.text(*cell.at(lx, 166), "late visit", size=AXIS_SIZE, color=LATE_TEXT, align="center")
    if due_from or due_to:
        a, b = due_from or due_to, due_to or due_from
        x0, x1 = X(a), max(X(b), X(a) + 6)
        d.rect(*cell.at(x0, 120), *cell.at(x1, BASELINE_Y), DUE)
        words = _window_words(due_from, due_to)
        top_labels.append(((x0 + x1) / 2, _width(words, AXIS_SIZE), words, MUTED, 400))
    if today:
        tx = X(today)
        d.line([cell.at(tx, 120), cell.at(tx, 218)], INK, width=3)
        top_labels.append((tx, _width("today", AXIS_SIZE, 600), "today", INK, 600))
    # The due window's words move left of "today" rather than overlap it; they are left
    # out only when there is no room left of it at all.
    if len(top_labels) == 2:
        (dx, dw, *drest), (tx_, tw, *_trest) = top_labels
        t_left = min(max(tx_, tw / 2), PLOT_W - tw / 2) - tw / 2
        limit = t_left - 12 - dw / 2
        if dx > limit:
            top_labels = [(limit, dw, *drest), top_labels[1]] if limit >= dw / 2 else top_labels[1:]
    for x, w, text, color, weight in top_labels:
        x = min(max(x, w / 2), PLOT_W - w / 2)
        d.text(*cell.at(x, 100), text, size=AXIS_SIZE, weight=weight, color=color, align="center")
    for day in dated:
        d.dot(*cell.at(X(day), BASELINE_Y), 9, ACCENT)

    # The dates: each visit and today. A label that would overlap the one before it is
    # pushed right; one that cannot then clear today's is left out (today's never is).
    marks = [(X(day), cs.day_short(day), False) for day in dated if day != today]
    if today:
        marks.append((X(today), cs.day_short(today), True))
    marks.sort(key=lambda m: (m[0], m[2]))
    fixed = next((m for m in marks if m[2]), None)
    stop = fixed[0] - _width(fixed[1], AXIS_SIZE) / 2 if fixed else PLOT_W
    placed: list[tuple[float, float, str]] = []  # left, right, text
    for x, text, is_today in marks:
        w = _width(text, AXIS_SIZE)
        left = x - w / 2
        if not is_today:
            if placed and left < placed[-1][1] + 8:
                left = placed[-1][1] + 8
            if left + w > stop - 8:
                continue
        placed.append((left, left + w, text))
    for left, _right, text in placed:
        d.text(*cell.at(left, DATE_Y), text, size=AXIS_SIZE, color=MUTED)

    if not (due_from or due_to):
        _vitals(cell, panel, last_by_day.get(dated[-1]) or {}, by_name)


def _vitals(cell: _Cell, panel: dict, last: dict, by_name: dict):
    """The last visit's vitals in one line over the timeline (one outside the panel's
    limits in bold) -- as many as fit."""
    x = 0.0
    for v in panel.get("vitals") or []:
        spec = by_name.get(v.get("series")) or {}
        value = cs._as_number(last.get(v.get("series")))
        if value is None:
            continue
        text = f"{v.get('label')} {_label(spec, value, unit=True)}"
        ok = (v.get("low") is None or value >= v["low"]) and (v.get("high") is None or value <= v["high"])
        w = _width(text, AXIS_SIZE, 400 if ok else 700)
        if x + w > PLOT_W:
            break
        cell.d.text(*cell.at(x, 40), text, size=AXIS_SIZE, weight=400 if ok else 700, color=MUTED if ok else INK)
        x += w + 16


# ---------------------------------------------------------------------------
# Words: list, signs, facts
# ---------------------------------------------------------------------------

LINE = 40
WORDS_SIZE = 22


def _numbered(cell: _Cell, items: list[str], top: float):
    y = top
    for n, item in enumerate(items, 1):
        if y > PLOT_H - 10:
            break
        cell.d.dot(*cell.at(14, y), 14, ACCENT)
        cell.d.text(*cell.at(14, y), str(n), size=AXIS_SIZE, weight=700, color=WHITE, align="center")
        cell.d.text(*cell.at(40, y), _fit(item, PLOT_W - 40, WORDS_SIZE), size=WORDS_SIZE)
        y += LINE


def _list_panel(cell: _Cell, panel: dict, row: dict):
    cell.header(cs.fill(panel.get("title") or "", row))
    items = [t for t in (cs.fill_if_set(i, row) for i in panel.get("items") or []) if t]
    _numbered(cell, items, 24)


def _split_signs(panel: dict, row: dict) -> list[str]:
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
    return [(known.get(sg) or {}).get("title") or sg[:1].upper() + sg[1:] for sg in signs]


def _signs_panel(cell: _Cell, panel: dict, row: dict):
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    cell.header(cs.fill(panel.get("title") or "", row), ("bad", badge) if badge else None)
    y = 24
    when = cs._as_date(row.get(panel.get("date"))) if panel.get("date") else None
    if when:
        cell.d.text(*cell.at(0, y), f"recorded {cs.day_short(when)}", size=AXIS_SIZE, color=MUTED)
        y += 36
    titles = _split_signs(panel, row)
    rows = int((PLOT_H - 10 - y) // LINE) + 1
    shown = titles if len(titles) <= rows else titles[: rows - 1]
    for t in shown:
        cell.d.dot(*cell.at(8, y), 6, ACCENT)
        cell.d.text(*cell.at(24, y), _fit(t, PLOT_W - 24, WORDS_SIZE, 600), size=WORDS_SIZE, weight=600)
        y += LINE
    if len(shown) < len(titles):
        cell.d.text(*cell.at(24, y), f"and {len(titles) - len(shown)} more", size=AXIS_SIZE, color=MUTED)


def _facts_panel(cell: _Cell, panel: dict, row: dict):
    badge = cs.fill_if_set(panel.get("badge"), row) if panel.get("badge") else None
    cell.header(cs.fill(panel.get("title") or "", row), ("good", badge) if badge else None)
    y = 24
    for n, line in enumerate(t for t in (cs.fill_if_set(x, row) for x in panel.get("lines") or []) if t):
        if y > PLOT_H - 10:
            break
        weight = 700 if n == 0 else 400
        cell.d.text(*cell.at(0, y), _fit(line, PLOT_W, WORDS_SIZE, weight), size=WORDS_SIZE, weight=weight)
        y += LINE


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------


def _px(field: str) -> dict:
    return {"field": field, "type": "quantitative", "scale": None}


def _colour() -> dict:
    return {"field": "color", "type": "nominal", "scale": None}


#: CSS px: the picture's frame, and its padding (the theme's is for the other charts).
SPEC_W, SPEC_H = W * S, H * S
SPEC_PADDING = {"top": PAD_TOP * S, "left": PAD_SIDE * S, "right": PAD_SIDE * S, "bottom": PAD_BOTTOM * S}


def spec(meta: dict) -> dict:
    """One layered view, 600 x 450 CSS px with its padding, drawing every row of
    ``case_text``."""
    xy = {"x": _px("x"), "y": _px("y")}
    rects = [
        {
            "transform": [{"filter": f"datum.kind == 'rect_{shape}'"}],
            "mark": {"type": "rect", **corners},
            "encoding": {**xy, "x2": _px("x2"), "y2": _px("y2"), "color": _colour()},
        }
        for shape, corners in (("pill", {"cornerRadius": PILL_H * S / 2}), ("plain", {"cornerRadius": 0}))
    ]
    texts = [
        {
            "transform": [{"filter": f"datum.kind == 'text_{align}_{code}'"}],
            "mark": {"type": "text", "align": align, "baseline": "middle", "fontWeight": weight},
            "encoding": {**xy, "text": {"field": "text", "type": "nominal"}, "size": _px("size"), "color": _colour()},
        }
        for align in ("left", "center", "right")
        for weight, code in WEIGHTS.items()
    ]
    layers = [
        {
            "transform": [{"filter": "datum.kind == 'area'"}],
            "mark": {"type": "area", "interpolate": "linear", "opacity": 1},
            "encoding": {
                **xy,
                "y2": _px("y2"),
                # No `order` here: on an area it is the STACK order, and it splits the
                # band into one sliver per row. The rows go left to right already.
                "detail": {"field": "seg", "type": "nominal"},
                "color": _colour(),
            },
        },
        *rects,
        # One layer per stroke: a line mark takes its width and dash from the mark, not
        # from a field (an encoded strokeWidth is ignored on a line).
        *(
            {
                "transform": [
                    {"filter": f"datum.kind == 'line' && datum.w == {w * S} && datum.dash == {str(dash).lower()}"}
                ],
                "mark": {
                    "type": "line",
                    "strokeCap": "butt" if dash else "round",
                    "strokeJoin": "round",
                    "strokeWidth": w * S,
                    **({"strokeDash": [10 * S, 6 * S]} if dash else {}),
                },
                "encoding": {
                    **xy,
                    "detail": {"field": "seg", "type": "nominal"},
                    "order": {"field": "o", "type": "quantitative"},
                    "color": _colour(),
                },
            }
            for w in STROKES
            for dash in (False, True)
        ),
        {
            "transform": [{"filter": "datum.kind == 'ring'"}],
            "mark": {"type": "point", "filled": False, "opacity": 1, "strokeWidth": 3 * S},
            "encoding": {**xy, "size": _px("area"), "color": _colour()},
        },
        {
            "transform": [{"filter": "datum.kind == 'dot'"}],
            "mark": {"type": "point", "filled": True, "shape": "circle", "opacity": 1},
            "encoding": {**xy, "size": _px("area"), "color": _colour()},
        },
        *texts,
    ]
    return {
        "data": {"name": "case_text"},
        "width": SPEC_W,
        "height": SPEC_H,
        "padding": SPEC_PADDING,
        "layer": layers,
    }
