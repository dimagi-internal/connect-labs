"""A workflow run, shared with the embedded canopy agent — and read by any agent.

**Sharing** is OFF unless a workflow turns it on, on its own definition:

    "config": {"agent": {"share": True}}

(set per instance with ``workflow_update_definition``; a template MAY declare a
default, which instances inherit on read like every other config key — none does
today). With it on, the run page carries the canopy SDK's agent panel, and canopy
may call the ``workflow_*`` run tools AS the visitor. Without it, those tools refuse
a canopy call; a person's own agent (a PAT or MCP sign-in) is not affected, since it
already reaches every tool.

**Reading** is what this module supplies: the run's grading, trimmed for an agent.
"Red" is decided by the grading the report draws (``semantic/snapshot.py:band_of``),
never guessed by an agent from numbers.

What an agent may DO on a run is not here and not agent-specific: it is the
workflow's own declared actions (``workflow/actions.py``), the same ones its buttons
run.
"""

from __future__ import annotations

from typing import Any

FLW_SEP = "::"


class GradingError(ValueError):
    """A read of a run's grading that cannot be answered as asked."""


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


def shares_with_agent(definition_data: dict | None, template_type: str | None = None) -> bool:
    """Whether a workflow shares its runs with the embedded agent.

    Read through ``with_inherited_config_flags`` so a template default would reach
    instances that predate it, and an instance's own setting — to anything — wins.
    """
    from connect_labs.workflow.templates import with_inherited_config_flags

    data = with_inherited_config_flags(definition_data or {}, template_type)
    raw = (data.get("config") or {}).get("agent")
    return isinstance(raw, dict) and bool(raw.get("share"))


def definition_shares_with_agent(definition) -> bool:
    return shares_with_agent(getattr(definition, "data", None) or {}, getattr(definition, "template_type", None))


# ---------------------------------------------------------------------------
# Worker keys
# ---------------------------------------------------------------------------


def worker_key(opportunity_id, username) -> str:
    """The key a worker is known by everywhere a run names one — the snapshot's
    ``byFLW[].key``, the page state's ``visible_ids``, an action's ``workers``."""
    return f"{opportunity_id}{FLW_SEP}{username}"


def split_worker_key(key: str) -> tuple[int, str]:
    """``(opportunity_id, username)`` from a worker key, or ValueError."""
    opp, sep, username = str(key or "").partition(FLW_SEP)
    if not sep or not username:
        raise ValueError(f"{key!r} is not a worker key (expected '<opportunity_id>::<username>')")
    try:
        return int(opp), username
    except ValueError:
        raise ValueError(f"{key!r} is not a worker key (the part before '::' must be an opportunity id)")


# ---------------------------------------------------------------------------
# Reading a run's grading
# ---------------------------------------------------------------------------

#: Bands a cell can carry (semantic/snapshot.py:grade). Only the first three are a
#: judgement; the rest say why there is no judgement.
BANDS = ("red", "yellow", "green", "insufficient", "notcredible", "notinapp", "unrecorded", "unbanded", "nodata")
BAND_MEANING = {
    "red": "off target -- outside the amber threshold",
    "yellow": "watch -- between the green and amber thresholds",
    "green": "on target",
    "insufficient": "too few cases to grade (below min_denominator)",
    "notcredible": "value shown but this organisation's recording is not credible for it",
    "notinapp": "the app does not ask the questions this indicator needs",
    "unrecorded": "the questions exist but were not recorded",
    "unbanded": "the indicator has no thresholds",
    "nodata": "no value",
}


def graded_payload(snapshot: dict | None) -> dict | None:
    """The graded semantic payload inside a runner-shaped snapshot, or None.

    The ``semantic_snapshot`` builder wraps it as ``{"state": {<state_key>: payload}}``
    (``snapshot_builders.wrap_for_runner``); the key is the spec's, so find the
    payload by its shape rather than assuming a name.
    """
    if not isinstance(snapshot, dict):
        return None
    if "byFLW" in snapshot:
        return snapshot
    for value in (snapshot.get("state") or {}).values():
        if isinstance(value, dict) and "byFLW" in value:
            return value
    return None


def indicator_catalog(payload: dict) -> list[dict]:
    """The indicators a graded payload was graded against, one line each — from its
    own frozen ``cMeasures``, so the thresholds described are the ones the bands were
    computed with."""
    out = []
    for m in payload.get("cMeasures") or []:
        out.append(
            {
                "id": m.get("indicator") or m.get("id"),
                "label": m.get("label") or m.get("title"),
                "plain": m.get("plain"),
                "category": m.get("category"),
                "unit": m.get("unit"),
                "direction": m.get("direction"),
                "bands": m.get("bands"),
                "target": m.get("target"),
                "min_denominator": m.get("min_denominator"),
                "headline": m.get("headline"),
            }
        )
    return out


def _cell(c: dict) -> dict:
    out = {"band": c.get("band"), "value": c.get("value"), "n": c.get("n")}
    if c.get("thinDenominator"):
        out["thin_coverage"] = True
    return out


def select_workers(
    payload: dict,
    *,
    band: str | None = None,
    indicators: list[str] | None = None,
    worker_keys: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Workers from a graded payload, filtered and trimmed for an agent.

    ``band`` keeps a worker when ANY of ``indicators`` (default: every indicator)
    carries that band, and names which in ``matched``. Case indexes and cohort
    fields are dropped: the agent needs the grading, and the worker key to act on.
    """
    if band is not None and band not in BANDS:
        raise GradingError(f"band must be one of {list(BANDS)}")
    wanted = set(indicators or [])
    keys = set(worker_keys or [])
    rows = []
    for f in payload.get("byFLW") or []:
        if keys and f.get("key") not in keys:
            continue
        cells = {k: v for k, v in (f.get("ind") or {}).items() if not wanted or k in wanted}
        matched = sorted(k for k, c in cells.items() if band and c.get("band") == band)
        if band and not matched:
            continue
        row = {
            "key": f.get("key"),
            "username": f.get("username") or f.get("flw"),
            "opportunity_id": f.get("opp"),
            "organisation": f.get("llo"),
            "cases": f.get("n"),
            "reds": f.get("reds"),
            "yellows": f.get("yellows"),
            "indicators": {k: _cell(c) for k, c in cells.items()},
        }
        if band:
            row["matched"] = matched
        rows.append(row)
    rows.sort(key=lambda r: (-len(r.get("matched") or []), -(r.get("reds") or 0), r.get("key") or ""))
    return {"total": len(rows), "truncated": len(rows) > limit, "workers": rows[:limit]}


def scope_rows(payload: dict, scope: str, indicators: list[str] | None = None) -> list[dict]:
    """Programme / organisation / opportunity rows, cells trimmed like ``select_workers``."""
    wanted = set(indicators or [])

    def trim(ind: dict) -> dict:
        return {k: _cell(c) for k, c in (ind or {}).items() if not wanted or k in wanted}

    if scope == "programme":
        return [{"scope": "programme", "indicators": trim(payload.get("programInd") or {})}]
    source = {"organisation": "byLLO", "opportunity": "byOpp"}.get(scope)
    if source is None:
        raise GradingError("scope must be one of worker, organisation, opportunity, programme")
    out = []
    for r in payload.get(source) or []:
        row = {k: r.get(k) for k in ("key", "llo", "opp", "name", "n", "reds", "yellows") if r.get(k) is not None}
        row["indicators"] = trim(r.get("ind") or {})
        out.append(row)
    return out
