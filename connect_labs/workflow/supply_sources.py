"""Supply data as a first-class workflow data source, beside pipelines.

A workflow definition declares `supply_sources`:

    [{"alias": "stock", "source": "worker_stock", "item": "rutf"},
     {"alias": "flow",  "source": "stock_flow",   "item": "rutf", "load": "on_demand"}]

and its render code reads `supply.<alias>` -- `{rows, rollup, metadata}` -- or asks
for an on-demand one with `actions.querySupply(alias, args)`. Every source is one
of the supply chain's own READ operations (`SOURCES`), the same ones the supply
pages and the labs MCP call, so a workflow, a page and an agent cannot disagree.
Workflows never write supply data this way; writes become workflow actions.

Scope follows the workflow exactly as pipelines do: every opportunity in
`definition.opportunity_ids` (or the primary), across programmes if the list
spans them. Supply data is held per programme, so each opportunity is resolved
to its programme from the viewer's own org data, and every call runs AS THE
VIEWER through `SupplyDataAccess` -- the same access rule as the supply pages,
so a workflow can show nothing its viewer could not open there. A source scoped
`"opportunity"` (a worker's stock) runs once per opportunity; one scoped
`"program"` (the stores, the orders) runs once per programme, however many of its
opportunities the workflow spans, so a store or an order is never counted twice.

Rows are tagged with `opportunity_id` (None for a programme-scoped row) and
`program_id`. `rollup` is computed here, never in render code: counts sum,
rates are recomputed from the sums (the repo's rule: rates are never summed).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

OPPORTUNITY = "opportunity"
PROGRAM = "program"


@dataclass(frozen=True)
class Source:
    operation: str  # a supply READ operation (operations.py registry)
    scope: str  # OPPORTUNITY | PROGRAM
    rows: str | None  # key of the list in the operation's result; None: the result is the list; "*": one row
    item: bool = False  # takes an item (resolved from the source's `item` slug)
    as_of: bool = False  # accepts as_of
    args: tuple[str, ...] = ()  # further arguments a caller may pass (on-demand)
    params: tuple[str, ...] = ()  # fixed settings a declaration may carry in `params` (e.g. window_days)


# The allow-list. Adding a source is one entry here; nothing else changes.
SOURCES: dict[str, Source] = {
    "worker_stock": Source("worker_stock", OPPORTUNITY, "workers", item=True, as_of=True, params=("window_days",)),
    "worker_stock_get": Source(
        "worker_stock_get",
        OPPORTUNITY,
        "*",
        item=True,
        as_of=True,
        args=("supply_point_id",),
        params=("window_days",),
    ),
    "network_stock": Source("network_stock", PROGRAM, "points", item=True, as_of=True, params=("window_days",)),
    "network_tree": Source("network_tree", PROGRAM, "roots", item=True, as_of=True, params=("window_days",)),
    "stock_flow": Source("stock_flow", PROGRAM, "*", item=True, as_of=True),
    # One forecast per programme, over the workflow's opportunities in it.
    "stock_forecast": Source(
        "stock_forecast",
        PROGRAM,
        "*",
        item=True,
        as_of=True,
        args=("scenario", "horizon_weeks"),
        params=("course_size", "horizon_weeks", "scenario"),
    ),
    "distribution_list": Source("distribution_list", OPPORTUNITY, None),
    "contract_list": Source("contract_list", PROGRAM, None),
    "shipment_list": Source("shipment_list", PROGRAM, None),
    "tender_list": Source("tender_list", PROGRAM, None),
    "supplier_list": Source("supplier_list", PROGRAM, None),
    "checks_list": Source("checks_list", PROGRAM, "checks"),
}


class SupplySourceError(ValueError):
    """A declared source the runtime refuses: unknown, malformed, or asked for an argument it does not take."""


def declared(definition) -> list[dict]:
    data = getattr(definition, "data", None) or {}
    return [s for s in (data.get("supply_sources") or []) if isinstance(s, dict) and s.get("alias")]


def find(definition, alias: str) -> dict:
    for spec in declared(definition):
        if spec["alias"] == alias:
            return spec
    raise SupplySourceError(f"this workflow declares no supply source {alias!r}")


def source_of(spec: dict) -> Source:
    name = spec.get("source")
    if name not in SOURCES:
        raise SupplySourceError(f"{name!r} is not a supply source; one of: {', '.join(sorted(SOURCES))}")
    return SOURCES[name]


def declaration_problems(sources) -> list[str]:
    """What is wrong with a `supply_sources` list, in words; empty when it is fine."""
    if not isinstance(sources, list):
        return ["supply_sources must be a list"]
    problems, aliases = [], set()
    for i, spec in enumerate(sources):
        if not isinstance(spec, dict) or not spec.get("alias"):
            problems.append(f"supply_sources[{i}] needs an alias")
            continue
        if spec["alias"] in aliases:
            problems.append(f"supply_sources alias {spec['alias']!r} is declared twice")
        aliases.add(spec["alias"])
        if spec.get("source") not in SOURCES:
            problems.append(
                f"supply_sources {spec['alias']!r}: source {spec.get('source')!r} is not one of "
                f"{', '.join(sorted(SOURCES))}"
            )
        source = SOURCES.get(spec.get("source"))
        extra = set(spec.get("params") or {}) - set(source.params if source else ())
        if source and extra:
            problems.append(f"supply_sources {spec['alias']!r}: {spec['source']} takes no {', '.join(sorted(extra))}")
        if spec.get("load", "eager") not in ("eager", "on_demand"):
            problems.append(f"supply_sources {spec['alias']!r}: load must be eager or on_demand")
    return problems


# ---- scope -------------------------------------------------------------------


def opportunity_programs(request, opportunity_ids) -> dict[int, int | None]:
    """{opportunity_id: program_id} from the viewer's own org data; None when they hold no such opportunity."""
    from connect_labs.labs.context import get_org_data

    held = {}
    for opp in (get_org_data(request) or {}).get("opportunities", []):
        try:
            held[int(opp["id"])] = int(opp["program"]) if opp.get("program") not in (None, "") else None
        except (KeyError, TypeError, ValueError):
            continue
    return {int(o): held.get(int(o)) for o in opportunity_ids}


def _access(request, program_id, opportunity_id):
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    token = ((request.session.get("labs_oauth") or {}) if hasattr(request, "session") else {}).get("access_token")
    return SupplyDataAccess(
        access_token=token,
        program_id=program_id,
        opportunity_id=opportunity_id,
        caller=Caller(request=request, user=request.user),
    )


def _item_id(access, slug: str | None):
    """The programme's trade item for a commodity slug or SKU (ids differ per programme), or None."""
    from connect_labs.supply_chain.operations import call_operation

    if not slug:
        return None
    for item in call_operation("item_list", access, {}):
        if slug in (item.get("commodity_slug"), item.get("sku")):
            return item["id"]
    raise SupplySourceError(f"programme {access.program_id} has no trade item for {slug!r}")


def _rows(source: Source, result) -> list[dict]:
    if source.rows is None:
        return list(result or [])
    if source.rows == "*":
        return [result] if result else []
    return list((result or {}).get(source.rows) or [])


def run(request, definition, spec: dict, *, opportunity_ids, as_of: date | None = None, args=None) -> dict:
    """One source over the workflow's opportunities: {rows, rollup, metadata}."""
    from connect_labs.supply_chain.operations import call_operation

    source = source_of(spec)
    args = dict(args or {})
    unknown = set(args) - set(source.args)
    if unknown:
        raise SupplySourceError(f"{spec['source']} takes no {', '.join(sorted(unknown))}")
    programs = opportunity_programs(request, opportunity_ids)
    if source.scope == PROGRAM:
        # Once per programme: its first opportunity in the workflow's own order carries the scope check.
        targets, seen = [], set()
        for opp, program in programs.items():
            if program is not None and program in seen:
                continue
            seen.add(program)
            targets.append((opp, program))
    else:
        targets = list(programs.items())

    rows, per_opp = [], {}
    for opp, program in targets:
        key = str(opp)
        if program is None:
            per_opp[key] = {"row_count": 0, "error": f"opportunity {opp} is not accessible to your account"}
            continue
        try:
            access = _access(request, program, opp)
            payload = {**(spec.get("params") or {}), **args}
            if source.item:
                item = _item_id(access, spec.get("item"))
                if item is not None:
                    payload["item_id"] = item
            if source.as_of and as_of is not None:
                payload["as_of"] = as_of.isoformat()
            if source.scope == OPPORTUNITY and spec["source"] in ("worker_stock", "distribution_list"):
                payload["opportunity_id"] = opp
            if spec["source"] == "stock_forecast":
                # Run once for the programme, over the workflow's own opportunities in it.
                payload["opportunity_ids"] = [o for o, p in programs.items() if p == program]
            result = call_operation(source.operation, access, payload)
        except Exception as error:  # noqa: BLE001 -- one opportunity's failure must not sink the rest
            logger.info("supply source %s failed for opportunity %s: %s", spec.get("alias"), opp, error)
            per_opp[key] = {"row_count": 0, "error": str(error)}
            continue
        found = _rows(source, result)
        for row in found:
            if isinstance(row, dict):
                row.setdefault("opportunity_id", opp if source.scope == OPPORTUNITY else None)
                row["program_id"] = program
        rows.extend(found)
        per_opp[key] = {"row_count": len(found), "program_id": program}

    return {
        "rows": rows,
        "rollup": rollup(spec["source"], rows),
        "metadata": {
            "source": spec["source"],
            "scope": source.scope,
            "item": spec.get("item"),
            "as_of": as_of.isoformat() if as_of else None,
            "opportunity_ids": [int(o) for o in opportunity_ids],
            "per_opp": per_opp,
        },
    }


def load(request, definition, *, opportunity_ids, as_of: date | None = None, aliases=None) -> dict:
    """Every eager source (or the named `aliases`): {alias: {rows, rollup, metadata}}."""
    out = {}
    for spec in declared(definition):
        if aliases is not None:
            if spec["alias"] not in aliases:
                continue
        elif spec.get("load") == "on_demand":
            continue
        try:
            out[spec["alias"]] = run(request, definition, spec, opportunity_ids=opportunity_ids, as_of=as_of)
        except SupplySourceError as error:
            out[spec["alias"]] = {"rows": [], "rollup": {}, "metadata": {"error": str(error)}}
    return out


# ---- rollups -------------------------------------------------------------------
#
# Counts sum; rates are recomputed from the sums, never averaged or summed.

RUNWAY = (("out", 0, 0), ("under_week", 0, 7), ("one_to_three_weeks", 7, 21), ("three_to_six_weeks", 21, 42))


def _amount(cell):
    if isinstance(cell, dict):
        cell = cell.get("amount")
    if cell in (None, ""):
        return None
    try:
        value = Decimal(str(cell))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _number(value: Decimal | None):
    if value is None:
        return None
    value = value.quantize(Decimal("0.01"))
    return int(value) if value == value.to_integral_value() else float(value)


def runway_bucket(row) -> str | None:
    """Which runway band a worker's stock sits in: out | under_week | one_to_three_weeks | ... | over_six_weeks."""
    on_hand = _amount(row.get("on_hand"))
    if on_hand is not None and on_hand <= 0:
        return "out"
    days = _amount(row.get("days_to_stockout"))
    if days is None:
        return None
    for name, low, high in RUNWAY[1:]:
        if low <= days < high:
            return name
    return "over_six_weeks"


def _worker_rollup(rows: list[dict]) -> dict:
    sums: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    runway: dict[str, int] = defaultdict(int)
    for row in rows:
        unit = row.get("unit") or ""
        for key in ("on_hand", "issued", "dispensed", "unapproved", "estimated"):
            value = _amount(row.get(key))
            if value is not None:
                sums[unit][key] += value
        runway[runway_bucket(row) or "not_yet"] += 1
    by_unit = {}
    for unit, figures in sums.items():
        # Recomputed, not averaged: the share of all dispensing that rests on unapproved visits.
        dispensed = figures.get("dispensed") or Decimal(0)
        by_unit[unit] = {
            **{key: _number(value) for key, value in figures.items()},
            "unapproved_share": _number(figures["unapproved"] / dispensed * 100) if dispensed else None,
        }
    return {
        "workers": len(rows),
        "by_unit": by_unit,
        "runway": dict(runway),
        "no_answer_visits": sum(int(row.get("no_answer_visits") or 0) for row in rows),
        "never_counted": sum(1 for row in rows if not row.get("reported_on")),
    }


def rollup(source_name: str, rows: list[dict]) -> dict:
    if source_name == "worker_stock":
        return _worker_rollup(rows)
    if source_name in ("network_stock", "network_tree"):
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for row in rows:
            value = _amount(row.get("on_hand"))
            if value is not None:
                sums[(row.get("on_hand") or {}).get("unit") or row.get("unit") or ""] += value
        return {"points": len(rows), "on_hand_by_unit": {u: _number(v) for u, v in sums.items()}}
    return {"rows": len(rows)}
