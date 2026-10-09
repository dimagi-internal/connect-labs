"""Coaching charts: Connect's theme, forced on every chart, and the named types."""

import io
import re

import pytest
from PIL import Image

from connect_labs.workflow.coach_charts import render, theme, types

TOPICS = [
    {"label": "Visits held: over 30 m away", "band": "yellow", "numerator": 31, "denominator": 73, "pct": 42},
    {"label": "Chlorine pass", "band": "red", "numerator": 3, "denominator": 73},
]


def _bars(topics=TOPICS, first_name="Ibrahim"):
    rows = types.topic_rows(topics)
    return types.topic_bars(rows, first_name=first_name), {"worker_topics": rows}


def _png(spec, datasets):
    return Image.open(io.BytesIO(render.render_png(spec, datasets))).convert("RGB")


def _rgb(hex_colour):
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))


def _colours(image):
    return {c for _, c in image.getcolors(maxcolors=1 << 22)}


# ---------------------------------------------------------------------------
# The theme
# ---------------------------------------------------------------------------


def _font_sizes(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.lower().endswith("fontsize") and isinstance(v, (int, float)):
                yield v
            else:
                yield from _font_sizes(v)
    elif isinstance(node, list):
        for v in node:
            yield from _font_sizes(v)


def _hex_colours(node):
    return set(re.findall(r"#[0-9a-fA-F]{6}", repr(node)))


def test_every_theme_colour_is_a_connect_token():
    assert _hex_colours(theme.THEME) <= theme.PALETTE


def test_no_themed_text_is_too_small_for_a_phone():
    sizes = list(_font_sizes(theme.THEME))
    assert sizes and min(sizes) >= theme.MIN_FONT_SIZE
    # Drawn at 2x and shown at ~0.86x in a ~930 px bubble: the smallest text is ~34 px on screen.
    assert theme.MIN_FONT_SIZE * theme.SCALE >= 40


def test_the_theme_replaces_whatever_config_a_spec_carries():
    spec, data = _bars()
    hostile = {
        **spec,
        "config": {"background": "#000000", "title": {"color": "#00ff00", "fontSize": 4}},
        "background": "#000000",
        "padding": 0,
        "autosize": "none",
    }
    drawn = render.themed(hostile, data)
    assert drawn["config"] == theme.THEME
    assert drawn["config"] is not theme.THEME  # a copy: no chart can edit the theme
    assert "background" not in drawn and "padding" not in drawn and "autosize" not in drawn
    image = _png(hostile, data)
    assert image.getpixel((2, 2)) == _rgb(theme.BACKGROUND)
    assert (0, 255, 0) not in _colours(image)


def test_a_specs_own_datasets_are_replaced_by_labs():
    spec, data = _bars()
    drawn = render.themed({**spec, "datasets": {"worker_topics": [{"i": 0, "label_lines": ["forged"]}]}}, data)
    assert drawn["datasets"] == data


def test_nothing_is_loaded_from_outside():
    spec = {
        "data": {"url": "https://example.com/figures.json"},
        "mark": "bar",
        "encoding": {"x": {"field": "v", "type": "quantitative"}},
    }
    with pytest.raises(render.RenderError):
        render.render_png(spec, {})


# ---------------------------------------------------------------------------
# topic_bars: today's card, in the theme
# ---------------------------------------------------------------------------


def test_topic_bars_is_1080_wide_and_as_tall_as_its_content():
    one, two = _png(*_bars(TOPICS[:1])), _png(*_bars())
    assert one.size[0] == two.size[0] == render.PNG_WIDTH == 1080
    assert 480 <= one.size[1] < two.size[1] <= render.MAX_HEIGHT


def test_topic_bars_draws_in_the_band_colours_and_the_track_token():
    colours = _colours(_png(*_bars()))
    assert {_rgb(theme.BAND_COLOURS["red"]), _rgb(theme.BAND_COLOURS["yellow"]), _rgb(theme.RULE)} <= colours
    assert _rgb(theme.DEEP_PURPLE) in colours  # the title


def test_topic_bars_png_is_small():
    assert len(render.render_png(*_bars())) < 300 * 1024


def test_drawing_is_deterministic():
    assert render.render_png(*_bars()) == render.render_png(*_bars())


def test_a_long_label_wraps_to_at_most_three_lines():
    lines = types.wrap("word " * 80)
    assert len(lines) == 3 and lines[-1].endswith("…")
    assert types.wrap("Chlorine pass") == ["Chlorine pass"]


def test_a_sliver_is_drawn_as_a_round_cap_and_zero_as_no_bar():
    [tiny, zero] = types.topic_rows(
        [{"label": "a", "numerator": 1, "denominator": 1000}, {"label": "b", "numerator": 0, "denominator": 9}]
    )
    assert tiny["share"] == 0.001 and tiny["bar"] > tiny["share"]
    assert zero["bar"] == 0


def test_a_chart_too_tall_for_a_phone_is_refused():
    rows = types.topic_rows([{"label": "x " * 40, "numerator": 1, "denominator": 2}] * 20)
    with pytest.raises(render.RenderError, match="fits a phone"):
        render.render_png(types.topic_bars(rows), {"worker_topics": rows})


# ---------------------------------------------------------------------------
# trend: end labels never overlap, and stay inside the plot
# ---------------------------------------------------------------------------


def _ends(**last):
    """History points for lines ending at ``last`` values (one earlier week each)."""
    points = []
    for who, value in last.items():
        name = "You" if who == "you" else "Peer " + who
        points += [
            {"week": "2026-09-20", "t": 0, "i": 0, "who": name, "value": 0.5},
            {"week": "2026-09-27", "t": 1, "i": 0, "who": name, "value": value},
        ]
    return points


def _assert_spread(labels, top=1.0):
    gap = top * types._END_LINE / types._TREND_HEIGHT
    ys = sorted(lab["y"] for lab in labels)
    assert all(b - a >= gap - 1e-9 for a, b in zip(ys, ys[1:])), ys
    assert ys[0] >= gap / 2 - 1e-9 and ys[-1] <= top - gap / 2 + 1e-9, ys


def test_peers_ending_at_the_same_value_share_one_label_inside_the_plot():
    labels = types.end_labels(_ends(you=0.42, A=0.0, B=0.0), top=1.0)
    assert sorted(lab["text"] for lab in labels) == ["Peer A, B", "You"]
    _assert_spread(labels)


def test_labels_that_would_collide_are_a_line_apart():
    labels = types.end_labels(_ends(you=0.03, A=0.0, B=0.09, C=0.95, D=1.0), top=1.0)
    assert {lab["text"] for lab in labels} >= {"You", "Peer A", "Peer B"}
    _assert_spread(labels)
    assert next(lab for lab in labels if lab["you"])["text"] == "You"


def test_more_labels_than_the_plot_holds_still_never_overlap():
    many = {chr(65 + n): n / 11 for n in range(12)}
    labels = types.end_labels(_ends(you=0.5, **many), top=1.0)
    _assert_spread(labels)


def test_a_trend_with_tied_peers_draws_at_phone_width():
    from connect_labs.workflow.coach_charts import datasets as D

    graded = {
        "cMeasures": [{"indicator": "X1", "label": "Visits held", "unit": "%"}],
        "byFLW": [{"key": "me", "name": "Ibrahim Lawal", "ind": {"X1": {"band": "yellow", "value": 0.42, "n": 73}}}],
    }
    runs = [
        {
            "week": f"2026-09-{d:02d}",
            "cells": {"me": {"X1": {"value": 0.4}}, "a": {"X1": {"value": 0}}, "b": {"X1": {"value": 0}}},
        }
        for d in (6, 13, 20, 27)
    ]
    ds = {"worker_topics": D.worker_topics(graded, "me", ["X1"])}
    ds["history"] = D.history(runs, graded, "me", ["X1"], {"a": "Peer A", "b": "Peer B"})
    assert _png(types.trend(ds, first_name="Ibrahim"), ds).size[0] == render.PNG_WIDTH
