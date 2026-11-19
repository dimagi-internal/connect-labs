"""One local partner (LLO) at a time: how much it has, and what is to be handed out this week.

Sophie Feintuch's round 3 ("organise Supply around workflows"), step 2. Knowing how much each LLO has meant moving
between Stock, Network, Map, Workers, Stock review, Movements and Where it went; the weekly handout meant Plan,
Distribution, Stock review and Workers. Here each is one page per LLO ("Stock by LLO", "This week"), read from the
figures those pages already read (`partners.status`, `stock.services.network`, `stock.services.issues`), so a figure
here cannot differ from the page it links to.

Read only. Every function takes the program, and a `visible` set of supply point ids when the viewer sees only some
of them (a partner under read enforcement); None means all.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from django.urls import reverse

from connect_labs.supply_chain import weekly
from connect_labs.supply_chain.models import Contract, Distribution, Shipment, StockIssue, SupplyPoint
from connect_labs.supply_chain.partners.status import partner_places, partner_status
from connect_labs.supply_chain.stock.services import issues as issue_services
from connect_labs.supply_chain.stock.services import network
from connect_labs.supply_chain.values import Quantity, day_text

# A handout still counts as "this week" for a fortnight after its day: a run left unconfirmed longer than that is a
# flag to follow up, not a handout of the week.
WEEK = 7
MOVING = ("delivered", "lost")


def _owner(program_id) -> dict:
    return issue_services.partner_by_point(program_id)


def _names(org_ids) -> dict:
    from connect_labs.labs.models import LabsOrg

    return dict(LabsOrg.objects.filter(pk__in=[o for o in org_ids if o]).values_list("pk", "name"))


def _stock_text(row) -> str:
    on_hand = row.get("on_hand")
    if isinstance(on_hand, Quantity):
        return f"{on_hand.amount:,.0f} {on_hand.unit or ''}".strip()
    return "no stock recorded"


def _next_deliveries(program_id, org_ids, today) -> dict:
    """{org id: the earliest day a shipment for one of its orders is expected, as a date}, for goods on the way."""
    contracts = {
        c.pk: c.partner_org_id or c.buyer_org_id
        for c in Contract.objects.filter(program_id=program_id).exclude(status="cancelled")
    }
    found: dict = {}
    for shipment in Shipment.objects.filter(contract__program_id=program_id, contract_id__in=list(contracts)).exclude(
        status__in=MOVING
    ):
        org = contracts.get(shipment.contract_id)
        if org in org_ids and shipment.expected_on and (org not in found or shipment.expected_on < found[org]):
            found[org] = shipment.expected_on
    return found


def _runs(program_id, point_ids):
    """The handout runs out of any of these points, newest first, with their lines."""
    return list(
        Distribution.objects.filter(program_id=program_id, supply_point_id__in=point_ids)
        .select_related("supply_point", "commodity")
        .prefetch_related("lines")
    )


def _unconfirmed(run) -> bool:
    return run.status != "confirmed"


def llo_rows(program_id, *, visible=None, today=None, memo=None) -> list[dict]:
    """One row per LLO that runs something: what it holds, its FLWs, open issues, the next delivery."""
    today = today or date.today()
    owner = _owner(program_id)
    status = partner_status(program_id, memo=memo)
    places = partner_places(program_id)
    org_ids = {o for pk, o in owner.items() if o and (visible is None or pk in visible)}
    names = _names(org_ids)
    deliveries = _next_deliveries(program_id, org_ids, today)
    by_org_points = defaultdict(set)
    for pk, org in owner.items():
        if org in org_ids:
            by_org_points[org].add(pk)
    runs_by_org = {org: _runs(program_id, pts) for org, pts in by_org_points.items()}
    rows = []
    for org in sorted(org_ids, key=lambda o: (names.get(o) or "").lower()):
        st = status.get(org, {})
        unconfirmed = [r for r in runs_by_org.get(org, []) if _unconfirmed(r)]
        delivery = deliveries.get(org)
        rows.append(
            {
                "org_id": org,
                "name": names.get(org) or f"Organization {org}",
                "stock_text": st.get("stock_text") or "no stock recorded",
                "locations": len([p for p in places.get(org, []) if visible is None or p["id"] in visible]),
                "flws": st.get("workers", 0),
                "flws_low": st.get("workers_running_out", 0),
                "open_issues": st.get("open_issues", 0),
                "next_delivery": day_text(delivery) if delivery else "",
                "unconfirmed_handouts": len(unconfirmed),
                "url": reverse("supply_chain:llo_stock", args=[org]),
                "week_url": reverse("supply_chain:llo_week", args=[org]),
            }
        )
    return rows


def roll_up(rows) -> dict:
    """What the programme as a whole holds across LLOs, for the funder's picture (top of Stock by LLO)."""
    return {
        "llos": len(rows),
        "flws": sum(r["flws"] for r in rows),
        "flws_low": sum(r["flws_low"] for r in rows),
        "open_issues": sum(r["open_issues"] for r in rows),
        "unconfirmed_handouts": sum(r["unconfirmed_handouts"] for r in rows),
    }


def llo_detail(program_id, org_id, *, visible=None, today=None, memo=None) -> dict | None:
    """One LLO's stock page: its RUTF stock locations and FLWs with what each holds, open issues, deliveries due."""
    today = today or date.today()
    owner = _owner(program_id)
    mine = {pk for pk, o in owner.items() if o == org_id and (visible is None or pk in visible)}
    if not mine:
        return None
    name = _names({org_id}).get(org_id) or f"Organization {org_id}"
    stock = {r["supply_point_id"]: r for r in network.network_stock(program_id) if r["supply_point_id"] in mine}
    kinds = dict(SupplyPoint.objects.filter(program_id=program_id, pk__in=mine).values_list("pk", "kind"))
    # One run-out figure per store and per FLW: the plan's (weekly.py), never a second one from months of stock.
    plan_stores = weekly.store_runs_dry(program_id, memo=memo)
    places = []
    for place in partner_places(program_id).get(org_id, []):
        if place["id"] not in mine:
            continue
        row = stock.get(place["id"], {})
        plan = plan_stores.get(place["id"]) or {}
        places.append(
            {
                "id": place["id"],
                "name": place["name"],
                "kind": place["kind"],
                "holds": _stock_text(row) if row else "no stock recorded",
                "runs_dry": plan.get("runs_dry_text") or "",
                "keeper": place["custodian"].name if place["custodian"] is not None else "",
                "send_url": reverse("supply_chain:consignment_dispatch") + f"?from_supply_point={place['id']}",
            }
        )
    status = weekly.flw_status(program_id, memo=memo)
    flws = []
    for pk, row in sorted(stock.items(), key=lambda kv: kv[1]["name"].lower()):
        if kinds.get(pk) != "user_held":
            continue
        w = status.get(pk)
        flws.append(
            {
                "id": pk,
                "name": row["name"],
                "holds": f"{w['held']:,} sachets" if w and w["known"] else _stock_text(row),
                "needs": f"{w['need']:,} sachets" if w else "",
                "low": bool(w and w["short"]),
                "status": (f"short by {w['short_by']:,}" if w["short"] else "covered") if w and w["known"] else "",
                "words": w["words"] if w else "",
                "url": reverse("supply_chain:worker_detail", args=[pk]),
            }
        )
    restocking = [
        {
            "name": s["name"],
            "holds": f"{s['own']:,.0f} {s['unit']}".strip(),
            "runs_dry": s["runs_dry_text"],
            "next_delivery": day_text(date.fromisoformat(s["next_delivery"])) if s.get("next_delivery") else "",
            "lasts": s["lasts_until_delivery"],
            "send": f"{s['send']:,.0f} {s['unit']}".strip() if s.get("send_needed") else "",
        }
        for s in plan_stores.values()
        if s.get("org_id") == org_id and s["supply_point_id"] in mine
    ]
    issues = [
        {
            "id": i.pk,
            "kind": i.kind,
            "where": i.supply_point.name,
            "quantity": f"{i.quantity:,.0f} {i.quantity_unit}".strip(),
            "status": "being followed up" if i.status == "following_up" else "not yet looked at",
            "days": (today - i.occurred_on).days,
            "taken_by": i.taken_by,
        }
        for i in StockIssue.objects.filter(program_id=program_id, supply_point_id__in=mine)
        .exclude(status="resolved")
        .select_related("supply_point")
    ]
    deliveries = _deliveries_due(program_id, org_id)
    partner = f"?partner={org_id}"
    return {
        "org_id": org_id,
        "name": name,
        "places": places,
        "flws": flws,
        "flws_low": [f for f in flws if f["low"]],
        "restocking": restocking,
        "issues": issues,
        "deliveries": deliveries,
        "links": {
            "issues": reverse("supply_chain:issues") + partner,
            "movements": reverse("supply_chain:movements") + partner,
            "where_it_went": reverse("supply_chain:flow") + partner,
            "suggestions": reverse("supply_chain:partner_deliveries") + partner,
            "count": reverse("supply_chain:stock_count_record"),
            "record_movement": reverse("supply_chain:movement_record"),
            "this_week": reverse("supply_chain:llo_week", args=[org_id]),
            "llo_card": reverse("supply_chain:partners"),
        },
    }


def _deliveries_due(program_id, org_id) -> list[dict]:
    out = []
    for contract in Contract.objects.filter(program_id=program_id).exclude(status="cancelled"):
        if (contract.partner_org_id or contract.buyer_org_id) != org_id:
            continue
        for shipment in Shipment.objects.filter(contract=contract, contract__program_id=program_id).exclude(
            status__in=MOVING
        ):
            out.append(
                {
                    "reference": contract.reference or f"Order {contract.pk}",
                    "expected": day_text(shipment.expected_on) if shipment.expected_on else "date not recorded",
                    "url": reverse("supply_chain:order_detail", args=[contract.pk]),
                }
            )
    return out


def week_rows(program_id, *, visible=None, today=None, memo=None) -> list[dict]:
    """One row per LLO for the week: handouts to confirm and FLWs low, from the runs the Distribution page reads."""
    today = today or date.today()
    base = llo_rows(program_id, visible=visible, today=today, memo=memo)
    owner = _owner(program_id)
    out = []
    for row in base:
        mine = {pk for pk, o in owner.items() if o == row["org_id"]}
        runs = _runs(program_id, mine)
        recent = [r for r in runs if r.distributed_on >= today - timedelta(days=WEEK * 2)]
        out.append(
            {
                **row,
                "handouts_recent": len(recent),
                "handouts_to_confirm": len([r for r in recent if _unconfirmed(r)]),
            }
        )
    return out


def _plan_for(program_id, org_id, mine, *, memo=None) -> dict | None:
    """What to give each of this LLO's FLWs at the next handout, with the working (the plan, drawn on This week)."""
    picture = weekly.plan_picture(program_id, memo=memo)
    if picture is None:
        return None
    handout = picture.get("handout") or {}
    groups = []
    for partner in picture.get("issue_groups") or []:
        if partner["org_id"] != org_id:
            continue
        for place in partner["places"]:
            rows = [
                {
                    "id": r["supply_point_id"],
                    "name": r["name"],
                    "children": r.get("children"),
                    "need": int(round(float(r["need_week"]))),
                    "held": (
                        int(round(float(r["held"]))) if r.get("stock_known") and r.get("held") is not None else None
                    ),
                    "to_issue": r["to_issue"],
                    "short": bool(r.get("short") and r.get("stock_known")),
                    "steps": r["steps"],
                    "result": r["result"],
                    "url": reverse("supply_chain:worker_detail", args=[r["supply_point_id"]]),
                }
                for r in place["rows"]
                if r["supply_point_id"] in mine
            ]
            if rows:
                groups.append({"place": place["name"], "to_issue": sum(r["to_issue"] for r in rows), "rows": rows})
    on = handout.get("on")
    return {
        "groups": groups,
        "total": sum(g["to_issue"] for g in groups),
        "handout": day_text(date.fromisoformat(on)) if on else "",
        "planned": bool(handout.get("planned")),
        "data_to": day_text(date.fromisoformat(handout["data_to"])) if handout.get("data_to") else "",
        "unit": "sachets",
    }


def llo_week(program_id, org_id, *, visible=None, today=None, memo=None) -> dict | None:
    """One LLO's week: the plan, handouts to record and confirm, FLWs short, and what to follow up."""
    today = today or date.today()
    detail = llo_detail(program_id, org_id, visible=visible, today=today, memo=memo)
    if detail is None:
        return None
    owner = _owner(program_id)
    mine = {pk for pk, o in owner.items() if o == org_id and (visible is None or pk in visible)}
    handouts = []
    for run in _runs(program_id, mine):
        if run.distributed_on < today - timedelta(days=WEEK * 2) and not _unconfirmed(run):
            continue
        lines = list(run.lines.all())
        status = run.status
        handouts.append(
            {
                "id": run.pk,
                "on": day_text(run.distributed_on),
                "from": run.supply_point.name,
                "reference": run.reference or "Handout",
                "flws": len(lines),
                "status": {
                    "planned": "planned, not yet confirmed",
                    "partly_confirmed": "partly confirmed",
                    "confirmed": "confirmed",
                }[status],
                "to_confirm": status != "confirmed",
                "unreceived": sum(1 for line in lines if line.is_confirmed and line.received_on is None),
                "confirm_url": reverse("supply_chain:distribution_confirm_as_planned", args=[run.pk]),
                "received_url": reverse("supply_chain:distribution_received_all", args=[run.pk]),
                "message_url": reverse("supply_chain:distribution_from_message", args=[run.pk]),
            }
        )
    return {
        **detail,
        "handouts": handouts,
        "plan": _plan_for(program_id, org_id, mine, memo=memo),
        "links": {
            **detail["links"],
            "record": reverse("supply_chain:distribution_record"),
            "plan_one": reverse("supply_chain:distribution_plan"),
        },
    }
