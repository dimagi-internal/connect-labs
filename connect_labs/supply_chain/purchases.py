"""A purchase, start to finish: one place for what was a run of pages.

Sophie Feintuch's round 3 ("organise Supply around workflows"): buying a round of RUTF meant moving between
sizing, tender, quote, comparison, award, orders, order and shipment. This reads what those pages already
hold, for one tender, and lays it out as the seven steps the work takes:

    size it -> ask suppliers -> compare -> award -> one order per LLO -> delivered -> paid

It decides nothing of its own. Every figure comes from the same reads the old pages use (`guide` for which
steps are done, `award_orders.split_progress` and `order_words` for the orders, `fulfilment.status.order_status`
for an order's own stages), so this page and those cannot disagree.

Pure over the dicts the operations return: the view fetches, this arranges, and the tests hand it dicts.
"""

from __future__ import annotations

from datetime import date

from django.urls import reverse

from connect_labs.supply_chain.values import day_text, quantity_phrase

STEPS = ("Size it", "Ask suppliers", "Compare", "Award", "Orders", "Delivered", "Paid")

# Invoice states that no longer count as money owed or moving.
_DEAD_INVOICE = ("rejected",)


def _count(n, noun) -> str:
    return f"{n} {noun}{'s' if n != 1 else ''}"


def live_invoices(invoices) -> list[dict]:
    return [i for i in invoices if i.get("status") not in _DEAD_INVOICE]


def is_paid(invoice) -> bool:
    return invoice.get("status") == "paid"


def money_state(contract, invoices) -> dict:
    """Where the money on one order stands: {"state": "none"|"owed"|"paid"|"not_billed", "words", "owed_to"}.

    The supplier's bills and a partner's invoice to us both count: an order a partner buys on our quote is
    only finished once the partner has been paid, which the old order list never showed.
    """
    if contract.get("consideration") not in (None, "", "priced"):
        return {"state": "not_billed", "words": "not billed on its own", "owed_to": ""}
    mine = live_invoices([i for i in invoices if i.get("contract_id") == contract["id"]])
    if not mine:
        return {"state": "none", "words": "no invoice yet", "owed_to": ""}
    unpaid = [i for i in mine if not is_paid(i)]
    if not unpaid:
        return {"state": "paid", "words": "paid", "owed_to": ""}
    from_partner = any(i.get("direction") == "from_partner" for i in unpaid)
    return {
        "state": "owed",
        "words": f"{len(unpaid)} invoice{'s' if len(unpaid) != 1 else ''} to pay",
        "owed_to": "partner" if from_partner else "supplier",
    }


def _line_phrase(tender) -> str:
    lines = [ln for ln in tender.get("lines") or [] if isinstance(ln, dict) and ln.get("quantity") not in (None, "")]
    return " and ".join(quantity_phrase(ln["quantity"], ln.get("quantity_unit") or "") for ln in lines)


def order_rows(*, tender_id, contracts, orgs, suppliers, shipments, invoices, words, today) -> list[dict]:
    """One row per live order of this purchase: who it is for, how far it has got, what is owed, where to act."""
    rows = []
    for contract in sorted(contracts, key=lambda c: c["id"]):
        if contract.get("status") == "cancelled":
            continue
        cid = contract["id"]
        mine = [s for s in shipments if s.get("contract_id") == cid]
        moving = [s for s in mine if s.get("status") not in ("delivered", "lost")]
        expected = max((s.get("expected_on") or "" for s in moving), default="")
        org_id = contract.get("partner_org_id") or contract.get("buyer_org_id")
        status_words, delivered = words.get(cid, ("", False))
        money = money_state(contract, invoices)
        rows.append(
            {
                "id": cid,
                "llo": (orgs.get(org_id) or {}).get("name") or "",
                "supplier": (suppliers.get(contract.get("supplier_id")) or {}).get("name") or "",
                "reference": contract.get("reference") or f"Order {cid}",
                "quantity": (
                    quantity_phrase(contract["quantity"], contract.get("quantity_unit") or "")
                    if contract.get("quantity") not in (None, "")
                    else ""
                ),
                "status": status_words or str(contract.get("status") or "").capitalize(),
                "delivered": bool(delivered),
                "in_transit": bool(moving),
                "expected": day_text(date.fromisoformat(expected[:10])) if expected else "",
                "money": money,
                "paid": money["state"] in ("paid", "not_billed"),
                "url": reverse("supply_chain:order_detail", args=[cid]),
                "actions": order_actions(cid, mine),
            }
        )
    return rows


def order_actions(contract_id, shipments) -> list[dict]:
    """ "What happened?" on one order: the forms that record it, each one named by what happened."""
    acts = [
        {"label": "It was dispatched", "url": reverse("supply_chain:shipment_record", args=[contract_id])},
        {"label": "It arrived", "url": reverse("supply_chain:receipt_record", args=[contract_id])},
        {"label": "An invoice came in", "url": reverse("supply_chain:invoice_record", args=[contract_id])},
        {"label": "An advance was paid", "url": reverse("supply_chain:advance_payment_record", args=[contract_id])},
        {"label": "Attach a document", "url": reverse("supply_chain:document_attach", args=[contract_id])},
        {
            "label": "Record something we owe",
            "url": reverse("supply_chain:contract_commitment_record", args=[contract_id]),
        },
    ]
    if shipments:
        acts.insert(
            3,
            {"label": "An extra charge", "url": reverse("supply_chain:charge_record", args=[shipments[-1]["id"]])},
        )
    return acts


def stages(
    *, tender, outreach, quotes, awards, orders, planned_orders, steps, comparison_urls, today=None
) -> list[dict]:
    """The seven steps, each {key, number, title, state, summary, links, primary}.

    `steps` is `guide.steps_for(facts, strict=True)` for this tender: it already says what this tender
    has done. `planned_orders` is how many orders the purchase should end with (one per LLO of a split
    tender, else one); `orders` are the live ones.
    """
    tid = tender["id"]
    done = {s.key: s.done for s in steps}
    live_quotes = [q for q in quotes if not q.get("voided") and not q.get("superseded_by_quote_id")]
    asked = len(outreach)
    replied = sum(1 for o in outreach if o.get("responded"))
    award = awards[0] if awards else None

    ordered_all = bool(orders) and len(orders) >= planned_orders
    delivered_n = sum(1 for o in orders if o["delivered"])
    paid_n = sum(1 for o in orders if o["paid"])

    tender_url = reverse("supply_chain:procurement_tender_detail", args=[tid])
    out = [
        {
            "key": "size",
            "title": "Size it",
            "done": bool(done.get("quantities")),
            "summary": (
                _line_phrase(tender) + (f" for {planned_orders} LLOs" if planned_orders > 1 else "")
                if done.get("quantities")
                else "How much to buy is not set yet."
            ),
            "links": [
                {"label": "How much to buy", "url": reverse("supply_chain:procurement_tender_sizing", args=[tid])},
                {"label": "Split by LLO", "url": reverse("supply_chain:procurement_tender_splits", args=[tid])},
            ],
            "primary": None,
        },
        {
            "key": "ask",
            "title": "Ask suppliers",
            "done": bool(done.get("replies")) or (tender.get("status") not in ("draft", None) and replied > 0),
            "summary": (
                f"{asked} asked · {replied} replied · {_count(len(live_quotes), 'quote')} in"
                if asked or live_quotes
                else "No supplier has been asked yet."
            ),
            "links": [{"label": "Suppliers and replies", "url": tender_url}],
            "primary": {
                "label": "Ask for a quote",
                "url": reverse("supply_chain:procurement_outreach_log", args=[tid]),
            },
        },
        {
            "key": "compare",
            "title": "Compare",
            "done": bool(done.get("compare")),
            "summary": (
                f"{len(live_quotes)} quote{'s' if len(live_quotes) != 1 else ''} to line up"
                if live_quotes
                else "Nothing to compare until a quote is in."
            ),
            "links": comparison_urls if live_quotes else [],
            "primary": None,
        },
        {
            "key": "award",
            "title": "Award",
            "done": bool(award),
            "summary": (
                "Awarded"
                + (f" on {day_text(date.fromisoformat(award['decided_on']))}" if award.get("decided_on") else "")
                if award
                else "No supplier chosen yet."
            ),
            "links": (
                [{"label": "The award", "url": reverse("supply_chain:award_detail", args=[award["id"]])}]
                if award
                else []
            ),
            "primary": None,
        },
        {
            "key": "orders",
            "title": "Orders",
            "done": ordered_all,
            "summary": (
                f"{len(orders)} of {planned_orders} ordered"
                if planned_orders > 1 or orders
                else "Nothing ordered yet."
            ),
            "links": [],
            "primary": (
                {
                    "label": "Draft an order for each LLO",
                    "url": reverse("supply_chain:award_detail", args=[award["id"]]) + "#orders",
                }
                if award and not ordered_all
                else None
            ),
        },
        {
            "key": "delivered",
            "title": "Delivered",
            "done": ordered_all and delivered_n == len(orders),
            "summary": f"{delivered_n} of {planned_orders} delivered" if orders else "Nothing has arrived yet.",
            "links": [],
            "primary": None,
        },
        {
            "key": "paid",
            "title": "Paid",
            "done": ordered_all and paid_n == len(orders),
            "summary": f"{paid_n} of {planned_orders} settled" if orders else "Nothing to pay yet.",
            "links": [],
            "primary": None,
        },
    ]
    first_open = next((i for i, s in enumerate(out) if not s["done"]), None)
    for i, stage in enumerate(out):
        stage["number"] = i + 1
        stage["state"] = "done" if stage["done"] else "now" if i == first_open else "todo"
    return out


def next_step(stage_list) -> dict | None:
    """The first step not yet done: {title, summary, primary}."""
    for stage in stage_list:
        if not stage["done"]:
            return stage
    return None
