"""All procurements in a program, one row each, for the overview at /supply/.

One row per tender still in progress (not yet ordered) and per active order.
Each row says where it is on the six-step stage bar, its next move and whose
that move is, and the last change anywhere under it (when, and who told us).

The moves come from `moves.py` and nowhere else: the same coarse rules the
tender page lists under "On us" and "On suppliers", so the overview and the
page cannot disagree, and no screen invents a judgement of its own.

Rows are ordered by last change, newest first -- not ranked. `today` and
`until` come from the caller; under as-of the view runs inside a rewound
transaction, so the rows read here are the past, and `today` is the as-of day.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain import records
from connect_labs.supply_chain.history.labels import actor_label, is_ai
from connect_labs.supply_chain.history.timeline import contract_scope_revisions, tender_scope_revisions

# The six steps every procurement moves through, tender to delivery.
# Steps, not outcomes: an order held at customs is in its Delivery step, which a step named
# "Delivered" would have claimed done. All six done is delivered.
STAGES = ("Requested", "Collecting quotes", "Comparing", "Awarding", "Ordered", "Delivery")

# The order's chain, in order; the stage is the furthest one reached.
ORDER_STAGES = ("placed", "dispatched", "received", "invoiced", "paid")
IN_TRANSIT = "in transit"
DELIVERED_AND_PAID = "delivered and paid"


def stage_bars(index: int) -> list[str]:
    """ "done" / "now" / "todo" for each of the six steps; `index` is the step under way (6: all done)."""
    return ["done" if i < index else "now" if i == index else "todo" for i in range(len(STAGES))]


@dataclass
class Row:
    kind: str  # "tender" | "order"
    title: str
    url: str
    stage_index: int
    stage: str
    last_change_at: datetime | None
    last_change_by: str
    last_change_is_ai: bool
    sub: str = ""
    ours: list = field(default_factory=list)
    theirs: list = field(default_factory=list)
    last_change_badge: str = ""
    last_change_title: str = ""
    buyer: str = ""
    origin: str = ""
    contract_id: int | None = None
    tender_id: int | None = None
    # The order page's own step name ("At customs — held"); blank for a tender.
    step_label: str = ""
    # Quote facts still absent on a tender: [(fact, number of quotes)], from the comparison's gaps.
    missing: list = field(default_factory=list)
    # How many quotes the comparison can rank, and whose: "1 of 3 comparable · Harmattan".
    comparable_chip: str = ""
    comparable_count: int = 0

    @property
    def bars(self) -> list[str]:
        return stage_bars(self.stage_index)

    @property
    def label(self) -> str:
        return self.step_label or self.stage

    @property
    def stage_name(self) -> str:
        """The step the bar's current segment stands for ("Collecting quotes")."""
        return self.label.split(" · ", 1)[0]

    @property
    def stage_detail(self) -> str:
        """What follows the step name ("4 of 6 answered")."""
        parts = self.label.split(" · ", 1)
        return parts[1] if len(parts) > 1 else ""

    @property
    def silent_names(self) -> list[str]:
        """The suppliers the no-reply rule lists on this row, by name."""
        return [m.text.split(": reply")[0] for m in self.theirs if m.rule == rules.RULE_NO_REPLY]

    @property
    def silent_chips(self) -> list[tuple[str, str]]:
        """[(name, "Silent 17d")]: each silent supplier with the chip the tender page gives it."""
        return [(m.text.split(": reply")[0], m.chip or "Silent") for m in self.theirs if m.rule == rules.RULE_NO_REPLY]

    @property
    def move_lines(self) -> list:
        """One line per counted move, ours first: every move but the no-reply ones, which share one line."""
        return list(self.ours) + [m for m in self.theirs if m.rule != rules.RULE_NO_REPLY]

    @property
    def other_count(self) -> int:
        """The moves on the party whose move is NOT the row's next one."""
        whose = self.whose
        return len(self.theirs) if whose == rules.US else len(self.ours) if whose == rules.SUPPLIERS else 0

    @property
    def next_move(self):
        return rules.first_move(self.ours, self.theirs)[0]

    @property
    def whose(self) -> str:
        return rules.first_move(self.ours, self.theirs)[1]


def standing_rows(program_id: int, today: date, *, until: date | None = None, own_org_id=None) -> list[Row]:
    """Every tender in progress and every active order in the program, newest change first.

    `own_org_id` is the organisation the program acts as (identity.resolve_org);
    an order bought by anyone else says so on its row.
    """
    rows = _tender_rows(program_id, today, until) + _order_rows(program_id, today, until, own_org_id)
    rows.sort(key=lambda r: (r.last_change_at is not None, r.last_change_at or datetime.min), reverse=True)
    return rows


def move_counts(rows) -> dict:
    """{"ours": n, "theirs": m}: the moves the rows list, counted from the rows themselves."""
    return {"ours": sum(len(r.ours) for r in rows), "theirs": sum(len(r.theirs) for r in rows)}


def our_moves(rows) -> int:
    return move_counts(rows)["ours"]


# ---- words ---------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _words(value: str) -> str:
    return (value or "").replace("_", " ")


def _ai_badge(label: str, call, revision=None) -> tuple[str, str]:
    """The AI pill's words and its title: who told us, and what they recorded from what."""
    agent = label.endswith(" (agent)")
    if agent:
        who = label.removesuffix(" (agent)")
    elif label.startswith("via AI · "):
        who = f"{label.removeprefix('via AI · ')} via an AI assistant"
    else:
        who = "An AI assistant"
    verb = _ACTION_VERBS.get(getattr(revision, "action", ""), "recorded")
    title = f"{who} {verb} {_record_words(revision)}"
    ref = getattr(call, "source_ref", "") or ""
    if "@" in ref:
        title += " from a forwarded email" if agent else " from an email"
    elif ref:
        title += " from a document"
    return label, title


_ACTION_VERBS = {"create": "recorded", "update": "updated", "delete": "removed"}


def _record_words(revision) -> str:
    """Which record a revision is on, as the timeline names it: "Quote · Northwind Foods"."""
    from connect_labs.supply_chain.history.labels import Lookup, _values_of, subject

    model = revision.content_type.model_class() if revision is not None else None
    if model is None:
        return "a change"
    lookup = Lookup()
    if revision.action == "create":
        values = {k: v[1] for k, v in (revision.changes or {}).items()}
    elif revision.action == "delete":
        values = dict(revision.changes or {})
    else:
        row = lookup.row(model, revision.object_id)
        values = _values_of(row) if row is not None else None
    return subject(model, values, lookup)


def _last_change(revisions):
    latest = revisions.first()
    if latest is None:
        return {"last_change_at": None, "last_change_by": "", "last_change_is_ai": False}
    label = actor_label(latest.call)
    ai = is_ai(latest.call)
    badge, title = _ai_badge(label, latest.call, latest) if ai else ("", "")
    return {
        "last_change_at": latest.recorded_at,
        "last_change_by": label,
        "last_change_is_ai": ai,
        "last_change_badge": badge,
        "last_change_title": title,
    }


# ---- tenders -------------------------------------------------------------


def _replied_ids(outreach, quotes) -> set:
    """The invited suppliers that answered: a reply logged, or a live quote from them."""
    invited = {o.supplier_id for o in outreach}
    replied = {o.supplier_id for o in outreach if o.responded} | {q.supplier_id for q in quotes if q.is_live}
    return replied & invited


def _chasing(tender, contracted, provisional) -> bool:
    """Replies are still being chased: the tender is open, or provisionally awarded and not yet ordered."""
    return rules.collecting(tender, provisional=provisional, contracted=contracted)


def awaiting_reply(program_id) -> dict:
    """{"suppliers": n, "tenders": m}: the silent suppliers rule (e) lists, counted."""
    from connect_labs.supply_chain.models import Award, Outreach, Quote, Tender

    tenders = {t.pk: t for t in Tender.objects.filter(program_id=program_id, status__in=("open", "awarded"))}
    if not tenders:
        return {"suppliers": 0, "tenders": 0}
    outreach, quotes = {}, {}
    for o in Outreach.objects.filter(tender_id__in=tenders):
        outreach.setdefault(o.tender_id, []).append(o)
    for q in Quote.objects.filter(tender_id__in=tenders):
        quotes.setdefault(q.tender_id, []).append(q)
    contracted = set(Tender.objects.filter(pk__in=tenders, contracts__isnull=False).values_list("pk", flat=True))
    newest = {}
    for award in Award.objects.filter(tender_id__in=tenders):
        newest.setdefault(award.tender_id, award)
    suppliers = count = 0
    for pk, tender in tenders.items():
        provisional = pk in newest and newest[pk].provisional
        if not _chasing(tender, pk in contracted, provisional):
            continue
        silent = rules.silent_suppliers(outreach.get(pk, []), quotes.get(pk, []))
        if silent:
            suppliers += len(silent)
            count += 1
    return {"suppliers": suppliers, "tenders": count}


def tender_stage(tender, *, invited=0, answered=0, comparable=None, quoted=None, awardee="", today=None):
    """(step index, words) for a tender not yet ordered."""
    if tender.status == "draft":
        return 0, "Draft · not yet sent"
    if tender.status == "open":
        words = "Collecting quotes"
        if invited:
            words += f" · {answered} of {invited} answered"
        # The deadline is said once on the overview: in the row's decide move, not here.
        return 1, words
    if tender.status == "awarded":
        return 3, f"Awarding · to {awardee}" if awardee else "Awarding"
    words = "Comparing"
    if comparable is not None and quoted:
        words += f" · {comparable} of {quoted} comparable"
    return 2, words


def _tender_rows(program_id, today, until):
    from connect_labs.supply_chain.models import Award, Commitment, Outreach, Quote, Tender

    tenders = list(Tender.objects.filter(program_id=program_id).exclude(contracts__isnull=False).distinct())
    ids = [t.pk for t in tenders]
    outreach, quotes, owed, awards = {}, {}, {}, {}
    for o in Outreach.objects.filter(tender_id__in=ids).select_related("supplier__org"):
        outreach.setdefault(o.tender_id, []).append(o)
    for q in Quote.objects.filter(tender_id__in=ids).select_related("supplier__org", "commodity", "item"):
        quotes.setdefault(q.tender_id, []).append(q)
    for c in Commitment.objects.filter(
        program_id=program_id, tender_id__in=ids, resolved_on__isnull=True
    ).select_related("owed_to_org"):
        owed.setdefault(c.tender_id, []).append(c)
    for award in Award.objects.filter(tender_id__in=ids).select_related("quote__supplier__org"):
        awards.setdefault(award.tender_id, award)

    rows = []
    for tender in tenders:
        award = awards.get(tender.pk)
        ours, theirs = rules.tender_moves(
            tender,
            today,
            outreach=outreach.get(tender.pk, []),
            quotes=quotes.get(tender.pk, []),
            commitments=owed.get(tender.pk, []),
            provisional=bool(award and award.provisional),
            contracted=False,
        )
        # Inside the tender's own row its name is already said: the decide move drops it.
        for m in ours:
            if m.rule == rules.RULE_DEADLINE:
                m.text = "Decide: extend, close or award"
        invited = {o.supplier_id for o in outreach.get(tender.pk, [])}
        answered = _replied_ids(outreach.get(tender.pk, []), quotes.get(tender.pk, []))
        awardee = award.quote.supplier.name if tender.status == "awarded" and award and award.quote_id else ""
        index, stage = tender_stage(tender, invited=len(invited), answered=len(answered), awardee=awardee, today=today)
        asked = min((o.sent_on for o in outreach.get(tender.pk, []) if o.sent_on), default=None)
        where = ", ".join(p.get("city") or p.get("name") or "" for p in tender.delivery_points or [] if p)
        sub = " · ".join(
            part
            for part in (
                f"to {where}" if where else "",
                f"{_plural(len(invited), 'supplier')} asked {_day(asked)}" if invited and asked else "",
            )
            if part
        )
        rows.append(
            Row(
                kind="tender",
                title=tender.label,
                url=reverse("supply_chain:procurement_tender_detail", args=[tender.pk]),
                tender_id=tender.pk,
                stage_index=index,
                stage=stage,
                sub=sub,
                ours=ours,
                theirs=theirs,
                missing=_missing_facts(tender, quotes.get(tender.pk, [])),
                **_comparable(tender, quotes.get(tender.pk, [])),
                **_last_change(tender_scope_revisions(tender.pk, program_id=program_id, until=until, orders=False)),
            )
        )
    return rows


def _comparable(tender, quotes) -> dict:
    """The tender's comparable-count chip, from the comparison itself; empty before any quote."""
    if not any(q.is_live for q in quotes):
        return {}
    from connect_labs.supply_chain.procurement.status import comparable_chip, comparisons

    compared = comparisons(tender, quotes)
    return {
        "comparable_chip": comparable_chip(compared),
        "comparable_count": sum(len(c.comparable) for c in compared),
    }


def _missing_facts(tender, quotes) -> list:
    """[(fact, quotes lacking it)]: each quote's open facts -- the list the comparison's headers
    count -- counted per fact across the tender's quotes, comparable or not."""
    if not any(q.is_live for q in quotes):
        return []
    from connect_labs.supply_chain.procurement.status import (
        _ROUND_DUTY,
        asked_since_quote,
        comparisons,
        fact_owner,
        quote_open_facts,
        waiver_on_file,
    )

    counts, owners = {}, {}
    by_id = {q.pk: q for q in quotes}
    on_file = waiver_on_file(tender)
    for comparison in comparisons(tender, quotes):
        for row in (*comparison.comparable, *comparison.blocked):
            quote = by_id.get(row.quote_id)
            asked = asked_since_quote(quote)
            for gap in quote_open_facts(tender, row, quote, waiver_on_file=on_file):
                fact = "duty terms" if gap == _ROUND_DUTY else gap
                counts[fact] = counts.get(fact, 0) + 1
                # Any quote not yet asked for it makes the fact ours to ask.
                owner = fact_owner(gap, asked)
                owners[fact] = rules.TO_ASK if owners.get(fact) == rules.TO_ASK else owner
    # Each fact carries whose it is, the comparison's own owner chip: (fact, quotes, owner).
    return sorted(((f, n, owners[f]) for f, n in counts.items()), key=lambda t: (-t[1], t[0]))


# ---- orders --------------------------------------------------------------


def _order_rows(program_id, today, until, own_org_id, *, only=None, keep_done=False):
    """The active orders' rows; `only` limits them to these ids, `keep_done` keeps finished ones."""
    from connect_labs.supply_chain.models import Commitment, Contract, Invoice, Payment, Receipt, Shipment

    found = Contract.objects.filter(program_id=program_id).exclude(status="cancelled")
    if only is not None:
        found = found.filter(pk__in=only)
    contracts = list(found.select_related("supplier__org", "buyer_org", "tender"))
    ids = [c.pk for c in contracts]
    shipments = {}
    for s in Shipment.objects.filter(contract_id__in=ids):
        shipments.setdefault(s.contract_id, []).append(s)
    receipts = list(Receipt.objects.filter(contract_id__in=ids).values_list("contract_id", "shipment_id")) + list(
        Receipt.objects.filter(shipment__contract_id__in=ids, contract_id__isnull=True).values_list(
            "shipment__contract_id", "shipment_id"
        )
    )
    received_contracts = {c for c, _ in receipts}
    received_shipments = {s for _, s in receipts if s is not None}
    unlinked = {c for c, s in receipts if s is None}
    with_payment = set(
        Payment.objects.filter(contract_id__in=ids, invoice__isnull=False).values_list("invoice_id", flat=True)
    )
    invoiced, settled, any_paid = set(), {}, set()
    for invoice_id, contract_id, status, amount in (
        Invoice.objects.filter(contract_id__in=ids)
        .exclude(status="rejected")
        .values_list("pk", "contract_id", "status", "amount")
    ):
        invoiced.add(contract_id)
        done = status == "paid" or (amount is None and invoice_id in with_payment)
        settled[contract_id] = settled.get(contract_id, True) and done
        if invoice_id in with_payment:
            any_paid.add(contract_id)
    paid = {c for c, done in settled.items() if done}
    part_paid = any_paid - paid
    owed = {}
    for c in Commitment.objects.filter(contract_id__in=ids, resolved_on__isnull=True).select_related("owed_to_org"):
        owed.setdefault(c.contract_id, []).append(c)

    from connect_labs.supply_chain.fulfilment.services.holds import holds_for

    holds = holds_for(contracts)
    rows = []
    for contract in contracts:
        words = _order_state(
            contract,
            shipments.get(contract.pk, []),
            received_shipments,
            contract.pk in received_contracts,
            contract.pk in invoiced,
            contract.pk in paid,
            contract.pk in part_paid,
            contract.pk in unlinked,
            holds=holds.get(contract.pk, []),
        )
        ours, theirs = rules.contract_moves(
            contract, today, holds=holds.get(contract.pk, []), commitments=owed.get(contract.pk, [])
        )
        delivered = contract.pk in received_contracts and contract.status != "part_received"
        # Done on both counts and nothing owed: no longer an active order.
        if delivered and contract.pk in paid and not ours and not keep_done:
            continue
        index = 4 if contract.status == "draft" else 6 if delivered else 5
        stage = "Ordered · " + words if index == 4 else ("Delivered · " if delivered else "Delivering · ") + words
        title = contract.reference or f"Order {contract.pk}"
        origin = (contract.tender.label or f"tender {contract.tender_id}") if contract.tender_id else ""
        buyer = (
            contract.buyer_org.name
            if contract.buyer_org_id is not None and contract.buyer_org_id != own_org_id
            else ""
        )
        quantity = ""
        if contract.quantity is not None and contract.quantity_unit:
            from connect_labs.supply_chain.values import quantity_phrase

            quantity = quantity_phrase(contract.quantity, contract.quantity_unit)
        sub = " · ".join(
            p for p in (f"from {origin}" if origin else "", quantity, f"bought by {buyer}" if buyer else "") if p
        )
        rows.append(
            Row(
                kind="order",
                title=f"{title} · {contract.supplier.name}",
                url=reverse("supply_chain:order_detail", args=[contract.pk]),
                stage_index=index,
                stage=stage,
                sub=sub,
                ours=ours,
                theirs=theirs,
                tender_id=contract.tender_id,
                step_label=_order_step(
                    contract,
                    shipments.get(contract.pk, []),
                    received_shipments,
                    contract.pk in received_contracts,
                    contract.pk in paid,
                    held=bool(holds.get(contract.pk)),
                ),
                origin=origin,
                contract_id=contract.pk,
                buyer=buyer,
                **_last_change(contract_scope_revisions(contract.pk, program_id=program_id, until=until)),
            )
        )
    return rows


def _order_step(contract, shipments, received_shipments, received, paid, *, held=False) -> str:
    """The order page's own step name for where the order is: "At customs — held", "Received", "Paid"."""
    if contract.status in ("cancelled", "draft"):
        return _words(contract.status).capitalize()
    full = received and contract.status != "part_received"
    moving = [s for s in shipments if s.pk not in received_shipments and s.status not in ("lost", "delivered")]
    in_transit = [s for s in moving if _dispatched(s)]
    if in_transit and not full:
        latest = records.latest_moving_shipment(in_transit)
        name = "In transit"
        if latest is not None and latest.status in ("at_customs", "cleared", "lost"):
            name = latest.status.replace("_", " ").capitalize()
        return name + (" — held" if held else "")
    if full:
        return "Paid" if paid else "Received"
    if received:
        return "Received, part"
    if any(_dispatched(s) for s in shipments):
        return "Dispatched"
    return "Ordered"


def _dispatched(shipment) -> bool:
    return shipment.dispatched_on is not None or shipment.status in (*records.IN_TRANSIT_STATUSES, "delivered")


def _order_state(contract, shipments, received_shipments, received, invoiced, paid, part_paid, unlinked, holds=()):
    """Where an order's goods and money are, in words: "paid, at customs, held"."""
    if contract.status in ("cancelled", "draft"):
        return _words(contract.status)
    reached = {
        "placed": True,
        "dispatched": any(_dispatched(s) for s in shipments),
        "received": received,
        "invoiced": invoiced,
        "paid": paid,
    }
    stage = [s for s in ORDER_STAGES if reached[s]][-1]
    if stage == "received" and contract.status == "part_received":
        stage = "part received"
    elif stage == "invoiced" and part_paid:
        stage = "part paid"
    outstanding = [s for s in shipments if s.pk not in received_shipments and s.status != "lost" and not unlinked]
    in_transit = [s for s in outstanding if _dispatched(s)]
    if in_transit and not received:
        where = IN_TRANSIT
        latest = records.latest_moving_shipment(in_transit)
        if latest is not None and latest.status not in ("dispatched", "in_transit"):
            where = records.shipment_whereabouts(latest.status) + (", held" if holds else "")
        stage = where if stage == "dispatched" else f"{stage}, {where}"
    elif stage == "paid" and received and contract.status != "part_received":
        stage = DELIVERED_AND_PAID
    return stage
