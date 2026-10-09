"""Coaching charts: Labs' datasets, anonymous peers, the custom-spec sanitizer and
building a frozen chart."""

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache
from PIL import Image

from connect_labs.workflow.coach_charts import chart, custom, datasets, render, theme
from connect_labs.workflow.coach_charts.datasets import ChartError

ME = "10::w02"
MEASURES = [
    {"indicator": "Q1", "label": "Chlorine pass", "unit": "%"},
    {"indicator": "X1", "label": "Visits held: over 30 m away", "unit": "%"},
    {"indicator": "X2", "label": "Unreadable tests", "unit": "%"},
    {"indicator": "R1", "label": "Eligible", "unit": "%", "flw_applicable": False},
]


def _w(key, name, x1, x2=0.05, q1=0.55):
    return {
        "key": key,
        "name": name,
        "organisation": "Gwandu partner",
        "ind": {
            "Q1": {"band": "unbanded", "value": q1, "n": 120},
            "X1": {"band": "yellow" if x1 > 0.3 else "green", "value": x1, "n": 73},
            "X2": {"band": "unbanded", "value": x2, "n": 140},
        },
    }


GRADED = {
    "cMeasures": MEASURES,
    "display": {"worker": {"name": "rider"}},
    "byFLW": [
        _w("10::w01", "Sani Abdullahi", 0.0, 0.016),
        _w(ME, "Ibrahim Lawal", 0.42, 0.15),
        _w("10::w03", "Haruna Umar", 0.0, 0.044),
        {"key": "10::reg", "name": "Registration worker", "ind": {"R1": {"band": "unbanded", "value": 0.7, "n": 84}}},
    ],
}
OTHERS = [("Sani Abdullahi", "w01"), ("Haruna Umar", "w03"), ("Registration worker", "reg")]
PEER_IDENTITY = ["Sani", "Abdullahi", "Haruna", "Umar", "w01", "w03", "10::w01", "10::w03", "Gwandu"]


def _history(keys, weeks):
    return [
        {"week": f"2026-09-{d:02d}", "cells": {k: {"X1": {"value": 0.5 - i * 0.05 if k == ME else 0.1}} for k in keys}}
        for i, d in enumerate([6, 13, 20, 27][-weeks:])
    ]


def _build(request, **kw):
    args = dict(
        graded=GRADED,
        worker_key=ME,
        worker_name="Ibrahim Lawal",
        default_topics=["X1"],
        others=OTHERS,
        salt="8478",
        history_loader=_history,
    )
    return chart.build_chart(request, **{**args, **kw})


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def _no_identity(obj):
    text = json.dumps(obj)
    return [term for term in PEER_IDENTITY if term in text]


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


def test_worker_topics_are_the_workers_own_figures():
    [row] = datasets.worker_topics(GRADED, ME, ["X1"])
    assert row["who"] == "You" and row["key"] == "X1"
    assert row["value"] == 0.42 and row["numerator"] == 31 and row["denominator"] == 73
    assert row["figure_text"] == "31 of 73 · 42%"


def test_peers_are_lettered_and_carry_no_identity():
    labels = datasets.peer_labels(GRADED, ME, ["X1"], salt="8478", max_peers=6)
    assert sorted(labels.values()) == ["Peer A", "Peer B"]  # the registration worker has no X1
    assert ME not in labels
    rows = datasets.peers(GRADED, ["X1"], labels)
    assert {r["who"] for r in rows} == {"Peer A", "Peer B"}
    assert _no_identity(rows) == []  # rows never name anyone
    assert all(set(r) == {"i", "key", "label", "unit", "who", "value", "n", "band"} for r in rows)


def test_peer_letters_are_stable_for_a_run_and_not_the_roster_order():
    a = datasets.peer_labels(GRADED, ME, ["X1"], salt="8478", max_peers=6)
    assert a == datasets.peer_labels(GRADED, ME, ["X1"], salt="8478", max_peers=6)
    orders = {tuple(datasets.peer_labels(GRADED, ME, ["X1"], salt=str(s), max_peers=6)) for s in range(20)}
    assert len(orders) == 2  # the hash, not the roster, decides who is A


def test_max_peers_caps_the_peers():
    assert len(datasets.peer_labels(GRADED, ME, ["X1"], salt="1", max_peers=1)) == 1


def test_a_topic_that_is_not_per_worker_is_refused():
    with pytest.raises(ChartError, match="R1"):
        datasets.check_topics(GRADED, ["X1", "R1"])
    with pytest.raises(ChartError, match="NOPE"):
        datasets.check_topics(GRADED, ["NOPE"])


def test_history_rows_label_peers_the_same_way():
    labels = datasets.peer_labels(GRADED, ME, ["X1"], salt="8478", max_peers=6)
    rows = datasets.history(_history([ME, *labels], 4), GRADED, ME, ["X1"], labels)
    assert {r["who"] for r in rows} == {"You", "Peer A", "Peer B"}
    assert [r["t"] for r in rows if r["who"] == "You"] == [0, 1, 2, 3]
    assert _no_identity(rows) == []  # rows never name anyone


def test_load_history_reads_saved_runs_in_scope_up_to_this_run():
    def run(id, end, done=True, opp=10, payload=None):
        snap = {"state": {"report": payload or {"byFLW": [{"key": ME, "ind": {"X1": {"value": id / 10, "n": 5}}}]}}}
        return SimpleNamespace(
            id=id,
            period_end=end,
            is_completed=done,
            opportunity_id=opp,
            program_id=None,
            snapshot=snap,
            completed_at="",
        )

    this = run(5, "2026-09-27")
    wda = MagicMock()
    wda.list_runs.return_value = [
        run(1, "2026-09-06"),
        run(2, "2026-09-13"),
        run(3, "2026-09-20", done=False),  # not completed
        run(4, "2026-09-20", opp=99),  # another scope
        run(6, "2026-10-04"),  # after this run
        this,
    ]
    out = datasets.load_history(wda, this, SimpleNamespace(id=7), [ME], weeks=8, user_id=1)
    assert [h["week"] for h in out] == ["2026-09-06", "2026-09-13", "2026-09-27"]
    assert out[-1]["cells"][ME]["X1"]["value"] == 0.5
    assert datasets.load_history(wda, this, SimpleNamespace(id=7), [ME], weeks=2, user_id=1)[0]["week"] == "2026-09-13"


# ---------------------------------------------------------------------------
# A custom spec: Labs' data only, Connect's look only
# ---------------------------------------------------------------------------


def _bar(**over):
    return {
        "data": {"name": "worker_topics"},
        "mark": "bar",
        "encoding": {"x": {"field": "value", "type": "quantitative"}, "y": {"field": "label", "type": "nominal"}},
        **over,
    }


def test_inline_data_values_and_urls_are_stripped():
    spec = {
        "datasets": {"worker_topics": [{"value": 0.99}]},
        "layer": [
            _bar(),
            {"data": {"values": [{"value": 0.9, "label": "Target"}]}, "mark": "rule", "encoding": {}},
            {"data": {"url": "https://example.com/x.json"}, "mark": "point"},
            {"data": {"name": "worker_topics"}, "mark": "rule", "encoding": {"x": {"datum": 0.8}}},
            {"data": {"name": "peers"}, "mark": {"type": "text", "text": "90%"}},
            {"data": {"name": "peers"}, "mark": "text", "encoding": {"text": {"value": "Target 90%"}}},
            {"data": {"name": "history"}, "transform": [{"calculate": "0.95", "as": "goal"}], "mark": "line"},
            {"data": {"sequence": {"start": 0, "stop": 10}}, "mark": "point"},
        ],
    }
    clean, stripped, used = custom.sanitize(spec)
    text = json.dumps(clean)
    for forged in ("0.99", "0.9,", "example.com", "0.8", "90%", "0.95", "sequence"):
        assert forged not in text, forged
    assert used == {"worker_topics", "peers", "history"}
    assert "datasets" in stripped and any("datum" in p for p in stripped)


def test_the_theme_cannot_be_overridden_from_a_spec():
    spec = _bar(
        config={"background": "#000000"},
        background="#000000",
        width=2000,
        mark={"type": "bar", "color": "#ff00ff", "font": "Comic Sans MS", "fontSize": 4},
    )
    spec["encoding"]["color"] = {"value": "#123456"}
    spec["encoding"]["y"]["scale"] = {"scheme": "magma"}
    spec["title"] = {"text": "Mine", "color": "#00ff00", "fontSize": 90}
    clean, stripped, _ = custom.sanitize(spec)
    text = json.dumps(clean)
    for gone in ("#000000", "#ff00ff", "Comic", "#123456", "magma", "#00ff00", "fontSize", "width"):
        assert gone not in text, gone
    assert clean["title"] == {"text": "Mine"}


def test_a_palette_colour_is_kept():
    clean, _, _ = custom.sanitize(_bar(mark={"type": "bar", "color": theme.CORNFLOWER}))
    assert clean["mark"]["color"] == theme.CORNFLOWER


def test_a_spec_that_reads_no_labs_data_is_refused():
    with pytest.raises(ChartError) as e:
        custom.sanitize({"data": {"values": [{"a": 1}]}, "mark": "bar"})
    assert e.value.code == "no_labs_data"


def test_an_oversized_spec_is_refused():
    with pytest.raises(ChartError) as e:
        custom.sanitize(_bar(description="x" * (custom.MAX_BYTES + 1)))
    assert e.value.code == "spec_too_large"


def test_image_marks_and_links_are_stripped():
    clean, stripped, _ = custom.sanitize(
        {"layer": [_bar(), {"data": {"name": "peers"}, "mark": "image", "encoding": {"url": {"field": "u"}}}]}
    )
    assert "image" not in json.dumps(clean)


# ---------------------------------------------------------------------------
# Building a chart
# ---------------------------------------------------------------------------


def _png_size(c):
    return Image.open(io.BytesIO(chart.png(c))).size


@pytest.mark.parametrize(
    "request_",
    [
        {"type": "topic_bars"},
        {"type": "peer_comparison", "params": {"topics": ["X1", "X2"]}},
        {"type": "trend", "params": {"weeks": 4}},
        {"type": "trend", "params": {"peers": True}},
        {"type": "custom", "spec": _bar(), "params": {"topics": ["X1", "X2", "Q1"]}},
    ],
)
def test_every_kind_of_chart_draws_at_phone_width(request_):
    c = _build(request_)
    width, height = _png_size(c)
    assert width == render.PNG_WIDTH and 300 <= height <= render.MAX_HEIGHT


def test_peer_comparison_shows_peers_only_as_letters_everywhere_a_worker_could_see():
    c = _build({"type": "peer_comparison", "params": {"topics": ["X1", "X2"]}})
    assert {r["who"] for r in c["datasets"]["peers"]} == {"Peer A", "Peer B"}
    assert _no_identity(c) == []  # spec, datasets, caption, alt
    assert "Peer A" in c["caption"] and "rider" in c["caption"]
    assert c["alt"] == c["caption"]


def test_a_custom_spec_that_names_a_peer_is_refused():
    with pytest.raises(ChartError) as e:
        _build({"type": "custom", "spec": _bar(title="You and Haruna")})
    assert e.value.code == "peer_identity"
    with pytest.raises(ChartError):
        _build({"type": "custom", "spec": _bar(title="Compared with w03")})


def test_the_workers_own_name_is_not_a_leak():
    c = _build({"type": "custom", "spec": _bar(title="Ibrahim, your week")})
    assert c["spec"]["title"] == "Ibrahim, your week"


def test_identity_terms_skip_names_that_are_not_peoples_words():
    terms = chart.identity_terms([("Registration worker", "flw_001"), ("Ibrahim Musa", "im")], "Ibrahim Lawal")
    assert "registration worker" in terms and "flw_001" in terms and "musa" in terms
    assert "worker" not in terms and "registration" not in terms and "ibrahim" not in terms


def test_the_charts_numbers_are_labs_not_the_requests():
    c = _build({"type": "custom", "spec": {**_bar(), "datasets": {"worker_topics": [{"value": 0.99}]}}})
    assert c["datasets"]["worker_topics"][0]["value"] == 0.42
    assert "datasets" not in c["spec"]


def test_unknown_types_and_bad_params_are_refused():
    for bad in ({"type": "pie"}, {"type": "trend", "params": {"weeks": 99}}, {"type": "topic_bars", "spec": {}}):
        with pytest.raises(ChartError) as e:
            _build(bad)
        assert e.value.code == "invalid_picture"


def test_a_trend_without_readable_history_is_refused():
    with pytest.raises(ChartError) as e:
        _build({"type": "trend"}, history_loader=None)
    assert e.value.code == "no_history"


def test_a_comparison_with_nobody_to_compare_is_refused():
    lonely = {**GRADED, "byFLW": [GRADED["byFLW"][1]]}
    with pytest.raises(ChartError) as e:
        _build({"type": "peer_comparison"}, graded=lonely)
    assert e.value.code == "no_peers"


def test_the_same_chart_has_the_same_id():
    assert chart.chart_id(_build({"type": "topic_bars"})) == chart.chart_id(_build({"type": "topic_bars"}))
    assert chart.chart_id(_build({"type": "topic_bars"})) != chart.chart_id(_build({"type": "peer_comparison"}))
