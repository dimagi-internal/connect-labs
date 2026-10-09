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


@dataclass(frozen=True)
class ChartType:
    name: str
    description: str
    params: dict  # JSON schema of the params
    datasets: tuple[str, ...]  # the named datasets it reads
    build: Callable[..., dict]


TYPES: dict[str, ChartType] = {
    "topic_bars": ChartType(
        name="topic_bars",
        description=(
            "The worker's own figures: per topic its label, the figure ('31 of 73 · 42%') and a bar "
            "in the band's colour. The default."
        ),
        params={
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "maxItems": 8,
                    "items": {"type": "string", "minLength": 1, "maxLength": 100},
                    "description": "Indicator keys to show, in order. Default: the briefing's topics.",
                }
            },
            "additionalProperties": False,
        },
        datasets=("worker_topics",),
        build=topic_bars,
    ),
}
