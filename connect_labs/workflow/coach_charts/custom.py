"""An agent's own Vega-Lite spec, made safe to draw: Labs' data only, Connect's look only.

When no named type fits, a request may carry a spec of its own. It decides what the
chart SHOWS -- marks, encodings, layers, titles -- and nothing else:

* **Data is Labs'.** A spec reads Labs' datasets by name (``{"data": {"name":
  "peers"}}``) and may not bring its own. Any other ``data`` (``values``, ``url``,
  generators), top-level ``datasets``, constant positions (``datum``), literal text
  marks, ``calculate`` expressions that read no field, and ``params`` are stripped.
  So a chart cannot show a number Labs did not supply.
* **The look is Connect's.** ``config`` and friends are dropped by the renderer
  (``render.themed``); here, font settings are stripped, colours outside the palette
  (``theme.PALETTE``) are stripped, and colour ``scheme``s are stripped, so the
  theme's own ranges apply. Image marks and links are stripped.
* **Bounded.** A spec over ``MAX_BYTES`` of JSON or ``MAX_DEPTH`` deep is refused.

``sanitize`` returns the cleaned spec, the paths it stripped (for the preview, so an
agent sees what was dropped) and the datasets it reads. A spec that reads none of
Labs' datasets is refused: it would show nothing of the worker's.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from connect_labs.workflow.coach_charts import theme
from connect_labs.workflow.coach_charts.datasets import NAMES, ChartError

MAX_BYTES = 20_000
MAX_DEPTH = 24

#: Keys that only style text; the theme sets them.
_FONT_KEYS = re.compile(r"^(font|fontSize|fontWeight|fontStyle|labelFont\w*|titleFont\w*|subtitleFont\w*)$")
#: Keys whose string value is a colour.
_COLOUR_KEYS = re.compile(
    r"^(color|fill|stroke|background|labelColor|titleColor|subtitleColor|gridColor|domainColor|tickColor)$"
)
#: Keys stripped wherever they appear.
_DROP_KEYS = {"datasets", "params", "href", "url", "usermeta", "$schema", "scheme", "config", "selection"}
_ALLOWED_MARKS = {"bar", "line", "area", "point", "circle", "square", "rule", "text", "tick", "rect", "arc", "trail"}


def _depth(node: Any, d: int = 0) -> int:
    if isinstance(node, dict):
        return max([d] + [_depth(v, d + 1) for v in node.values()])
    if isinstance(node, list):
        return max([d] + [_depth(v, d + 1) for v in node])
    return d


def sanitize(spec: Any) -> tuple[dict, list[str], set[str]]:
    """``(clean spec, stripped paths, datasets read)``. Raises ``ChartError`` when the
    spec is not an object, is too large or deep, or reads no Labs dataset."""
    if not isinstance(spec, dict):
        raise ChartError("invalid_spec", "`spec` must be a Vega-Lite object")
    size = len(json.dumps(spec, default=str))
    if size > MAX_BYTES:
        raise ChartError("spec_too_large", f"`spec` is {size} bytes of JSON; at most {MAX_BYTES}")
    if _depth(spec) > MAX_DEPTH:
        raise ChartError("spec_too_deep", f"`spec` nests deeper than {MAX_DEPTH}")
    stripped: list[str] = []
    used: set[str] = set()
    clean = _walk(copy.deepcopy(spec), "", stripped, used)
    for key in ("background", "padding", "autosize", "width"):
        if key in clean:
            clean.pop(key)
            stripped.append(key)
    if not used:
        raise ChartError(
            "no_labs_data",
            "`spec` reads none of Labs' datasets; read them by name: {'data': {'name': ...}}, "
            f"one of {list(NAMES)}",
        )
    return clean, stripped, used


def _walk(node: Any, path: str, stripped: list[str], used: set[str]) -> Any:
    if isinstance(node, list):
        return [_walk(v, f"{path}[{n}]", stripped, used) for n, v in enumerate(node)]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        here = f"{path}.{key}" if path else key
        if key in _DROP_KEYS or _FONT_KEYS.match(key):
            stripped.append(here)
            continue
        if key == "data" or key == "from":
            kept = _data(value, here, stripped, used)
            if kept is not None:
                out[key] = kept
            continue
        if key == "mark":
            kept = _mark(value, here, stripped, used)
            if kept is not None:
                out[key] = kept
            continue
        if key == "datum":
            stripped.append(here)  # a constant position is data the request supplies
            continue
        if key == "encoding" and isinstance(value, dict):
            out[key] = _encoding(value, here, stripped, used)
            continue
        if key == "transform" and isinstance(value, list):
            out[key] = [t for n, t in enumerate(value) if _transform_ok(t, f"{here}[{n}]", stripped)]
            out[key] = [_walk(t, f"{here}[{n}]", stripped, used) for n, t in enumerate(out[key])]
            continue
        if key == "range" and isinstance(value, list):
            if any(isinstance(v, str) and v.startswith("#") and v.lower() not in theme.PALETTE for v in value):
                stripped.append(here)
                continue
        if _COLOUR_KEYS.match(key) and isinstance(value, str):
            if value.lower() not in theme.PALETTE:
                stripped.append(here)
                continue
        out[key] = _walk(value, here, stripped, used)
    return out


def _data(value: Any, path: str, stripped: list[str], used: set[str]) -> dict | None:
    """Only ``{"name": <a Labs dataset>}``, or a lookup's ``{"data": ..., "key", "fields"}``."""
    if isinstance(value, dict) and set(value) == {"name"} and value.get("name") in NAMES:
        used.add(value["name"])
        return {"name": value["name"]}
    if isinstance(value, dict) and "data" in value and set(value) <= {"data", "key", "fields"}:
        inner = _data(value["data"], f"{path}.data", stripped, used)
        return {**value, "data": inner} if inner is not None else None
    stripped.append(path)
    return None


def _mark(value: Any, path: str, stripped: list[str], used: set[str]) -> Any:
    kind = value.get("type") if isinstance(value, dict) else value
    if kind not in _ALLOWED_MARKS:
        stripped.append(path)
        return None
    if isinstance(value, dict):
        kept = _walk(value, path, stripped, used)
        if "text" in kept:
            kept.pop("text")
            stripped.append(f"{path}.text")
        return kept
    return value


def _encoding(enc: dict, path: str, stripped: list[str], used: set[str]) -> dict:
    out = {}
    for channel, value in enc.items():
        here = f"{path}.{channel}"
        if channel in ("href", "url"):
            stripped.append(here)
            continue
        if channel in ("text", "tooltip") and isinstance(value, dict) and "value" in value:
            stripped.append(here)  # literal text could carry a number Labs did not supply
            continue
        if channel in ("color", "fill", "stroke") and _off_palette_value(value):
            stripped.append(here)
            continue
        kept = _walk(value, here, stripped, used)
        if isinstance(kept, dict) and not kept:
            continue
        out[channel] = kept
    return out


def _off_palette_value(value: Any) -> bool:
    """A colour channel set to a literal colour (or a condition's) outside the palette."""
    if not isinstance(value, dict):
        return False
    conditions = value.get("condition")
    conditions = conditions if isinstance(conditions, list) else [conditions] if isinstance(conditions, dict) else []
    literals = [v.get("value") for v in [value, *conditions] if isinstance(v, dict)]
    return any(isinstance(c, str) and c.lower() not in theme.PALETTE for c in literals)


def _transform_ok(t: Any, path: str, stripped: list[str]) -> bool:
    if not isinstance(t, dict):
        stripped.append(path)
        return False
    if "calculate" in t and "datum." not in str(t.get("calculate")):
        stripped.append(path)  # a constant made up as a field
        return False
    if "sequence" in t or "impute" in t:
        stripped.append(path)
        return False
    return True
