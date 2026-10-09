"""A case's picture: its story's chart type (``case_types.py``) over datasets Labs builds
from the case's own visits, frozen like every coaching chart (``chart.py``).

Datasets (every number in the picture is one of these):

``case_weights``  one row per weighing: ``t`` (days since the first), ``w`` (g),
                  ``w_label``, ``first`` / ``last``, ``flag`` (a weighing to check)
                  and ``flag_label``;
``case_segments`` the weight line as segments, ``dashed`` into and out of a flagged
                  weighing;
``case_band``     the healthy-growth band from the first weighing: 15 and 20 g/kg/day,
                  compounded daily (``lo``, ``hi``, ``mid``);
``case_skin``     skin-to-skin hours per visit that recorded them;
``case_text``     the panel's words and shapes, positioned in pixels.

The caption is Labs' own and has no numbers: it names the baby's story in plain words.
"""

from __future__ import annotations

import datetime as dt
import math

from connect_labs.workflow import case_coaching as cc
from connect_labs.workflow.coach_charts import case_types, theme
from connect_labs.workflow.coach_charts.datasets import ChartError

VERSION = 1
INNER = case_types.INNER

STORY_TYPES = {
    cc.THRIVING: "case_thriving",
    cc.WEIGHT_CHECK: "case_weight_check",
    cc.FALTERING: "case_faltering",
    cc.DANGER: "case_danger_sign",
}

CAPTIONS = {
    cc.THRIVING: "A growth chart of this baby's weight at each visit, climbing above the healthy growth band.",
    cc.WEIGHT_CHECK: (
        "A growth chart of this baby's weight at each visit, with one weighing circled to check, "
        "and a short checklist for weighing a baby."
    ),
    cc.FALTERING: (
        "A growth chart of this baby's weight, flat below the healthy growth band, "
        "with the skin-to-skin hours at each visit underneath."
    ),
    cc.DANGER: (
        "A card with the danger sign recorded for this baby, that the baby was not referred, " "and what to do next."
    ),
}


def request_type(story: str) -> str:
    return STORY_TYPES[story]


# ---------------------------------------------------------------------------
# Text layout (pixels): lines wrapped in the theme font
# ---------------------------------------------------------------------------


class _Panel:
    def __init__(self):
        self.rows: list[dict] = []
        self.y = 0

    def text(self, text, *, x=0, size=22, bold=False, color=theme.INK, width=None, line=None) -> int:
        from connect_labs.workflow.coach_charts.types import wrap

        line = line or round(size * 1.3)
        lines = wrap(text, size=size, width=width or (INNER - x), max_lines=6)
        for ln in lines:
            self.rows.append(
                {
                    "kind": "text",
                    "x": x,
                    "y": self.y + line / 2,
                    "text": ln,
                    "size": size,
                    "bold": bold,
                    "color": color,
                }
            )
            self.y += line
        return len(lines)

    def dot(self, *, x, y, diameter, color):
        self.rows.append({"kind": "dot", "x": x, "y": y, "area": round(math.pi * (diameter / 2) ** 2), "color": color})

    def ctext(self, text, *, x, y, size=20, color="#ffffff"):
        self.rows.append({"kind": "ctext", "x": x, "y": y, "text": text, "size": size, "color": color})

    def rect(self, *, x, y, x2, y2, color, opacity=1.0):
        self.rows.append({"kind": "rect", "x": x, "y": y, "x2": x2, "y2": y2, "color": color, "opacity": opacity})

    def gap(self, n):
        self.y += n


def _numbered(panel: _Panel, items, *, color=theme.INDIGO, x=0, size=22):
    for n, item in enumerate(items, 1):
        top = panel.y
        lines = panel.text(item, x=x + 46, size=size)
        mid = top + round(size * 1.3) / 2
        panel.dot(x=x + 17, y=mid, diameter=34, color=color)
        panel.ctext(str(n), x=x + 17, y=mid, size=20)
        panel.gap(10 if lines else 0)


def _checklist(panel: _Panel):
    top = panel.y
    panel.gap(18)
    panel.text("Weighing checklist", x=20, size=24, bold=True, color=theme.DEEP_PURPLE)
    panel.gap(8)
    _numbered(panel, cc.WEIGHING_CHECKLIST, x=20)
    panel.gap(8)
    panel.rows.insert(
        0, {"kind": "rect", "x": 0, "y": top, "x2": INNER, "y2": panel.y, "color": theme.RULE, "opacity": 0.55}
    )


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


def _nice_step(span: float) -> int:
    for step in (50, 100, 200, 250, 500, 1000, 2000, 5000):
        if span / step <= 5:
            return step
    return 10000


def _ticks(ts: list[int], max_t: int, min_px: float = 80) -> list[int]:
    """Visit days to label on the x axis, never closer than ``min_px``; the last always."""
    lo, hi = case_types.LEFT_ROOM, INNER - case_types.RIGHT_ROOM
    px = lambda t: lo + (hi - lo) * t / max(max_t, 1)  # noqa: E731
    kept: list[int] = []
    for t in sorted(set(ts)):
        if not kept or px(t) - px(kept[-1]) >= min_px:
            kept.append(t)
    last = max(ts) if ts else 0
    if kept and kept[-1] != last:
        if px(last) - px(kept[-1]) < min_px:
            kept.pop()
        kept.append(last)
    return kept


def _band(w0: float, max_t: int) -> list[dict]:
    rows = []
    for t in range(0, max_t + 1):
        lo, hi = w0 * (1 + cc.HEALTHY_LOW / 1000) ** t, w0 * (1 + cc.HEALTHY_HIGH / 1000) ** t
        rows.append({"t": t, "lo": round(lo), "hi": round(hi), "mid": round((lo + hi) / 2), "end": t == max_t})
    return rows


def build_datasets(case: cc.Case, story: cc.Story) -> tuple[dict, dict]:
    """``(datasets, meta)`` for the case's picture; meta is layout the spec needs."""
    ws = cc.weighings(case)
    meta: dict = {"case_name": case.name or None}
    ds: dict = {"case_weights": [], "case_segments": [], "case_band": [], "case_skin": [], "case_text": []}
    start = ws[0].date if ws else (case.visits[0].date if case.visits else dt.date.today())
    meta["start"] = (start.year, start.month, start.day)
    t_of = lambda d: (d - start).days  # noqa: E731
    max_t = max([t_of(w.date) for w in ws] + [t_of(v.date) for v in case.visits if v.skin_to_skin_h is not None] + [1])
    meta["max_t"] = max_t

    flagged: set[int] = set()
    detail = story.detail or {}
    if story.key == cc.WEIGHT_CHECK:
        lo_i, hi_i = detail.get("from"), detail.get("to")
        flagged = {w.index for w in ws if lo_i is not None and lo_i <= w.index <= hi_i}
        if detail.get("kind") in ("jump", "drop", "range"):
            flagged = {hi_i}
    for n, w in enumerate(ws):
        row = {
            "t": t_of(w.date),
            "w": round(w.grams),
            "w_label": f"{cc.g(w.grams)} g",
            "first": n == 0,
            "last": n == len(ws) - 1,
            "flag": w.index in flagged,
            "flag_label": None,
        }
        ds["case_weights"].append(row)
    if story.key == cc.WEIGHT_CHECK and ds["case_weights"]:
        target = next((r for r, w in zip(ds["case_weights"], ws) if w.index == detail.get("to")), None)
        if target is not None:
            target["flag_label"] = detail.get("label")
            meta["flag_right"] = target["t"] > max_t * 0.5
            meta["flag_dy"] = 56 if detail.get("kind") != "drop" else -56
    # Segments: dashed into and out of a flagged weighing.
    for n in range(len(ws) - 1):
        a, b = ds["case_weights"][n], ds["case_weights"][n + 1]
        dashed = bool(a["flag"] or b["flag"])
        for p in (a, b):
            ds["case_segments"].append({"seg": n, "t": p["t"], "w": p["w"], "dashed": dashed})

    weights = [r["w"] for r in ds["case_weights"]]
    if story.key in (cc.THRIVING, cc.FALTERING) and ws:
        ds["case_band"] = _band(ws[0].grams, max_t)
    band_vals = [r["lo"] for r in ds["case_band"]] + [r["hi"] for r in ds["case_band"]]
    vals = weights + band_vals or [0]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.12, 60)
    step = _nice_step(hi - lo + 2 * pad)
    y_lo = max(0, math.floor((lo - pad) / step) * step)
    y_hi = math.ceil((hi + pad) / step) * step
    meta["y_domain"] = [y_lo, y_hi]
    meta["y_ticks"] = list(range(int(y_lo), int(y_hi) + 1, step))[1:]
    meta["ticks"] = _ticks([r["t"] for r in ds["case_weights"]], max_t)

    recorded = [v for v in case.visits if v.skin_to_skin_h is not None]
    for n, v in enumerate(recorded):
        last = n == len(recorded) - 1
        ds["case_skin"].append(
            {
                "t": t_of(v.date),
                "h": v.skin_to_skin_h,
                "h_label": f"{cc.hours(v.skin_to_skin_h)} h",
                "date_label": cc.day_short(v.date),
                "tone": case_types.SUNSET if last and story.key == cc.FALTERING else theme.CORNFLOWER,
            }
        )
    meta["skin_top"] = max([v.skin_to_skin_h for v in recorded] + [1]) * 1.3

    panel = _Panel()
    if story.key == cc.THRIVING:
        first, last = ws[0], ws[-1]
        panel.rect(x=0, y=0, x2=228, y2=56, color=case_types.GREEN)
        panel.ctext("Great work!", x=114, y=28, size=26)
        panel.rows.append(
            {
                "kind": "text",
                "x": 250,
                "y": 18,
                "text": f"+{cc.g(last.grams - first.grams)} g",
                "size": 30,
                "bold": True,
                "color": case_types.GREEN,
            }
        )
        panel.rows.append(
            {
                "kind": "text",
                "x": 250,
                "y": 46,
                "text": f"since {cc.day_short(first.date)}",
                "size": 20,
                "bold": False,
                "color": theme.MUTED,
            }
        )
        panel.y = 56
    elif story.key == cc.WEIGHT_CHECK:
        _checklist(panel)
    elif story.key == cc.FALTERING:
        panel.gap(4)
        panel.text("Skin-to-skin hours each visit", size=22, bold=True, color=theme.DEEP_PURPLE)
    elif story.key == cc.DANGER:
        _danger_card(panel, case, story)
    meta["text_height"] = max(panel.y, 1)
    ds["case_text"] = panel.rows
    return ds, meta


def _danger_card(panel: _Panel, case: cc.Case, story: cc.Story):
    detail = story.detail or {}
    when = cc.day(dt.date.fromisoformat(detail["date"])) if detail.get("date") else ""
    signs = [cc.SIGN_BY_KEY[s] for s in detail.get("signs") or [] if s in cc.SIGN_BY_KEY]
    panel.rect(x=0, y=0, x2=196, y2=46, color=case_types.SUNSET)
    panel.ctext("NOT REFERRED", x=98, y=23, size=22)
    panel.rows.append(
        {"kind": "text", "x": 212, "y": 23, "text": f"Visit on {when}", "size": 22, "bold": True, "color": theme.INK}
    )
    panel.y = 66
    for sign in signs[:3]:
        top = panel.y
        panel.dot(x=14, y=top + 15, diameter=28, color=case_types.SUNSET)
        panel.ctext("!", x=14, y=top + 15, size=20)
        panel.text(sign.title, x=40, size=24, bold=True)
        panel.text(sign.why, x=40, size=21, color=theme.MUTED)
        panel.gap(12)
    if len(signs) > 3:
        panel.text("Also recorded: " + ", ".join(s.name for s in signs[3:]), size=21, color=theme.MUTED)
        panel.gap(12)
    panel.rect(x=0, y=panel.y, x2=INNER, y2=panel.y + 2, color=theme.RULE)
    panel.gap(20)
    panel.text(cc.DANGER_WHY, size=22, color=theme.INK)
    panel.gap(18)
    panel.text("What to do", size=24, bold=True, color=theme.DEEP_PURPLE)
    panel.gap(8)
    _numbered(panel, cc.DANGER_ACTIONS, color=case_types.SUNSET)


def build_case_chart(case: cc.Case, story: cc.Story, *, others: list[tuple[str, str]] | None = None) -> dict:
    """The frozen chart for ``case`` and ``story``: ``{v, type, params, spec, datasets,
    caption, alt, notes}``. Raises ``ChartError`` when it cannot be drawn."""
    from connect_labs.workflow.coach_charts import chart as charts
    from connect_labs.workflow.coach_charts import render

    kind = request_type(story.key)
    ds, meta = build_datasets(case, story)
    if story.key != cc.DANGER and not ds["case_weights"]:
        raise ChartError("no_weights", "this case has no weighings to draw")
    spec = case_types.CASE_TYPES[kind](meta)
    caption = CAPTIONS[story.key]
    terms = charts.identity_terms(others or [], "")
    leaked = sorted(
        {t for s in [caption, *charts._strings(spec), *charts._strings(ds)] for t in charts.identity_leaks(s, terms)}
    )
    if leaked:
        raise ChartError("peer_identity", "the picture's text names another worker")
    chart = {
        "v": VERSION,
        "type": kind,
        "params": {"case": case.case_id, "story": story.key},
        "spec": spec,
        "datasets": ds,
        "caption": caption,
        "alt": caption,
        "notes": [],
    }
    try:
        charts.png(chart)  # drawn now: a picture that cannot be drawn is refused at preview
    except render.RenderError as e:
        raise ChartError("unrenderable", e.message) from e
    return chart
