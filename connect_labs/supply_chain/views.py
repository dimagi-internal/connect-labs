"""Django pages for the supply domain.

Every mutation goes through an operation, so the web UI can never do something
the API and MCP surfaces cannot. OperationBase.op() is the only way a view
reaches the domain.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.checks import course_applies_to_category, courses_carried_by_kits
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.compliance import kit_spec_verdict


@method_decorator(login_required, name="dispatch")
class OperationBase(TemplateView):
    def op(self, name, **payload):
        return call_operation(name, _access(self.request), payload)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        return context


def newest_standing_first(quotes):
    """Quotes ordered as a reader scans them: what stands, then what does not.

    `quote_list` comes back newest-created first, which put a withdrawn
    duplicate above the quote that replaced it -- so the first price on the
    page was one that no longer applies. Two stable passes rather than one
    compound key: sort by date, then by standing, and Python's stable sort
    keeps the dates in order inside each group.
    """

    def standing(quote):
        if quote.get("voided"):
            return 2
        return 1 if quote.get("superseded_by_quote_id") else 0

    ordered = sorted(quotes, key=lambda q: q.get("received_on") or "", reverse=True)
    ordered.sort(key=standing)
    return ordered


def annotate_product(product, own_items, products=(), all_items=()):
    """Hang a product's trade items off it, each measured against its spec.

    Module-level rather than a method because two pages need identically
    annotated products -- the catalogue list and one product on its own -- and
    a second copy of this would be a second opinion about whether a trade item
    passes its specification.

    `products` is the whole catalogue, for kits: the zinc inside a co-pack is
    held to the zinc product's requirements, so checking a kit needs more
    than its own product. The verdict comes from the same function the checks
    feed uses, so the page and the feed cannot disagree about a kit.
    """
    requirements_by_slug = {p["slug"]: p.get("spec_requirements") or [] for p in products}
    names = {p["slug"]: p["name"] for p in products}
    packs = {i["base_per_pack"] for i in own_items if i.get("base_per_pack")}
    weights = {i["base_unit_grams"] for i in own_items if i.get("base_unit_grams")}

    for item in own_items:
        checked = kit_spec_verdict(
            item.get("spec_attributes"),
            product.get("spec_requirements") or [],
            item.get("components"),
            requirements_by_slug,
        )
        item["spec_verdict"] = checked["verdict"]
        # Each product inside a kit, with its own name and its own verdict, so
        # the page can say WHICH part fails rather than that something does.
        # Paired by position: `kit_spec_verdict` returns the parts in input
        # order, and a kit may hold two components of one product.
        item["component_rows"] = [
            {
                **component,
                "name": names.get(component.get("commodity_slug"), component.get("commodity_slug")),
                "verdict": part["verdict"],
            }
            for component, part in zip(item.get("components") or [], checked["components"])
        ]
        # Two different kinds of disagreement, and they are not the same
        # finding. Differing from the product's nominal pack is often
        # legitimate -- a manufacturer may genuinely pack 144. Two trade items
        # under one product differing from EACH OTHER is what makes a single
        # per-sachet figure impossible.
        item["differs_from_product"] = bool(
            item.get("base_per_pack")
            and product.get("base_per_pack")
            and item["base_per_pack"] != product["base_per_pack"]
        )
        item["pack_disagrees_with_siblings"] = len(packs) > 1
        item["weight_disagrees_with_siblings"] = len(weights) > 1
        item["gtins"] = [
            {"level": level, "value": item.get(key)}
            for level, key in (
                ("base unit", "gtin_base"),
                ("pack", "gtin_pack"),
                ("case", "gtin_case"),
            )
            if item.get(key)
        ]

    product["items"] = own_items
    product["pack_values"] = sorted(packs)
    product["weight_values"] = sorted(weights)
    # The ration table is a programme decision, not part of the
    # specification, so its absence is stated rather than defaulted.
    product["has_course_definition"] = bool((product.get("course_definition") or {}).get("base_units_per_course"))
    # ...but only where a course is a thing. The page was warning "No ration
    # table set" against an infant scale, which is not dispensed over days and
    # will never have one. Same rule as the check, from the same place, so the
    # page and the feed cannot disagree about whether something is missing.
    # `all_items` for the one exception the feed also makes: a product whose
    # course a kit states (the tablets inside a one-course packet) needs none.
    product["course_applies"] = course_applies_to_category(product.get("category")) and (
        product["slug"] not in courses_carried_by_kits(all_items or own_items)
    )
    # Same reasoning one field over. A height board has no gram weight per
    # unit and does not expire, so amber "not stated" against those was a
    # warning nobody could ever close -- and a permanent warning teaches a
    # reader to stop reading warnings, which is what it costs. Only
    # `equipment`: a diagnostic or a consumable genuinely can expire, so those
    # keep the amber.
    product["weight_and_shelf_life_apply"] = product.get("category") != "equipment"
    return product


class CatalogueView(OperationBase):
    """What can be bought, at the two levels the supply chain actually uses.

    **Products** are spec-defined: "ready-to-use therapeutic food, 92 g
    sachet, 150 to the carton, 18 months minimum" is a specification, not a
    thing you can order. It carries the reference to the standard it comes
    from and, where one exists, the UNICEF Supply Division material number
    that names it across every prequalified manufacturer.

    **Trade items** are orderable: one manufacturer's product, with its own
    SKU and its own GTIN, checked against the product's requirements.

    This is not "item master" renamed. The two levels answer the question
    Jonathan asked -- how do you know every supplier's RUTF is really the same
    92 g sachet while still knowing their specific SKU -- and the answer has
    three identifier levels, of which this page shows two: the product says
    what it must be, the trade item says whose it is and how it is packed, and
    the batch (on the Stock page) says which physical lot arrived. Conflating
    any two of them is how a per-sachet comparison silently goes 4% wrong.
    """

    template_name = "supply_chain/catalogue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = self.op("item_list")
        products = self.op("commodity_list")

        by_product: dict[str, list] = {}
        for item in items:
            by_product.setdefault(item["commodity_slug"], []).append(item)

        for product in products:
            annotate_product(product, by_product.get(product["slug"], []), products, items)

        context["products"] = products
        context["orphan_items"] = [
            item for item in items if item["commodity_slug"] not in {p["slug"] for p in products}
        ]
        return context


class DomainHomeView(OperationBase):
    """The chain, end to end, and what the record cannot answer about it.

    Two panels, two different kinds of thing, deliberately not merged:

      the funnel   STATES -- how many records sit at each stage. "6 of 7
                   awaiting a reply" is a fact, and whether it is a problem
                   depends on when they were sent.
      the checks   GAPS and CONTRADICTIONS -- what the record cannot answer,
                   and where it disagrees with itself.

    The checks are grouped by AUDIENCE and not ranked (design doc section 22
    and 24). An earlier mockup had a "Needs you" banner with a priority
    order; that came out, because prioritising is a judgement about what
    matters today and the database does not contain what it would take to
    make it. Grouping by who can answer is routing, which IS a fact.
    """

    template_name = "supply_chain/home.html"

    AUDIENCE_ORDER = ("supplier", "partner", "internal")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            # Everything below is programme-scoped; SupplyDataAccess raises
            # rather than inventing a scope, and a half-rendered page is
            # worse than an explicit "choose a programme".
            return context

        commodity_slug = self.request.GET.get("commodity") or None
        commodities = self.op("commodity_list")
        if commodity_slug is None and len(commodities) == 1:
            commodity_slug = commodities[0]["slug"]

        context["commodities"] = commodities
        context["commodity_slug"] = commodity_slug
        context["summary"] = self.op("chain_summary", commodity_slug=commodity_slug)
        context["rounds"] = self.op("round_list")
        context["contracts"] = self.op("contract_list")
        # The buyer of record by name: "programme org" is the role, not who.
        context["orgs"] = {o["id"]: o for o in self.op("org_list")}

        checks = self.op("checks_list")
        context["checks"] = checks
        context["checks_by_audience"] = self._checks_by_audience(checks, rounds=context["rounds"])
        return context

    # Where each audience's answering actually happens. The raw feed is
    # deliberately unranked and unworded -- that is what makes it good agent
    # surface -- so the page's job is to route, not to re-render it. A
    # supplier's questions belong beside its figures on the comparison
    # screen; ours belong on the record that is missing the fact.
    def _checks_by_audience(self, checks, rounds):
        first_round = rounds[0]["id"] if rounds else None
        groups = []
        for audience in self.AUDIENCE_ORDER:
            items = [c for c in checks["checks"] if c["audience"] == audience]
            if not items:
                continue
            kinds = {c["kind"] for c in items}
            groups.append(
                {
                    "audience": audience,
                    "items": items,
                    "headline": self._headline(kinds, len(items)),
                    "href": self._destination(audience, items, first_round),
                }
            )
        return groups

    def _headline(self, kinds, count):
        """What the group is, in the words of the thing rather than the kind."""
        if kinds == {"quote_not_comparable"}:
            return f"{'quote' if count == 1 else 'quotes'} not yet comparable"
        if kinds == {"commodity_course_undefined"}:
            return "ration table not set"
        return "open " + ("check" if count == 1 else "checks")

    def _destination(self, audience, items, first_round):
        if audience == "internal":
            return reverse("supply_chain:catalogue")
        rounds = {c["facts"].get("round_id") for c in items if c["facts"].get("round_id")}
        # One round involved -- go straight to its comparison. Several, and
        # the board is the honest landing place rather than picking one.
        if len(rounds) == 1:
            return reverse("supply_chain:procurement_comparison", args=[rounds.pop()])
        return reverse("supply_chain:procurement_round_board")


class ChecksView(OperationBase):
    """Every check, with the facts behind it, grouped by kind.

    The overview says how many and who can answer; this is where each one is
    read in full. Grouped by KIND rather than ranked: a kind is what the
    finding is, which is a fact, where an order of importance is a judgement
    the database cannot make (design doc sections 22 and 24). Each row links
    to the record it is about, because that is where it gets answered.
    """

    template_name = "supply_chain/checks.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        kind = self.request.GET.get("kind") or None
        category = self.request.GET.get("category") or None
        checks = self.op(
            "checks_list",
            kinds=[kind] if kind else None,
            categories=[category] if category else None,
        )
        groups: dict[str, list] = {}
        for check in checks["checks"]:
            groups.setdefault(check["kind"], []).append(check)
        context["checks"] = checks
        # Names for the records the facts refer to by id, so a card reads
        # "round CHC" and links to it rather than "round id 33".
        context["refs"] = {
            "round": {r["id"]: r.get("label") or f"round {r['id']}" for r in self.op("round_list")},
            "supplier": {s["id"]: s["name"] for s in self.op("supplier_list")},
        }
        context["groups"] = [
            {"kind": kind_name, "category": checks["kinds"][kind_name], "items": items}
            for kind_name, items in groups.items()
        ]
        context["kind"] = kind
        context["category"] = category
        return context


class OrdersView(OperationBase):
    """Contracts, and who is buying under each.

    The buyer of record is a column and not a footnote, because it changes
    the landed cost: the same goods at the same price cost different amounts
    depending on who imports them.
    """

    template_name = "supply_chain/orders.html"

    def _progress(self, contracts):
        """{contract id: {"received", "due_on"}} -- which orders need attention, at a glance.

        Received is what was accepted against the order in its own unit, from
        one `receipt_list` for the programme rather than a match per row; a
        line in another unit is left out rather than added across units. Due
        is the signed date plus the promised lead time, when both exist.
        """
        from datetime import date, timedelta
        from decimal import Decimal

        units = {c["id"]: c.get("quantity_unit") for c in contracts}
        received: dict[int, Decimal] = {}
        for receipt in self.op("receipt_list"):
            contract_id = receipt.get("contract_id")
            if contract_id not in units:
                continue
            for line in receipt.get("lines") or []:
                if line.get("quantity_unit") == units[contract_id] and line.get("quantity_accepted"):
                    received[contract_id] = received.get(contract_id, Decimal(0)) + Decimal(line["quantity_accepted"])
        progress = {}
        for contract in contracts:
            due_on = None
            if contract.get("signed_on") and contract.get("promised_lead_time_days") is not None:
                due_on = date.fromisoformat(contract["signed_on"]) + timedelta(
                    days=contract["promised_lead_time_days"]
                )
            got = received.get(contract["id"], Decimal(0))
            # Due only matters while something is still to come.
            waiting = contract.get("quantity") is not None and got < Decimal(contract["quantity"])
            progress[contract["id"]] = {"received": got, "due_on": due_on if waiting else None}
        return progress

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        context["contracts"] = self.op("contract_list")
        context["references"] = {c["id"]: c["reference"] or f"order {c['id']}" for c in context["contracts"]}
        covered_by: dict[int, list] = {}
        for contract in context["contracts"]:
            if contract.get("covers_shortfall_of_id") and contract.get("status") != "cancelled":
                covered_by.setdefault(contract["covers_shortfall_of_id"], []).append(
                    context["references"][contract["id"]]
                )
        context["covered_by"] = covered_by
        context["progress"] = self._progress(context["contracts"])
        # Lateness from the same check the order page reads, so the list and the
        # page cannot disagree: "due 29 Aug" here beside "26 days past the
        # promised lead time" there read as two different orders.
        context["late"] = {
            c["subject"]["id"]: c for c in self.op("checks_list", kinds=["contract_delivery_overdue"])["checks"]
        }
        context["item_names"] = {i["id"]: i["name"] for i in self.op("item_list")}
        context["orgs"] = {o["id"]: o for o in self.op("org_list")}
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        return context


def _link_updates(contract_id):
    """What suppliers reported about this order through their update links.

    Read from the link submissions rather than an operation: they are the
    record of which link a write came through, which the rows themselves do
    not carry. The contract was already fetched through the programme-scoped
    `contract_get`, so this reads nothing the page could not already show.
    """
    from connect_labs.supply_chain.models import Contract
    from connect_labs.supply_chain.update_links import service

    contract = Contract.objects.filter(pk=contract_id).first()
    updates = service.updates_for_contract(contract) if contract is not None else []
    # One panel per organisation, each listing only what came through ITS
    # link. A single panel headed by whoever wrote last put the distributor's
    # dispatch under the partner's name (the IPTSc render).
    panels: dict[int, dict] = {}
    for update in updates:
        panels.setdefault(update["org_id"], {"org": update["org"], "updates": []})["updates"].append(update)
    return list(panels.values())


def _amount(figure):
    """A derived quantity's amount as a Decimal, or None when it is not one."""
    from decimal import Decimal, InvalidOperation

    if not isinstance(figure, dict) or figure.get("amount") is None:
        return None
    try:
        return Decimal(str(figure["amount"]))
    except (InvalidOperation, ValueError):
        return None


def _outstanding_after_cover(match):
    """What is still outstanding once the orders covering a shortfall are counted.

    None when nothing covers the order, or when a cover is in another unit and
    cannot be subtracted -- the card then shows the order's own figure rather
    than an arithmetic across units. Never below zero: a cover larger than the
    gap leaves nothing outstanding, not a negative.
    """
    from decimal import Decimal

    from connect_labs.supply_chain.values import decimal_string

    covers = match.get("covered_by") or []
    outstanding = match.get("outstanding")
    remaining = _amount(outstanding)
    if not covers or remaining is None:
        return None
    unit = outstanding.get("unit")
    for cover in covers:
        if cover.get("quantity") is None or cover.get("quantity_unit") != unit:
            return None
        remaining -= Decimal(str(cover["quantity"]))
    return {"amount": decimal_string(max(remaining, Decimal(0))), "unit": unit}


def _tellers(contract, points, orgs, suppliers):
    """Whose word a reported row on this order is: {source: organisation name}.

    A partner's is the organisation that runs the store the goods go to, else
    a buyer that is not the programme; a supplier's is the supplier. Lets a
    receipt the programme took down from a partner say so, rather than read
    as the programme's own.
    """
    point = next((p for p in points if p["id"] == contract.get("delivery_supply_point_id")), None)
    partner_id = (point or {}).get("managed_by_org_id")
    if partner_id is None and contract.get("buyer_of_record") != "programme_org":
        partner_id = contract.get("buyer_org_id")
    supplier = suppliers.get(contract.get("supplier_id")) or {}
    tellers = {"supplier_reported": (orgs.get(supplier.get("org_id")) or {}).get("name") or supplier.get("name")}
    if partner_id is not None and orgs.get(partner_id):
        tellers["partner_reported"] = orgs[partner_id]["name"]
    return {source: name for source, name in tellers.items() if name}


class OrderDetailView(OperationBase):
    """One order, with the two derivations that decide whether to pay it.

    `contract_landed_cost` under each of the three buyers of record, so the
    tax consequence of the routing choice is visible rather than implied.
    `contract_match` for ordered against received against invoiced, whose
    `payable_now` is the value of what ARRIVED.
    """

    template_name = "supply_chain/order_detail.html"

    def _fulfilment(self, contract, match):
        """How an order that ran short was filled: this order plus each order covering it.

        One view of the whole answer to "did the 700 arrive" -- the distributor's
        450 and the partner's 250 -- where the page otherwise showed only this
        order's own half and a link to the other. Only for an order something
        covers; for any other it would restate the match card beside it.
        Received is summed only when every source reports it in the order's
        own unit, and is otherwise left out rather than added across units.
        """
        if not match.get("covered_by"):
            return None
        unit = contract.get("quantity_unit")
        suppliers = {s["id"]: s["name"] for s in self.op("supplier_list")}
        sources = [
            {
                "contract_id": contract["id"],
                "reference": contract.get("reference") or f"order {contract['id']}",
                "supplier": suppliers.get(contract["supplier_id"], ""),
                "role": "this order — what it delivered",
                "contributes": match.get("received"),
                "received": match.get("received"),
            }
        ]
        for cover in match["covered_by"]:
            cover_match = self.op("contract_match", contract_id=cover["contract_id"])
            sources.append(
                {
                    "contract_id": cover["contract_id"],
                    "reference": cover.get("reference") or f"order {cover['contract_id']}",
                    "supplier": cover["supplier"]["name"],
                    "role": "covers the shortfall",
                    "contributes": {"amount": cover.get("quantity"), "unit": cover.get("quantity_unit")},
                    "received": cover_match.get("received"),
                }
            )
        received = [_amount(s["received"]) for s in sources]
        same_unit = all(isinstance(s["received"], dict) and s["received"].get("unit") == unit for s in sources)
        total = sum(received) if same_unit and None not in received else None
        from connect_labs.supply_chain.values import decimal_string

        return {
            "ordered": match.get("ordered"),
            "sources": sources,
            "received_total": {"amount": decimal_string(total), "unit": unit} if total is not None else None,
            "complete": total is not None and total >= (_amount(match.get("ordered")) or 0),
        }

    def _award(self, contract):
        """The award this order was placed against, and that award's approvals.

        (None, []) for an order placed without one. The approvals are read the
        way the award page reads them, so the two pages cannot disagree.
        """
        from connect_labs.supply_chain.procurement.views import approvals_as_read

        award_id = contract.get("award_id")
        if not award_id:
            return None, []
        awards = self.op("award_list", round_id=contract["round_id"]) if contract.get("round_id") else []
        award = next((a for a in awards if a["id"] == award_id), None)
        if award is None:
            awards = self.op("award_list")
            award = next((a for a in awards if a["id"] == award_id), None)
        if award is None:
            return None, []
        award["supplier_name"] = next(
            (s["name"] for s in self.op("supplier_list") if s["id"] == award["supplier_id"]), ""
        )
        return award, approvals_as_read(self.op, award_id)

    def _where_now(self, contract, item):
        """Where durable equipment from this order is held now, by supply point.

        Equipment is kept and moved, never used up, so after it arrives the
        question an order leaves is "where did it go": the dispenser import
        closed on the item page to answer it, away from the order that says
        how many came and how many were refused. The ledger holds the item,
        not the order, so the panel says so when other orders of the same item
        also feed those balances. Only points holding something; None for any
        item that is used up, where cover and resupply are the stock page's.
        """
        if not item or item.get("stock_class") != "durable":
            return None
        points = [
            p
            for p in self.op("network_stock", item_id=item["id"])["points"]
            if _amount(p.get("on_hand")) not in (None, 0)
        ]
        units = {p["on_hand"].get("unit") for p in points}
        total = None
        if points and len(units) == 1:
            from connect_labs.supply_chain.values import decimal_string

            total = {"amount": decimal_string(sum(_amount(p["on_hand"]) for p in points)), "unit": units.pop()}
        others = [c for c in self.op("contract_list") if c.get("item_id") == item["id"] and c["id"] != contract["id"]]
        return {"points": points, "total": total, "other_orders": len(others)}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        contract_id = int(kwargs["contract_id"])
        # A contract that is not in this programme is a 404, not a 500. Without
        # this, `contract_landed_cost` below is handed a missing contract and
        # raises deep in a derivation, so a mistyped or stale URL returns a
        # server error that names nothing.
        contract = self.op("contract_get", contract_id=contract_id)
        if contract is None:
            raise Http404(f"no contract {contract_id} in this programme")
        context["contract"] = contract
        context["landed"] = self.op("contract_landed_cost", contract_id=contract_id, compare_buyers=True)
        context["match"] = self.op("contract_match", contract_id=contract_id)
        context["fulfilment"] = self._fulfilment(contract, context["match"])
        context["outstanding_after_cover"] = _outstanding_after_cover(context["match"])
        # The trade item, by name, for the header: the page said who and how many
        # but never what.
        context["item"] = (
            next((i for i in self.op("item_list") if i["id"] == contract["item_id"]), None)
            if contract.get("item_id")
            else None
        )
        context["award"], context["award_approvals"] = self._award(contract)

        context["where_now"] = self._where_now(contract, context["item"])
        # The short order this one covers, by the reference people use for it.
        if contract.get("covers_shortfall_of_id"):
            context["covers"] = self.op("contract_get", contract_id=contract["covers_shortfall_of_id"])
        context["shipments"] = self.op("shipment_list", contract_id=contract_id)
        context["receipts"] = self.op("receipt_list", contract_id=contract_id)
        # Where each receipt landed, by name: the received table said what
        # arrived and never where.
        points = self.op("supply_point_list")
        context["supply_points"] = {p["id"]: p["name"] for p in points}
        context["invoices"] = self.op("invoice_list", contract_id=contract_id)
        # The order's evidence includes what was filed against its consignments:
        # four import documents on file read "Nothing attached" here (the
        # dispenser import), because they hang off the shipment.
        documents = self.op("document_list", contract_id=contract_id)
        seen = {d["id"] for d in documents}
        for shipment in context["shipments"]:
            filed = self.op("document_list", shipment_id=shipment["id"])
            kinds = {d["kind"] for d in filed}
            required = shipment.get("required_documents") or []
            shipment["required_on_file"] = sum(1 for e in required if e.get("kind") in kinds)
            for document in filed:
                if document["id"] not in seen:
                    seen.add(document["id"])
                    documents.append({**document, "shipment_reference": shipment.get("reference")})
        context["documents"] = documents
        context["orgs"] = {o["id"]: o for o in self.op("org_list")}
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        context["tellers"] = _tellers(contract, points, context["orgs"], context["suppliers"])
        context["link_updates"] = _link_updates(contract_id)
        # Lateness, read from the checks rather than recomputed here, so this
        # page and the checks feed cannot disagree about whether it is late.
        shipment_ids = {s["id"] for s in context["shipments"]}
        late = self.op("checks_list", kinds=["contract_delivery_overdue", "shipment_overdue"])["checks"]
        context["contract_late"] = next(
            (c for c in late if c["kind"] == "contract_delivery_overdue" and c["subject"]["id"] == contract_id), None
        )
        if context["contract_late"]:
            from datetime import date

            context["contract_due_on"] = date.fromisoformat(context["contract_late"]["facts"]["expected_on"])
        context["late_shipments"] = {
            c["subject"]["id"]: c
            for c in late
            if c["kind"] == "shipment_overdue" and c["subject"]["id"] in shipment_ids
        }
        return context


class ShipmentDetailView(OperationBase):
    """One consignment: what it carries, what it needs to clear, and what landing it cost.

    The page an import is worked from. The documents it requires are a
    checklist -- each one on file or outstanding, and who owes it -- derived
    from the same rule as the `shipment_documents_outstanding` check: a
    document of that kind attached to this shipment. The charges paid to
    land it sit beside them, because they are paid at the same port by the
    same people.
    """

    template_name = "supply_chain/shipment_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        shipment_id = int(kwargs["shipment_id"])
        shipment = self.op("shipment_get", shipment_id=shipment_id)
        if shipment is None:
            raise Http404(f"no shipment {shipment_id} in this programme")
        contract = self.op("contract_get", contract_id=shipment["contract_id"])
        orgs = {o["id"]: o for o in self.op("org_list")}
        documents = self.op("document_list", shipment_id=shipment_id)
        by_kind: dict[str, list] = {}
        for document in documents:
            by_kind.setdefault(document["kind"], []).append(document)

        context["shipment"] = shipment
        context["contract"] = contract
        context["supplier"] = self.op("supplier_get", supplier_id=contract["supplier_id"])
        context["orgs"] = orgs
        context["documents"] = documents
        context["checklist"] = [
            {
                "kind": entry["kind"],
                "owed_by": orgs.get(entry.get("owed_by_org_id")),
                "documents": by_kind.get(entry["kind"], []),
            }
            for entry in shipment.get("required_documents") or []
        ]
        context["outstanding_count"] = sum(1 for line in context["checklist"] if not line["documents"])
        context["charges"] = self.op("charge_list", shipment_id=shipment_id)
        late = self.op("checks_list", kinds=["shipment_overdue"])["checks"]
        context["late"] = next((c for c in late if c["subject"]["id"] == shipment_id), None)
        return context


class StockView(OperationBase):
    """Stock across the network: the ledger, what was reported, and the gap.

    Both figures, always. They disagree routinely and the disagreement is the
    finding; a screen showing one would destroy the only signal it had.
    """

    template_name = "supply_chain/stock.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context

        opportunity_id = self.request.GET.get("opportunity_id") or None
        item_id = self.request.GET.get("item_id") or None
        context["items"] = self.op("item_list")
        context["item_id"] = int(item_id) if item_id else None
        context["opportunity_id"] = opportunity_id
        context["network"] = self.op(
            "network_stock",
            **{
                k: v
                for k, v in (
                    ("opportunity_id", int(opportunity_id) if opportunity_id else None),
                    ("item_id", context["item_id"]),
                )
                if v is not None
            },
        )
        from connect_labs.supply_chain.stock.services.resupply import NO_CONSUMPTION_YET

        # What each balance is a balance of, and whether the point has ever
        # dispensed: with no consumption, demand, months, resupply and band are
        # one fact, said once.
        names = {i["id"]: i["name"] for i in context["items"]}
        for point in context["network"].get("points") or []:
            point["item_name"] = names.get(point.get("item_id"))
            amc = point.get("amc")
            point["no_consumption_yet"] = isinstance(amc, dict) and NO_CONSUMPTION_YET in (
                amc.get("unconfirmed") or []
            )
            on_hand = point.get("on_hand")
            point["stocked_out"] = (
                isinstance(on_hand, dict)
                and on_hand.get("amount") not in (None, "")
                and (float(on_hand["amount"]) <= 0)
            )
        return context


class MovementsView(OperationBase):
    """The movements behind one supply point's balance, newest first.

    What the stock page's ledger figure is made of: "700 packets" is the two
    receipts of 450 and 250, and a reader could not see that anywhere. Read
    through `movement_list`, which the API already offered; nothing here
    derives a figure of its own.
    """

    template_name = "supply_chain/movements.html"

    @staticmethod
    def _id(value):
        try:
            return int(value) if value else None
        except ValueError:
            raise Http404("not a record id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        point_id = self._id(self.request.GET.get("supply_point_id"))
        item_id = self._id(self.request.GET.get("item_id"))
        points = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        if point_id is not None and point_id not in points:
            raise Http404(f"no supply point {point_id} in this programme")
        context["point"] = points.get(point_id)
        context["points"] = {pk: p["name"] for pk, p in points.items()}
        context["item"] = next((i for i in self.op("item_list") if i["id"] == item_id), None) if item_id else None
        context["movements"] = self.op(
            "movement_list",
            **{k: v for k, v in (("supply_point_id", point_id), ("item_id", item_id)) if v is not None},
        )
        # Each receipt by the note number people use for it, and its order.
        receipts = self.op("receipt_list", supply_point_id=point_id) if point_id is not None else []
        context["receipts"] = {r["id"]: r for r in receipts}
        contracts = self.op("contract_list")
        context["references"] = {c["id"]: c["reference"] or f"order {c['id']}" for c in contracts}
        # A receipt has no supply point it came FROM -- it came from a supplier.
        # "—" hid Harmattan and Tamarind behind the one column that should name them.
        suppliers = {s["id"]: s["name"] for s in self.op("supplier_list")}
        context["received_from"] = {
            c["id"]: suppliers.get(c.get("supplier_id")) for c in contracts if c.get("supplier_id") in suppliers
        }
        # The page says "a balance is these movements added up": show the sum it
        # means, from the same ledger read the stock page uses, in the unit the rows
        # share. Only for one item at one point -- across items there is no one sum.
        context["balance"] = None
        if point_id is not None and item_id is not None and context["movements"]:
            units = {m.get("quantity_unit") for m in context["movements"]}
            unit = units.pop() if len(units) == 1 else None
            on_hand = self.op("stock_on_hand", supply_point_id=point_id, item_id=item_id, unit=unit)
            context["balance"] = on_hand.get("ledger")
        return context


class DistributionView(OperationBase):
    """Resupply runs out to field workers, and what each line moved."""

    template_name = "supply_chain/distribution.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        context["runs"] = self.op("distribution_list")
        context["points"] = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        return context


class ProductDetailView(OperationBase):
    """One product: its specification, its trade items, and who can supply it.

    The catalogue page answers "what can this programme buy against". This one
    answers the question a buyer actually arrives with -- *who sells this, what
    have they charged, and have we ever bought it* -- which is spread across
    four tiers and was reachable only through the API.

    The supply base here is derived, never stored. See
    `procurement/services/supply_base.py` for why: a list of who supplies what
    is an assertion nobody made, and it would read the same the day it was
    typed and a year after the supplier stopped replying.
    """

    template_name = "supply_chain/product_detail.html"

    def get_context_data(self, slug, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)

        product = next((c for c in self.op("commodity_list") if c["slug"] == slug), None)
        if product is None:
            raise Http404(f"no product '{slug}' in this catalogue")
        all_items = self.op("item_list")
        items = [i for i in all_items if i["commodity_slug"] == slug]
        context["product"] = annotate_product(product, items, self.op("commodity_list"), all_items)

        if not context["has_program_context"]:
            # The specification is reference data and reads fine on its own.
            # Everything below is programme-scoped, so it is omitted rather
            # than half-rendered.
            return context

        context["supply_base"] = self.op("commodity_supply_base", commodity_slug=slug)
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        context["rounds"] = {r["id"]: r for r in self.op("round_list")}
        context["sourced_in"] = [
            r
            for r in context["rounds"].values()
            if any((line or {}).get("commodity_slug") == slug for line in (r.get("lines") or []))
        ]
        context["quotes"] = newest_standing_first([q for q in self.op("quote_list") if q["commodity_slug"] == slug])
        context["contracts"] = [c for c in self.op("contract_list") if c["commodity_slug"] == slug]
        context["items_by_id"] = {i["id"]: i for i in items}
        return context


class ItemDetailView(OperationBase):
    """One trade item -- the level you can actually order and actually count.

    A product cannot be shipped and a batch has not arrived yet; this is the
    layer in between, and it is the only one that knows how many sachets are
    in the carton. So this page carries the three things that hang off that
    fact: what it was quoted at, what was contracted, and what is on hand.
    """

    template_name = "supply_chain/item_detail.html"

    def get_context_data(self, item_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)

        item = self.op("item_get", item_id=item_id)
        if item is None:
            raise Http404(f"no trade item {item_id} in this catalogue")
        products = self.op("commodity_list")
        product = next((c for c in products if c["slug"] == item["commodity_slug"]), None)
        if product is not None:
            # Annotated through its own product so the verdict, the sibling
            # disagreements and the GTIN list are computed the one way.
            siblings = [i for i in self.op("item_list") if i["commodity_slug"] == item["commodity_slug"]]
            annotate_product(product, siblings, products)
            item = next((i for i in siblings if i["id"] == item["id"]), item)
        context["item"] = item
        context["product"] = product

        if not context["has_program_context"]:
            return context

        context["supply_base"] = self.op(
            "commodity_supply_base", commodity_slug=item["commodity_slug"], item_id=item["id"]
        )
        context["suppliers"] = {s["id"]: s for s in self.op("supplier_list")}
        context["rounds"] = {r["id"]: r for r in self.op("round_list")}
        context["quotes"] = newest_standing_first([q for q in self.op("quote_list") if q["item_id"] == item["id"]])
        context["contracts"] = [c for c in self.op("contract_list") if c["item_id"] == item["id"]]
        context["documents"] = self.op("document_list", item_id=item["id"])
        # Stock is per trade item, never per product: two manufacturers' RUTF
        # in one store are two balances, and adding them needs the pack
        # specification this page is about.
        context["network"] = self.op("network_stock", item_id=item["id"])
        return context


class SupplierDirectoryView(OperationBase):
    """Who we can buy from, and how far each relationship has got.

    Replaces a "Registries" page that listed suppliers beside a second copy of
    the commodity table, was linked from nowhere, and whose only supplier
    columns were name, country, status and a GLN the model does not have. The
    commodities live on the Catalogue tab; this page is about the companies.
    """

    template_name = "supply_chain/suppliers.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        suppliers = self.op("supplier_list")
        context["suppliers"] = suppliers
        if not context["has_program_context"]:
            return context

        quotes = self.op("quote_list")
        outreach = self.op("outreach_list")
        contracts = self.op("contract_list")
        for supplier in suppliers:
            own_quotes = [q for q in quotes if q["supplier_id"] == supplier["id"]]
            own_outreach = [o for o in outreach if o["supplier_id"] == supplier["id"]]
            supplier["quote_count"] = len([q for q in own_quotes if not q["voided"]])
            supplier["invitation_count"] = len(own_outreach)
            supplier["contract_count"] = len([c for c in contracts if c["supplier_id"] == supplier["id"]])
            # The most recent thing that happened either way, so a directory
            # sorted by name still shows at a glance who has gone quiet.
            dates = [q["received_on"] for q in own_quotes if q.get("received_on")]
            dates += [o["sent_on"] for o in own_outreach if o.get("sent_on")]
            supplier["last_activity"] = max(dates) if dates else None
        return context


class SupplierDetailView(OperationBase):
    """One supplier, and everything this programme has ever done with them.

    A supplier is reference data reused across rounds, so their history is
    scattered by design: invitations sit under rounds, quotes under rounds
    again, contracts under the programme, and receipts and invoices under the
    contracts. Reading it meant four screens and an id in your head. This
    gathers it in the order it happened to them -- we asked, they answered, we
    chose, we ordered, it arrived, they billed us.
    """

    template_name = "supply_chain/supplier_detail.html"

    def get_context_data(self, supplier_id, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)

        supplier = self.op("supplier_get", supplier_id=supplier_id)
        if supplier is None:
            raise Http404(f"no supplier {supplier_id} on file")
        context["supplier"] = supplier

        if not context["has_program_context"]:
            return context

        rounds = {r["id"]: r for r in self.op("round_list")}
        context["rounds"] = rounds

        outreach = [o for o in self.op("outreach_list") if o["supplier_id"] == supplier_id]
        for invitation in outreach:
            invitation["round"] = rounds.get(invitation["round_id"])
        context["outreach"] = outreach

        # Each quote's derived figures come from `quote_get` rather than being
        # recomputed here, so the number on this page and the number on the
        # quote's own page cannot drift apart.
        quotes = []
        for quote in self.op("quote_list"):
            if quote["supplier_id"] != supplier_id:
                continue
            detail = self.op("quote_get", quote_id=quote["id"])
            quotes.append(
                {
                    **quote,
                    "round": rounds.get(quote["round_id"]),
                    "figures": (detail or {}).get("figures") or {},
                    "unanswered": len((detail or {}).get("missing") or []),
                }
            )
        context["quotes"] = newest_standing_first(quotes)

        context["awards"] = [a for a in self.op("award_list") if a["supplier_id"] == supplier_id]

        contracts = [c for c in self.op("contract_list") if c["supplier_id"] == supplier_id]
        for contract in contracts:
            contract["shipments"] = self.op("shipment_list", contract_id=contract["id"])
            contract["receipts"] = self.op("receipt_list", contract_id=contract["id"])
            contract["invoices"] = self.op("invoice_list", contract_id=contract["id"])
        context["contracts"] = contracts

        context["documents"] = self.op("document_list", supplier_id=supplier_id)
        commodities = {c["slug"]: c for c in self.op("commodity_list")}
        context["commodities"] = commodities
        context["items_by_id"] = {i["id"]: i for i in self.op("item_list")}

        # What this supplier is connected to, read back off the same evidence
        # the product pages use rather than off a list somebody maintained.
        # Asked per product because the derivation is per product: the answer
        # to "do they supply RUTF" is not the answer to "do they supply F-75".
        supplies = []
        for slug, commodity in commodities.items():
            claim = next(
                (c for c in self.op("commodity_supply_base", commodity_slug=slug) if c["supplier_id"] == supplier_id),
                None,
            )
            if claim:
                supplies.append({**claim, "slug": slug, "name": commodity["name"]})
        context["supplies"] = supplies
        return context


@login_required
def document_open(request, document_id):
    """Open one document: its link, or a short-lived signed URL to the stored file.

    Signed on the click rather than stored, because a stored file lives in a
    private bucket and a signed URL expires. Scoped through the programme's
    data access, so a document id from another programme is a 404.
    """
    from django.shortcuts import redirect

    try:
        url = _access(request).document_url(int(document_id))
    except ValueError as error:
        raise Http404(str(error)) from error
    return redirect(url)
