"""How long after its last new case a registry's figures can still move.

A saved run is graded AS OF its date over every visit up to then. Most of what a
registry computes is a fact of the visits and cannot change once no visit arrives.
What can is anything that reads the report date: in KMC, `days_since_first_visit`,
through the maturity gates (`eligible_28d`, `eligible_42d`) that decide which babies
an indicator counts yet. A baby first visited on day F enters the 28-day indicators
on F+28 and the 42-day ones on F+42 -- and a baby who was never seen after day 28
turns into a loss to follow-up on that date, with no new data at all.

So an opportunity's figures SETTLE: after its latest anchor date plus the largest
window any indicator waits on, every later report repeats the same numbers. The
benchmark publisher uses this to end each trend line where the figures stopped
moving (`benchmarks/publish.py::opportunity_ends`) instead of drawing the repeats
as a flat line out to the end of the axis.

The window is DERIVED from the registry rather than declared beside it, so it
cannot drift from the definitions: add an indicator gated on `eligible_90d` and the
window becomes 90 with no code change. The derivation is deliberately literal:

  1. a property is date-dependent when its SQL reads `:as_of`, or reads a
     date-dependent property;
  2. a window is a constant a date-dependent property is compared against with
     `>=` or `>` (`days_since_first_visit >= :MATURITY_GROWTH_DAYS`);
  3. only properties some indicator actually reaches count -- `eligible_90d`
     exists and no indicator reads it, so it moves nothing yet.

A registry that reads the report date some other way (a comparison this cannot
parse) is reported by `settle_problems`, so it fails loudly in a test rather than
settling at the wrong date.
"""

from __future__ import annotations

import re
from typing import Any

_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_CUBE_REF = re.compile(r"\{CUBE\}\.([A-Za-z_][A-Za-z0-9_]*)")
_MEASURE_REF = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_CONSTANT = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
# `<identifier> >= :CONST` / `<identifier> > :CONST`: the only shape a maturity gate
# takes in the registries this repo ships.
_GATE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*>=?\s*:([A-Za-z_][A-Za-z0-9_]*)")

AS_OF = "as_of"


def _sql_of(item: dict[str, Any]) -> str:
    parts = [str(item.get("sql") or "")]
    for f in item.get("filters") or []:
        if isinstance(f, dict):
            parts.append(str(f.get("sql") or ""))
    return " ".join(parts)


def _definitions(props_doc: dict[str, Any]) -> dict[str, str]:
    """name -> SQL for every aggregate and property: the things SQL can refer to."""
    out: dict[str, str] = {}
    for section in ("aggregates", "properties"):
        for item in (props_doc or {}).get(section) or []:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = str(item.get("sql") or "")
    return out


def _refs(sql: str, names: set[str]) -> set[str]:
    return {m for m in _IDENT.findall(sql or "") if m in names}


def _date_dependent(defs: dict[str, str]) -> set[str]:
    names = set(defs)
    dependent = {n for n, sql in defs.items() if AS_OF in _CONSTANT.findall(sql)}
    changed = True
    while changed:
        changed = False
        for n, sql in defs.items():
            if n not in dependent and _refs(sql, names) & dependent:
                dependent.add(n)
                changed = True
    return dependent


def _direct_refs(indicators_doc: dict[str, Any], names: set[str]) -> set[str]:
    """Aggregates and properties a measure names itself ({CUBE}.x, or bare)."""
    out: set[str] = set()
    for m in (indicators_doc or {}).get("measures") or []:
        if not isinstance(m, dict):
            continue
        sql = _sql_of(m)
        out |= {r for r in _CUBE_REF.findall(sql) if r in names}
        out |= {r for r in _IDENT.findall(_MEASURE_REF.sub(" ", sql)) if r in names}
    return out


def _reached(defs: dict[str, str], start: set[str]) -> set[str]:
    names = set(defs)
    frontier, reached = set(start), set()
    while frontier:
        n = frontier.pop()
        if n in reached:
            continue
        reached.add(n)
        frontier |= _refs(defs[n], names) - reached
    return reached


def _types(props_doc: dict[str, Any]) -> dict[str, str]:
    return {
        str(p["name"]): str(p.get("type") or "")
        for p in (props_doc or {}).get("properties") or []
        if isinstance(p, dict) and p.get("name")
    }


def _windows(props_doc: dict[str, Any], indicators_doc: dict[str, Any]) -> tuple[list[int], list[str]]:
    defs = _definitions(props_doc)
    constants = (props_doc or {}).get("constants") or {}
    dependent = _date_dependent(defs)
    direct = _direct_refs(indicators_doc, set(defs))
    reached = _reached(defs, direct)
    windows: list[int] = []
    problems: list[str] = []
    for name in sorted(reached):
        for left, const in _GATE.findall(defs[name]):
            if left not in dependent:
                continue
            value = constants.get(const)
            try:
                windows.append(int(value))
            except (TypeError, ValueError):
                problems.append(f"{name}: gate constant :{const} is not a whole number of days ({value!r})")
    # A date-dependent value an indicator reads AS A VALUE (not a yes/no gate)
    # changes with every report forever, so there is no date it settles on.
    for name in sorted(direct & dependent):
        if _types(props_doc).get(name) != "bool":
            problems.append(
                f"{name}: an indicator reads this date-dependent value directly, so its figures never settle"
            )
    return windows, problems


def settle_after_days(props_doc: dict[str, Any], indicators_doc: dict[str, Any]) -> int:
    """Days after its anchor date past which no indicator in this registry can change.

    0 when nothing an indicator reaches depends on the report date.
    """
    windows, _problems = _windows(props_doc, indicators_doc)
    return max(windows, default=0)


def settle_problems(props_doc: dict[str, Any], indicators_doc: dict[str, Any]) -> list[str]:
    """Why `settle_after_days` might be wrong for this registry. Empty when it is sound."""
    return _windows(props_doc, indicators_doc)[1]
