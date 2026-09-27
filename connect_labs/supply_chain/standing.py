"""Where every tender and order in a program stands: one line each, for /supply/.

Each line says what stage the record has reached, what it is waiting on, the
last change anywhere under it (when, and who told us), and any stale flags.
Design doc docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md
§4.1, §4.2.

Two things this deliberately does not do:

  - Rank. Rows are ordered by last change, newest first. A stale flag is a
    stated fact -- "No reply in 20 days from 2 suppliers" -- and whether that
    matters more than the row above it is the reader's judgement, which the
    database does not contain (DomainHomeView's docstring; design doc
    sections 22 and 24).
  - Claim one buyer for the program. A local partner may place an order, so
    an order row names its buyer whenever that is not the program's own
    organisation.

`today` and `until` come from the caller. Under as-of the view already runs
inside a rewound transaction, so the rows read here are the past; `today` is
the as-of date, so "20 days" is counted from it, and `until` cuts the
revision log at the end of that day.

Cost: the rows and their children are read with one query per kind for the
whole program, but each row's last change goes through the Task 7 scope
collectors (`tender_scope_revisions` / `contract_scope_revisions`), about ten
queries a row -- fine for a program's handful of tenders and orders. If a
program ever holds hundreds, batch it: collect every row's scope ids first,
then read the newest revision per scope in one query over the union.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.history.labels import actor_label, is_ai
from connect_labs.supply_chain.history.timeline import contract_scope_revisions, tender_scope_revisions

# An invitation unanswered this many days after it was sent is stale (§4.2).
NO_REPLY_DAYS = 14
# How many silent suppliers the flag names before it counts the rest ("+2").
NAMED_SILENT = 2
PROVISIONAL_STAGE = "awarded, provisional"

# The rule behind each flag, for its title: what raised it, said once.
NO_REPLY_RULE = (
    f"An invited supplier who has neither replied nor quoted {NO_REPLY_DAYS} or more days after "
    "we last asked, while the tender is open."
)
BLOCKED_RULE = (
    "A live quote the comparison cannot rank yet, named with what it is missing -- the same gaps "
    "that keep it out of the comparison ranking (freight, duties, the pack, the quantity...)."
)
AWARDED_GAP_RULE = (
    "The awarded quote against the product's specification: a requirement it states no figure for, "
    "or one its figure does not meet."
)
ETA_RULE = "A shipment whose expected arrival day has passed and that has not been received."

# The order's chain, in order; the stage is the furthest one reached.
ORDER_STAGES = ("placed", "dispatched", "received", "invoiced", "paid")
# How a stage reads on the overview where the chain's own word is not what a
# program manager would say: goods dispatched and not yet arrived are in
# transit, and an order both received and paid is done on both counts.
IN_TRANSIT = "in transit"
DELIVERED_AND_PAID = "delivered and paid"


class Flag(str):
    """A flag's words, carrying the rule that raised it -- shown as its title.

    `heading` and `lines`, when a flag names several things: "Can't compare
    yet" over one line per supplier ("Northwind Foods: freight"). The string
    itself still says all of it on one line, for anything reading it as text.
    """

    rule: str = ""
    heading: str = ""
    lines: tuple = ()

    def __new__(cls, text, rule="", *, heading="", lines=()):
        flag = super().__new__(cls, text)
        flag.rule = rule
        flag.heading = heading
        flag.lines = tuple(lines)
        return flag


@dataclass
class Row:
    kind: str  # "tender" | "order"
    title: str
    url: str
    stage: str
    waiting_on: str
    last_change_at: datetime | None
    last_change_by: str
    last_change_is_ai: bool
    stale: list[str] = field(default_factory=list)
    # The AI pill's words and its title: "AI · ACE", "Entered by the ACE
    # agent from a forwarded email". Blank when a person made the change.
    last_change_badge: str = ""
    last_change_title: str = ""
    # A second line under "waiting on": "3 of 4 replied".
    waiting_detail: str = ""
    # Who placed an order, when it is not the program's own organisation.
    buyer: str = ""
    # The order this row is; None on a tender's row.
    contract_id: int | None = None
    # The tender this row is, or the order was placed from; None when an
    # order came from no tender.
    tender_id: int | None = None
    # A tender whose award could still be beaten (see `_tender_rows`), and
    # the reason the buyer gave for it, so "provisional" reads with its why.
    provisional: bool = False
    award_why: str = ""
    # Why the award is provisional, from the comparison it froze:
    # "provisional — 2 of 3 suppliers not yet comparable".
    provisional_caveat: str = ""


def standing_rows(program_id: int, today: date, *, until: date | None = None, own_org_id=None) -> list[Row]:
    """Every tender and order in the program, newest change first.

    `own_org_id` is the organisation the program acts as (identity.resolve_org);
    an order bought by anyone else says so on its row.
    """
    rows = _tender_rows(program_id, today, until) + _order_rows(program_id, today, until, own_org_id)
    # Newest change first; a record with no revision at all (never captured) last.
    rows.sort(key=lambda r: (r.last_change_at is not None, r.last_change_at or datetime.min), reverse=True)
    return rows


# ---- words ---------------------------------------------------------------


def _day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _words(value: str) -> str:
    return (value or "").replace("_", " ")


def _one_line(text) -> str:
    """The award's reason as typed, on one line; never cut -- the cell wraps it."""
    return " ".join((text or "").split())


def _provisional_caveat(award) -> str:
    """ "provisional — 2 of 3 suppliers not yet comparable", read from the comparison the award froze."""
    snapshot = getattr(award, "comparison_snapshot", None) or {}
    total, comparable = snapshot.get("total_count"), snapshot.get("comparable_count")
    if not isinstance(total, int) or not isinstance(comparable, int) or total <= comparable:
        return "provisional"
    return f"provisional — {total - comparable} of {_plural(total, 'supplier')} not yet comparable"


def _names(names, limit=NAMED_SILENT) -> str:
    """ "Northwind Foods, Sahel Nutrition +1": up to `limit` names, then how many more."""
    shown = ", ".join(names[:limit])
    return f"{shown} +{len(names) - limit}" if len(names) > limit else shown


def _ai_badge(label: str, call) -> tuple[str, str]:
    """ "AI · ACE" and its title, from who told us: one compact pill, the long form on hover."""
    agent = label.endswith(" (agent)")
    if agent:
        name = label.removesuffix(" (agent)")
        badge, title = f"AI · {name}", f"Entered by the {name} agent"
    elif label.startswith("via AI · "):
        name = label.removeprefix("via AI · ")
        badge, title = f"AI · {name}", f"Entered by {name} through an AI assistant"
    else:
        badge, title = "AI", "Entered through an AI assistant"
    ref = getattr(call, "source_ref", "") or ""
    if "@" in ref:
        title += " from a forwarded email" if agent else " from an email"
    elif ref:
        title += " from a document"
    return badge, title


def _last_change(revisions):
    latest = revisions.first()
    if latest is None:
        return {"last_change_at": None, "last_change_by": "", "last_change_is_ai": False}
    label = actor_label(latest.call)
    ai = is_ai(latest.call)
    badge, title = _ai_badge(label, latest.call) if ai else ("", "")
    return {
        "last_change_at": latest.recorded_at,
        "last_change_by": label,
        "last_change_is_ai": ai,
        "last_change_badge": badge,
        "last_change_title": title,
    }


# ---- tenders -------------------------------------------------------------


def _tender_rows(program_id, today, until):
    from connect_labs.supply_chain.models import Award, Outreach, Quote, Tender

    tenders = list(Tender.objects.filter(program_id=program_id))
    tender_ids = [t.pk for t in tenders]
    outreach = {}
    for o in Outreach.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "supplier__org"
    ):
        outreach.setdefault(o.tender_id, []).append(o)
    quotes = {}
    for q in Quote.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "supplier__org", "commodity", "item"
    ):
        quotes.setdefault(q.tender_id, []).append(q)
    contracted = set(
        Tender.objects.filter(program_id=program_id, contracts__isnull=False).values_list("pk", flat=True)
    )
    # The newest award per tender (Award is ordered newest decision first):
    # who it went to, and -- when provisional -- the reason it was made anyway.
    awards = {}
    for award in Award.objects.filter(tender__program_id=program_id, tender_id__in=tender_ids).select_related(
        "quote__supplier", "quote__commodity", "quote__item"
    ):
        awards.setdefault(award.tender_id, award)
    provisional = {tid: a for tid, a in awards.items() if a.provisional}

    rows = []
    for tender in tenders:
        award = awards.get(tender.pk) if tender.status == "awarded" else None
        waiting_on, waiting_detail, stale = _tender_state(
            tender, outreach.get(tender.pk, []), quotes.get(tender.pk, []), tender.pk in contracted, today, award
        )
        stage = _words(tender.status)
        awardee = award.quote.supplier.name if award is not None and award.quote_id else ""
        if awardee:
            stage = f"awarded to {awardee}"
        # An award made while suppliers were still blocked from the comparison
        # (the frozen snapshot's `provisional`) could still be beaten: say so.
        is_provisional = tender.status == "awarded" and tender.pk in provisional
        if is_provisional:
            stage = f"{stage}, provisional" if awardee else PROVISIONAL_STAGE
        rows.append(
            Row(
                kind="tender",
                title=tender.label,
                url=reverse("supply_chain:procurement_tender_detail", args=[tender.pk]),
                tender_id=tender.pk,
                stage=stage,
                waiting_on=waiting_on,
                waiting_detail=waiting_detail,
                stale=stale,
                provisional=is_provisional,
                award_why=_one_line(provisional[tender.pk].rationale) if is_provisional else "",
                provisional_caveat=_provisional_caveat(provisional[tender.pk]) if is_provisional else "",
                **_last_change(tender_scope_revisions(tender.pk, program_id=program_id, until=until)),
            )
        )
    return rows


def _tender_state(tender, outreach, quotes, contracted, today, award=None):
    """(waiting on, a second line under it, flags) for one tender."""
    live = [q for q in quotes if q.is_live]
    invited = {o.supplier_id for o in outreach}
    replied = {o.supplier_id for o in outreach if o.responded} | {q.supplier_id for q in live}
    replied &= invited
    suppliers = {o.supplier_id: o.supplier for o in outreach}

    stale = []
    # A supplier's clock runs from the latest time we asked it: a re-invite is a new request.
    latest_ask = {}
    for o in outreach:
        if o.sent_on is not None and o.supplier_id not in replied:
            latest_ask[o.supplier_id] = max(latest_ask.get(o.supplier_id, o.sent_on), o.sent_on)
    overdue = {sid: (today - sent).days for sid, sent in latest_ask.items() if (today - sent).days >= NO_REPLY_DAYS}
    # Only while replies are still being taken: once a tender is closed or
    # awarded a silent supplier is history, not something waiting.
    if overdue and tender.status == "open":
        # Named, longest silent first: "No reply in 17 days: Northwind Foods"
        # is something to act on; "from 1 supplier" sends the reader to find
        # out who. The age is the one every one of them has passed, so the
        # sentence is true of each.
        silent = sorted(overdue, key=lambda sid: (-overdue[sid], suppliers[sid].name))
        stale.append(
            Flag(
                f"No reply in {min(overdue.values())} days: {_names([suppliers[sid].name for sid in silent])}",
                NO_REPLY_RULE,
            )
        )
    # Every live quote the comparison cannot rank, named with what it is
    # missing -- read off the comparison itself, so "0 of 3 comparable" there
    # and this flag here count the same offers for the same reasons. Not once
    # the tender is awarded: the decision has been made, and what the chosen
    # quote still lacks is flagged on its own below.
    if tender.status not in ("awarded", "cancelled"):
        blocked = _blocked(tender, live)
        if blocked:
            # One line per supplier: a comma list of names and bracketed gaps
            # ran to four lines in a narrow cell and read as one sentence.
            stale.append(
                Flag(
                    "Can't compare yet: " + "; ".join(blocked),
                    BLOCKED_RULE,
                    heading="Can't compare yet",
                    lines=blocked,
                )
            )
    if award is not None:
        gaps = _award_gaps(award)
        if gaps:
            stale.append(Flag(f"Awarded quote: {gaps}", AWARDED_GAP_RULE))

    detail = ""
    if tender.status == "awarded":
        awardee = award.quote.supplier.name if award is not None and award.quote_id else ""
        if contracted:
            waiting_on = "—"
        else:
            waiting_on = f"a contract with {awardee}" if awardee else "a contract"
    elif tender.status == "draft":
        waiting_on = "opening"
    elif invited and replied == invited:
        waiting_on = "award decision"
    elif tender.status == "closed":
        waiting_on = "award decision"
    elif invited:
        # Who, not how many: "Northwind Foods — no reply since 9 Sep" is who
        # to chase. The day is the latest we asked any of them, so it is true
        # of each; the count moves to the line under it.
        silent = sorted(invited - replied, key=lambda sid: suppliers[sid].name)
        asked = [latest_ask[sid] for sid in silent if sid in latest_ask]
        since = f"no reply since {_day(max(asked))}" if asked else "no reply yet"
        waiting_on = f"{_names([suppliers[sid].name for sid in silent])} — {since}"
        detail = f"{len(replied)} of {len(invited)} replied"
    else:
        waiting_on = "invitations"
    return waiting_on, detail, stale


def _blocked(tender, live) -> list[str]:
    """ "Northwind Foods: freight" for every live quote the comparison blocks, by supplier name."""
    from connect_labs.supply_chain.procurement.services.comparison import compare_tender

    by_commodity = {}
    for quote in live:
        by_commodity.setdefault(quote.commodity_id, []).append(quote)
    named = []
    for quotes in by_commodity.values():
        comparison = compare_tender(
            tender,
            quotes[0].commodity,
            quotes,
            {q.supplier_id: q.supplier for q in quotes},
            items_by_id={q.item_id: q.item for q in quotes if q.item_id},
        )
        for row in comparison.blocked:
            text = f"{row.supplier_name}: {', '.join(row.gaps)}" if row.gaps else row.supplier_name
            if text not in named:
                named.append(text)
    return sorted(named)


def _award_gaps(award) -> str:
    """What the awarded quote leaves open against the specification: "shelf life not stated"."""
    from connect_labs.supply_chain.procurement.services.compliance import FAIL, check_compliance, requirement_label

    quote = award.quote
    if quote is None or quote.commodity_id is None:
        return ""
    results = check_compliance(quote, quote.commodity, item=quote.item)

    def names(outcome):
        return [
            requirement_label(r.field, r.requirement.get("unit", "")).lower() for r in results if r.outcome == outcome
        ]

    parts = []
    if names("not_stated"):
        parts.append(f"{', '.join(names('not_stated'))} not stated")
    if names(FAIL):
        parts.append(f"{', '.join(names(FAIL))} outside the specification")
    return "; ".join(parts)


# ---- orders --------------------------------------------------------------


def _order_rows(program_id, today, until, own_org_id):
    from connect_labs.supply_chain.models import Contract, Invoice, Payment, Receipt, Shipment

    contracts = list(Contract.objects.filter(program_id=program_id).select_related("supplier__org", "buyer_org"))
    ids = [c.pk for c in contracts]
    shipments = {}
    for s in Shipment.objects.filter(contract__program_id=program_id, contract_id__in=ids):
        shipments.setdefault(s.contract_id, []).append(s)
    receipts = list(
        Receipt.objects.filter(contract__program_id=program_id, contract_id__in=ids).values_list(
            "contract_id", "shipment_id"
        )
    ) + list(
        Receipt.objects.filter(
            shipment__contract__program_id=program_id, shipment__contract_id__in=ids, contract_id__isnull=True
        ).values_list("shipment__contract_id", "shipment_id")
    )
    received_contracts = {c for c, _ in receipts}
    received_shipments = {s for _, s in receipts if s is not None}
    # A receipt against the order but no shipment could be any of them, so
    # none of that order's shipments is called "not received".
    unlinked = {c for c, s in receipts if s is None}
    invoiced = set(
        Invoice.objects.filter(contract__program_id=program_id, contract_id__in=ids)
        .exclude(status="rejected")
        .values_list("contract_id", flat=True)
    )
    # Paid means every live invoice is settled -- fulfilment/repository.py
    # derives an invoice's "paid" from what has been paid against its amount,
    # so a half payment leaves it "part_paid" and the order still waiting on
    # payment. An invoice with no amount cannot be derived, so a payment
    # against it is taken as settling it.
    with_payment = set(
        Payment.objects.filter(invoice__contract__program_id=program_id, invoice__contract_id__in=ids).values_list(
            "invoice_id", flat=True
        )
    )
    settled, any_paid = {}, set()
    for invoice_id, contract_id, status, amount in (
        Invoice.objects.filter(contract__program_id=program_id, contract_id__in=ids)
        .exclude(status="rejected")
        .values_list("pk", "contract_id", "status", "amount")
    ):
        done = status == "paid" or (amount is None and invoice_id in with_payment)
        settled[contract_id] = settled.get(contract_id, True) and done
        if invoice_id in with_payment:
            any_paid.add(contract_id)
    paid = {c for c, done in settled.items() if done}
    part_paid = any_paid - paid

    rows = []
    for contract in contracts:
        stage, waiting_on, stale = _order_state(
            contract,
            shipments.get(contract.pk, []),
            received_shipments,
            contract.pk in received_contracts,
            contract.pk in invoiced,
            contract.pk in paid,
            contract.pk in part_paid,
            contract.pk in unlinked,
            today,
        )
        title = contract.reference or f"Order {contract.pk}"
        rows.append(
            Row(
                kind="order",
                title=f"{title} · {contract.supplier.name}",
                url=reverse("supply_chain:order_detail", args=[contract.pk]),
                stage=stage,
                waiting_on=waiting_on,
                stale=stale,
                tender_id=contract.tender_id,
                contract_id=contract.pk,
                buyer=(
                    contract.buyer_org.name
                    if contract.buyer_org_id is not None and contract.buyer_org_id != own_org_id
                    else ""
                ),
                **_last_change(contract_scope_revisions(contract.pk, program_id=program_id, until=until)),
            )
        )
    return rows


def _dispatched(shipment) -> bool:
    return shipment.dispatched_on is not None or shipment.status in (*records.IN_TRANSIT_STATUSES, "delivered")


def _order_state(
    contract, shipments, received_shipments, received, invoiced, paid, part_paid, unlinked_receipt, today
):
    if contract.status in ("cancelled", "draft"):
        return _words(contract.status), "—", []

    reached = {
        "placed": True,
        "dispatched": any(_dispatched(s) for s in shipments),
        "received": received,
        "invoiced": invoiced,
        "paid": paid,
    }
    stage = [s for s in ORDER_STAGES if reached[s]][-1]
    # The order's own status says when only some of the goods have arrived.
    if stage == "received" and contract.status == "part_received":
        stage = "part received"
    elif stage == "invoiced" and part_paid:
        stage = "part paid"

    outstanding = [
        s for s in shipments if s.pk not in received_shipments and s.status != "lost" and not unlinked_receipt
    ]
    stale = [
        Flag(f"ETA {_day(s.expected_on)} passed, not received", ETA_RULE)
        for s in sorted(outstanding, key=lambda s: (s.expected_on or date.max, s.pk))
        if s.expected_on is not None and s.expected_on < today
    ]

    in_transit = [s for s in outstanding if _dispatched(s)]
    if in_transit and not received:
        # Goods on the road are the order's news even when the money moved first
        # (paid in advance): "paid" alone would read as finished.
        stage = IN_TRANSIT if stage == "dispatched" else f"{stage}, {IN_TRANSIT}"
    elif stage == "paid" and received and contract.status != "part_received":
        stage = DELIVERED_AND_PAID
    if in_transit:
        etas = sorted(s.expected_on for s in in_transit if s.expected_on is not None)
        waiting_on = f"arrival (ETA {_day(etas[0])})" if etas else "arrival"
    elif not received:
        waiting_on = "dispatch"
    elif not invoiced:
        waiting_on = "invoice"
    elif not paid:
        waiting_on = "payment"
    else:
        waiting_on = "—"
    return stage, waiting_on, stale
