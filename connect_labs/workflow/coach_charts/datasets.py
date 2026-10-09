"""Labs' named datasets: every number a coaching chart shows, read from the run.

A chart never carries data of its own. Whatever a request asks for -- a named type or
an agent's own spec -- reads these, resolved here from the run's grading (the same
cells ``workflow_run_indicators`` returns) and from the workflow's saved runs:

``worker_topics``  the worker's own figures, one row per topic:
                   ``i, key, label, label_lines, unit, band, band_key, value, n,
                   numerator, denominator, pct, share, bar, figure_text, who``
                   (``who`` is ``"You"``);
``peers``          the same topics for OTHER workers on this run, one row per peer
                   and topic: ``i, key, label, unit, who, value, n, band``. ``who`` is
                   ``Peer A``, ``Peer B``, ... -- never a name or username;
``history``        week by week (one point per saved run, up to this run):
                   ``week, t, i, key, label, unit, who, value, n`` (``t`` counts weeks from 0) for the worker
                   (``who: "You"``) and, when asked, the peers (same labels).

PEERS ARE ANONYMOUS. A peer is labelled by a letter, assigned in an order that is a
keyed hash of the run and the worker key -- stable for one chart, revealing nothing
about who is who -- and nothing else of theirs (name, username, organisation, key)
enters a dataset. Only peers in the grading the viewer can read are used, so a chart
shows nobody the viewer could not see on the report. No team average is computed.

Pure functions apart from ``load_history`` (which reads saved runs as the viewer).
"""

from __future__ import annotations

import hashlib
import logging
import string
from typing import Any

from connect_labs.workflow import coach_briefing
from connect_labs.workflow.coach_charts import types

logger = logging.getLogger(__name__)

YOU = "You"
MAX_PEERS = 12
DEFAULT_PEERS = 6
MAX_WEEKS = 26
DEFAULT_WEEKS = 8

#: Which datasets a request may name.
NAMES = ("worker_topics", "peers", "history")


class ChartError(ValueError):
    """A chart that cannot be drawn as asked. ``code`` is stable; the message is for a person."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.public_message = message
        super().__init__(message)


def peer_label(n: int) -> str:
    """``Peer A`` ... ``Peer Z``, then ``Peer AA`` (never reached: at most ``MAX_PEERS``)."""
    letters = string.ascii_uppercase
    return "Peer " + (letters[n] if n < 26 else letters[n // 26 - 1] + letters[n % 26])


def _measures(graded: dict) -> dict[str, dict]:
    return {
        (m.get("indicator") or m.get("id")): m
        for m in graded.get("cMeasures") or []
        if m.get("indicator") or m.get("id")
    }


def check_topics(graded: dict, keys: list[str]) -> list[str]:
    """``keys`` as indicator keys of this run that make sense per worker, in order and
    without repeats. Raises ``ChartError`` naming any that are not."""
    measures = _measures(graded)
    out, bad = [], []
    for key in keys:
        meta = measures.get(key)
        if meta is None or meta.get("flw_applicable") is False:
            bad.append(key)
        elif key not in out:
            out.append(key)
    if bad:
        per_worker = sorted(k for k, m in measures.items() if m.get("flw_applicable") is not False)
        raise ChartError("unknown_topic", f"not per-worker indicators of this run: {bad}; known: {per_worker}")
    return out


def _cell_figure(meta: dict, key: str, cell: dict) -> dict:
    """A cell as a topic (``coach_briefing._topic``): label, band, unit, and its counts
    or value."""
    return coach_briefing._topic(key, meta, cell or {})


def _value(cell: dict | None) -> float | None:
    v = (cell or {}).get("value")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def worker_topics(graded: dict, worker_key: str, topics: list[str]) -> list[dict]:
    """The worker's own rows. A topic the worker has no figure for is still a row (its
    figure says so), so the chart shows what was asked for."""
    measures = _measures(graded)
    row = next((f for f in graded.get("byFLW") or [] if f.get("key") == worker_key), None)
    if row is None:
        raise ChartError("not_graded", "this worker is not graded on this run")
    cells = row.get("ind") or {}
    out = []
    for i, key in enumerate(topics):
        meta, cell = measures[key], cells.get(key) or {}
        topic = _cell_figure(meta, key, cell)
        [shown] = types.topic_rows([_figure_topic(topic)])
        shown.update(
            i=i,
            key=key,
            unit=meta.get("unit") or "",
            value=_value(cell),
            n=cell.get("n"),
            who=YOU,
        )
        out.append(shown)
    return out


def _figure_topic(topic: dict) -> dict:
    """A topic as ``types.topic_rows`` draws it: counts when it has them, else its words."""
    if "numerator" in topic:
        return {**topic, "pct": topic.get("pct")}
    figure = coach_briefing.topic_figure(topic)
    return {**topic, "figure": "no figure" if figure == "figure not given" else figure}


def _peer_order(salt: str, keys: list[str]) -> list[str]:
    return sorted(keys, key=lambda k: hashlib.sha256(f"{salt}:{k}".encode()).hexdigest())


def peer_labels(graded: dict, worker_key: str, topics: list[str], *, salt: str, max_peers: int) -> dict[str, str]:
    """``{worker key: "Peer A", ...}`` for up to ``max_peers`` other workers with a figure
    on at least one of ``topics``, lettered in a keyed-hash order. The mapping never
    leaves Labs: datasets carry only the labels."""
    candidates = []
    for f in graded.get("byFLW") or []:
        key = f.get("key")
        if not key or key == worker_key:
            continue
        cells = f.get("ind") or {}
        if any(_value(cells.get(t)) is not None for t in topics):
            candidates.append(key)
    chosen = _peer_order(salt, candidates)[: max(0, min(max_peers, MAX_PEERS))]
    return {key: peer_label(n) for n, key in enumerate(chosen)}


def peers(graded: dict, topics: list[str], labels: dict[str, str]) -> list[dict]:
    measures = _measures(graded)
    by_key = {f.get("key"): f for f in graded.get("byFLW") or []}
    out = []
    for worker_key, label in sorted(labels.items(), key=lambda kv: (len(kv[1]), kv[1])):
        cells = (by_key.get(worker_key) or {}).get("ind") or {}
        for i, key in enumerate(topics):
            cell = cells.get(key) or {}
            value = _value(cell)
            if value is None:
                continue
            meta = measures[key]
            out.append(
                {
                    "i": i,
                    "key": key,
                    "label": str(meta.get("label") or meta.get("title") or key),
                    "unit": meta.get("unit") or "",
                    "who": label,
                    "value": value,
                    "n": cell.get("n"),
                    "band": cell.get("band"),
                }
            )
    return out


def history(
    runs: list[dict], graded: dict, worker_key: str, topics: list[str], labels: dict[str, str] | None
) -> list[dict]:
    """``history`` rows from ``runs`` (``load_history``: ``[{"week", "cells": {worker
    key: {indicator: cell}}}]``, oldest first) for the worker and, when ``labels`` is
    given, those peers."""
    measures = _measures(graded)
    who = {worker_key: YOU, **(labels or {})}
    out = []
    for t, run in enumerate(runs):
        for key, name in who.items():
            cells = (run.get("cells") or {}).get(key) or {}
            for i, topic in enumerate(topics):
                value = _value(cells.get(topic))
                if value is None:
                    continue
                meta = measures[topic]
                out.append(
                    {
                        "week": run["week"],
                        "t": t,
                        "i": i,
                        "key": topic,
                        "label": str(meta.get("label") or meta.get("title") or topic),
                        "unit": meta.get("unit") or "",
                        "who": name,
                        "value": value,
                        "n": (cells.get(topic) or {}).get("n"),
                    }
                )
    return out


# ---------------------------------------------------------------------------
# Saved runs (I/O)
# ---------------------------------------------------------------------------

#: How long one viewer's read of a workflow's saved-run history is reused.
HISTORY_CACHE_SECONDS = 600


def load_history(wda, run, definition, worker_keys: list[str], *, weeks: int, user_id: int) -> list[dict]:
    """Up to ``weeks`` completed runs of the workflow, ending with this run's period,
    in this run's own scope, oldest first: ``[{"week": period_end, "cells": {worker
    key: {indicator: {value, n, band}}}}]`` for ``worker_keys`` only.

    Read as the viewer (``wda`` is theirs), from each run's stored snapshot -- the
    week as it was published. Cached briefly per viewer, keyed by the workflow's
    history version (``history_cache``), so a newly completed run is seen at once."""
    from django.core.cache import cache

    from connect_labs.workflow import history_cache
    from connect_labs.workflow.agent_sharing import graded_payload

    weeks = max(1, min(int(weeks), MAX_WEEKS))
    scope = (getattr(run, "opportunity_id", None), getattr(run, "program_id", None))
    wanted = sorted(set(worker_keys))
    digest = hashlib.sha1(",".join(wanted).encode()).hexdigest()[:12]
    key = (
        f"coach-chart-history:v1:{user_id}:{definition.id}:v{history_cache.version(definition.id)}:"
        f"{scope[0]}:{scope[1]}:{run.id}:{weeks}:{digest}"
    )
    hit = cache.get(key)
    if hit is not None:
        return hit
    end = str(run.period_end or "")
    saved = [
        r
        for r in wda.list_runs(definition_id=definition.id)
        if r.is_completed
        and (getattr(r, "opportunity_id", None), getattr(r, "program_id", None)) == scope
        and r.period_end
        and (not end or str(r.period_end) <= end)
    ]
    if run.is_completed and all(r.id != run.id for r in saved):
        saved.append(run)
    saved.sort(key=lambda r: (str(r.period_end), str(getattr(r, "completed_at", "") or "")))
    by_week: dict[str, Any] = {}
    for r in saved:
        by_week[str(r.period_end)] = r  # the latest completed run of a week wins
    out = []
    for week in sorted(by_week)[-weeks:]:
        payload = graded_payload(by_week[week].snapshot) or {}
        rows = {f.get("key"): f for f in payload.get("byFLW") or []}
        cells = {
            k: {ind: {f: c.get(f) for f in ("value", "n", "band")} for ind, c in (rows[k].get("ind") or {}).items()}
            for k in wanted
            if k in rows
        }
        out.append({"week": week, "cells": cells})
    cache.set(key, out, HISTORY_CACHE_SECONDS)
    return out
