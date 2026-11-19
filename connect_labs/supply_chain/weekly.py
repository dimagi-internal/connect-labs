"""The weekly-cycle rule: an FLW is flagged only when they cannot cover the visits due before their next handout.

Sophie Feintuch, round 3: "Ending the week at or near zero is the plan, not a problem. FLWs get at most a week's
supply ... So 'runs out within a week', 'no stock on hand' or 'below minimum' at the end of a week says nothing.
Flag an FLW only if they don't hold enough for the visits due before their next restock." The months-based bands
(a minimum of half a month, a maximum of one and a half) flag every FLW every week, so they are not used for an FLW.

The rule is the plan's own (`plan.service.issue_rows`: what the FLW holds against what the children due a visit before
the next handout, the children expected and the appetite tests will need), so Stock by LLO, This week and the Overview
say exactly what the plan says. One function reads it for all of them, and a store's run-out day is the plan's too:
there is one run-out figure per FLW and per store.
"""

from __future__ import annotations

from datetime import date

from connect_labs.supply_chain.models import DispensingRule


def plan_picture(program_id, *, memo=None) -> dict | None:
    """The planning picture for the program's product (the first one a dispensing rule gives out), or None.

    `memo` is a dict kept for one request, so a page that asks twice computes once.
    """
    key = ("weekly-picture", program_id)
    if memo is not None and key in memo:
        return memo[key]
    from connect_labs.supply_chain.plan import service

    rule = (
        DispensingRule.objects.filter(program_id=program_id, status="active")
        .select_related("item")
        .order_by("pk")
        .first()
    )
    picture = None
    if rule is not None:
        picture = service.compute(program_id, rule.item)
        picture.pop("forecast", None)
        picture["item_id"] = rule.item_id
    if memo is not None:
        memo[key] = picture
    return picture


def _whole(value) -> int:
    return int(round(float(value))) if value is not None else 0


def shortfall_words(row: dict, next_handout: str = "") -> str:
    """ "8 children due before the next handout: needs 87, holds 83, 4 short" for one FLW's plan row."""
    need, held = _whole(row.get("need_week")), _whole(row.get("held"))
    children = row.get("children")
    due = f"{children} children due before the next handout{next_handout}: " if children else ""
    if not row.get("stock_known"):
        return f"{due}needs {need}; what they hold is not known"
    if row.get("short"):
        return f"{due}needs {need}, holds {held}, {need - held} short"
    return f"{due}needs {need}, holds {held}"


def flw_status(program_id, *, memo=None) -> dict:
    """{supply point id: {...}} for every FLW the plan reads, by the weekly rule.

    name, short (bool), need, held, short_by, children, known, window_days, to_issue, words.
    """
    picture = plan_picture(program_id, memo=memo)
    if picture is None:
        return {}
    next_on = (picture.get("handout") or {}).get("next_on")
    after = ""
    if next_on:
        from connect_labs.supply_chain.values import day_text

        after = f" ({day_text(date.fromisoformat(next_on))})"
    out = {}
    for row in picture["issue"]:
        need, held = _whole(row.get("need_week")), _whole(row.get("held"))
        short = bool(row.get("short")) and bool(row.get("stock_known"))
        out[row["supply_point_id"]] = {
            "id": row["supply_point_id"],
            "name": row["name"],
            "short": short,
            "need": need,
            "held": held,
            "short_by": max(0, need - held) if short else 0,
            "children": row.get("children"),
            "known": bool(row.get("stock_known")),
            "window_days": row.get("window_days"),
            "to_issue": row.get("to_issue") or 0,
            "words": shortfall_words(row, after),
        }
    return out


def short_by_org(program_id, owner: dict, *, memo=None) -> dict:
    """{org id: [the FLWs short by the weekly rule]}, `owner` being {supply point id: org id}."""
    out: dict = {}
    for pk, row in flw_status(program_id, memo=memo).items():
        if row["short"]:
            out.setdefault(owner.get(pk), []).append(row)
    return out


def store_runs_dry(program_id, *, memo=None) -> dict:
    """{supply point id: the plan's store row, whose run-out words ("runs dry 13 Dec") the pages share}."""
    picture = plan_picture(program_id, memo=memo)
    if picture is None:
        return {}
    return {s["supply_point_id"]: s for s in picture["stores"]}
