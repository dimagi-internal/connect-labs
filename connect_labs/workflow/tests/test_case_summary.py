"""The ``case_summary`` picture (Design D): status pills worked out from each panel's
config, the panel order the registry gives, one blue for every chart, and a target line
whose value is a registry constant."""

from __future__ import annotations

import datetime as dt
import io

import pytest

from connect_labs.workflow.coach_charts import case_summary as summary

D = dt.date
SERIES = [
    {"name": "weight", "label": "Weight", "unit": "g", "format": "grams"},
    {"name": "skin_to_skin", "label": "Skin-to-skin", "unit": "h", "format": "hours"},
    {"name": "feeds", "label": "Feeds", "format": "number"},
]
VISITS = [
    {"visit_date": "2026-08-19", "weight": 2000, "feeds": 26},
    {"visit_date": "2026-09-02", "weight": 2200, "feeds": 24, "skin_to_skin": 16},
    {"visit_date": "2026-09-09", "weight": 2200, "feeds": 22, "skin_to_skin": 8},
]
ROW = {"next_visit_start": "2026-09-12", "next_visit_end": "2026-09-16", "next_visit_overdue_days": 23}
WEIGHT = {
    "kind": "series",
    "series": "weight",
    "title": "Weight",
    "reference": {"low": 15, "high": 20, "label": "healthy growth"},
}
VISITS_PANEL = {
    "kind": "visits",
    "title": "Visits",
    "due_from": "next_visit_start",
    "due_to": "next_visit_end",
    "overdue_days": "next_visit_overdue_days",
}
S2S = {"kind": "series", "series": "skin_to_skin", "title": "Skin-to-skin", "floor": {"value": 8, "label": "target"}}
FEEDS = {"kind": "series", "series": "feeds", "title": "Feeds in 24 h", "floor": {"value": 12, "label": "min"}}
RED_GREEN = {"#fde2de", "#b42318", "#e6f5e5", "#1b6b5f", "#e44434", "#00a63e", "#fdece8"}


def _pts(*values, start=D(2026, 8, 19), step=7):
    return [(start + dt.timedelta(days=step * i), float(v)) for i, v in enumerate(values)]


def _state(panels):
    return {"name": "s", "label": "Weight has stalled", "picture": {"type": "case_summary", "panels": panels}}


def _build(panels, row=ROW, visits=VISITS, as_of="2026-10-09"):
    ds, _meta = summary.build(_state(panels), row, visits, SERIES, "Baby Yusuf", as_of=as_of)
    return ds


# ---------------------------------------------------------------------------
# The pill: worked out from the panel's config
# ---------------------------------------------------------------------------


def test_a_series_below_its_reference_band_is_behind_with_its_rate():
    # 2,000 -> 2,200 g over 21 days: 200 / 2000 / 21 * 1000 = 4.8 g/kg/day, under 15.
    pts = [(D(2026, 8, 19), 2000.0), (D(2026, 9, 2), 2200.0), (D(2026, 9, 9), 2200.0)]
    assert summary.series_status(WEIGHT, pts, SERIES[0], {}) == ("bad", "Behind · 4.8 g/kg/day")


def test_a_series_within_its_reference_band_is_on_target():
    pts = [(D(2026, 8, 19), 2000.0), (D(2026, 9, 9), 2800.0)]  # 19 g/kg/day
    assert summary.series_status(WEIGHT, pts, SERIES[0], {}) == ("good", "On target")


def test_a_series_below_its_floor_is_below_target_and_at_or_above_it_on_target():
    assert summary.series_status(S2S, _pts(16, 6), SERIES[1], {}) == ("bad", "Below target")
    assert summary.series_status(S2S, _pts(16, 8), SERIES[1], {}) == ("good", "On target")
    assert summary.series_status(FEEDS, _pts(26, 11), SERIES[2], {}) == ("bad", "Below target")
    assert summary.series_status(FEEDS, _pts(26, 22), SERIES[2], {}) == ("good", "On target")


def test_the_registry_may_word_the_pill():
    panel = {**S2S, "status": {"bad": "Too few hours", "good": "Enough hours"}}
    assert summary.series_status(panel, _pts(9, 4), SERIES[1], {}) == ("bad", "Too few hours")
    assert summary.series_status(panel, _pts(9, 9), SERIES[1], {}) == ("good", "Enough hours")
    ref = {**WEIGHT, "status": {"bad": "Slow: {rate} {unit}"}}
    pts = [(D(2026, 8, 19), 2000.0), (D(2026, 9, 9), 2200.0)]
    assert summary.series_status(ref, pts, SERIES[0], {}) == ("bad", "Slow: 4.8 g/kg/day")


def test_a_ringed_step_is_bad_in_its_own_words_and_a_plain_series_has_no_pill():
    panel = {"kind": "series", "series": "weight", "highlight": {"from": "a", "to": "b", "label": "{step|signed} g?"}}
    assert summary.series_status(panel, _pts(2000, 2300), SERIES[0], {"step": 300}) == ("bad", "+300 g?")
    assert summary.series_status({"kind": "series", "series": "weight"}, _pts(2000, 2300), SERIES[0], {}) is None


def test_visits_past_their_due_window_are_overdue_and_within_it_on_schedule():
    assert summary.visits_status(VISITS_PANEL, ROW, D(2026, 10, 9)) == ("bad", "23 days overdue")
    on_time = {**ROW, "next_visit_overdue_days": 0}
    assert summary.visits_status(VISITS_PANEL, on_time, D(2026, 9, 14)) == ("good", "On schedule")
    # No overdue column: the run date less the window's end.
    panel = {k: v for k, v in VISITS_PANEL.items() if k != "overdue_days"}
    assert summary.visits_status(panel, ROW, D(2026, 9, 17)) == ("bad", "1 day overdue")
    assert summary.visits_status(panel, ROW, D(2026, 9, 16)) == ("good", "On schedule")
    assert summary.visits_status({"kind": "visits"}, ROW, D(2026, 10, 9)) is None


# ---------------------------------------------------------------------------
# The picture
# ---------------------------------------------------------------------------


def _titles(rows) -> list[str]:
    """Each panel's title, read back from its letters, in grid order (top row first)."""
    letters = [
        r for r in rows if r.get("size") == summary.PANEL_TITLE_SIZE * summary.S and r["color"] == summary.ACCENT
    ]
    cells: dict[tuple, list] = {}
    for r in letters:
        cells.setdefault((r["y"], r["x"] > summary.COL_W * summary.S), []).append(r)
    return ["".join(r["text"] for r in sorted(v, key=lambda r: r["x"])) for _k, v in sorted(cells.items())]


def test_the_panels_are_in_the_registrys_order():
    rows = _build([WEIGHT, VISITS_PANEL, S2S, FEEDS])["case_text"]
    assert _titles(rows) == ["WEIGHT", "VISITS", "SKIN-TO-SKIN", "FEEDSIN24H"]
    rows = _build([FEEDS, S2S, VISITS_PANEL, WEIGHT])["case_text"]
    assert _titles(rows) == ["FEEDSIN24H", "SKIN-TO-SKIN", "VISITS", "WEIGHT"]


def test_the_pills_are_the_only_red_or_green():
    rows = _build([WEIGHT, VISITS_PANEL, S2S, FEEDS])["case_text"]
    pills = [r for r in rows if r["kind"] == "rect_pill"]
    assert sorted(r["color"] for r in pills) == ["#e6f5e5", "#e6f5e5", "#fde2de", "#fde2de"]
    texts = {r["text"] for r in rows if r["kind"].startswith("text")}
    assert {"Behind · 4.8 g/kg/day", "23 days overdue", "On target"} <= texts
    pill_text = {"Behind · 4.8 g/kg/day", "23 days overdue", "On target"}
    for r in rows:
        if r["kind"] == "rect_pill" or r.get("text") in pill_text:
            continue
        # The late stretch on the visits timeline is the one tinted band.
        if r["color"] in RED_GREEN:
            assert r["kind"] == "rect_plain" or r.get("text") == "late visit", r
    lines = [r for r in rows if r["kind"] in ("line", "dot", "area")]
    assert not {r["color"] for r in lines} & RED_GREEN


def test_the_skin_to_skin_target_is_a_dashed_line_labelled_at_its_end():
    rows = _build([WEIGHT, VISITS_PANEL, S2S, FEEDS])["case_text"]
    dashed = [r for r in rows if r["kind"] == "line" and r["dash"]]
    assert {r["color"] for r in dashed} == {summary.MUTED}
    assert {r["w"] for r in dashed} == {3 * summary.S}
    # Two target lines (skin-to-skin, feeds), each one segment from end to end.
    assert len({r["seg"] for r in dashed}) == 2
    texts = [r["text"] for r in rows if r["kind"].startswith("text")]
    assert "target" in texts and "8 h" in texts and "min" in texts and "12" in texts
    assert not any("danger" in t for t in texts)


def test_the_series_panels_share_one_frame_of_dates():
    """Skin-to-skin has no reading on 19 Aug, yet 19 Aug is at the same x as in the
    weight panel, and shows a dash."""
    rows = _build([WEIGHT, VISITS_PANEL, S2S, FEEDS])["case_text"]
    dates = [r for r in rows if r.get("text") == "19 Aug" and r["kind"] == "text_center_r"]  # series panels
    xs = sorted({round(r["x"] - (summary.COL_W + summary.CELL_GUTTER) * summary.S * (r["x"] > 300), 3) for r in dates})
    assert len(dates) >= 3 and len(xs) == 1
    assert any(r.get("text") == "–" for r in rows)


def test_the_visits_timeline_runs_to_today_with_the_due_window_and_the_late_stretch():
    rows = _build([VISITS_PANEL])["case_text"]
    texts = [r["text"] for r in rows if r["kind"].startswith("text")]
    assert "today" in texts and "due 12–16 Sep" in texts and "late visit" in texts and "9 Oct" in texts
    assert not any(t in texts for t in ("on time", "late", "sign"))  # no legend
    bands = [r["color"] for r in rows if r["kind"] == "rect_plain"]
    assert summary.LATE in bands and summary.DUE in bands


def test_an_on_time_case_has_no_late_stretch():
    row = {**ROW, "next_visit_overdue_days": 0, "next_visit_start": "2026-10-12", "next_visit_end": "2026-10-16"}
    rows = _build([VISITS_PANEL], row=row)["case_text"]
    assert summary.LATE not in [r["color"] for r in rows if r["kind"] == "rect_plain"]
    assert "On schedule" in [r.get("text") for r in rows]


@pytest.mark.parametrize(
    "panels",
    [[WEIGHT, VISITS_PANEL, S2S, FEEDS], [WEIGHT, {"kind": "list", "title": "Keep going", "items": ["a", "b"]}]],
)
def test_it_draws_at_1200_by_900(panels):
    from PIL import Image

    from connect_labs.workflow.coach_charts import render

    ds = _build(panels)
    png = render.render_png(summary.spec({}), ds)
    assert Image.open(io.BytesIO(png)).size == (1200, 900)


def test_a_short_late_stretch_still_says_late_and_when_the_visit_was_due():
    """Five days overdue: the late stretch is too narrow for its words, and the due
    window sits close to today -- both are still drawn, left of today's tick, without
    overlapping "today"."""
    row = {"next_visit_start": "2026-10-02", "next_visit_end": "2026-10-06", "next_visit_overdue_days": 5}
    visits = [{"visit_date": d} for d in ("2026-09-08", "2026-09-22", "2026-09-29")]
    rows = _build([VISITS_PANEL], row=row, visits=visits, as_of="2026-10-11")["case_text"]
    by_text = {r["text"]: r for r in rows if r["kind"].startswith("text")}
    assert "late visit" in by_text and "due 2–6 Oct" in by_text and "today" in by_text
    due, today = by_text["due 2–6 Oct"], by_text["today"]
    half = lambda t, w=400: summary._width(t, summary.AXIS_SIZE, w) * summary.S / 2  # noqa: E731
    assert due["x"] + half("due 2–6 Oct") <= today["x"] - half("today", 600)
