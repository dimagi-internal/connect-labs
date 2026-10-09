"""The named chart types: each a function from its params (and the worker's first name)
to a Vega-Lite spec that reads Labs' named datasets (``datasets.py``) and holds no data
of its own -- except each topic's label, wrapped to the width of its column.

A type is how most charts are asked for -- ``{"type": "topic_bars"}`` -- and how the
look improves over time: change a type here and every chart of that type changes. A
chart no type fits is a custom spec (``custom.py``), drawn in the same theme.

Every type fills the landscape frame (``theme.WIDTH`` x ``theme.HEIGHT``, 3:2) that
Connect's messenger shows at full bubble width (connect-labs#2413): topics sit side by
side in columns rather than stacked down a portrait card.

Sizes are CSS pixels (``theme.py``): the PNG doubles them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from connect_labs.workflow.coach_charts import theme

INNER = theme.WIDTH - 2 * theme.PADDING
#: The gap between side-by-side columns.
GAP = 24

# topic_bars: today's card, re-expressed. Per topic: the label (wrapped to at most three
# lines), the figure in bold under it, and a bar of numerator over denominator in the
# band's colour. Topics sit in two columns; from five topics on, each is one line
# (label and figure) over a thinner bar.
_LABEL_LINE = 28
_BAR_HEIGHT = 22
_MAX_LABEL_LINES = 3
_COMPACT_BAR = 16
_COMPACT_GAP = 14
#: Block sizes by layout: one topic alone has the frame to itself, two share one row,
#: three or four fill two rows. Label size / line height / max lines, figure size / line
#: height / weight, and the bar's gap and thickness. A figure too wide for its column
#: is drawn smaller, never under the theme's label size.
_SIZES = {
    "one": {"label": 28, "line": 36, "lines": 2, "figure": 56, "figure_line": 72, "weight": 700, "gap": 12, "bar": 40},
    "row": {"label": 24, "line": 31, "lines": 3, "figure": 34, "figure_line": 46, "weight": 700, "gap": 12, "bar": 28},
    "grid": {"label": 22, "line": 28, "lines": 2, "figure": 22, "figure_line": 30, "weight": 600, "gap": 8, "bar": 18},
}

#: How a band maps to a colour, for every type: unknown bands are the neutral grey.
BAND_DOMAIN = ["red", "yellow", "amber", "green", "other"]
BAND_RANGE = [theme.BAND_COLOURS[b] for b in BAND_DOMAIN[:-1]] + [theme.NEUTRAL]


@lru_cache(maxsize=4)
def _font(size: int):
    from PIL import ImageFont

    from connect_labs.workflow.coach_charts.render import FONTS_DIR

    return ImageFont.truetype(str(FONTS_DIR / "WorkSans-Regular.ttf"), size)


def wrap(text: str, size: int = theme.LABEL_SIZE, width: int = INNER, max_lines: int = _MAX_LABEL_LINES) -> list[str]:
    """``text`` broken into lines no wider than ``width`` CSS px in the theme font; a
    word too long for a line is broken by character, and anything past ``max_lines``
    ends in an ellipsis."""
    font = _font(size)
    lines: list[str] = []
    current = ""
    for word in str(text or "").split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while font.getlength(word) > width:
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines or [""]


def column_width(columns: int) -> int:
    """The width of each of ``columns`` side-by-side columns, ``GAP`` apart."""
    return int((INNER - (columns - 1) * GAP) / max(columns, 1))


def _text_width(text: str, size: int = theme.LABEL_SIZE, bold: bool = False) -> float:
    """``text``'s width in the theme font; bold is drawn about 8% wider than regular."""
    return _font(size).getlength(str(text or "")) * (1.08 if bold else 1.0)


def band_colour(band: Any) -> str:
    return theme.BAND_COLOURS.get(str(band or "").lower(), theme.NEUTRAL)


def _band_key(band: Any) -> str:
    """The band as the colour scale knows it: ``red``/``yellow``/``amber``/``green``, else ``other``."""
    key = str(band or "").lower()
    return key if key in theme.BAND_COLOURS else "other"


def _title(text: str, first_name: str | None) -> dict:
    title: dict[str, Any] = {"text": text}
    if first_name:
        title["subtitle"] = first_name
    return title


def _band_scale() -> dict:
    return {"domain": BAND_DOMAIN, "range": BAND_RANGE}


# ---------------------------------------------------------------------------
# topic_bars
# ---------------------------------------------------------------------------


def topic_bars(rows: list[dict], *, first_name: str | None = None, title: str = "Your figures") -> dict:
    """Today's card as a Vega-Lite spec over the ``worker_topics`` dataset, laid out
    landscape: one topic fills the frame, more sit in two columns (``rows`` are that
    dataset's rows, needed only for the layout -- each block's height follows its
    wrapped label). The body is stretched to fill the frame under the title."""
    n = len(rows)
    columns = 1 if n <= 1 else 2
    width = column_width(columns)
    grid_rows = -(-n // columns) if n else 1
    compact = grid_rows >= 3
    sizes = _SIZES["one" if n == 1 else ("row" if grid_rows == 1 else "grid")]
    label_size, label_line = (theme.LABEL_SIZE, _LABEL_LINE) if compact else (sizes["label"], sizes["line"])
    blocks = []
    for i, row in enumerate(rows):
        figure = str(row.get("figure_text") or "")
        has_bar = row.get("share") is not None
        if compact:
            room = width - (_text_width(figure, bold=True) + 12 if figure else 0)
            lines = wrap(row.get("label") or "", width=int(room), max_lines=1)
            bar_y = _LABEL_LINE + 6
            height = bar_y + _COMPACT_BAR if has_bar else _LABEL_LINE
            bar_h, figure_layer = _COMPACT_BAR, {
                "mark": {"type": "text", "align": "right", "baseline": "top", "fontWeight": 600},
                "encoding": {
                    "text": {"field": "figure_text", "type": "nominal"},
                    "x": {"value": width},
                    "y": {"value": 0},
                },
            }
        else:
            lines = wrap(row.get("label") or "", size=label_size, width=width, max_lines=sizes["lines"])
            figure_y = len(lines) * label_line
            bar_y = figure_y + sizes["figure_line"] + sizes["gap"]
            bar_h = sizes["bar"]
            height = bar_y + bar_h if has_bar else figure_y + sizes["figure_line"]
            fits = width / max(_text_width(figure, size=sizes["figure"], bold=True), 1)
            figure_size = max(theme.LABEL_SIZE, min(sizes["figure"], int(sizes["figure"] * fits)))
            figure_layer = {
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "top",
                    "fontWeight": sizes["weight"],
                    "fontSize": figure_size,
                },
                "encoding": {
                    "text": {"field": "figure_text", "type": "nominal"},
                    "x": {"value": 0},
                    "y": {"value": figure_y + 4},
                },
            }
        layers: list[dict] = [
            {
                "mark": {
                    "type": "text",
                    "align": "left",
                    "baseline": "top",
                    "lineHeight": label_line,
                    "fontSize": label_size,
                },
                "encoding": {"text": {"value": lines}, "x": {"value": 0}, "y": {"value": 0}},
            },
            figure_layer,
        ]
        if has_bar:
            layers += [
                {
                    "mark": {"type": "rect", "color": theme.RULE, "cornerRadius": bar_h // 2},
                    "encoding": {
                        "x": {"value": 0},
                        "x2": {"value": width},
                        "y": {"value": bar_y},
                        "y2": {"value": bar_y + bar_h},
                    },
                },
                {
                    "transform": [{"filter": "datum.bar > 0"}],
                    "mark": {"type": "rect", "cornerRadius": bar_h // 2},
                    "encoding": {
                        "x": {
                            "datum": 0,
                            "type": "quantitative",
                            "scale": {"domain": [0, 1], "range": [0, width]},
                            "axis": None,
                        },
                        "x2": {"field": "bar", "type": "quantitative"},
                        "y": {"value": bar_y},
                        "y2": {"value": bar_y + bar_h},
                        "color": {"field": "band_key", "type": "nominal", "scale": _band_scale(), "legend": None},
                    },
                },
            ]
        blocks.append({"height": height, "transform": [{"filter": f"datum.i == {i}"}], "layer": layers})
    # Every block in a grid row is as tall as the row's tallest; the last row takes up
    # what is left of the frame.
    gap = _COMPACT_GAP if compact else 18
    heights = (
        [max(b["height"] for b in blocks[r * columns : (r + 1) * columns]) for r in range(grid_rows)] if blocks else []
    )
    spare = theme.body_height(bool(first_name)) - 4 - sum(heights) - gap * (len(heights) - 1)
    if heights and spare > 0:
        heights[-1] += spare
    for k, b in enumerate(blocks):
        b["height"] = heights[k // columns]
    return {
        "title": _title(title, first_name),
        "data": {"name": "worker_topics"},
        "columns": columns,
        "spacing": {"row": gap, "column": GAP},
        "concat": [{**b, "width": width} for b in blocks],
    }


def topic_rows(topics: list[dict]) -> list[dict]:
    """``worker_topics`` rows for the bars from topic dicts as a picture payload holds
    them (``label``, ``band`` and ``numerator``/``denominator``/``pct``, or ``figure``):
    what each block shows, plus its wrapped label and bar share."""
    rows = []
    for i, t in enumerate(topics):
        row: dict[str, Any] = {
            "i": i,
            "key": t.get("key"),
            "label": str(t.get("label") or t.get("key") or ""),
            "band": t.get("band"),
            "band_key": _band_key(t.get("band")),
        }
        row["label_lines"] = wrap(row["label"])
        if "numerator" in t and "denominator" in t:
            num, den = int(t["numerator"]), int(t["denominator"])
            row.update(numerator=num, denominator=den)
            pct = t.get("pct")
            if pct is None and den:
                pct = round(100 * num / den)
            if pct is not None:
                row["pct"] = int(round(pct))
            share = min(max(num / den, 0.0), 1.0) if den else 0.0
            row["share"] = share
            # A sliver narrower than the bar is tall is drawn as a full round cap.
            row["bar"] = max(share, _BAR_HEIGHT / INNER) if share > 0 else 0
            row["figure_text"] = f"{num} of {den}" + (f" · {row['pct']}%" if "pct" in row else "")
        else:
            row["figure_text"] = str(t.get("figure") or "")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# peer_comparison and trend
# ---------------------------------------------------------------------------

_ROW = 40  # the tallest bar row (CSS px)
_MIN_ROW = 25  # the shortest: a 22 px label still clears its neighbours
_WHO_WIDTH = 96  # room the row labels ("You", "Peer A") take, left of the bars
_VALUE_ROOM = 64  # room right of the longest bar for its value ("100%")
_COLUMN_GAP = 16  # between peer_comparison's topic columns
_TREND_HEIGHT = 200
_PEER_LABEL_ROOM = 112  # fits the widest end label, "Peer A, B"
_END_LINE = 22  # how far apart stacked end labels are
_AXIS_ROOM = 54  # a trend's week labels under its plot


def _value_format(unit: str) -> str:
    return ".0%" if unit == "%" else ",.3~r"


def _headings(rows: list[dict], width: int, max_lines: int = 3) -> tuple[list[list[str]], int]:
    """Each topic's label wrapped to ``width``, and the height every heading takes (the
    tallest's), so the plots under them line up."""
    lines = [wrap(r.get("label") or "", width=width, max_lines=max_lines) for r in rows]
    return lines, max([len(ls) for ls in lines] or [1]) * _LABEL_LINE + 10


def _heading(i: int, lines: list[str], width: int, height: int) -> dict:
    return {
        "data": {"name": "worker_topics"},
        "transform": [{"filter": f"datum.i == {i}"}],
        "width": width,
        "height": height,
        "mark": {"type": "text", "align": "left", "baseline": "top", "lineHeight": _LABEL_LINE},
        "encoding": {"text": {"value": lines}, "x": {"value": 0}, "y": {"value": 0}},
    }


def peer_comparison(datasets: dict, *, first_name: str | None = None, title: str = "How you compare") -> dict:
    """Per topic, the worker's figure beside each anonymous peer's: one bar per person,
    the worker first and in the band's colour, every peer in the neutral grey and
    labelled by letter, the value at the end of each bar. Topics sit side by side, one
    column each, with the people named once down the left."""
    mine = datasets.get("worker_topics") or []
    others = datasets.get("peers") or []
    n = max(len(mine), 1)
    width = int((INNER - _WHO_WIDTH - n * _COLUMN_GAP) / n)
    who = ["You"] + sorted({p["who"] for p in others}, key=lambda w: (len(w), w))
    headings, heading_height = _headings(mine, width, max_lines=3 if n == 1 else 2)
    body = theme.body_height(bool(first_name)) - heading_height - 6
    row_h = max(_MIN_ROW, min(_ROW, body // len(who)))
    bar_h = min(24, row_h - 8)
    y = {"field": "who", "type": "nominal", "sort": who, "scale": {"domain": who}, "axis": None}
    names = {
        "spacing": 6,
        "vconcat": [
            {"width": _WHO_WIDTH, "height": heading_height, "mark": {"type": "text"}, "data": {"values": []}},
            {
                "data": {"values": [{"who": w} for w in who]},
                "width": _WHO_WIDTH,
                "height": row_h * len(who),
                "layer": [
                    {
                        "transform": [{"filter": "datum.who == 'You'"}],
                        "mark": {"type": "text", "align": "left", "baseline": "middle", "fontWeight": 600},
                        "encoding": {"x": {"value": 0}, "y": y, "text": {"field": "who", "type": "nominal"}},
                    },
                    {
                        "transform": [{"filter": "datum.who != 'You'"}],
                        "mark": {"type": "text", "align": "left", "baseline": "middle"},
                        "encoding": {"x": {"value": 0}, "y": y, "text": {"field": "who", "type": "nominal"}},
                    },
                ],
            },
        ],
    }
    columns = [names]
    for row, lines in zip(mine, headings):
        i, unit = row["i"], row.get("unit") or ""
        rate = unit == "%"  # a rate is a fraction: its scale is 0..1
        scale: dict = {"range": [0, width - _VALUE_ROOM]}
        scale.update({"domain": [0, 1]} if rate else {"zero": True})
        x = {"field": "value", "type": "quantitative", "axis": None, "scale": scale}
        bars = {
            "layer": [
                {
                    "data": {"name": "worker_topics"},
                    "transform": [{"filter": f"datum.i == {i} && isValid(datum.value)"}],
                    "layer": [
                        {
                            "mark": {"type": "bar", "cornerRadius": 6, "height": bar_h},
                            "encoding": {
                                "x": x,
                                "y": y,
                                # The worker's own bar: the band's colour, or the series colour
                                # when the indicator has no bands -- never the peers' grey.
                                "color": {
                                    "field": "band_key",
                                    "type": "nominal",
                                    "scale": {"domain": BAND_DOMAIN, "range": BAND_RANGE[:-1] + [theme.INDIGO]},
                                    "legend": None,
                                },
                            },
                        },
                        {
                            "mark": {"type": "text", "align": "left", "dx": 8, "fontWeight": 600},
                            "encoding": {
                                "x": x,
                                "y": y,
                                "text": {"field": "value", "type": "quantitative", "format": _value_format(unit)},
                            },
                        },
                    ],
                },
                {
                    "data": {"name": "peers"},
                    "transform": [{"filter": f"datum.i == {i}"}],
                    "layer": [
                        {
                            "mark": {"type": "bar", "cornerRadius": 6, "height": bar_h, "color": theme.NEUTRAL},
                            "encoding": {"x": x, "y": y},
                        },
                        {
                            "mark": {"type": "text", "align": "left", "dx": 8, "color": theme.MUTED},
                            "encoding": {
                                "x": x,
                                "y": y,
                                "text": {"field": "value", "type": "quantitative", "format": _value_format(unit)},
                            },
                        },
                    ],
                },
            ],
            "width": width,
            "height": row_h * len(who),
        }
        columns.append({"spacing": 6, "vconcat": [_heading(i, lines, width, heading_height), bars]})
    return {"title": _title(title, first_name), "spacing": _COLUMN_GAP, "hconcat": columns}


def _nice_top(value: float) -> float:
    """A round top for a non-rate axis, a little above the largest value."""
    if value <= 0:
        return 1.0
    import math

    step = 10 ** math.floor(math.log10(value * 1.1))
    return math.ceil(value * 1.1 / step) * step


def end_labels(points: list[dict], *, top: float, height: int = _TREND_HEIGHT, line: int = _END_LINE) -> list[dict]:
    """Where each line's end label goes, in data units on a 0..``top`` axis: ``[{week,
    text, y, you}]``.

    Peers whose lines end within half a line of each other share one label ("Peer A,
    B"); then labels are spread so no two are closer than ``line`` px, and all sit at
    least half a line inside the plot. The worker's "You" is placed with the rest (it
    is drawn bold, in indigo)."""
    if not points:
        return []
    last_t = max(p["t"] for p in points)
    week = next(p["week"] for p in points if p["t"] == last_t)
    ends: dict[str, float] = {}
    for p in sorted(points, key=lambda p: p["t"]):
        ends[p["who"]] = p["value"]  # each line's last value
    gap = top * line / height
    peers = sorted(((v, w) for w, v in ends.items() if w != "You"), key=lambda vw: (vw[0], len(vw[1]), vw[1]))
    groups: list[list] = []
    for value, who in peers:
        if groups and value - groups[-1][0][0] < gap / 2:
            groups[-1].append((value, who))
        else:
            groups.append([(value, who)])
    labels = [
        {"text": _peer_group_text([w for _, w in g]), "want": sum(v for v, _ in g) / len(g), "you": False}
        for g in groups
    ]
    if "You" in ends:
        labels.append({"text": "You", "want": ends["You"], "you": True})
    lo, hi = gap / 2, top - gap / 2
    if len(labels) * gap > hi - lo + gap:
        # More labels than the plot holds: every peer under one label.
        labels = [{"text": "Peers", "want": min(v for v, _ in peers), "you": False}] + [
            lab for lab in labels if lab["you"]
        ]
    labels.sort(key=lambda lab: (lab["want"], lab["you"]))
    ys: list[float] = []
    for lab in labels:  # upward: each at least a line above the one below
        ys.append(max(lab["want"], lo, (ys[-1] + gap) if ys else lo))
    for n in range(len(ys) - 1, -1, -1):  # downward: back inside the top
        ys[n] = min(ys[n], hi if n == len(ys) - 1 else ys[n + 1] - gap)
    return [{"week": week, "text": lab["text"], "y": y, "you": lab["you"]} for lab, y in zip(labels, ys)]


def _first_last_weeks(points: list[dict]) -> list:
    """The first and last weeks of ``points`` (ISO dates) as axis values: Vega-Lite
    datetimes in UTC, as Vega parses an ISO date (their labels are formatted in UTC too)."""
    weeks = sorted({str(p["week"])[:10] for p in points})
    out = []
    for w in weeks[:1] + weeks[-1:] if len(weeks) > 1 else weeks:
        y, m, d = (int(part) for part in w.split("-"))
        out.append({"year": y, "month": m, "date": d, "utc": True})
    return out


def _peer_group_text(names: list[str]) -> str:
    """``Peer A``; ``Peer A, B`` for two peers that share an end; ``3 peers`` for more
    (so the label fits right of the plot)."""
    if len(names) > 2:
        return f"{len(names)} peers"
    letters = sorted((n.removeprefix("Peer ") for n in names), key=lambda x: (len(x), x))
    return "Peer " + ", ".join(letters)


def trend(datasets: dict, *, first_name: str | None = None, title: str = "Week by week") -> dict:
    """Per topic, the worker's weekly line (one point per saved run) and, when the
    ``history`` dataset carries them, each anonymous peer's own thinner grey line,
    labelled by letter at its last point. Topics sit side by side, one plot each."""
    mine = datasets.get("worker_topics") or []
    n = max(len(mine), 1)
    width = column_width(n)
    # Topics side by side leave no margin for end labels: there, "You" sits over the
    # worker's last point and the peers' grey lines go unlabelled (the caption says
    # what they are).
    narrow = n > 1
    label_room = 8 if narrow else _PEER_LABEL_ROOM
    headings, heading_height = _headings(mine, width, max_lines=2)
    height = theme.body_height(bool(first_name)) - heading_height - 6 - _AXIS_ROOM
    blocks = []
    for row, lines in zip(mine, headings):
        i, unit = row["i"], row.get("unit") or ""
        points = [h for h in datasets.get("history") or [] if h.get("i") == i]
        top = 1.0 if unit == "%" else _nice_top(max([h["value"] for h in points] or [0]))
        ends = [] if narrow else end_labels(points, top=top, height=height)
        y = {
            "field": "value",
            "type": "quantitative",
            "title": None,
            # Labels sit inside the plot, on their gridlines: an axis outside it would push
            # the chart past the theme's width.
            "axis": {
                "format": _value_format(unit),
                "tickCount": 2 if narrow else 4,
                "grid": True,
                "labelAlign": "left",
                "labelBaseline": "bottom",
                "labelPadding": -2,
                "labelOffset": -4,
            },
            "scale": {"domain": [0, top], "nice": False},
        }
        x = {
            "field": "week",
            "type": "temporal",
            "title": None,
            # Room right of the last week for the end labels ("Peer A").
            "scale": {"range": [8, width - label_room]},  # 8: a first point is not clipped
            "axis": (
                {
                    "format": "%-d %b",
                    "labelOverlap": True,
                    "labelAlign": "left",
                    "tickCount": 4,
                    "grid": False,
                }
                if not narrow
                # The first and last weeks only, flush with the plot's ends.
                else {
                    "labelExpr": "utcFormat(datum.value, '%-d %b')",
                    "values": _first_last_weeks(points),
                    "labelFlush": True,
                    "labelOverlap": False,
                    "grid": False,
                }
            ),
        }
        you_label = {
            # Narrow: "You" over the worker's last point.
            "transform": [{"filter": "datum.who == 'You' && datum.t == datum.last_t"}],
            "mark": {
                "type": "text",
                "align": "right",
                "baseline": "bottom",
                "dy": -10,
                "fontWeight": 600,
                "color": theme.INDIGO,
            },
            "encoding": {"x": x, "y": y, "text": {"value": "You"}},
        }
        lines_view = {
            "data": {"name": "history"},
            "transform": [
                {"filter": f"datum.i == {i}"},
                {"joinaggregate": [{"op": "max", "field": "t", "as": "last_t"}], "groupby": ["who"]},
            ],
            "width": width,
            "height": height,
            "layer": [
                {
                    "transform": [{"filter": "datum.who != 'You'"}],
                    "mark": {"type": "line", "color": theme.NEUTRAL, "strokeWidth": 2},
                    "encoding": {"x": x, "y": y, "detail": {"field": "who", "type": "nominal"}},
                },
                {
                    # The end labels, placed by Labs (``end_labels``): peers ending together
                    # share one label ("Peer A, B"), and no two labels are closer than a
                    # line, all inside the plot.
                    "data": {"values": ends},
                    "transform": [{"filter": "!datum.you"}],
                    "mark": {"type": "text", "align": "left", "baseline": "middle", "dx": 6, "color": theme.MUTED},
                    "encoding": {
                        "x": x,
                        "y": {**y, "field": "y"},
                        "text": {"field": "text", "type": "nominal"},
                    },
                },
                {
                    "data": {"values": ends},
                    "transform": [{"filter": "datum.you"}],
                    "mark": {
                        "type": "text",
                        "align": "left",
                        "baseline": "middle",
                        "dx": 10,
                        "fontWeight": 600,
                        "color": theme.INDIGO,
                    },
                    "encoding": {"x": x, "y": {**y, "field": "y"}, "text": {"field": "text", "type": "nominal"}},
                },
                {
                    "transform": [{"filter": "datum.who == 'You'"}],
                    "mark": {
                        "type": "line",
                        "color": theme.INDIGO,
                        "strokeWidth": 4,
                        "point": {"color": theme.INDIGO, "size": 90},
                    },
                    "encoding": {"x": x, "y": y},
                },
            ],
        }
        if narrow:
            lines_view["layer"].append(you_label)
        blocks.append({"spacing": 6, "vconcat": [_heading(i, lines, width, heading_height), lines_view]})
    return {"title": _title(title, first_name), "spacing": GAP, "hconcat": blocks}


@dataclass(frozen=True)
class ChartType:
    name: str
    description: str
    params: dict  # JSON schema of the params
    datasets: tuple[str, ...]  # the named datasets it reads
    build: Callable[..., dict]


_TOPICS = {
    "type": "array",
    "minItems": 1,
    "items": {"type": "string", "minLength": 1, "maxLength": 100},
    "description": (
        "Indicator keys to show, in order (workflow_run_context -> indicators). Default: the briefing's topics."
    ),
}
_PEERS = {
    "type": "integer",
    "minimum": 1,
    "maximum": 12,
    "description": "At most this many anonymous peers (default 6).",
}
_WEEKS = {
    "type": "integer",
    "minimum": 2,
    "maximum": 26,
    "description": "How many weeks back, ending with this run (default 8).",
}

TYPES: dict[str, ChartType] = {
    "topic_bars": ChartType(
        name="topic_bars",
        description=(
            "The worker's own figures: per topic its label, the figure ('31 of 73 · 42%') and a bar "
            "in the band's colour. The default."
        ),
        params={"type": "object", "properties": {"topics": {**_TOPICS, "maxItems": 8}}, "additionalProperties": False},
        datasets=("worker_topics",),
        build=lambda ds, first_name, params: topic_bars(ds["worker_topics"], first_name=first_name),
    ),
    "peer_comparison": ChartType(
        name="peer_comparison",
        description=(
            "Per topic, the worker's figure beside each other worker's on this run -- every peer its "
            "own bar labelled Peer A, Peer B, ... (never a name, never an average)."
        ),
        params={
            "type": "object",
            "properties": {"topics": {**_TOPICS, "maxItems": 4}, "max_peers": _PEERS},
            "additionalProperties": False,
        },
        datasets=("worker_topics", "peers"),
        build=lambda ds, first_name, params: peer_comparison(ds, first_name=first_name),
    ),
    "trend": ChartType(
        name="trend",
        description=(
            "Per topic, the worker's figure week by week (one point per saved run, ending with this "
            "run); with `peers: true`, each anonymous peer's own grey line too."
        ),
        params={
            "type": "object",
            "properties": {
                "topics": {**_TOPICS, "maxItems": 3},
                "weeks": _WEEKS,
                "peers": {"type": "boolean", "description": "Add each anonymous peer's line (default false)."},
                "max_peers": _PEERS,
            },
            "additionalProperties": False,
        },
        datasets=("worker_topics", "history"),
        build=lambda ds, first_name, params: trend(ds, first_name=first_name),
    ),
}
