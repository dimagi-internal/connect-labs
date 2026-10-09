"""CASE pictures: the generic chart types a case state names (``case_state.picture.type``).

A worker chart (``types.py``) pictures a worker's indicators; these picture ONE case,
for the case state it is in (``semantic/case_states.py``), so a field worker reads it
at a glance on a phone. The type is chosen by the registry, and so is every word on it
(titles, badges, reference-band label, checklist, sign texts); every NUMBER comes from
the case's own row and visits (``case_chart.py``):

* ``series_vs_reference``   a series climbing above a shaded reference band from its
                            first reading, a star at the latest reading, and a badge;
* ``series_highlight_step`` the series with one reading ringed and labelled, dashed into
                            and out of it, and a checklist beside it;
* ``series_with_bars``      the series against the reference band, with a second series
                            as bars beside it;
* ``sign_card``             a card: the labels a case column lists and when, why each
                            matters, and -- beside them -- what to do.

Every picture is landscape (``theme.WIDTH`` x ``theme.HEIGHT``, 3:2): Connect's messenger
sizes the bubble to a portrait picture's narrow width (connect-labs#2413). So the parts
of a picture sit SIDE BY SIDE, each in its own column (``LAYOUT``), under the title.

Positions computed in Python ride in the data and are drawn with ``scale: null``
(pixels). Sizes are CSS px (``theme``); no text is under ``theme.MIN_FONT_SIZE``.
"""

from __future__ import annotations

from typing import Any

from connect_labs.workflow.coach_charts import case_summary, theme

INNER = theme.WIDTH - 2 * theme.PADDING
GREEN = theme.BAND_COLOURS["green"]
SUNSET = theme.BAND_COLOURS["red"]
MARIGOLD = theme.BAND_COLOURS["yellow"]

#: A five-pointed star (Vega symbol path, unit size).
STAR = (
    "M0,-1L0.2245,-0.309L0.951,-0.309L0.363,0.118L0.588,0.809L0,0.382L-0.588,0.809"
    "L-0.363,0.118L-0.951,-0.309L-0.2245,-0.309Z"
)

#: The body under the title and subtitle (CSS px), and the gap between side-by-side parts.
BODY = theme.body_height(subtitle=True)
GAP = 24
#: The x axis's date labels under a plot.
AXIS_ROOM = 52
#: Room left of the first reading for the axis labels drawn inside the plot.
LEFT_ROOM = 84

#: Per type: the series plot's width, the room right of its last reading (for the
#: reading's label and the band's), and each text panel's width. Widths sum to INNER
#: with ``GAP`` between side-by-side parts.
LAYOUT: dict[str, dict] = {
    "series_vs_reference": {"plot": INNER, "right_room": 112, "panels": {"top": INNER}},
    "series_highlight_step": {"plot": 256, "right_room": 34, "panels": {"side": INNER - GAP - 256}},
    "series_with_bars": {"plot": 272, "right_room": 92, "panels": {"bars": INNER - GAP - 272}},
    "sign_card": {"plot": INNER, "right_room": 0, "panels": {"left": 256, "right": INNER - GAP - 256}},
}
#: series_highlight_step: the ringed reading's label, in a row of its own over the plot.
FLAG_SIZE = 24
FLAG_ROW = 32
#: series_vs_reference: the badge row above the plot.
BADGE_ROW = 56
#: series_with_bars: at most this many bars (the latest visits) fit beside the plot.
MAX_BARS = 3

_PX = {"type": "quantitative", "scale": None}


def _px(field: str) -> dict:
    return {"field": field, **_PX}


def plot_range(kind: str) -> tuple[int, int]:
    """Where the readings run, in px across the type's plot."""
    layout = LAYOUT[kind]
    return LEFT_ROOM, layout["plot"] - layout["right_room"]


def x_scale(ds_meta: dict) -> dict:
    lo, hi = plot_range(ds_meta["kind"])
    return {"domain": [0, max(ds_meta["max_t"], 1)], "range": [lo, hi], "nice": False, "zero": False}


def x_encoding(ds_meta: dict) -> dict:
    """Days since the first weighing, labelled with the date at the chosen visits."""
    y, m, d = ds_meta["start"]
    return {
        "field": "t",
        "type": "quantitative",
        "title": None,
        "scale": x_scale(ds_meta),
        "axis": {
            "values": ds_meta["ticks"],
            "labelExpr": f"timeFormat(datetime({y}, {m - 1}, {d} + datum.value), '%-d %b')",
            "grid": False,
            "labelFlush": False,
        },
    }


def _unit(ds_meta: dict) -> str:
    """The series' unit for its axis labels: letters only, so it is safe in an expression."""
    unit = str(ds_meta.get("unit") or "")
    return unit if unit.isalpha() and len(unit) <= 6 else ""


def y_encoding(ds_meta: dict, field: str = "w") -> dict:
    lo, hi = ds_meta["y_domain"]
    return {
        "field": field,
        "type": "quantitative",
        "title": None,
        "scale": {"domain": [lo, hi], "nice": False, "zero": False},
        # Labels sit inside the plot on their gridlines, as in ``types.trend``: an axis
        # outside would push the picture past the phone's width.
        "axis": {
            "values": ds_meta["y_ticks"],
            "labelExpr": "format(datum.value, ',')" + (f" + ' {_unit(ds_meta)}'" if _unit(ds_meta) else ""),
            "grid": True,
            "labelAlign": "left",
            "labelBaseline": "bottom",
            "labelPadding": -2,
            "labelOffset": -4,
        },
    }


def _band_layers(meta: dict, *, dx: int = 8) -> list[dict]:
    """The reference band, labelled ``dx`` px right of its end."""
    x, y = x_encoding(meta), y_encoding(meta, "lo")
    return [
        {
            "data": {"name": "case_band"},
            "mark": {"type": "area", "color": GREEN, "opacity": 0.16},
            "encoding": {"x": x, "y": y, "y2": {"field": "hi"}},
        },
        {
            "data": {"name": "case_band"},
            "transform": [{"filter": "datum.end"}],
            "mark": {
                "type": "text",
                "align": "left",
                "baseline": "middle",
                "dx": dx,
                "color": GREEN,
                "fontWeight": 600,
            },
            "encoding": {"x": x, "y": {**y, "field": "mid"}, "text": {"field": "label_1"}},
        },
        {
            "data": {"name": "case_band"},
            "transform": [{"filter": "datum.end"}],
            "mark": {"type": "text", "align": "left", "baseline": "top", "dx": dx, "dy": 8, "color": GREEN},
            "encoding": {"x": x, "y": {**y, "field": "mid"}, "text": {"field": "label_2"}},
        },
    ]


def _weight_line(meta: dict, *, dashed_segments: bool = False) -> list[dict]:
    x, y = x_encoding(meta), y_encoding(meta)
    if dashed_segments:
        line = {
            "data": {"name": "case_segments"},
            "mark": {"type": "line", "color": theme.INDIGO, "strokeWidth": 5},
            "encoding": {
                "x": x,
                "y": y,
                "detail": {"field": "seg", "type": "nominal"},
                "strokeDash": {
                    "field": "dashed",
                    "type": "nominal",
                    "scale": {"domain": [False, True], "range": [[1, 0], [10, 8]]},
                    "legend": None,
                },
            },
        }
    else:
        line = {
            "data": {"name": "case_series"},
            "mark": {"type": "line", "color": theme.INDIGO, "strokeWidth": 5},
            "encoding": {"x": x, "y": y},
        }
    points = {
        "data": {"name": "case_series"},
        "mark": {
            "type": "point",
            "filled": True,
            "size": 220,
            "color": theme.INDIGO,
            "stroke": "#ffffff",
            "strokeWidth": 3,
        },
        "encoding": {"x": x, "y": y},
    }
    return [line, points]


def _weight_labels(
    meta: dict, which: str, *, color: str = theme.INDIGO, dy: int = -28, size: int = 24, above: bool = False
) -> dict:
    """The ``which`` reading's label: right of it, or ``above`` it (where the band's
    label needs the room to its right)."""
    x, y = x_encoding(meta), y_encoding(meta)
    beside = which == "last" and not above
    return {
        "data": {"name": "case_series"},
        "transform": [{"filter": f"datum.{which}"}],
        "mark": {
            "type": "text",
            "align": "left" if beside else ("right" if above else "center"),
            "baseline": "middle",
            "dx": 18 if beside else (12 if above else 0),
            "dy": 0 if beside else dy,
            "fontWeight": 700,
            "fontSize": size,
            "color": color,
        },
        "encoding": {"x": x, "y": y, "text": {"field": "w_label"}},
    }


def _text_panel(meta: dict, panel: str) -> dict:
    """The ``panel`` part of the ``case_text`` dataset -- shapes and text positioned in
    pixels by Labs -- as a view its panel's width and height."""
    common = {"x": _px("x"), "y": _px("y")}
    return {
        "data": {"name": "case_text"},
        "transform": [{"filter": f"datum.panel == '{panel}'"}],
        "width": LAYOUT[meta["kind"]]["panels"][panel],
        "height": meta["panel_height"].get(panel, 1),
        "layer": [
            {
                "transform": [{"filter": "datum.kind == 'rect'"}],
                "mark": {"type": "rect", "cornerRadius": theme.RADIUS},
                "encoding": {
                    **common,
                    "x2": _px("x2"),
                    "y2": _px("y2"),
                    "color": {"field": "color", "type": "nominal", "scale": None},
                    "opacity": {"field": "opacity", "type": "quantitative", "scale": None},
                },
            },
            {
                "transform": [{"filter": "datum.kind == 'dot'"}],
                "mark": {"type": "point", "filled": True, "shape": "circle"},
                "encoding": {
                    **common,
                    "size": _px("area"),
                    "color": {"field": "color", "type": "nominal", "scale": None},
                    "opacity": {"value": 1},
                },
            },
            {
                "transform": [{"filter": "datum.kind == 'text' && !datum.bold"}],
                "mark": {"type": "text", "baseline": "middle", "align": "left"},
                "encoding": {
                    **common,
                    "text": {"field": "text", "type": "nominal"},
                    "size": _px("size"),
                    "color": {"field": "color", "type": "nominal", "scale": None},
                },
            },
            {
                "transform": [{"filter": "datum.kind == 'text' && datum.bold"}],
                "mark": {"type": "text", "baseline": "middle", "align": "left", "fontWeight": 700},
                "encoding": {
                    **common,
                    "text": {"field": "text", "type": "nominal"},
                    "size": _px("size"),
                    "color": {"field": "color", "type": "nominal", "scale": None},
                },
            },
            {
                "transform": [{"filter": "datum.kind == 'ctext'"}],
                "mark": {"type": "text", "baseline": "middle", "align": "center", "fontWeight": 700},
                "encoding": {
                    **common,
                    "text": {"field": "text", "type": "nominal"},
                    "size": _px("size"),
                    "color": {"field": "color", "type": "nominal", "scale": None},
                },
            },
        ],
    }


def _title(text: str, subtitle: str | None) -> dict:
    out: dict[str, Any] = {"text": text}
    if subtitle:
        out["subtitle"] = subtitle
    return out


def _body_height(meta: dict) -> int:
    """The body's height under the title: taller when there is no subtitle (case name)."""
    return theme.body_height(subtitle=bool(meta.get("case_name")))


# ---------------------------------------------------------------------------
# The types
# ---------------------------------------------------------------------------


def series_vs_reference(meta: dict) -> dict:
    x, y = x_encoding(meta), y_encoding(meta)
    top = meta["panel_height"].get("top", 1)
    plot = {
        "width": LAYOUT["series_vs_reference"]["plot"],
        "height": _body_height(meta) - top - 16 - AXIS_ROOM,
        "layer": [
            *_band_layers(meta, dx=28),
            *_weight_line(meta),
            {
                # The milestone: a star at the latest weighing.
                "data": {"name": "case_series"},
                "transform": [{"filter": "datum.last"}],
                "mark": {
                    "type": "point",
                    "shape": STAR,
                    "filled": True,
                    "size": 1800,
                    "color": MARIGOLD,
                    "stroke": "#ffffff",
                    "strokeWidth": 2,
                    "opacity": 1,
                },
                "encoding": {"x": x, "y": y},
            },
            # The latest weighing's figure above its star; the band's label is to its right.
            _weight_labels(meta, "last", above=True, dy=-40),
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": 16,
        "vconcat": [_text_panel(meta, "top"), plot],
    }


def series_highlight_step(meta: dict) -> dict:
    x, y = x_encoding(meta), y_encoding(meta)
    width = LAYOUT["series_highlight_step"]["plot"]
    # The ringed reading's question sits in its own line over the plot, centred over the
    # ring as far as the plot allows -- never across the line or the axis labels.
    label = {
        "data": {"name": "case_series"},
        "transform": [{"filter": "datum.flag_label"}],
        "width": width,
        "height": FLAG_ROW,
        "mark": {
            "type": "text",
            "align": "center",
            "baseline": "middle",
            "fontWeight": 700,
            "fontSize": FLAG_SIZE,
            "color": SUNSET,
        },
        "encoding": {
            "x": {**x, "field": "flag_lx", "axis": None},
            "y": {"value": FLAG_ROW / 2},
            "text": {"field": "flag_label"},
        },
    }
    plot = {
        "width": width,
        "height": _body_height(meta) - AXIS_ROOM - FLAG_ROW - 8,
        "layer": [
            *_weight_line(meta, dashed_segments=True),
            {
                # The weighing to check, ringed.
                "data": {"name": "case_series"},
                "transform": [{"filter": "datum.flag"}],
                "mark": {
                    "type": "point",
                    "filled": False,
                    "size": 2000,
                    "color": SUNSET,
                    "strokeWidth": 5,
                    "opacity": 1,
                },
                "encoding": {"x": x, "y": y},
            },
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": GAP,
        "hconcat": [{"spacing": 8, "vconcat": [label, plot]}, _text_panel(meta, "side")],
    }


def series_with_bars(meta: dict) -> dict:
    body = _body_height(meta)
    plot = {
        "width": LAYOUT["series_with_bars"]["plot"],
        "height": body - AXIS_ROOM,
        "layer": [*_band_layers(meta), *_weight_line(meta), _weight_labels(meta, "last", above=True)],
    }
    head = meta["panel_height"].get("bars", 1)
    # One bar per visit (the latest ``MAX_BARS``), in visit order, beside the plot.
    bars_x = {
        "field": "t",
        "type": "ordinal",
        "title": None,
        "axis": None,
        "scale": {"paddingInner": 0.3, "paddingOuter": 0.05},
    }
    bars_y = {
        "field": "h",
        "type": "quantitative",
        "title": None,
        "axis": None,
        "scale": {"domain": [0, meta["skin_top"]]},
    }
    bars = {
        "data": {"name": "case_bars"},
        "width": LAYOUT["series_with_bars"]["panels"]["bars"],
        # The bars stand on the same baseline as the plot; their dates sit where its dates do.
        "height": body - head - 12 - AXIS_ROOM - 4,
        "layer": [
            {
                "mark": {"type": "bar", "cornerRadiusTopLeft": 6, "cornerRadiusTopRight": 6},
                "encoding": {
                    "x": bars_x,
                    "y": bars_y,
                    "color": {"field": "tone", "type": "nominal", "scale": None},
                },
            },
            {
                "mark": {"type": "text", "baseline": "bottom", "dy": -6, "fontWeight": 700},
                "encoding": {
                    "x": bars_x,
                    "y": bars_y,
                    "text": {"field": "h_label"},
                    "color": {"field": "tone", "type": "nominal", "scale": None},
                },
            },
            {
                "mark": {"type": "text", "baseline": "top", "dy": 10, "color": theme.MUTED},
                "encoding": {
                    "x": bars_x,
                    "y": {k: v for k, v in bars_y.items() if k != "field"} | {"datum": 0},
                    "text": {"field": "date_label"},
                },
            },
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": GAP,
        "hconcat": [plot, {"spacing": 12, "vconcat": [_text_panel(meta, "bars"), bars]}],
    }


def sign_card(meta: dict) -> dict:
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": GAP,
        "hconcat": [_text_panel(meta, "left"), _text_panel(meta, "right")],
    }


#: The picture types a case state may name (``semantic/case_states.PICTURE_TYPES``).
CASE_TYPES = {
    "series_vs_reference": series_vs_reference,
    "series_highlight_step": series_highlight_step,
    "series_with_bars": series_with_bars,
    "sign_card": sign_card,
    "case_summary": case_summary.spec,
}
