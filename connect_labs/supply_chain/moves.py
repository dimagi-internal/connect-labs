"""Whose move it is: the only rules that put an item on "On us" or "On suppliers".

The tender page and the overview read these lists and nothing else, so the two
cannot disagree, and no screen invents a move of its own. Each item carries a
short rule label saying why it is listed. The rules, and only these:

  ON US
  (a) owed          an open Commitment (a counterparty's question to us, or a
                    promise of ours), or a document a shipment requires from
                    us that is not on file. Grouped per counterparty: "Reply to
                    Northgate Commodities (3 questions)", "Provide 2 documents
                    to Crescent Freight & Clearing". A shipment held waiting on
                    us is exactly these documents and promises (holds.py), so
                    rule (d) is folded in here rather than listed twice.
  (b) deadline      a tender still open past its response deadline:
                    "Decide on <tender>: extend, close or award".
  (c) invoice check an invoice that bills above its order (the
                    invoice_above_contract check): "Review invoice <ref>".

  ON SUPPLIERS
  (e) no reply      a supplier invited to a tender still collecting quotes who
                    has neither replied nor quoted: "<supplier>: reply (silent N days)".
  (f) a question WE asked a supplier is not recorded anywhere in the model, so
      it raises nothing; no model is invented for it.

What a quote leaves out is not a move: it shows as a blank in the supplier
table and the comparison grid, where it is read against the other quotes.
"""

from dataclasses import dataclass, field
from datetime import date

from django.urls import reverse

US = "us"
SUPPLIERS = "suppliers"

RULE_OWED = "owed"
RULE_DEADLINE = "deadline"
RULE_INVOICE = "invoice check"
RULE_NO_REPLY = "no reply"

# The rule label's longer reading, for its title attribute.
RULE_TITLES = {
    RULE_OWED: "We owe this counterparty: an open question or promise, or a document a shipment is held on.",
    RULE_DEADLINE: "The tender is still open after its response deadline.",
    RULE_INVOICE: "An invoice bills more than its order agreed.",
    RULE_NO_REPLY: "An invited supplier has neither replied nor quoted.",
}


@dataclass
class Move:
    whose: str  # US | SUPPLIERS
    rule: str
    text: str
    detail: str = ""
    cta: str = "Open"
    href: str = ""
    since: date | None = None
    tender_id: int | None = None
    contract_id: int | None = None
    # The counterparty a move is about, for a page that lists per supplier.
    org_id: int | None = None
    supplier_id: int | None = None
    commitment_ids: list = field(default_factory=list)

    @property
    def rule_title(self) -> str:
        return RULE_TITLES.get(self.rule, "")

    @property
    def is_ours(self) -> bool:
        return self.whose == US


def _day(d) -> str:
    return f"{d.day} {d.strftime('%b')}" if d else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _tender_url(tender_id) -> str:
    return reverse("supply_chain:procurement_tender_detail", args=[tender_id])


def _order_url(contract_id) -> str:
    return reverse("supply_chain:order_detail", args=[contract_id])


# ---- (a) owed --------------------------------------------------------------


def owed_moves(commitments, holds=(), *, tender_id=None, contract_id=None, supplier_of_org=None) -> list[Move]:
    """Rule (a): what we owe, one item per counterparty and kind.

    `commitments`: open Commitment rows on one tender or order. `holds`: the
    order's held documents (holds.Hold with a shipment), grouped by who asked.
    `supplier_of_org`: {org id: supplier id}, so a reply opens that supplier's
    drafted reply on the tender page.
    """
    supplier_of_org = supplier_of_org or {}
    base = _tender_url(tender_id) if tender_id else _order_url(contract_id)
    groups = {}
    for c in commitments:
        if c.resolved_on is not None:
            continue
        groups.setdefault((c.owed_to_org_id, c.kind), []).append(c)
    moves = []
    for (org_id, kind), items in groups.items():
        name = items[0].owed_to_org.name
        since = min(c.raised_on for c in items)
        supplier_id = supplier_of_org.get(org_id)
        if kind == "question":
            text = f"Reply to {name} ({_plural(len(items), 'question')})"
            cta = "Reply"
            # The drafted reply when the page drafts one (a supplier on a tender), else the list.
            href = f"{base}#draft-reply-{supplier_id}" if tender_id and supplier_id else f"{base}#owed"
        else:
            text = f"Keep {_plural(len(items), 'promise')} to {name}"
            cta = "Open"
            href = f"{base}#owed"
        moves.append(
            Move(
                US,
                RULE_OWED,
                text,
                detail=f"open since {_day(since)}",
                cta=cta,
                href=href,
                since=since,
                tender_id=tender_id,
                contract_id=contract_id,
                org_id=org_id,
                supplier_id=supplier_id,
                commitment_ids=[c.pk for c in items],
            )
        )
    documents = {}
    for hold in holds:
        if hold.commitment_id is not None:
            continue  # a promise: already counted from the commitment itself
        documents.setdefault(hold.asked_by or "customs", []).append(hold)
    for asked_by, items in documents.items():
        moves.append(
            Move(
                US,
                RULE_OWED,
                f"Provide {_plural(len(items), 'document')} to {asked_by}",
                detail="shipment held: " + ", ".join(h.name or h.what for h in items),
                cta="Provide",
                href=f"{base}#owed",
                since=min((h.since for h in items if h.since), default=None),
                contract_id=contract_id,
            )
        )
    moves.sort(key=lambda m: (m.since or date.max, m.text))
    return moves


# ---- (b) deadline ----------------------------------------------------------


def deadline_move(tender, today) -> Move | None:
    """Rule (b): an open tender past its response deadline is ours to decide."""
    deadline = tender.response_deadline
    if tender.status != "open" or deadline is None or deadline >= today:
        return None
    return Move(
        US,
        RULE_DEADLINE,
        f"Decide on {tender.label}: extend, close or award",
        detail=f"deadline passed {_day(deadline)}",
        cta="Decide",
        href=f"{_tender_url(tender.pk)}#decide",
        since=deadline,
        tender_id=tender.pk,
    )


# ---- (c) invoice check -----------------------------------------------------


def invoice_moves(contract, today) -> list[Move]:
    """Rule (c): the invoice_above_contract check, one item per invoice it names."""
    if contract.consideration != "priced":
        return []
    from connect_labs.supply_chain.checks import _invoice_above_contract
    from connect_labs.supply_chain.fulfilment.services.landed import landed_total
    from connect_labs.supply_chain.values import money_digits

    found = _invoice_above_contract(contract, landed_total(contract), today)
    if found is None:
        return []
    facts = getattr(found, "facts", None) or (found.get("facts") if isinstance(found, dict) else None) or {}
    above = facts.get("above") or []
    currency = facts.get("currency") or ""
    total = next((line for line in above if line.get("field") == "total"), None)
    refs = list(dict.fromkeys(line.get("invoice") for line in above if line.get("invoice")))
    if not refs:
        invoices = [i for i in contract.invoices.all() if i.status != "rejected"]
        refs = [i.reference or str(i.pk) for i in invoices[-1:]]
    detail = (
        f"{currency} {money_digits(total['difference'])} above agreed".strip()
        if total and total.get("difference") is not None
        else "above agreed"
    )
    since = getattr(found, "since", None) or (found.get("since") if isinstance(found, dict) else None)
    if isinstance(since, str):
        since = date.fromisoformat(since[:10])
    return [
        Move(
            US,
            RULE_INVOICE,
            f"Review invoice {ref}",
            detail=detail,
            cta="Review",
            href=f"{_order_url(contract.pk)}#match",
            since=since if isinstance(since, date) else None,
            contract_id=contract.pk,
        )
        for ref in refs
    ]


# ---- (e) no reply ----------------------------------------------------------


def collecting(tender, *, provisional=False, contracted=False) -> bool:
    """Replies are still wanted: the tender is open, or awarded provisionally and not yet ordered."""
    return tender.status == "open" or (tender.status == "awarded" and provisional and not contracted)


def silent_suppliers(outreach, quotes) -> dict:
    """{supplier id: [outreach rows]} for every invited supplier with no reply and no live quote."""
    quoted = {q.supplier_id for q in quotes if q.is_live}
    replied = {o.supplier_id for o in outreach if o.responded}
    out = {}
    for o in outreach:
        if o.supplier_id in quoted or o.supplier_id in replied:
            continue
        out.setdefault(o.supplier_id, []).append(o)
    return out


def no_reply_moves(tender, outreach, quotes, today, *, provisional=False, contracted=False) -> list[Move]:
    """Rule (e): each invited supplier still silent, on a tender still collecting quotes."""
    if not collecting(tender, provisional=provisional, contracted=contracted):
        return []
    moves = []
    for supplier_id, rows in silent_suppliers(outreach, quotes).items():
        asked = max((o.sent_on for o in rows if o.sent_on), default=None)
        chased = max((o.last_reminder_on for o in rows if o.last_reminder_on), default=None)
        days = (today - asked).days if asked else None
        name = rows[0].supplier.name
        parts = [f"asked {_day(asked)}" if asked else "", f"chased {_day(chased)}" if chased else ""]
        moves.append(
            Move(
                SUPPLIERS,
                RULE_NO_REPLY,
                f"{name}: reply" + (f" (silent {_plural(days, 'day')})" if days is not None else ""),
                detail=" · ".join(p for p in parts if p),
                cta="Remind",
                href=f"{_tender_url(tender.pk)}#draft-supplier-{supplier_id}",
                since=asked,
                tender_id=tender.pk,
                supplier_id=supplier_id,
            )
        )
    moves.sort(key=lambda m: (m.since or date.max, m.text))
    return moves


# ---- per record ------------------------------------------------------------


def tender_moves(tender, today, *, outreach=None, quotes=None, commitments=None, provisional=None, contracted=None):
    """Every move on one tender: (on us, on suppliers). Reads what it is not handed."""
    from connect_labs.supply_chain.models import Award, Commitment, Contract, Outreach, Quote

    if outreach is None:
        outreach = list(Outreach.objects.filter(tender=tender).select_related("supplier"))
    if quotes is None:
        quotes = list(Quote.objects.filter(tender=tender))
    if commitments is None:
        commitments = list(
            Commitment.objects.filter(tender=tender, resolved_on__isnull=True).select_related("owed_to_org")
        )
    if contracted is None:
        contracted = Contract.objects.filter(tender=tender).exists()
    if provisional is None:
        award = Award.objects.filter(tender=tender).first()
        provisional = bool(award and award.provisional)
    # The supplier record each counterparty is invited as, so a reply opens its drafted email.
    supplier_of_org = {o.supplier.org_id: o.supplier_id for o in outreach if getattr(o.supplier, "org_id", None)}
    ours = owed_moves(commitments, tender_id=tender.pk, supplier_of_org=supplier_of_org)
    deadline = deadline_move(tender, today)
    if deadline is not None:
        ours.append(deadline)
    theirs = no_reply_moves(tender, outreach, quotes, today, provisional=provisional, contracted=contracted)
    return ours, theirs


def contract_moves(contract, today, *, holds=None, commitments=None):
    """Every move on one order: (on us, on suppliers). No rule puts an order on a supplier."""
    from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
    from connect_labs.supply_chain.models import Commitment

    if holds is None:
        holds = holds_on_us(contract)
    if commitments is None:
        commitments = list(
            Commitment.objects.filter(contract=contract, resolved_on__isnull=True).select_related("owed_to_org")
        )
    ours = owed_moves(commitments, holds, contract_id=contract.pk)
    ours += invoice_moves(contract, today)
    return ours, []


def first_move(ours, theirs):
    """The row's next move and whose it is: ours first, then the suppliers'."""
    if ours:
        return ours[0], US
    if theirs:
        return theirs[0], SUPPLIERS
    return None, ""
