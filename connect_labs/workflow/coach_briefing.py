"""The per-worker briefing a coaching conversation starts from.

When "Start coaching" (a ``start_ocs_outreach`` action) runs on an indicator report,
each worker's conversation is opened with THEIR facts, read from the run's own
grading (``run_grading.py``) -- the same cells the report colours -- rather than the
action's one generic prompt. The text is a contract with the coaching bot, which
parses it (ACE builds that bot; the format is ACE's ``lib/coach-briefing.ts``
``renderBriefing``), so its shape is fixed:

    BRIEFING (system text — do not show to the worker)
    Programme: <registry display title, or the workflow's name>
    Worker: <worker display name, or username>
    Topics, most important first:
    1. <indicator label> [<INDICATOR_ID>] — <numerator> of <denominator> (<pct>%), band red
    2. ...
    Follow your conversation steps from the opening.

Topics are the worker's red indicators, then yellow ones, each in the registry's own
order -- only indicators the registry says make sense per worker
(``meta.flw_applicable`` not false). A worker with none has nothing to be coached on
and is skipped rather than sent a conversation about nothing.

Pure functions: no I/O, so the composition is tested on its own.
"""

from __future__ import annotations

from typing import Any

HEADER = "BRIEFING (system text — do not show to the worker)"
FOOTER = "Follow your conversation steps from the opening."
NOTE_HEADER = "Programme team's note:"

#: The bands a coach raises, most urgent first.
COACHABLE_BANDS = ("red", "yellow")

#: Why a worker is left out of a coaching run, said to the person confirming it.
SKIP_NOTHING_OFF_TARGET = "nothing off target"
SKIP_NOT_GRADED = "not graded on this run"


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _order(measures: list[dict]) -> dict[str, tuple]:
    """Each indicator's place in the registry: its declared ``order``, else its position."""
    out = {}
    for pos, m in enumerate(measures):
        ind = m.get("indicator") or m.get("id")
        if ind and ind not in out:
            order = m.get("order")
            out[ind] = (float(order) if _is_num(order) else float(pos), pos)
    return out


def coachable_topics(payload: dict, cells: dict) -> list[dict]:
    """A worker's topics from their graded ``cells`` (``byFLW[].ind``): red, then
    yellow, each in registry order, ``flw_applicable`` indicators only."""
    measures = [m for m in payload.get("cMeasures") or [] if m.get("indicator") or m.get("id")]
    by_id = {(m.get("indicator") or m.get("id")): m for m in measures}
    order = _order(measures)
    topics = []
    for ind, cell in (cells or {}).items():
        meta = by_id.get(ind)
        if meta is None or meta.get("flw_applicable") is False:
            continue
        band = (cell or {}).get("band")
        if band not in COACHABLE_BANDS:
            continue
        topics.append(_topic(ind, meta, cell))
    topics.sort(key=lambda t: (COACHABLE_BANDS.index(t["band"]), order.get(t["key"], (float("inf"), 0))))
    return topics


def _topic(ind: str, meta: dict, cell: dict) -> dict:
    unit = meta.get("unit") or ""
    value, n = cell.get("value"), cell.get("n")
    topic: dict[str, Any] = {
        "key": ind,
        "label": meta.get("label") or meta.get("title") or ind,
        "band": cell.get("band"),
        "unit": unit,
    }
    if _is_num(cell.get("numerator")) and _is_num(cell.get("denominator")):
        # A cell that carries its own counts: use them as they are.
        topic["numerator"], topic["denominator"] = int(round(cell["numerator"])), int(round(cell["denominator"]))
        if cell["denominator"]:
            topic["pct"] = 100.0 * cell["numerator"] / cell["denominator"]
    elif unit == "%" and _is_num(value) and _is_num(n) and n:
        # A rate cell is a FRACTION over `n` cases (semantic/snapshot.py:grade).
        topic["numerator"], topic["denominator"] = int(round(value * n)), int(n)
        topic["pct"] = 100.0 * value
    elif _is_num(value):
        topic["value"] = value
    return topic


def _figure(t: dict) -> str:
    if "numerator" in t:
        fig = f"{t['numerator']} of {t['denominator']}"
        return fig + (f" ({round(t['pct'])}%)" if "pct" in t else "")
    if "value" in t:
        v = t["value"]
        shown = str(int(round(v))) if abs(v - round(v)) < 1e-9 else f"{v:.1f}"
        unit = t.get("unit") or ""
        return shown + ("" if unit in ("", "n") else unit if unit == "%" else f" {unit}")
    return "figure not given"


def render_briefing(*, programme: str, worker: str, topics: list[dict], note: str | None = None) -> str:
    """The briefing text, in the shape the coaching bot parses."""
    lines = [HEADER, f"Programme: {programme}", f"Worker: {worker}", "Topics, most important first:"]
    for i, t in enumerate(topics, 1):
        lines.append(f"{i}. {t['label']} [{t['key']}] — {_figure(t)}, band {t['band']}")
    lines.append(FOOTER)
    if note and note.strip():
        lines += [NOTE_HEADER, note.strip()]
    return "\n".join(lines)


def fit_briefing(*, programme: str, worker: str, topics: list[dict], note: str | None, limit: int) -> tuple[str, list]:
    """``render_briefing`` within ``limit`` characters, dropping the least important
    topics first (never the first). Returns the text and the topics it kept."""
    kept = list(topics)
    text = render_briefing(programme=programme, worker=worker, topics=kept, note=note)
    while len(text) > limit and len(kept) > 1:
        kept.pop()
        text = render_briefing(programme=programme, worker=worker, topics=kept, note=note)
    return text, kept


def programme_name(payload: dict, definition) -> str:
    """What the briefing calls the programme: the registry's display title, else the
    workflow's own name."""
    title = (payload.get("display") or {}).get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return (getattr(definition, "name", None) or "").strip() or "this programme"
