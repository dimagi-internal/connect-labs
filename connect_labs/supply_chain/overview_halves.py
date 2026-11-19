"""The Overview in two halves, Buying and Distribution (round 3, step 3).

Sophie Feintuch: "the Overview says most things two or three times: where each tender stands appears in four places,
and partner stock in four. Split it into the same two halves as the side menu, divided at the delivery arriving at a
partner's warehouse. Each half has the same two blocks": Needs attention (waiting on suppliers, on partners, on us;
every
item links to where you act) and Where things stand (one table). Read from the rows the old Overview read
(`standing`, `sourcing_rows`, the LLO figures of `llo_stock`, the plan of `weekly`), so nothing is stored here and no
figure can differ from the page it links to.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain import weekly
from connect_labs.supply_chain.values import quantity_phrase

US_GROUP = "Waiting on us"
SUPPLIERS_GROUP = "Waiting on suppliers"
PARTNERS_GROUP = "Waiting on partners"
GROUP_ORDER = (US_GROUP, PARTNERS_GROUP, SUPPLIERS_GROUP)


def buying_attention(standing, pay_items=()) -> list[dict]:
    """[{group, items: [{text, about, href}]}]: what is open in Buying, by whose move it is; empty groups left out."""
    groups: dict = {name: [] for name in GROUP_ORDER}
    for row in standing:
        if row.under_tender:
            continue  # an order of a split tender is read on its tender's row
        for mv in row.ours:
            groups[US_GROUP].append(
                {"text": mv.text, "about": row.title, "detail": mv.detail or "", "href": mv.href or row.url}
            )
        silent = row.silent_names
        for mv in row.theirs:
            if mv.rule == rules.RULE_NO_REPLY:
                continue
            groups[SUPPLIERS_GROUP].append(
                {"text": mv.text, "about": row.title, "detail": mv.detail or "", "href": mv.href or row.url}
            )
        if silent:
            noun = "supplier has" if len(silent) == 1 else "suppliers have"
            groups[SUPPLIERS_GROUP].append(
                {
                    "text": f"{len(silent)} {noun} not replied",
                    "about": row.title,
                    "href": row.url,
                    "detail": ", ".join(silent),
                }
            )
        for partner in row.partners or []:
            if partner.get("state") == "waiting":
                groups[PARTNERS_GROUP].append(
                    {"text": f"{partner['name']} has not ordered", "about": row.title, "href": row.url}
                )
    groups[US_GROUP].extend(pay_items)
    return [{"group": name, "items": groups[name]} for name in GROUP_ORDER if groups[name]]


def pay_items(contracts, invoices, orgs, suppliers, purchase_url) -> list[dict]:
    """Invoices to pay, as moves on us: "Pay Acacia's invoice", each linked to its purchase (or order)."""
    from connect_labs.supply_chain import purchases

    by_id = {c["id"]: c for c in contracts if c.get("status") != "cancelled"}
    out = []
    for inv in purchases.live_invoices(invoices):
        contract = by_id.get(inv.get("contract_id"))
        if contract is None or purchases.is_paid(inv) or contract.get("consideration") not in (None, "", "priced"):
            continue
        if inv.get("direction") == "from_partner":
            who = (orgs.get(contract.get("partner_org_id") or contract.get("buyer_org_id")) or {}).get("name")
        else:
            who = (suppliers.get(contract.get("supplier_id")) or {}).get("name")
        out.append(
            {
                "text": f"Pay {who or 'the supplier'}'s invoice",
                "about": contract.get("reference") or f"Order {contract['id']}",
                "href": purchase_url(contract),
            }
        )
    return out


def issue_sentence(issue: dict) -> str:
    """One issue as a sentence: "12 cartons taken from North Star's warehouse by Kishimi District Clinic"."""
    qty = quantity_phrase(issue["amount"], issue["unit"])
    where, kind = issue["where"], issue["kind"]
    if kind == "taken":
        who = issue.get("taken_by") or "someone"
        return f"{qty} taken from {where} by {who}"
    if kind == "expiring":
        return f"{qty} expiring soon at {where}"
    if kind == "expired":
        return f"{qty} expired at {where}"
    if kind == "damaged":
        return f"{qty} damaged at {where}"
    if kind == "missing":
        return f"{qty} missing from {where}"
    return f"{qty} reported at {where}"


def _handouts_to_confirm(row) -> str:
    n = row["unconfirmed_handouts"]
    return f"{row['name']}: {n} handout{'s' if n != 1 else ''} to confirm"


def _chase_item(item) -> dict:
    parts = [p for p in (item.get("what"), item.get("about")) if p]
    return {
        "text": item["ask"],
        "about": " · ".join(parts),
        "detail": item.get("amount") or "",
        "href": item.get("href") or "",
    }


def distribution_attention(rows, issues, chase=()) -> list[dict]:
    """[{group, items}]: open stock issues, handouts not confirmed, FLWs short for the week (counts per LLO), what the
    chase list asks of partners, and alerts that could not be sent (a failed alert was only ever in the alert log)."""
    out = []
    if issues:
        out.append(
            {
                "group": "Open stock issues",
                "items": [
                    {
                        "text": issue_sentence(i),
                        "detail": f"{i['status']} · {i['days']} days",
                        "href": reverse("supply_chain:issues") + f"?partner={i['org_id']}",
                    }
                    for i in issues
                ],
            }
        )
    confirm = [r for r in rows if r["unconfirmed_handouts"]]
    if confirm:
        out.append(
            {
                "group": "Handouts not yet confirmed",
                "items": [
                    {
                        "text": _handouts_to_confirm(r),
                        "href": r["week_url"],
                    }
                    for r in confirm
                ],
            }
        )
    partner_items = [i for g in chase if g["audience"] == "partner" for i in g["items"]]
    if partner_items:
        out.append({"group": PARTNERS_GROUP, "items": [_chase_item(i) for i in partner_items]})
    failed = [i for g in chase for i in g["items"] if i.get("kind") == "alert_failure"]
    if failed:
        out.append({"group": "Alerts that could not be sent", "items": [_chase_item(i) for i in failed]})
    short = [r for r in rows if r["flws_low"]]
    if short:
        out.append(
            {
                "group": "FLWs short for this week's visits",
                "items": [
                    {
                        "text": f"{r['name']}: {r['flws_low']} FLW{'s' if r['flws_low'] != 1 else ''} short",
                        "href": r["url"],
                    }
                    for r in short
                ],
            }
        )
    return out


def unresolved_issues(program_id, visible, today, names) -> list[dict]:
    """Every open stock issue, newest first, with what the sentence needs."""
    from connect_labs.supply_chain.models import StockIssue
    from connect_labs.supply_chain.stock.services import issues as issue_services

    owner = issue_services.partner_by_point(program_id)
    out = []
    for i in (
        StockIssue.objects.filter(program_id=program_id).exclude(status="resolved").select_related("supply_point")
    ):
        if visible is not None and i.supply_point_id not in visible:
            continue
        out.append(
            {
                "kind": i.kind,
                "amount": i.quantity,
                "unit": i.quantity_unit,
                "where": i.supply_point.name,
                "taken_by": i.taken_by,
                "status": "being followed up" if i.status == "following_up" else "not yet looked at",
                "days": (today - i.occurred_on).days,
                "org_id": owner.get(i.supply_point_id) or "",
            }
        )
    return out


def weeks_covered(program_id, owner, *, memo=None) -> dict:
    """{org id: about how many weeks its RUTF lasts}: what it holds, over what its FLWs need in a week."""
    from connect_labs.supply_chain.stock.services import network
    from connect_labs.supply_chain.values import Quantity

    picture = weekly.plan_picture(program_id, memo=memo)
    if picture is None:
        return {}
    need: dict = defaultdict(float)
    for r in picture["issue"]:
        org = owner.get(r["supply_point_id"])
        need[org] += float(r["need_week"]) * 7 / (r.get("window_days") or 7)
    held: dict = defaultdict(float)
    for row in network.network_stock(program_id):
        org = owner.get(row["supply_point_id"])
        base = row.get("on_hand_in_base")
        if org is not None and isinstance(base, Quantity):
            held[org] += float(base.amount)
    return {org: held[org] / need[org] for org in need if need[org] > 0}


def enough_line(program_id, *, memo=None) -> dict | None:
    """The plan's one line for the whole program: "Will we have enough? Yes, at a full carton per child"."""
    picture = weekly.plan_picture(program_id, memo=memo)
    if picture is None:
        return None
    cases = (picture.get("break_even") or {}).get("cases") or []
    if not cases:
        return None
    case = cases[0]
    if case["covers"]:
        text = "Yes, at a full carton per child."
    else:
        text = f"Not at a full carton per child: about {float(case['to_buy']):,.0f} more sachets are needed."
    return {"text": text, "covers": bool(case["covers"]), "url": reverse("supply_chain:plan_programme")}


def distribution_rows(llo_rows, weeks: dict) -> list[dict]:
    """One row per LLO for Where things stand."""
    out = []
    for r in llo_rows:
        w = weeks.get(r["org_id"])
        out.append(
            {
                **r,
                "weeks": f"about {w:,.0f} weeks" if w is not None and w < 200 else ("many weeks" if w else "—"),
            }
        )
    return out


def today_or(value) -> date:
    return value or date.today()


__all__ = [
    "buying_attention",
    "pay_items",
    "distribution_attention",
    "unresolved_issues",
    "weeks_covered",
    "enough_line",
    "distribution_rows",
]
