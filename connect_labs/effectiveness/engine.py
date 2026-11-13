"""Run a Connect CEA model's own formulas, as connect-cea-models exports them.

Each model ships as ``data/<model>.json`` (its ``spec.json``): every input with its
default and source, every Model-tab row as an Excel formula over ``i_<input>`` and
``r_<row>`` names, and the results its builder worked out. Labs evaluates those
formulas rather than re-typing the arithmetic, so a model changes here only when
its spec does (``python manage.py effectiveness_vendor``), and a test proves Labs gets the
spec's own results.

Only arithmetic, comparisons, ``IF``, ``MIN``, ``MAX``, ``SQRT`` and ``ABS`` are
allowed, as in ``tools/export_spec.py`` upstream. A formula is parsed with ``ast``
and walked, never ``eval``-ed.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("data")
MODELS = ("chc", "kc", "mbw")

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_CMP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_FN = {"MIN": min, "MAX": max, "SQRT": math.sqrt, "ABS": abs}


#: Each model's spec file, fixed here: a model name is a key into this table, never part of a path.
_SPEC_FILES = {m: DATA / f"{m}.json" for m in MODELS}


@lru_cache(maxsize=None)
def spec(model: str) -> dict:
    """A model's vendored spec.json."""
    try:
        path = _SPEC_FILES[model]
    except KeyError:
        raise KeyError(f"unknown model {model!r}") from None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def source() -> dict:
    """Which connect-cea-models commit the vendored specs came from."""
    with (DATA / "SOURCE.json").open(encoding="utf-8") as f:
        return json.load(f)


def defaults(model: str) -> dict:
    """The model's own input values (its "Current inputs" column)."""
    return {i["key"]: i["value"] for i in spec(model)["inputs"]}


@lru_cache(maxsize=4096)
def _parse(expr: str):
    e = expr.replace("^", "**").replace("<>", "!=")
    e = re.sub(r"(?<![<>!=])=(?!=)", "==", e)
    return ast.parse(e, mode="eval")


def evaluate(expr: str, names: dict):
    """Evaluate one formula over ``names`` (i_* and r_* values)."""

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name):
            return names[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            return _CMP[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            fn = n.func.id.upper()
            if fn == "IF" and len(n.args) == 3:
                return ev(n.args[1]) if ev(n.args[0]) else ev(n.args[2])
            if fn in _FN:
                return _FN[fn](*[ev(a) for a in n.args])
        raise ValueError(f"unsupported formula element: {ast.dump(n)[:120]}")

    return ev(_parse(expr))


def run(model: str, overrides: dict | None = None, column: str = "model") -> dict:
    """Every Model row for the model's defaults with ``overrides`` applied.

    ``column`` is "model", or "check0"/"check1" for GiveWell's own cases (the grey
    columns), which tests use to prove the formulas give GiveWell's published
    multiples.
    """
    s = spec(model)
    names = {f"i_{k}": v for k, v in {**defaults(model), **(overrides or {})}.items()}
    todo = {}
    for row in s["rows"]:
        if row.get("text"):
            continue
        f = row["formula"] if column == "model" else row["checks"][int(column[-1])]
        if f is None:
            names[f"r_{row['key']}"] = 0  # a blank cell, which a spreadsheet reads as 0
        else:
            todo[row["key"]] = f
    out = {}
    # A result row sits above the rows it reads, so work in dependency order.
    while todo:
        done = []
        for key, f in todo.items():
            try:
                v = f if isinstance(f, (int, float)) else evaluate(f, names)
            except KeyError:
                continue
            names[f"r_{key}"] = out[key] = v
            done.append(key)
        if not done:
            raise ValueError(f"{model}: rows that cannot be worked out: {sorted(todo)}")
        for key in done:
            del todo[key]
    return out


def location_keys(model: str) -> list[str]:
    """The inputs a model sets per place."""
    return [i["key"] for i in spec(model)["inputs"] if i["block"] == "location"]


def country(model: str, iso3: str) -> dict | None:
    """A country's location inputs from the spec (``places``), or None if the model has none for it."""
    return (spec(model).get("places") or {}).get((iso3 or "").upper())
