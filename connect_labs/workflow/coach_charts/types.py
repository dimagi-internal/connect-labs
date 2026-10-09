"""The named chart types: each a function from its params (and the worker's first name)
to a Vega-Lite spec that reads Labs' named datasets (``datasets.py``) and holds no data
of its own.

A type is how most charts are asked for -- ``{"type": "topic_bars"}`` -- and how the
look improves over time: change a type here and every chart of that type changes. A
chart no type fits is a custom spec (``custom.py``), drawn in the same theme.

Sizes are CSS pixels (``theme.py``): the PNG doubles them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from connect_labs.workflow.coach_charts import theme

INNER = theme.WIDTH - 2 * theme.PADDING

# topic_bars: today's card, re-expressed. Per topic: the label (wrapped to at most three
# lines), the figure in bold under it, and a bar of numerator over denominator in the
# band's colour.
_LABEL_LINE = 28
_FIGURE_LINE = 30
_BAR_GAP = 12
_BAR_HEIGHT = 22
_BLOCK_GAP = 22
_MAX_LABEL_LINES = 3
#: A card is never shorter than this (CSS px, padding included; 500 px in the PNG):
#: one short topic is a card, not a sliver. The title block's height is estimated
#: from the theme's sizes to work out how much body that leaves.
MIN_CARD = 250
_TITLE_BLOCK = theme.TITLE_SIZE + 8 + theme.THEME["title"]["offset"]
_SUBTITLE_BLOCK = theme.SUBTITLE_SIZE + theme.THEME["title"]["subtitlePadding"] + 6

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
    """Today's card as a Vega-Lite spec over the ``worker_topics`` dataset: one block per
    row (``rows`` are that dataset's rows, needed only for the layout -- each block's
    height follows its wrapped label)."""
    blocks = []
    total = 0
    for i, row in enumerate(rows):
        lines = len(row.get("label_lines") or [""])
        figure_y = lines * _LABEL_LINE
        has_bar = row.get("share") is not None
        bar_y = figure_y + _FIGURE_LINE + _BAR_GAP
        height = bar_y + _BAR_HEIGHT if has_bar else figure_y + _FIGURE_LINE
        layers: list[dict] = [
            {
                "mark": {"type": "text", "align": "left", "baseline": "top", "lineHeight": _LABEL_LINE},
                "encoding": {
                    "text": {"field": "label_lines", "type": "nominal"},
                    "x": {"value": 0},
                    "y": {"value": 0},
                },
            },
            {
                "mark": {"type": "text", "align": "left", "baseline": "top", "fontWeight": 600},
                "encoding": {
                    "text": {"field": "figure_text", "type": "nominal"},
                    "x": {"value": 0},
                    "y": {"value": figure_y + 2},
                },
            },
        ]
        if has_bar:
            layers += [
                {
                    "mark": {"type": "rect", "color": theme.RULE, "cornerRadius": _BAR_HEIGHT // 2},
                    "encoding": {
                        "x": {"value": 0},
                        "x2": {"value": INNER},
                        "y": {"value": bar_y},
                        "y2": {"value": bar_y + _BAR_HEIGHT},
                    },
                },
                {
                    "transform": [{"filter": "datum.bar > 0"}],
                    "mark": {"type": "rect", "cornerRadius": _BAR_HEIGHT // 2},
                    "encoding": {
                        "x": {"datum": 0, "type": "quantitative", "scale": {"domain": [0, 1]}, "axis": None},
                        "x2": {"field": "bar", "type": "quantitative"},
                        "y": {"value": bar_y},
                        "y2": {"value": bar_y + _BAR_HEIGHT},
                        "color": {"field": "band_key", "type": "nominal", "scale": _band_scale(), "legend": None},
                    },
                },
            ]
        blocks.append({"height": height, "transform": [{"filter": f"datum.i == {i}"}], "layer": layers})
        total += height + (_BLOCK_GAP if i else 0)
    min_body = MIN_CARD - 2 * theme.PADDING - _TITLE_BLOCK - (_SUBTITLE_BLOCK if first_name else 0)
    if blocks and total < min_body:
        blocks[-1]["height"] += min_body - total
    return {
        "title": _title(title, first_name),
        "data": {"name": "worker_topics"},
        "spacing": _BLOCK_GAP,
        "vconcat": [{**b, "width": INNER} for b in blocks],
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

_ROW = 40  # one bar row (CSS px)
_WHO_WIDTH = 96  # room the row labels ("You", "Peer A") take, left of the bars
_VALUE_ROOM = 64  # room right of the longest bar for its value ("100%")
_TREND_HEIGHT = 200
_PEER_LABEL_ROOM = 84
_END_LINE = 22  # how far apart stacked end labels are


def _value_format(unit: str) -> str:
    return ".0%" if unit == "%" else ",.3~r"


def _label_layer(lines: int) -> tuple[dict, int]:
    """The topic's label as the top of a block, and the height it takes."""
    return (
        {
            "mark": {"type": "text", "align": "left", "baseline": "top", "lineHeight": _LABEL_LINE},
            "encoding": {"text": {"field": "label_lines", "type": "nominal"}, "x": {"value": 0}, "y": {"value": 0}},
        },
        lines * _LABEL_LINE + 10,
    )


def peer_comparison(datasets: dict, *, first_name: str | None = None, title: str = "How you compare") -> dict:
    """Per topic, the worker's figure beside each anonymous peer's: one bar per person,
    the worker first and in the band's colour, every peer in the neutral grey and
    labelled by letter, the value at the end of each bar."""
    mine = datasets.get("worker_topics") or []
    others = datasets.get("peers") or []
    blocks = []
    for row in mine:
        i, unit = row["i"], row.get("unit") or ""
        who = ["You"] + sorted({p["who"] for p in others if p["i"] == i}, key=lambda w: (len(w), w))
        label, label_height = _label_layer(len(row.get("label_lines") or [""]))
        rate = unit == "%"  # a rate is a fraction: its scale is 0..1
        # The row labels are drawn as text inside the plot, left of a bar scale that
        # starts at _WHO_WIDTH: an axis would shift the plot by its own width, and the
        # chart must stay exactly the theme's width.
        scale: dict = {"range": [_WHO_WIDTH, INNER - _VALUE_ROOM]}
        scale.update({"domain": [0, 1]} if rate else {"zero": True})
        x = {"field": "value", "type": "quantitative", "axis": None, "scale": scale}
        y = {"field": "who", "type": "nominal", "sort": who, "scale": {"domain": who}, "axis": None}
        who_label = {
            "mark": {"type": "text", "align": "left", "baseline": "middle"},
            "encoding": {"x": {"value": 0}, "y": y, "text": {"field": "who", "type": "nominal"}},
        }
        bars = {
            "layer": [
                {
                    "data": {"name": "worker_topics"},
                    "transform": [{"filter": f"datum.i == {i} && isValid(datum.value)"}],
                    "layer": [
                        {**who_label, "mark": {**who_label["mark"], "fontWeight": 600}},
                        {
                            "mark": {"type": "bar", "cornerRadius": 6, "height": 24},
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
                        who_label,
                        {
                            "mark": {"type": "bar", "cornerRadius": 6, "height": 24, "color": theme.NEUTRAL},
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
            "width": INNER,
            "height": _ROW * len(who),
        }
        heading = {
            "data": {"name": "worker_topics"},
            "transform": [{"filter": f"datum.i == {i}"}],
            "width": INNER,
            "height": label_height,
            "layer": [label],
        }
        blocks.append({"spacing": 6, "vconcat": [heading, bars]})
    return {"title": _title(title, first_name), "spacing": _BLOCK_GAP, "vconcat": blocks}


def trend(datasets: dict, *, first_name: str | None = None, title: str = "Week by week") -> dict:
    """Per topic, the worker's weekly line (one point per saved run) and, when the
    ``history`` dataset carries them, each anonymous peer's own thinner grey line,
    labelled by letter at its last point."""
    mine = datasets.get("worker_topics") or []
    blocks = []
    for row in mine:
        i, unit = row["i"], row.get("unit") or ""
        label, label_height = _label_layer(len(row.get("label_lines") or [""]))
        y = {
            "field": "value",
            "type": "quantitative",
            "title": None,
            # Labels sit inside the plot, on their gridlines: an axis outside it would push
            # the chart past the theme's width.
            "axis": {
                "format": _value_format(unit),
                "tickCount": 4,
                "grid": True,
                "labelAlign": "left",
                "labelBaseline": "bottom",
                "labelPadding": -2,
                "labelOffset": -4,
            },
            "scale": {"domain": [0, 1]} if unit == "%" else {"zero": True},
        }
        x = {
            "field": "week",
            "type": "temporal",
            "title": None,
            # Room right of the last week for the peers' labels ("Peer A").
            "scale": {"range": [8, INNER - _PEER_LABEL_ROOM]},  # 8: a first point is not clipped
            "axis": {"format": "%-d %b", "labelOverlap": True, "labelAlign": "left", "tickCount": 4, "grid": False},
        }
        lines = {
            "data": {"name": "history"},
            "transform": [
                {"filter": f"datum.i == {i}"},
                {"joinaggregate": [{"op": "max", "field": "t", "as": "last_t"}], "groupby": ["who"]},
            ],
            "width": INNER,
            "height": _TREND_HEIGHT,
            "layer": [
                {
                    "transform": [{"filter": "datum.who != 'You'"}],
                    "mark": {"type": "line", "color": theme.NEUTRAL, "strokeWidth": 2},
                    "encoding": {"x": x, "y": y, "detail": {"field": "who", "type": "nominal"}},
                },
                {
                    # Each peer named at its last point; on a rate, peers ending close
                    # together are stacked a line apart rather than drawn over each other.
                    "transform": [
                        {"filter": "datum.who != 'You' && datum.t == datum.last_t"},
                        {"calculate": "round(datum.value * 100 / 6)", "as": "end_bucket"},
                        {"window": [{"op": "row_number", "as": "end_rank"}], "groupby": ["end_bucket"]},
                        {
                            "calculate": (
                                "datum.unit == '%' ? "
                                f"datum.value + (datum.end_rank - 1) * {_END_LINE / _TREND_HEIGHT} : datum.value"
                            ),
                            "as": "label_value",
                        },
                    ],
                    "mark": {"type": "text", "align": "left", "baseline": "middle", "dx": 6, "color": theme.MUTED},
                    "encoding": {
                        "x": x,
                        "y": {**y, "field": "label_value"},
                        "text": {"field": "who", "type": "nominal"},
                    },
                },
                {
                    "transform": [{"filter": "datum.who == 'You' && datum.t == datum.last_t"}],
                    "mark": {
                        "type": "text",
                        "align": "left",
                        "baseline": "middle",
                        "dx": 10,
                        "fontWeight": 600,
                        "color": theme.INDIGO,
                    },
                    "encoding": {"x": x, "y": y, "text": {"field": "who", "type": "nominal"}},
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
        heading = {
            "data": {"name": "worker_topics"},
            "transform": [{"filter": f"datum.i == {i}"}],
            "width": INNER,
            "height": label_height,
            "layer": [label],
        }
        blocks.append({"spacing": 6, "vconcat": [heading, lines]})
    return {"title": _title(title, first_name), "spacing": _BLOCK_GAP, "vconcat": blocks}


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
