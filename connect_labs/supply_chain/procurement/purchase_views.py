"""Purchases: the list of what is being bought, and one purchase from sizing to payment.

Round 3 of Sophie Feintuch's walk of the demo asked for Supply to be organised around the work rather
than around record types. Buying a round of RUTF was eight pages (sizing, tender, quote, comparison,
award, orders, order, shipment); here it is one list and one page per purchase. The arranging is in
`supply_chain.purchases`; this file fetches.

Only reads. Every action on a purchase is still the form that did it before, opened from the step it
belongs to, so nothing a person could do is lost by moving it.
"""

from __future__ import annotations

from datetime import date

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain import guide, purchases
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.guide import guide_context
from connect_labs.supply_chain.procurement.services.award_orders import order_words
from connect_labs.supply_chain.procurement.sourcing import marketplace_in_use, sourcing_rows
from connect_labs.supply_chain.procurement.views import _Base
from connect_labs.supply_chain.values import day_text

# What the list can be narrowed to, in the order the chips are drawn: (key, chip label).
FILTERS = (
    ("active", "In progress"),
    ("unpaid", "Unpaid invoices"),
    ("transit", "In transit"),
    ("all", "All purchases"),
)


def _today(request) -> date:
    return getattr(request, "supply_as_of", None) or date.today()


def _contract_flags(contracts, shipments, invoices, words) -> dict:
    """For a set of live orders: whether any is in transit, owes money, and whether all are finished."""
    live = [c for c in contracts if c.get("status") != "cancelled"]
    moving = any(
        s.get("contract_id") in {c["id"] for c in live} and s.get("status") not in ("delivered", "lost")
        for s in shipments
    )
    money = [purchases.money_state(c, invoices) for c in live]
    owed = any(m["state"] == "owed" for m in money)
    delivered = bool(live) and all(words.get(c["id"], ("", False))[1] for c in live)
    paid = bool(live) and all(m["state"] in ("paid", "not_billed") for m in money)
    return {"in_transit": moving, "unpaid": owed, "finished": delivered and paid}


class PurchasesView(_Base):
    """Every purchase of the program, with the filters that stand in for the old Orders list."""

    template_name = "supply_chain/purchases.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        chosen = self.request.GET.get("show") or "active"
        context["show"] = chosen if chosen in dict(FILTERS) else "active"
        if not context["has_program_context"]:
            return context
        access = _access(self.request)
        program_id = access.program_id
        today = _today(self.request)
        tenders = self.op("tender_list")
        visible = {t["id"] for t in tenders}
        rows = sourcing_rows(program_id, today)
        if context["acl_scoped"]:
            # `sourcing_rows` reads the tables itself; a partner keeps only the tenders it may list.
            rows = [r for r in rows if r["id"] in visible]
        contracts = self.op("contract_list")
        shipments = self.op("shipment_list")
        invoices = self.op("invoice_list")
        words = order_words(program_id, [c["id"] for c in contracts], today)
        orgs = {o["id"]: o for o in self.op("org_list")}
        suppliers = {s["id"]: s for s in self.op("supplier_list")}
        by_tender: dict = {}
        for c in contracts:
            by_tender.setdefault(c.get("tender_id"), []).append(c)
        for row in rows:
            row["purchase_url"] = reverse("supply_chain:purchase", args=[row["id"]])
            row.update(_contract_flags(by_tender.get(row["id"], []), shipments, invoices, words))
        direct = []
        for c in by_tender.get(None, []):
            if c.get("status") == "cancelled":
                continue
            flags = _contract_flags([c], shipments, invoices, words)
            org_id = c.get("partner_org_id") or c.get("buyer_org_id")
            direct.append(
                {
                    "id": c["id"],
                    "label": c.get("reference") or f"Order {c['id']}",
                    "supplier": (suppliers.get(c.get("supplier_id")) or {}).get("name") or "",
                    "llo": (orgs.get(org_id) or {}).get("name") or "",
                    "status": words.get(c["id"], ("", False))[0] or str(c.get("status") or "").capitalize(),
                    "money": purchases.money_state(c, invoices),
                    "signed": day_text(date.fromisoformat(c["signed_on"])) if c.get("signed_on") else "",
                    "url": reverse("supply_chain:order_detail", args=[c["id"]]),
                    **flags,
                }
            )
        counts = {
            "active": sum(1 for r in rows if not r["finished"]) + sum(1 for d in direct if not d["finished"]),
            "unpaid": sum(1 for r in rows if r["unpaid"]) + sum(1 for d in direct if d["unpaid"]),
            "transit": sum(1 for r in rows if r["in_transit"]) + sum(1 for d in direct if d["in_transit"]),
            "all": len(rows) + len(direct),
        }
        keep = {
            "active": lambda r: not r["finished"],
            "unpaid": lambda r: r["unpaid"],
            "transit": lambda r: r["in_transit"],
            "all": lambda r: True,
        }[context["show"]]
        context["rows"] = [r for r in rows if keep(r)]
        context["direct"] = [d for d in direct if keep(d)]
        context["filters"] = [
            {
                "key": key,
                "label": label,
                "count": counts[key],
                "current": key == context["show"],
                "url": f"{self.request.path}?show={key}",
            }
            for key, label in FILTERS
        ]
        context["marketplace_in_use"] = marketplace_in_use(program_id)
        if not context["acl_scoped"]:
            # The purchase guide is the running organisation's checklist, drawn from program-wide facts and
            # naming every tender: a partner under read enforcement is not shown it.
            context.update(guide_context(self.request, program_id))
        return context


class PurchaseView(_Base):
    """One purchase: the steps from sizing to payment, each with where it stands and where to act."""

    template_name = "supply_chain/purchase.html"

    def get_context_data(self, tender_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        tender = self.op("tender_get", tender_id=tender_id)
        if tender is None:
            raise Http404(f"no purchase {tender_id} in this program")
        access = _access(self.request)
        program_id = access.program_id
        today = _today(self.request)
        outreach = self.op("outreach_list", tender_id=tender_id)
        quotes = self.op("quote_list", tender_id=tender_id)
        awards = self.op("award_list", tender_id=tender_id)
        contracts = self.op("contract_list", tender_id=tender_id)
        live_ids = {c["id"] for c in contracts if c.get("status") != "cancelled"}
        shipments = [s for s in self.op("shipment_list") if s.get("contract_id") in live_ids]
        invoices = [i for i in self.op("invoice_list") if i.get("contract_id") in live_ids]
        words = order_words(program_id, sorted(live_ids), today)
        orgs = {o["id"]: o for o in self.op("org_list")}
        suppliers = {s["id"]: s for s in self.op("supplier_list")}
        splits = [s for s in tender.get("partner_splits") or [] if isinstance(s, dict)]
        planned = len(splits) or 1

        orders = purchases.order_rows(
            tender_id=tender_id,
            contracts=contracts,
            orgs=orgs,
            suppliers=suppliers,
            shipments=shipments,
            invoices=invoices,
            words=words,
            today=today,
        )
        # A split tender names every LLO from the start: the ones with no order yet are listed as waiting,
        # because "Savanna has not ordered" is the thing to see on the page.
        ordered_orgs = {c.get("partner_org_id") or c.get("buyer_org_id") for c in contracts if c["id"] in live_ids}
        waiting = [
            {
                "llo": s.get("org_name") or (orgs.get(s.get("org_id")) or {}).get("name") or "",
                "org_id": s.get("org_id"),
            }
            for s in splits
            if s.get("org_id") not in ordered_orgs
        ]

        facts = next((f for t, f in guide.tender_facts_for(program_id) if t.pk == tender_id), {})
        steps = guide.steps_for(facts, strict=True)
        comparison_urls = []
        for line in tender.get("lines") or []:
            if isinstance(line, dict) and line.get("commodity_slug"):
                comparison_urls.append(
                    {
                        "label": "Compare" + (f" {line['commodity_slug']}" if len(tender["lines"]) > 1 else ""),
                        "url": reverse("supply_chain:procurement_comparison", args=[tender_id])
                        + f"?commodity={line['commodity_slug']}",
                    }
                )
        stage_list = purchases.stages(
            tender=tender,
            outreach=outreach,
            quotes=quotes,
            awards=awards,
            orders=orders,
            planned_orders=planned,
            steps=steps,
            comparison_urls=comparison_urls,
            today=today,
        )
        context.update(
            tender=tender,
            stages=stage_list,
            next_step=purchases.next_step(stage_list),
            orders=orders,
            waiting=waiting,
            needed_by=day_text(date.fromisoformat(tender["needed_by"])) if tender.get("needed_by") else "",
            start_by=day_text(date.fromisoformat(tender["start_by"])) if tender.get("start_by") else "",
            tender_url=reverse("supply_chain:procurement_tender_detail", args=[tender_id]),
            edit_url=reverse("supply_chain:procurement_tender_edit", args=[tender_id]),
            award=awards[0] if awards else None,
        )
        return context
