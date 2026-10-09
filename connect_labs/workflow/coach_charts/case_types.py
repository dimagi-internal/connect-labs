"""CASE pictures: the generic chart types a case state names (``case_state.picture.type``).

A worker chart (``types.py``) pictures a worker's indicators; these picture ONE case,
for the case state it is in (``semantic/case_states.py``), so a field worker reads it
at a glance on a phone. The type is chosen by the registry, and so is every word on it
(titles, badges, reference-band label, checklist, sign texts); every NUMBER comes from
the case's own row and visits (``case_chart.py``):

* ``series_vs_reference``   a series climbing above a shaded reference band from its
                            first reading, a star at the latest reading, and a badge;
* ``series_highlight_step`` the series with one reading ringed and labelled, dashed into
                            and out of it, and a checklist;
* ``series_with_bars``      the series against the reference band, with a second series
                            as bars underneath;
* ``sign_card``             a card: the labels a case column lists and when, why each
                            matters, and what to do.

Positions computed in Python ride in the data and are drawn with ``scale: null``
(pixels). Sizes are CSS px (``theme``); no text is under ``theme.MIN_FONT_SIZE``.
"""

from __future__ import annotations

from typing import Any

from connect_labs.workflow.coach_charts import theme

INNER = theme.WIDTH - 2 * theme.PADDING
GREEN = theme.BAND_COLOURS["green"]
SUNSET = theme.BAND_COLOURS["red"]
MARIGOLD = theme.BAND_COLOURS["yellow"]

#: A five-pointed star (Vega symbol path, unit size).
STAR = (
    "M0,-1L0.2245,-0.309L0.951,-0.309L0.363,0.118L0.588,0.809L0,0.382L-0.588,0.809"
    "L-0.363,0.118L-0.951,-0.309L-0.2245,-0.309Z"
)

PLOT_HEIGHT = 290
#: Room right of the last weighing for its label and the band's.
RIGHT_ROOM = 112
#: Room left of the first reading for the axis labels drawn inside the plot.
LEFT_ROOM = 100

_PX = {"type": "quantitative", "scale": None}


def _px(field: str) -> dict:
    return {"field": field, **_PX}


def x_scale(max_t: float) -> dict:
    return {"domain": [0, max(max_t, 1)], "range": [LEFT_ROOM, INNER - RIGHT_ROOM], "nice": False, "zero": False}


def x_encoding(ds_meta: dict) -> dict:
    """Days since the first weighing, labelled with the date at the chosen visits."""
    y, m, d = ds_meta["start"]
    return {
        "field": "t",
        "type": "quantitative",
        "title": None,
        "scale": x_scale(ds_meta["max_t"]),
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


def _band_layers(meta: dict) -> list[dict]:
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
                "dx": 8,
                "color": GREEN,
                "fontWeight": 600,
            },
            "encoding": {"x": x, "y": {**y, "field": "mid"}, "text": {"field": "label_1"}},
        },
        {
            "data": {"name": "case_band"},
            "transform": [{"filter": "datum.end"}],
            "mark": {"type": "text", "align": "left", "baseline": "top", "dx": 8, "dy": 8, "color": GREEN},
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


def _weight_labels(meta: dict, which: str, *, color: str = theme.INDIGO, dy: int = -24, size: int = 24) -> dict:
    x, y = x_encoding(meta), y_encoding(meta)
    return {
        "data": {"name": "case_series"},
        "transform": [{"filter": f"datum.{which}"}],
        "mark": {
            "type": "text",
            "align": "left" if which == "last" else "center",
            "baseline": "middle",
            "dx": 18 if which == "last" else 0,
            "dy": dy if which != "last" else 0,
            "fontWeight": 700,
            "fontSize": size,
            "color": color,
        },
        "encoding": {"x": x, "y": y, "text": {"field": "w_label"}},
    }


def _text_panel(name: str, height: int) -> dict:
    """Shapes and text from the ``name`` dataset, positioned in pixels by Labs."""
    common = {"x": _px("x"), "y": _px("y")}
    return {
        "data": {"name": name},
        "width": INNER,
        "height": height,
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


# ---------------------------------------------------------------------------
# The types
# ---------------------------------------------------------------------------


def series_vs_reference(meta: dict) -> dict:
    x, y = x_encoding(meta), y_encoding(meta)
    plot = {
        "width": INNER,
        "height": PLOT_HEIGHT,
        "layer": [
            *_band_layers(meta),
            *_weight_line(meta),
            {
                # The milestone: a star at the latest weighing.
                "data": {"name": "case_series"},
                "transform": [{"filter": "datum.last"}],
                "mark": {
                    "type": "point",
                    "shape": STAR,
                    "filled": True,
                    "size": 2200,
                    "color": MARIGOLD,
                    "stroke": "#ffffff",
                    "strokeWidth": 2,
                    "opacity": 1,
                },
                "encoding": {"x": x, "y": y},
            },
            {**_weight_labels(meta, "last"), "mark": {**_weight_labels(meta, "last")["mark"], "dx": 30}},
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": 16,
        "vconcat": [_text_panel("case_text", meta["text_height"]), plot],
    }


def series_highlight_step(meta: dict) -> dict:
    x, y = x_encoding(meta), y_encoding(meta)
    flag_label_align = "right" if meta.get("flag_right") else "left"
    plot = {
        "width": INNER,
        "height": PLOT_HEIGHT - 30,
        "layer": [
            *_weight_line(meta, dashed_segments=True),
            {
                # The weighing to check, ringed.
                "data": {"name": "case_series"},
                "transform": [{"filter": "datum.flag"}],
                "mark": {
                    "type": "point",
                    "filled": False,
                    "size": 2600,
                    "color": SUNSET,
                    "strokeWidth": 5,
                    "opacity": 1,
                },
                "encoding": {"x": x, "y": y},
            },
            {
                "data": {"name": "case_series"},
                "transform": [{"filter": "datum.flag_label"}],
                "mark": {
                    "type": "text",
                    "align": flag_label_align,
                    "baseline": "middle",
                    "dx": -36 if flag_label_align == "right" else 36,
                    "dy": meta.get("flag_dy", -10),
                    "fontWeight": 700,
                    "fontSize": 26,
                    "color": SUNSET,
                },
                "encoding": {"x": x, "y": y, "text": {"field": "flag_label"}},
            },
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": 20,
        "vconcat": [plot, _text_panel("case_text", meta["text_height"])],
    }


def series_with_bars(meta: dict) -> dict:
    plot = {
        "width": INNER,
        "height": PLOT_HEIGHT - 50,
        "layer": [*_band_layers(meta), *_weight_line(meta), _weight_labels(meta, "last")],
    }
    bars_x = {
        "field": "t",
        "type": "quantitative",
        "title": None,
        "scale": x_scale(meta["max_t"]),
        "axis": None,
    }
    bars = {
        "data": {"name": "case_bars"},
        "width": INNER,
        "height": 150,
        "layer": [
            {
                "mark": {"type": "bar", "width": 34, "cornerRadiusTopLeft": 6, "cornerRadiusTopRight": 6},
                "encoding": {
                    "x": bars_x,
                    "y": {
                        "field": "h",
                        "type": "quantitative",
                        "title": None,
                        "axis": None,
                        "scale": {"domain": [0, meta["skin_top"]]},
                    },
                    "color": {"field": "tone", "type": "nominal", "scale": None},
                },
            },
            {
                "mark": {"type": "text", "baseline": "bottom", "dy": -6, "fontWeight": 700},
                "encoding": {
                    "x": bars_x,
                    "y": {
                        "field": "h",
                        "type": "quantitative",
                        "scale": {"domain": [0, meta["skin_top"]]},
                        "axis": None,
                    },
                    "text": {"field": "h_label"},
                    "color": {"field": "tone", "type": "nominal", "scale": None},
                },
            },
            {
                "mark": {"type": "text", "baseline": "top", "dy": 8, "color": theme.MUTED},
                "encoding": {
                    "x": bars_x,
                    "y": {
                        "datum": 0,
                        "type": "quantitative",
                        "scale": {"domain": [0, meta["skin_top"]]},
                        "axis": None,
                    },
                    "text": {"field": "date_label"},
                },
            },
        ],
    }
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "spacing": 14,
        "vconcat": [plot, _text_panel("case_text", meta["text_height"]), bars],
    }


def sign_card(meta: dict) -> dict:
    return {
        "title": _title(meta["title"], meta.get("case_name")),
        "vconcat": [_text_panel("case_text", meta["text_height"])],
    }


#: The picture types a case state may name (``semantic/case_states.PICTURE_TYPES``).
CASE_TYPES = {
    "series_vs_reference": series_vs_reference,
    "series_highlight_step": series_highlight_step,
    "series_with_bars": series_with_bars,
    "sign_card": sign_card,
}
