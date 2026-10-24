"""How a query's result is shown: the agent's labels, number formats and chart.

An agent that runs ``data_query`` may say how its result reads -- ``visits_per_worker``
as "Visits per worker", a rate as a percent, a time series as a line -- with an
optional ``display`` argument. Labs checks it strictly and keeps it IN the kept
result (``kept_results``), not only in the tool input: a sensitive conversation may
keep no tool input, and the card reads only the kept result.

Shape (every key optional)::

    {
      "columns": {"<result column>": {"label": "Visits per worker", "format": "number_1"}},
      "chart": {"type": "bar" | "line" | "stat", "x": "<column>", "y": ["<column>", ...]},
      "currency": "USD"
    }

* A column named here must be one of the result's columns; ``chart.x`` and every
  ``chart.y`` too (checked once the query has run: ``check_columns``).
* ``format`` comes from ``FORMATS``: ``number_N``, ``percent_N`` (the value is a
  fraction: 0.253 is 25.3%), ``currency_N`` (in ``currency``, an ISO 4217 code,
  default USD), ``compact_N`` (12.3K) or ``date``; N is 0-4 decimal places.
* A label is plain text: control characters are dropped, whitespace collapsed, and it
  is cut at ``MAX_LABEL``. The card escapes it like any other text.
* ``chart.y`` holds at most ``MAX_SERIES`` columns; ``stat`` needs exactly one ``y``
  and no ``x``; ``bar`` and ``line`` need an ``x``.

A display that breaks a rule is refused (``DisplayError``) with what is wrong and
what is allowed, so the agent can fix it rather than have it silently ignored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

#: The formats a column may take. ``_N`` is the number of decimal places, 0-4.
FORMAT_KINDS = ("number", "percent", "currency", "compact")
MAX_DECIMALS = 4
FORMATS = tuple(f"{k}_{n}" for k in FORMAT_KINDS for n in range(MAX_DECIMALS + 1)) + ("date",)
CHART_TYPES = ("bar", "line", "stat")
MAX_LABEL = 60
MAX_SERIES = 5
#: More columns than any result the card draws well; a display naming more is a mistake.
MAX_COLUMNS = 100
DEFAULT_CURRENCY = "USD"

_FORMAT = re.compile(r"^(?:(?:number|percent|currency|compact)_[0-4]|date)$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")

#: The argument's JSON Schema, for the tool's input schema. The rules a schema cannot
#: say (a column must exist, a label is cut) are ``validate`` / ``check_columns``.
SCHEMA = {
    "type": "object",
    "description": (
        "Optional: how the person's card shows the result. `columns` maps a result column to "
        f"{{label, format}}: a plain-text label (max {MAX_LABEL} chars) and a format -- number_N for "
        "counts and amounts (counts: number_0), percent_N for rates (the value must be a FRACTION, 0.253 -> "
        "25.3%), currency_N (in `currency`, ISO 4217, default USD), compact_N (12.3K) or date; N is 0-4 "
        "decimals. `chart` picks the chart: {type: line, x: <date column>, y: [...]} for a value over time; "
        "{type: bar, x: <category>, y: [...]} for categories (the card sorts bars largest first); "
        f"{{type: stat, y: [<column>]}} for one headline number. At most {MAX_SERIES} series; never mix a "
        "percent with a count in one chart. Every column named must be in the result."
    ),
    "properties": {
        "columns": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "maxLength": 200},
                    "format": {"type": "string", "enum": list(FORMATS)},
                },
                "additionalProperties": False,
            },
        },
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": list(CHART_TYPES)},
                "x": {"type": "string"},
                "y": {
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_SERIES},
                    ]
                },
            },
            "required": ["type"],
            "additionalProperties": False,
        },
        "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
    },
    "additionalProperties": False,
}


class DisplayError(ValueError):
    """A ``display`` that breaks a rule. ``str()`` says which, and what is allowed."""


def clean_text(value, limit: int) -> str:
    """``value`` as plain text: no control or bidi characters, whitespace collapsed, cut."""
    text = _CONTROL.sub("", str(value or ""))
    return " ".join(text.split())[:limit].rstrip()


def clean_label(value) -> str:
    """A label as plain text, cut at ``MAX_LABEL``."""
    return clean_text(value, MAX_LABEL)


def validate(display) -> dict | None:
    """``display`` checked for everything but which columns exist, normalised (labels
    cleaned, ``chart.y`` a list), or None when there is none. Raises ``DisplayError``."""
    if display is None:
        return None
    if not isinstance(display, Mapping):
        raise DisplayError("display must be an object: {columns, chart, currency}")
    unknown = set(display) - {"columns", "chart", "currency"}
    if unknown:
        raise DisplayError(f"display has keys it does not take: {sorted(unknown)} (allowed: columns, chart, currency)")
    out: dict = {}
    columns = display.get("columns")
    if columns is not None:
        if not isinstance(columns, Mapping):
            raise DisplayError("display.columns must map a result column to {label, format}")
        if len(columns) > MAX_COLUMNS:
            raise DisplayError(f"display.columns names {len(columns)} columns; at most {MAX_COLUMNS}")
        cleaned = {}
        for name, spec in columns.items():
            if not isinstance(spec, Mapping):
                raise DisplayError(f"display.columns[{name!r}] must be an object: {{label, format}}")
            extra = set(spec) - {"label", "format"}
            if extra:
                raise DisplayError(f"display.columns[{name!r}] has keys it does not take: {sorted(extra)}")
            entry = {}
            if spec.get("label") is not None:
                label = clean_label(spec["label"])
                if label:
                    entry["label"] = label
            fmt = spec.get("format")
            if fmt is not None:
                if not isinstance(fmt, str) or not _FORMAT.match(fmt):
                    raise DisplayError(
                        f"display.columns[{name!r}].format {fmt!r} is not one of: number_N, percent_N, "
                        "currency_N, compact_N (N = 0-4 decimals) or date"
                    )
                entry["format"] = fmt
            if entry:
                cleaned[str(name)] = entry
        if cleaned:
            out["columns"] = cleaned
    chart = display.get("chart")
    if chart is not None:
        out["chart"] = _chart(chart)
    currency = display.get("currency")
    if currency is not None:
        if not isinstance(currency, str) or not _CURRENCY.match(currency):
            raise DisplayError(f"display.currency {currency!r} must be a three-letter ISO 4217 code, e.g. USD or KES")
        out["currency"] = currency
    return out or None


def _chart(chart) -> dict:
    if not isinstance(chart, Mapping):
        raise DisplayError("display.chart must be an object: {type, x, y}")
    extra = set(chart) - {"type", "x", "y"}
    if extra:
        raise DisplayError(f"display.chart has keys it does not take: {sorted(extra)} (allowed: type, x, y)")
    kind = chart.get("type")
    if kind not in CHART_TYPES:
        raise DisplayError(f"display.chart.type {kind!r} is not one of: {', '.join(CHART_TYPES)}")
    y = chart.get("y")
    if isinstance(y, str):
        y = [y]
    if y is not None and (not isinstance(y, Sequence) or not all(isinstance(c, str) and c for c in y)):
        raise DisplayError("display.chart.y must be a column name or a list of them")
    y = list(dict.fromkeys(y or []))
    if len(y) > MAX_SERIES:
        raise DisplayError(f"display.chart.y names {len(y)} series; at most {MAX_SERIES}")
    x = chart.get("x")
    if x is not None and (not isinstance(x, str) or not x):
        raise DisplayError("display.chart.x must be a column name")
    if kind == "stat":
        if len(y) != 1:
            raise DisplayError("a stat chart shows one number: give exactly one column in display.chart.y")
        if x is not None:
            raise DisplayError("a stat chart has no x: leave display.chart.x out")
        return {"type": kind, "y": y}
    if not x:
        raise DisplayError(
            f"a {kind} chart needs display.chart.x: the {'date' if kind == 'line' else 'category'} column"
        )
    if x in y:
        raise DisplayError(f"display.chart.x {x!r} cannot also be a series in display.chart.y")
    out = {"type": kind, "x": x}
    if y:
        out["y"] = y
    return out


def check_columns(display: dict | None, columns: Sequence[str]) -> dict | None:
    """``display`` (already ``validate``-d) against the result's ``columns``: every column
    it names must be one of them. Raises ``DisplayError`` naming the ones that are not."""
    if not display:
        return display
    have = set(columns or ())
    named = list((display.get("columns") or {}).keys())
    chart = display.get("chart") or {}
    if chart.get("x"):
        named.append(chart["x"])
    named.extend(chart.get("y") or [])
    missing = [c for c in dict.fromkeys(named) if c not in have]
    if missing:
        raise DisplayError(
            f"display names columns the result does not have: {missing}. The result's columns are: "
            f"{list(columns or ())}"
        )
    return display
