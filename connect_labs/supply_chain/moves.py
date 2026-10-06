"""Whose move it is: the only rules that put an item on "On us" or "On suppliers".

The tender page and the overview read these lists and nothing else, so the two
cannot disagree, and no screen invents a move of its own. Each item carries a
short rule label saying why it is listed. The rules, and only these:

  ON US
  (a) owed          an open Commitment (a counterparty's question to us, or a
                    promise of ours), or a document a shipment requires from
                    us that is not on file (the duty exemption a nil duty
                    rests on among them). Grouped per counterparty: "Reply to
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
                    has not quoted and has no open question to us. Silent:
                    "<supplier>: reply (silent N days)". Replied with
                    questions, all of them answered:
                    "<supplier>: quote (answered N days ago)". A reply of
                    questions is not a quote; once we answer them the quote
                    is theirs to send.
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

# The words every screen uses for whose step it is (owner decision 2026-10-04: "on us"
# read as jargon). Ours is a to-do list, theirs is who we are waiting on. A quote's
# missing fact is tagged with the same words; it is still not a move (rules above).
TO_DO = "To do"
WAITING_ON_SUPPLIERS = "Waiting on suppliers"
# A supplier's fact nobody has asked it for yet: ours to ask (procurement/status.fact_owner).
TO_ASK = "to_ask"
OWNER_CHIP = {US: "to do", SUPPLIERS: "waiting", TO_ASK: "to ask"}


def facts_chip(gaps, owner) -> str:
    """A quote's open facts for one party, as a chip: the fact when there is one, else a count.

    "duty terms · to do", "3 facts · to do", "sachets per carton · waiting".
    """
    gaps = list(gaps or [])
    if not gaps:
        return ""
    what = gaps[0] if len(gaps) == 1 else f"{len(gaps)} facts"
    return f"{what} · {OWNER_CHIP.get(owner) or OWNER_CHIP[SUPPLIERS]}"


RULE_OWED = "owed"
RULE_DEADLINE = "deadline"
RULE_INVOICE = "invoice check"
RULE_NO_REPLY = "no reply"

# Rule (e)'s two states, and the chip the second reads as wherever a supplier is listed.
SILENT = "silent"
AWAITING_QUOTE = "awaiting_quote"
AWAITING_QUOTE_CHIP = "Answered · awaiting quote"

# The rule label's longer reading, for its title attribute.
RULE_TITLES = {
    RULE_OWED: "We owe this counterparty: an open question or promise, or a document a shipment is held on.",
    RULE_DEADLINE: "The tender is still open after its response deadline.",
    RULE_INVOICE: "An invoice bills more than its order agreed.",
    RULE_NO_REPLY: "An invited supplier has not quoted, and has no question open to us.",
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
    # A supplier's state as a chip, in the tender page's own words ("Silent 17d").
    chip: str = ""
    # The counterparty by name, for a list that names it beside its chip.
    party: str = ""
    # Rule (e) only: SILENT (no reply at all) or AWAITING_QUOTE (replied, no quote yet).
    state: str = ""
    # A fact a list shows under the row even where it leaves dates to a table beside
    # it: "reply sent 6 Oct" on a supplier our answers went out to.
    note: str = ""

    @property
    def rule_title(self) -> str:
        return RULE_TITLES.get(self.rule, "")

    @property
    def detail_parts(self) -> list[str]:
        """The detail split at its " · " joints, so each fact wraps whole."""
        return [p for p in (self.detail or "").split(" · ") if p]

    @property
    def is_ours(self) -> bool:
        return self.whose == US


# ---- one verb per move -----------------------------------------------------
#
# A supplier's next move reads the same wherever it is offered -- the tender's
# Suppliers table and the On us / On suppliers rails: its verb, and the anchor on
# the tender page it opens, come from `supplier_action` and nowhere else.

ACTION_REMIND = "remind"  # a silent supplier: open the reminder drafted to them
ACTION_REPLY = "reply"  # their questions to us: open the reply drafted to them
ACTION_RECORD_REPLY = "record_reply"  # their email came in: log it against the invitation
ACTION_ASK = "ask"  # facts their quote left out: open the email asking for them
ACTION_RECORD_QUOTE = "record_quote"  # their quote is what comes next: record it on the tender

_VERBS = {
    ACTION_REMIND: "Remind",
    ACTION_REPLY: "Reply",
    ACTION_RECORD_REPLY: "Record a reply",
    ACTION_ASK: "Ask",
    ACTION_RECORD_QUOTE: "Record a quote",
}


def supplier_action(kind: str, supplier_id=None) -> dict:
    """{"label": verb, "anchor": the tender page's drafted email it opens, "" when it opens elsewhere}."""
    anchors = {
        ACTION_REMIND: f"draft-supplier-{supplier_id}",
        ACTION_ASK: f"draft-supplier-{supplier_id}",
        ACTION_REPLY: f"draft-reply-{supplier_id}",
    }
    return {"label": _VERBS[kind], "anchor": anchors.get(kind, "") if supplier_id else ""}


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
            action = supplier_action(ACTION_REPLY, supplier_id)
            cta = action["label"]
            # The drafted reply when the page drafts one (a supplier on a tender), else the list.
            href = f"{base}#{action['anchor']}" if tender_id and action["anchor"] else f"{base}#owed"
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
        # Held vs owed follows each hold's basis: only a document a recorded requirement
        # cites holds the shipment; the rest are owed under the tender's duty terms.
        held = [h.name or h.what for h in items if h.basis != "duty_terms"]
        owed = [h.name or h.what for h in items if h.basis == "duty_terms"]
        detail = " · ".join(
            part
            for part in (
                "shipment held: " + ", ".join(held) if held else "",
                "owed under duty terms: " + ", ".join(owed) if owed else "",
            )
            if part
        )
        moves.append(
            Move(
                US,
                RULE_OWED,
                f"Provide {_plural(len(items), 'document')} to {asked_by}",
                detail=detail,
                # The same control as the owed list's own: each item there is attached.
                cta="Attach",
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
            # The invoice row's own controls, Pay and Dispute, are where this lands.
            cta="Pay or dispute",
            href=f"{_order_url(contract.pk)}#invoices",
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


def awaiting_quote(outreach, quotes, commitments=(), answered=None) -> dict:
    """{supplier id: [outreach rows]} for every invited supplier whose reply was questions we have answered.

    Its reply was questions, not a quote; none of them is still open to us (an open
    one is ours, rule (a)); at least one was answered (`answered`: {org id: day}, from
    questions_answered_on); it has not declined; and it has not quoted. Its quote is
    what comes next. A reply of questions none of which were recorded says nothing
    of who is next, so it is not listed.
    """
    answered = answered or {}
    quoted = {q.supplier_id for q in quotes if q.is_live}
    asking = {c.owed_to_org_id for c in commitments if c.kind == "question" and c.resolved_on is None}
    rows = {}
    for o in outreach:
        rows.setdefault(o.supplier_id, []).append(o)
    out = {}
    for supplier_id, mine in rows.items():
        kinds = {o.response_kind for o in mine if o.responded}
        if supplier_id in quoted or "needs_info" not in kinds or "declined" in kinds:
            continue
        org_id = getattr(mine[0].supplier, "org_id", None)
        if org_id in asking or org_id not in answered:
            continue
        out[supplier_id] = mine
    return out


def questions_answered_on(commitments) -> dict:
    """{org id: the last day an answer to its questions went out}: sent, else marked answered."""
    out = {}
    for c in commitments:
        if c.kind != "question" or c.resolved_on is None:
            continue
        day = c.reply_sent_on or c.resolved_on
        if day and (c.owed_to_org_id not in out or day > out[c.owed_to_org_id]):
            out[c.owed_to_org_id] = day
    return out


def replies_sent_on(commitments) -> dict:
    """{org id: the last day a reply answering its questions was marked sent} -- sent only, never just resolved."""
    out = {}
    for c in commitments:
        day = getattr(c, "reply_sent_on", None)
        if c.kind == "question" and day and (c.owed_to_org_id not in out or day > out[c.owed_to_org_id]):
            out[c.owed_to_org_id] = day
    return out


def no_reply_moves(
    tender,
    outreach,
    quotes,
    today,
    *,
    commitments=(),
    answered=None,
    sent=None,
    provisional=False,
    contracted=False,
) -> list[Move]:
    """Rule (e): each invited supplier we are waiting on for a quote, on a tender still collecting quotes.

    `answered`: {org id: day our answers to its questions went out} (questions_answered_on).
    `sent`: {org id: day our reply was marked sent} (replies_sent_on), said under the row.
    """
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
                cta=supplier_action(ACTION_REMIND, supplier_id)["label"],
                href=f"{_tender_url(tender.pk)}#{supplier_action(ACTION_REMIND, supplier_id)['anchor']}",
                since=asked,
                tender_id=tender.pk,
                supplier_id=supplier_id,
                chip=f"Silent {days}d" if days is not None else "Silent",
                party=name,
                state=SILENT,
            )
        )
    answered = answered or {}
    sent = sent or {}
    for supplier_id, rows in awaiting_quote(outreach, quotes, commitments, answered).items():
        supplier = rows[0].supplier
        reply_sent = sent.get(getattr(supplier, "org_id", None))
        replied = max((o.responded_on for o in rows if o.responded and o.responded_on), default=None)
        ours = answered.get(getattr(supplier, "org_id", None))
        # The ball moved to them on the later of their reply and our answer to it.
        since = max(d for d in (replied, ours) if d) if (replied or ours) else None
        word = "answered" if ours and since == ours else "replied"
        days = (today - since).days if since else None
        parts = [f"replied {_day(replied)}" if replied else "", f"answered {_day(ours)}" if ours else ""]
        moves.append(
            Move(
                SUPPLIERS,
                RULE_NO_REPLY,
                f"{supplier.name}: quote" + (f" ({word} {_plural(days, 'day')} ago)" if days is not None else ""),
                detail=" · ".join(p for p in parts if p),
                cta=supplier_action(ACTION_RECORD_QUOTE)["label"],
                href=reverse("supply_chain:procurement_quote_entry") + f"?tender={tender.pk}",
                since=since,
                tender_id=tender.pk,
                supplier_id=supplier_id,
                chip=AWAITING_QUOTE_CHIP,
                party=supplier.name,
                state=AWAITING_QUOTE,
                note=f"reply sent {_day(reply_sent)}" if reply_sent else "",
            )
        )
    moves.sort(key=lambda m: (m.since or date.max, m.text))
    return moves


# ---- per record ------------------------------------------------------------


def tender_moves(
    tender,
    today,
    *,
    outreach=None,
    quotes=None,
    commitments=None,
    answered=None,
    sent=None,
    provisional=None,
    contracted=None,
):
    """Every move on one tender: (on us, on suppliers). Reads what it is not handed.

    `commitments`: the open ones. `answered`: {org id: day our answers went out}, read
    from the tender's answered questions when not handed (questions_answered_on), and
    `sent` with it (replies_sent_on); handed `answered` alone, no row says a reply went.
    """
    from connect_labs.supply_chain.models import Award, Commitment, Contract, Outreach, Quote

    if outreach is None:
        outreach = list(Outreach.objects.filter(tender=tender).select_related("supplier"))
    if quotes is None:
        quotes = list(Quote.objects.filter(tender=tender))
    if commitments is None:
        commitments = list(
            Commitment.objects.filter(tender=tender, resolved_on__isnull=True).select_related("owed_to_org")
        )
    if answered is None:
        closed = list(Commitment.objects.filter(tender=tender, kind="question", resolved_on__isnull=False))
        answered = questions_answered_on(closed)
        if sent is None:
            sent = replies_sent_on(closed)
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
    theirs = no_reply_moves(
        tender,
        outreach,
        quotes,
        today,
        commitments=commitments,
        answered=answered,
        sent=sent,
        provisional=provisional,
        contracted=contracted,
    )
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


def open_at_decision(tender, on, *, outreach=None, quotes=None, commitments=None) -> dict:
    """What was still open on a tender on the day `on`: the facts an early award is made over.

    {"deadline": iso date or None, "deadline_days": days left (None once it has passed or when
    the tender is no longer open), "silent": suppliers silent under rule (e), "awaiting_quote":
    suppliers that replied without a quote under rule (e), "replies_owed":
    counterparties whose questions we have not answered under rule (a)}. An award is allowed
    over any of them; the award form shows them and the award records them, so the decision
    says what it was made over.
    """
    ours, theirs = tender_moves(tender, on, outreach=outreach, quotes=quotes, commitments=commitments)
    deadline = tender.response_deadline
    still_open = tender.status == "open" and deadline is not None and deadline >= on
    if commitments is None:
        from connect_labs.supply_chain.models import Commitment

        commitments = Commitment.objects.filter(tender=tender, resolved_on__isnull=True)
    owed_to = {c.owed_to_org_id for c in commitments if c.kind == "question" and c.resolved_on is None}
    return {
        "deadline": deadline.isoformat() if deadline else None,
        "deadline_days": (deadline - on).days if still_open else None,
        "silent": sum(1 for m in theirs if m.state == SILENT),
        "awaiting_quote": sum(1 for m in theirs if m.state == AWAITING_QUOTE),
        "replies_owed": len(owed_to),
    }


def open_at_decision_chips(facts) -> list[dict]:
    """The open facts as chips, in the moves' own words; none when nothing was open."""
    facts = facts or {}
    chips = []
    days = facts.get("deadline_days")
    if days is not None:
        chips.append(
            {"label": "Deadline today" if days == 0 else f"Deadline in {_plural(days, 'day')}", "tone": "neutral"}
        )
    if facts.get("silent"):
        chips.append({"label": f"{facts['silent']} silent", "tone": "theirs"})
    if facts.get("awaiting_quote"):
        chips.append({"label": f"{facts['awaiting_quote']} awaiting quote", "tone": "theirs"})
    if facts.get("replies_owed"):
        n = facts["replies_owed"]
        chips.append({"label": f"{n} {'reply' if n == 1 else 'replies'} owed", "tone": "ours"})
    return chips


def first_move(ours, theirs):
    """The row's next move and whose it is: ours first, then the suppliers'."""
    if ours:
        return ours[0], US
    if theirs:
        return theirs[0], SUPPLIERS
    return None, ""
