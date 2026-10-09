"""From a request for a picture to a frozen chart: what the agent asked to see, with
every number Labs' own.

A request is ``{"type": <a named type>, "params": {...}}`` or ``{"type": "custom",
"spec": <Vega-Lite>, "params": {...}}`` (``PICTURE_SCHEMA``). ``build_chart`` resolves
it against the run -- the topics, Labs' datasets, the spec -- and returns the CHART:

    {"v": 1, "type", "params", "spec", "datasets", "caption", "alt", "notes"}

A chart is complete and frozen: its spec plus its datasets draw the same picture
anywhere (``render.render_png`` today, a webview later), and nothing in it is read
again from the run. The caption and alt text are Labs' words, never the request's.
No text in a chart may name a peer (``identity_leaks``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from connect_labs.workflow import coach_briefing
from connect_labs.workflow.coach_charts import custom, datasets, render, types
from connect_labs.workflow.coach_charts.datasets import ChartError

VERSION = 1
CUSTOM = "custom"
DEFAULT_TYPE = "topic_bars"

PICTURE_SCHEMA = {
    "type": "object",
    "description": (
        "The picture sent with the conversation: a named chart type and its params, or "
        "`type: custom` with a Vega-Lite `spec` that reads Labs' datasets by name. Labs "
        "supplies every number and Connect's theme. Default: {type: topic_bars}."
    ),
    "properties": {
        "type": {"type": "string", "enum": [*types.TYPES, CUSTOM]},
        "params": {"type": "object"},
        "spec": {"type": "object"},
    },
    "additionalProperties": False,
}

_CUSTOM_PARAMS = {
    "type": "object",
    "properties": {
        "topics": {**types._TOPICS, "maxItems": 8},
        "weeks": types._WEEKS,
        "peers": {"type": "boolean", "description": "Give `history` the peers' lines too (default false)."},
        "max_peers": types._PEERS,
    },
    "additionalProperties": False,
}


def normalise_request(request: Any) -> dict:
    """The request in canonical form (``type`` always set), checked against the type's
    params schema. Raises ``ChartError``."""
    import jsonschema

    if request is None:
        request = {"type": DEFAULT_TYPE}
    try:
        jsonschema.validate(request, PICTURE_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ChartError("invalid_picture", f"picture: {e.message}") from e
    kind = request.get("type") or (CUSTOM if "spec" in request else DEFAULT_TYPE)
    params = dict(request.get("params") or {})
    if kind == CUSTOM:
        if "spec" not in request:
            raise ChartError("invalid_picture", "picture: a custom chart needs `spec`")
        schema = _CUSTOM_PARAMS
    else:
        if "spec" in request:
            raise ChartError("invalid_picture", f"picture: `spec` is for type custom, not {kind}")
        schema = types.TYPES[kind].params
    try:
        jsonschema.validate(params, schema)
    except jsonschema.ValidationError as e:
        raise ChartError("invalid_picture", f"picture.params: {e.message}") from e
    out = {"type": kind, "params": params}
    if kind == CUSTOM:
        out["spec"] = request["spec"]
    return out


# ---------------------------------------------------------------------------
# Peer identity
# ---------------------------------------------------------------------------


def identity_terms(people: list[tuple[str, str]], worker_name: str = "") -> list[str]:
    """What would identify a peer, from ``(name, username)`` pairs of everyone the
    worker must not see named: each full name, each username, and each word of a
    person-like name (two or more capitalised words) that is not also in the worker's
    own name."""
    own = {w.lower() for w in (worker_name or "").split()}
    terms: set[str] = set()
    for name, username in people:
        name, username = (name or "").strip(), (username or "").strip()
        if len(name) >= 3:
            terms.add(name.lower())
        if len(username) >= 3:
            terms.add(username.lower())
        words = name.split()
        if len(words) >= 2 and all(w[:1].isupper() for w in words):
            terms.update(w.lower() for w in words if len(w) >= 3 and w.lower() not in own)
    return sorted(terms)


def identity_leaks(text: str, terms: list[str]) -> list[str]:
    """The ``terms`` that appear in ``text`` as whole words (case-insensitive)."""
    low = (text or "").lower()
    return [t for t in terms if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", low)]


def _strings(node: Any):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


# ---------------------------------------------------------------------------
# Building a chart
# ---------------------------------------------------------------------------


def _caption(kind: str, *, first: str, labels: list[str], peers: int, weeks: int, noun: str) -> str:
    whose = f"{first}'s" if first else "the worker's"
    shown = "; ".join(labels)
    others = f"{peers} other {noun}{'' if peers == 1 else 's'} (unnamed: Peer A, Peer B, ...)"
    if kind == "peer_comparison":
        return f"A bar chart of {whose} figures beside {others} for: {shown}."
    if kind == "trend":
        lines = f", with {others}" if peers else ""
        return f"A line chart of {whose} figures week by week over {weeks} weeks{lines} for: {shown}."
    if kind == CUSTOM:
        beside = f" beside {others}" if peers else ""
        return f"A chart of {whose} figures{beside} for: {shown}."
    return f"A bar chart of {whose} figures for: {shown}."


def build_chart(
    request: Any,
    *,
    graded: dict,
    worker_key: str,
    worker_name: str,
    default_topics: list[str],
    others: list[tuple[str, str]],
    salt: str,
    history_loader: Callable[[list[str], int], list[dict]] | None = None,
) -> dict:
    """The frozen chart for ``request`` about ``worker_key``.

    ``graded`` is the run's grading as the viewer reads it; ``default_topics`` the
    indicator keys the briefing raises; ``others`` the ``(name, username)`` of every
    other worker on the run (for the identity check); ``salt`` keys the peer order
    (the run id); ``history_loader(worker_keys, weeks)`` reads saved runs
    (``datasets.load_history``) -- None when the caller may not read them.

    Raises ``ChartError`` for a request that cannot be drawn as asked."""
    req = normalise_request(request)
    kind, params = req["type"], req["params"]
    topics = datasets.check_topics(graded, list(params.get("topics") or default_topics))
    if not topics:
        raise ChartError("no_topics", "nothing to picture: give `params.topics` (indicator keys)")

    notes: list[str] = []
    if kind == CUSTOM:
        spec, stripped, needs = custom.sanitize(req["spec"])
        if stripped:
            notes.append("stripped from the spec (Labs supplies data and the theme): " + ", ".join(stripped[:20]))
    else:
        needs = set(types.TYPES[kind].datasets)
        if kind == "peer_comparison":
            needs.add("peers")
    with_peer_lines = bool(params.get("peers"))

    ds: dict[str, list] = {"worker_topics": datasets.worker_topics(graded, worker_key, topics)}
    labels: dict[str, str] = {}
    if "peers" in needs or ("history" in needs and with_peer_lines):
        labels = datasets.peer_labels(
            graded, worker_key, topics, salt=salt, max_peers=params.get("max_peers") or datasets.DEFAULT_PEERS
        )
    if "peers" in needs:
        ds["peers"] = datasets.peers(graded, topics, labels)
        if not ds["peers"]:
            raise ChartError("no_peers", "no other worker on this run has a figure for these topics")
    weeks = params.get("weeks") or datasets.DEFAULT_WEEKS
    if "history" in needs:
        if history_loader is None:
            raise ChartError("no_history", "this run's saved history cannot be read here")
        keys = [worker_key, *(labels if with_peer_lines else {})]
        runs = history_loader(keys, weeks)
        ds["history"] = datasets.history(runs, graded, worker_key, topics, labels if with_peer_lines else None)
        if not any(r["who"] == datasets.YOU for r in ds["history"]):
            raise ChartError("no_history", "this worker has no figures for these topics in the saved runs")
        weeks = len({r["week"] for r in ds["history"]})
    for name in needs - set(ds):
        ds[name] = []

    first = coach_briefing.first_name(worker_name)
    if kind != CUSTOM:
        spec = types.TYPES[kind].build(ds, first, params)

    peer_count = len(
        {r["who"] for r in ds.get("peers") or []}
        | {r["who"] for r in ds.get("history") or [] if r["who"] != datasets.YOU}
    )
    noun = ((graded.get("display") or {}).get("worker") or {}).get("name") or "worker"
    caption = _caption(
        kind, first=first, labels=[r["label"] for r in ds["worker_topics"]], peers=peer_count, weeks=weeks, noun=noun
    )
    terms = identity_terms(others, worker_name)
    leaked = sorted({t for s in [caption, *_strings(spec)] for t in identity_leaks(s, terms)})
    if leaked:
        raise ChartError(
            "peer_identity", "the chart's text names another worker; peers are shown only as Peer A, B, ..."
        )
    chart = {
        "v": VERSION,
        "type": kind,
        "params": params,
        "spec": spec,
        "datasets": ds,
        "caption": caption,
        "alt": caption,
        "notes": notes,
    }
    try:
        png(chart)  # drawn now, so a chart that cannot be drawn is refused at preview
    except render.RenderError as e:
        raise ChartError("unrenderable", e.message) from e
    return chart


# ---------------------------------------------------------------------------
# Drawing a chart
# ---------------------------------------------------------------------------

PNG_CACHE_SECONDS = 24 * 3600


def chart_id(chart: dict) -> str:
    """A chart's content address: the same chart always has the same id."""
    body = json.dumps({k: chart[k] for k in ("v", "spec", "datasets", "caption")}, sort_keys=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def png(chart: dict) -> bytes:
    """The chart's PNG, cached by content (``chart_id``)."""
    from django.core.cache import cache

    key = f"coach-chart-png:v1:{chart_id(chart)}"
    try:
        hit = cache.get(key)
    except Exception:  # noqa: BLE001 -- a cache outage only costs a redraw
        hit = None
    if hit is not None:
        return hit
    data = render.render_png(chart["spec"], chart["datasets"])
    try:
        cache.set(key, data, PNG_CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return data
