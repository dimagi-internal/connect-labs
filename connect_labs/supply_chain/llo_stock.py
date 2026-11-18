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

from connect_labs.supply_chain.models import Contract, Distribution, Shipment, StockIssue, SupplyPoint
from connect_labs.supply_chain.partners.status import RUNNING_OUT, partner_places, partner_status
from connect_labs.supply_chain.stock.services import issues as issue_services
from connect_labs.supply_chain.stock.services import network
from connect_labs.supply_chain.values import Quantity, day_text

# A handout still counts as "this week" for a fortnight after its day: a run left unconfirmed longer than that is a
# flag to follow up, not a handout of the week.
WEEK = 7
MOVING = ("delivered", "lost")

STATUS_WORDS = {
    "stockout": "none left",
    "below_min": "low",
    "negative": "records disagree",
    "ok": "ok",
    "overstocked": "plenty",
}


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


def llo_rows(program_id, *, visible=None, today=None) -> list[dict]:
    """One row per LLO that runs something: what it holds, its FLWs, open issues, the next delivery."""
    today = today or date.today()
    owner = _owner(program_id)
    status = partner_status(program_id)
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


def llo_detail(program_id, org_id, *, visible=None, today=None) -> dict | None:
    """One LLO's stock page: its RUTF stock locations and FLWs with what each holds, open issues, deliveries due."""
    today = today or date.today()
    owner = _owner(program_id)
    mine = {pk for pk, o in owner.items() if o == org_id and (visible is None or pk in visible)}
    if not mine:
        return None
    name = _names({org_id}).get(org_id) or f"Organization {org_id}"
    stock = {r["supply_point_id"]: r for r in network.network_stock(program_id) if r["supply_point_id"] in mine}
    kinds = dict(SupplyPoint.objects.filter(program_id=program_id, pk__in=mine).values_list("pk", "kind"))
    places = []
    for place in partner_places(program_id).get(org_id, []):
        if place["id"] not in mine:
            continue
        row = stock.get(place["id"], {})
        places.append(
            {
                "id": place["id"],
                "name": place["name"],
                "kind": place["kind"],
                "holds": _stock_text(row) if row else "no stock recorded",
                "status": STATUS_WORDS.get(row.get("status"), ""),
                "keeper": place["custodian"].name if place["custodian"] is not None else "",
                "send_url": reverse("supply_chain:consignment_dispatch") + f"?from_supply_point={place['id']}",
            }
        )
    flws = []
    for pk, row in sorted(stock.items(), key=lambda kv: kv[1]["name"].lower()):
        if kinds.get(pk) != "user_held":
            continue
        flws.append(
            {
                "id": pk,
                "name": row["name"],
                "holds": _stock_text(row),
                "low": row["status"] in RUNNING_OUT,
                "status": STATUS_WORDS.get(row["status"], ""),
                "url": reverse("supply_chain:worker_detail", args=[pk]),
            }
        )
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
        "issues": issues,
        "deliveries": deliveries,
        "links": {
            "issues": reverse("supply_chain:issues") + partner,
            "movements": reverse("supply_chain:movements") + partner,
            "where_it_went": reverse("supply_chain:flow") + partner,
            "network": reverse("supply_chain:network") + partner,
            "workers": reverse("supply_chain:workers") + partner,
            "suggestions": reverse("supply_chain:partner_deliveries") + partner,
            "restocking": reverse("supply_chain:plan_stores") + partner,
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


def week_rows(program_id, *, visible=None, today=None) -> list[dict]:
    """One row per LLO for the week: handouts to confirm and FLWs low, from the runs the Distribution page reads."""
    today = today or date.today()
    base = llo_rows(program_id, visible=visible, today=today)
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


def llo_week(program_id, org_id, *, visible=None, today=None) -> dict | None:
    """One LLO's week: handouts to plan, record and confirm, FLWs low, and what to follow up."""
    today = today or date.today()
    detail = llo_detail(program_id, org_id, visible=visible, today=today)
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
    partner = f"?partner={org_id}"
    return {
        **detail,
        "handouts": handouts,
        "links": {
            **detail["links"],
            "plan": reverse("supply_chain:plan") + partner,
            "record": reverse("supply_chain:distribution_record"),
            "plan_one": reverse("supply_chain:distribution_plan"),
            "distribution": reverse("supply_chain:distribution") + partner,
        },
    }
