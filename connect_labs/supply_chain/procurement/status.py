"""The tender's status view and the comparison grid: facts, laid out, no prose.

`tender_status` builds the top of a tender's page -- header, the six-step
stage bar, four tiles, one row per supplier, the On us / On suppliers lists
(from `moves.py`, nothing else), the terms and a related earlier order.
`comparison_grid` builds the comparison: one column per quote, one row per
fact, with a gap shown as a gap ("not stated") rather than explained.

Every fact carries where it came from (`src`): "person" (recorded by
someone), "ai" (from an email, recorded through an AI) or "calc" (worked out
here). The page draws a small mark per source and a legend once.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.records import document_kind_label, document_not_on_file
from connect_labs.supply_chain.standing import STAGES, stage_bars
from connect_labs.supply_chain.values import money_digits, quantity_phrase, unit_noun

PERSON, AI, CALC = "person", "ai", "calc"

# Chip tones, by meaning (tailwind.css .status-chip--<tone>): done/primary, on us, on suppliers, neutral.
PRIMARY, OURS, THEIRS, NEUTRAL = "primary", "ours", "theirs", "neutral"

_ROUND_DUTY = "tender duty terms"


def _day(d) -> str:
    return f"{d.day} {d.strftime('%b')}" if d else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _src_of_actor(label: str) -> str:
    """An actor label from the history as a source: AI-recorded or a person's."""
    return AI if label.endswith("(agent)") or label.startswith("via AI") else PERSON


# ---- comparisons -----------------------------------------------------------


def comparisons(tender, quotes) -> list:
    """The comparison for each product the tender buys that has a live quote (service objects)."""
    from connect_labs.supply_chain.procurement.services.comparison import compare_tender

    by_commodity = {}
    for quote in quotes:
        if quote.is_live:
            by_commodity.setdefault(quote.commodity_id, []).append(quote)
    out = []
    for group in by_commodity.values():
        out.append(
            compare_tender(
                tender,
                group[0].commodity,
                group,
                {q.supplier_id: q.supplier for q in group},
                items_by_id={q.item_id: q.item for q in group if q.item_id},
            )
        )
    return out


def comparable_chip(compared) -> str:
    """ "1 of 3 comparable · Harmattan": the comparison's own count, and who is comparable, by first name.

    Read off the same rows (and so the same gap list) the comparison draws,
    so the overview, the tender's header and the comparison cannot disagree.
    "" when no quote has come in.
    """
    comparable = [row for c in compared for row in c.comparable]
    quoted = len(comparable) + sum(len(c.blocked) for c in compared)
    if not quoted:
        return ""
    names = list(dict.fromkeys((row.supplier_name or "").split(" ")[0] for row in comparable if row.supplier_name))
    return f"{len(comparable)} of {quoted} comparable" + (f" · {', '.join(names)}" if names else "")


def _blocked_by_terms(row) -> bool:
    return _ROUND_DUTY in (row.gaps or [])


# The duty exemption a quote costed on the waiver rests on, named as the document is everywhere.
_WAIVER_DOC = document_kind_label("duty_exemption")
# Our own estimates, recorded on the tender's Terms box: never a supplier's to give.
_ESTIMATE_GAPS = ("freight estimate", "clearing estimate")


@lru_cache(maxsize=1)
def _our_gap_words() -> frozenset:
    """The gaps that are ours: every gap whose question is addressed to us (questions.py), plus
    the two only the tender carries -- the duty exemption and our clearing estimate.

    One table decides both whose a gap is and whether a follow-up asks the supplier for it,
    so the comparison, the tender's Suppliers table and the drafted emails cannot disagree.
    """
    from connect_labs.supply_chain.procurement.services.comparison import _GAP_WORDS
    from connect_labs.supply_chain.procurement.services.questions import _REASON_QUESTIONS, INTERNAL

    internal = {
        _GAP_WORDS[key] for _, key, _, audience in _REASON_QUESTIONS if audience == INTERNAL and key in _GAP_WORDS
    }
    return frozenset({*internal, _ROUND_DUTY, _WAIVER_DOC, *_ESTIMATE_GAPS})


def _gap_word(gap: str) -> str:
    """A gap as its chip names it: the tender-wide duty gap reads as the tender's duty terms."""
    return "duty terms" if gap == _ROUND_DUTY else gap


def gap_owner(gap: str) -> str:
    """Whose a gap is: our tender terms and the rates we record are ours; what a quote states, the supplier's."""
    return rules.US if gap in _our_gap_words() or gap.startswith("exchange rate") else rules.SUPPLIERS


def asked_since_quote(quote, outreach=None) -> bool:
    """Whether we have chased this supplier since its quote arrived.

    Until we have, a fact only the supplier can state is ours to ask, not theirs to send: a
    supplier cannot be waited on for a question nobody put to it. A chase is the invitation's
    `last_reminder_on`, which a sent reminder or a sent follow-up both record.
    """
    if quote is None or not getattr(quote, "pk", None):
        return True
    if outreach is None:
        from connect_labs.supply_chain.models import Outreach

        outreach = Outreach.objects.filter(tender_id=quote.tender_id, supplier_id=quote.supplier_id)
    chased = [o.last_reminder_on for o in outreach if o.supplier_id == quote.supplier_id and o.last_reminder_on]
    since = quote.received_on
    return bool(since and chased and max(chased) >= since)


def fact_owner(gap: str, asked: bool = True) -> str:
    """gap_owner, with a supplier's fact nobody has asked it for yet read as ours to ask."""
    owner = gap_owner(gap)
    return rules.TO_ASK if owner == rules.SUPPLIERS and not asked else owner


def fact_chips(gaps, asked: bool = True) -> list:
    """[(fact, owner)]: open facts as the outlined gap chips name them, ours first."""
    ours, theirs = split_gaps(gaps)
    return [(_gap_word(g), rules.US) for g in ours] + [(_gap_word(g), fact_owner(g, asked)) for g in theirs]


def split_gaps(gaps) -> tuple[list, list]:
    """(ours, the supplier's), by gap_owner -- one rule for the tile and the grid."""
    gaps = list(gaps or [])
    return [g for g in gaps if gap_owner(g) == rules.US], [g for g in gaps if gap_owner(g) != rules.US]


def _field(row, name, default=None):
    """A comparison row's field, from the service object or its snapshot dict alike."""
    return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)


def waiver_on_file(tender) -> bool:
    """Under the waiver, whether a copy of it is on the tender (True under any other terms)."""
    if getattr(tender, "duty_terms", "") != "buyer_waiver":
        return True
    from connect_labs.supply_chain.procurement.services.pricing import duty_exemption_on_file

    return duty_exemption_on_file(tender=tender)


def quote_open_facts(tender, row, quote, *, waiver_on_file=True) -> list:
    """Every fact one quote still lacks, ONE list: the comparison's header chips and next steps,
    the tender's Suppliers table and comparable-quotes tile, and the overview's Quote gaps all read it.

    The comparison's gaps, our estimates still to record (which do not block the ranking), and,
    for a quote costed on a duty waiver with no copy of it on file, the duty exemption.
    Split it by `split_gaps` for whose each fact is.
    """
    from connect_labs.supply_chain.procurement.services.pricing import quote_rests_on_relief

    gaps = list(_field(row, "gaps") or [])
    waiver_gap = (
        not waiver_on_file and quote is not None and _ROUND_DUTY not in gaps and quote_rests_on_relief(quote, tender)
    )
    return [*gaps, *(_field(row, "open_estimates") or []), *([_WAIVER_DOC] if waiver_gap else [])]


# ---- the tender's status ---------------------------------------------------


def tender_status(tender, today, *, program_id, draft_anchors=(), own_org_id=None, reminder_counts=None) -> dict:
    """Everything the status view shows for one tender."""
    from connect_labs.supply_chain.history.timeline import ai_entered_quotes, duty_terms_set_by
    from connect_labs.supply_chain.models import Award, Commitment, Contract, Outreach, Quote, Receipt

    outreach = list(Outreach.objects.filter(tender=tender).select_related("supplier__org"))
    quotes = list(
        Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item").order_by("pk")
    )
    live = [q for q in quotes if q.is_live]
    commitments = list(
        Commitment.objects.filter(tender=tender, resolved_on__isnull=True).select_related("owed_to_org")
    )
    contract = Contract.objects.filter(tender=tender).exclude(status="cancelled").order_by("pk").first()
    award = Award.objects.filter(tender=tender).select_related("quote__supplier__org").first()
    provisional = bool(award and award.provisional)
    ours, theirs = rules.tender_moves(
        tender,
        today,
        outreach=outreach,
        quotes=quotes,
        commitments=commitments,
        provisional=provisional,
        contracted=contract is not None,
    )
    compared = comparisons(tender, quotes)
    rows_by_quote = {row.quote_id: row for c in compared for row in (*c.comparable, *c.blocked, *c.not_comparable)}
    # Each quote's open facts, by the one rule the comparison's headers count with.
    quote_by_id = {q.pk: q for q in quotes}
    on_file = waiver_on_file(tender)
    open_facts = {
        row.quote_id: quote_open_facts(tender, row, quote_by_id.get(row.quote_id), waiver_on_file=on_file)
        for c in compared
        for row in (*c.comparable, *c.blocked)
    }
    comparable = sum(len(c.comparable) for c in compared)
    quoted = sum(len(c.comparable) + len(c.blocked) for c in compared)
    ai_quotes = ai_entered_quotes([q.pk for q in live], program_id=program_id)

    # One row per supplier asked, then any who quoted unasked (a marketplace bid).
    suppliers, order = {}, []
    for o in sorted(outreach, key=lambda o: (o.sent_on or date.min, o.pk)):
        if o.supplier_id not in suppliers:
            suppliers[o.supplier_id] = o.supplier
            order.append(o.supplier_id)
    for q in live:
        if q.supplier_id not in suppliers:
            suppliers[q.supplier_id] = q.supplier
            order.append(q.supplier_id)
    invited = {o.supplier_id for o in outreach}
    silent = rules.silent_suppliers(outreach, quotes) if invited else {}
    replied = (invited & ({o.supplier_id for o in outreach if o.responded} | {q.supplier_id for q in live})) or set()
    owed_by_org = {}
    for c in commitments:
        owed_by_org.setdefault(c.owed_to_org_id, []).append(c)
    deadline = tender.response_deadline

    supplier_rows = []
    for sid in order:
        supplier = suppliers[sid]
        mine = [o for o in outreach if o.supplier_id == sid]
        theirs_quotes = [q for q in live if q.supplier_id == sid]
        questions = owed_by_org.get(getattr(supplier, "org_id", None), [])
        asked = max((o.sent_on for o in mine if o.sent_on), default=None)
        chased = max((o.last_reminder_on for o in mine if o.last_reminder_on), default=None)
        row = {"name": supplier.name, "supplier_id": sid, "href": reverse("supply_chain:supplier_detail", args=[sid])}
        # Last chased reads the same chase record as Invitations, replied or not.
        count = max(((reminder_counts or {}).get(o.pk, 0) for o in mine), default=0)
        if chased:
            count = max(count, 1)
        row["chased"] = (_day(chased) + (f" · {_ordinal(count)} reminder" if count else "")) if chased else ""
        if theirs_quotes:
            quote = theirs_quotes[-1]
            compared_row = rows_by_quote.get(quote.pk)
            late = bool(quote.received_on and deadline and quote.received_on > deadline)
            row["chip"] = {"label": "Quote, late" if late else "Quote", "tone": PRIMARY}
            row["quote"] = _quote_summary(quote, compared_row)
            row["quote_src"] = AI if quote.pk in ai_quotes else PERSON
            # Split as the comparison splits it: the supplier's facts are what is missing from
            # the quote (and what Ask asks for); ours are a count, linking to the comparison.
            ours_g, theirs_g = split_gaps(open_facts.get(quote.pk, []))
            row["missing"] = theirs_g
            # Whose step each missing fact is: ours to ask until we chase after the quote, then theirs.
            row["missing_owner"] = rules.SUPPLIERS if asked_since_quote(quote, mine) else rules.TO_ASK
            row["on_us"] = rules.facts_chip(ours_g, rules.US)
            row["on_us_href"] = (
                reverse("supply_chain:procurement_comparison", args=[tender.pk]) + f"?commodity={quote.commodity.slug}"
                if ours_g
                else ""
            )
            ask = rules.supplier_action(rules.ACTION_ASK, sid)
            if theirs_g and ask["anchor"] in draft_anchors:
                row["action"] = {"label": ask["label"], "href": f"#{ask['anchor']}"}
            else:
                row["action"] = {
                    "label": "Open quote",
                    "href": reverse("supply_chain:procurement_quote_detail", args=[quote.pk]),
                }
        elif questions:
            row["chip"] = {"label": "Questions for us", "tone": OURS}
            row["missing"], row["on_us"] = [], ""
            reply = rules.supplier_action(rules.ACTION_REPLY, sid)
            row["action"] = {
                "label": reply["label"],
                "href": f"#{reply['anchor']}" if reply["anchor"] in draft_anchors else "#owed",
            }
        elif sid in silent:
            days = (today - asked).days if asked else None
            row["chip"] = {"label": f"Silent {days}d" if days is not None else "Silent", "tone": THEIRS}
            row["missing"], row["on_us"] = [], ""
            remind = rules.supplier_action(rules.ACTION_REMIND, sid)
            if remind["anchor"] in draft_anchors:
                # The reminder is already drafted: Remind opens it, as the On suppliers rail does.
                row["action"] = {"label": remind["label"], "href": f"#{remind['anchor']}"}
            elif mine:
                row["action"] = {
                    "label": rules.supplier_action(rules.ACTION_RECORD_REPLY)["label"],
                    "href": reverse("supply_chain:procurement_outreach_reply", args=[mine[0].pk]),
                }
        else:
            kind = next((o.response_kind for o in mine if o.responded and o.response_kind), "")
            # One word for a reply that was questions, here and in the comparison: questions for us.
            if kind == "needs_info":
                row["chip"] = {"label": "Questions for us", "tone": OURS}
            else:
                row["chip"] = {"label": (kind.replace("_", " ") or "replied").capitalize(), "tone": NEUTRAL}
            row["missing"], row["on_us"] = [], ""
        supplier_rows.append(row)

    # The rail on this page: in the table's order, named by supplier (the chip carries the silence),
    # and a reminder not yet due shows when it falls due instead of Remind.
    from datetime import timedelta

    from connect_labs.supply_chain.procurement.operations import DEFAULT_REMINDER_INTERVAL_DAYS

    interval = getattr(tender, "reminder_interval_days", None) or DEFAULT_REMINDER_INTERVAL_DAYS
    position = {sid: i for i, sid in enumerate(order)}
    theirs.sort(key=lambda m: position.get(m.supplier_id, len(order)))
    for m in theirs:
        name = suppliers[m.supplier_id].name if m.supplier_id in suppliers else m.text
        rows_for = silent.get(m.supplier_id) or []
        asked = max((o.sent_on for o in rows_for if o.sent_on), default=None)
        chased = max((o.last_reminder_on for o in rows_for if o.last_reminder_on), default=None)
        m.text = name
        m.chip = f"Silent {(today - asked).days}d" if asked else "Silent"
        if rules.supplier_action(rules.ACTION_REMIND, m.supplier_id)["anchor"] not in draft_anchors and chased:
            due = chased + timedelta(days=interval)
            # Never a reminder after the response deadline: the deadline is the last word.
            if deadline and due > deadline:
                m.due = f"deadline {_day(deadline)}"
            else:
                m.due = f"next due {_day(due)}"
    for m in ours:
        if m.rule == rules.RULE_DEADLINE:
            m.text = "Decide: extend, close or award"

    # Tiles.
    with_questions = sum(1 for sid in invited if owed_by_org.get(getattr(suppliers[sid], "org_id", None)))
    silent_days = [
        (today - max(o.sent_on for o in rows if o.sent_on)).days
        for rows in silent.values()
        if any(o.sent_on for o in rows)
    ]
    chased_silent = sum(1 for rows in silent.values() if any(o.last_reminder_on for o in rows))
    blocked_terms = sum(1 for c in compared for row in c.blocked if _blocked_by_terms(row))
    split = [split_gaps(facts) for facts in open_facts.values()]
    waiting_us = sum(1 for ours_g, _ in split if ours_g)
    supplier_facts = sum(len(theirs_g) for _, theirs_g in split)
    supplier_count = sum(1 for _, theirs_g in split if theirs_g)
    oldest = min((m.since for m in ours if m.since), default=None)
    tiles = [
        {
            "label": "Answered",
            "value": f"{len(replied)} / {len(invited)}" if invited else str(len(live)),
            "sub": " · ".join(
                p
                for p in (
                    _plural(len({q.supplier_id for q in live}), "quote"),
                    f"{with_questions} with questions" if with_questions else "",
                )
                if p
            ),
            "tone": "",
        },
        {
            "label": "Silent",
            "value": str(len(silent)),
            "sub": (
                f"up to {_plural(max(silent_days), 'day')} · {chased_silent} of {len(silent)} chased"
                if silent_days
                else "nobody"
            ),
            "tone": "",
        },
        {
            "label": "Comparable quotes",
            "value": f"{comparable} / {quoted}",
            "sub": " · ".join(
                p
                for p in (
                    f"{_plural(waiting_us, 'quote')} with facts to do" if waiting_us else "",
                    (
                        f"{_plural(supplier_facts, 'fact')} missing from {_plural(supplier_count, 'supplier')}"
                        if supplier_facts
                        else ""
                    ),
                )
                if p
            ),
            "tone": "",
        },
        {
            "label": rules.TO_DO,
            "value": str(len(ours)),
            "sub": f"oldest open {_plural((today - oldest).days, 'day')}" if oldest and oldest <= today else "",
            "tone": OURS if ours else "",
        },
    ]

    # The stage bar.
    received = None
    if contract is not None:
        received = (
            Receipt.objects.filter(contract=contract)
            .order_by("-received_on")
            .values_list("received_on", flat=True)
            .first()
        )
    if contract is not None:
        index = 6 if received and contract.status in ("received", "closed") else 5
    elif tender.status == "draft":
        index = 0
    elif tender.status == "open":
        index = 1
    elif tender.status == "awarded":
        index = 3
    else:
        index = 2
    first_ask = min((o.sent_on for o in outreach if o.sent_on), default=None)
    awardee = award.quote.supplier.name if award and award.quote_id else ""
    collecting_note = f"{len(replied)} of {len(invited)} answered" if invited else ""
    if tender.status == "open" and deadline:
        ahead = (deadline - today).days
        collecting_note += (" · " if collecting_note else "") + (
            "deadline passed"
            if ahead < 0
            else "deadline today" if ahead == 0 else f"deadline in {_plural(ahead, 'day')}"
        )
    notes = [
        (
            (f"{_day(first_ask)}, {_plural(len(invited), 'supplier')}" if first_ask else "not yet sent")
            if tender.status != "draft"
            else "not yet sent"
        ),
        collecting_note or "—",
        f"{comparable} of {quoted} comparable" if quoted else "—",
        (awardee + (" · provisional" if provisional and contract is None else "")) if awardee else "—",
        (contract.reference or f"Order {contract.pk}") if contract is not None else "—",
        f"received {_day(received)}" if received else "—",
    ]
    stages = [
        {"name": name, "note": note, "state": state} for name, note, state in zip(STAGES, notes, stage_bars(index))
    ]

    # Terms.
    terms_by = duty_terms_set_by(tender.pk, program_id=program_id) if tender.duty_terms else ""
    terms = {
        "duty": tender.duty_terms,
        "duty_estimate": tender.duty_estimate_percent,
        "duty_set_on": tender.duty_terms_set_on,
        "duty_set_by": terms_by,
        "duty_src": _src_of_actor(terms_by) if terms_by else PERSON,
        "deadline": deadline,
        "deadline_days_past": (today - deadline).days if deadline and deadline < today else 0,
        "asked": first_ask,
        "blocks": blocked_terms,
    }

    return {
        "header": _header(tender, len(invited)),
        "stages": stages,
        "tiles": tiles,
        "suppliers": supplier_rows,
        "ours": ours,
        "theirs": theirs,
        "terms": terms,
        "related_order": related_order(tender, today, own_org_id=own_org_id),
        "comparable": comparable,
        "quoted": quoted,
        "comparable_chip": comparable_chip(compared),
        "primary": _primary_action(tender, ours, comparable),
        # Each live quote's open facts as chips -- (fact, owner) -- for the History's quote lines.
        "quote_facts": {
            qid: fact_chips(facts, asked_since_quote(quote_by_id.get(qid), outreach))
            for qid, facts in open_facts.items()
            if facts
        },
    }


def _primary_action(tender, ours, comparable) -> str:
    """The header's filled button follows the stage: "open" for a draft, "decide" while
    the tender is ours to decide (deadline passed) with nothing yet comparable, else "compare"."""
    if tender.status == "draft":
        return "open"
    if comparable == 0 and any(m.rule == rules.RULE_DEADLINE for m in ours):
        return "decide"
    return "compare"


def _quote_summary(quote, row) -> str:
    """ "USD 50.10 / carton · CPT Kano": the price as quoted and the term it is on."""
    words = (row.as_quoted if row is not None else "") or (
        f"{quote.as_quoted_currency} {money_digits(quote.as_quoted_amount)}"
        if quote.as_quoted_amount is not None
        else ""
    )
    words = words.replace(" per ", " / ")
    return " · ".join(p for p in (words or "no price", (quote.incoterm or "").strip()) if p)


def _header(tender, invited: int) -> dict:
    """The tender's title as what it buys, where to, and on what terms."""
    from connect_labs.supply_chain.models import Commodity

    slugs = [line.get("commodity_slug") for line in tender.lines or [] if isinstance(line, dict)]
    names = dict(Commodity.objects.filter(slug__in=slugs).values_list("slug", "name")) if slugs else {}
    parts = []
    for line in tender.lines or []:
        if not isinstance(line, dict):
            continue
        name = names.get(line.get("commodity_slug"), line.get("commodity_slug") or "")
        qty = ""
        if line.get("quantity") not in (None, "") and line.get("quantity_unit"):
            try:
                qty = quantity_phrase(line["quantity"], line["quantity_unit"])
            except Exception:  # noqa: BLE001 -- a malformed line still names the product
                qty = ""
        parts.append(f"{name}, {qty}" if qty else name)
    places = [p for p in tender.delivery_points or [] if isinstance(p, dict)]
    cities = list(
        dict.fromkeys(p.get("city") or p.get("name") or "" for p in places if p.get("city") or p.get("name"))
    )
    title = " + ".join(parts) or tender.label
    if cities:
        title += " to " + ", ".join(cities)
    where = "; ".join(", ".join(x for x in (p.get("name"), p.get("city")) if x) for p in places)
    if tender.pickup_accepted:
        where = (where + " · " if where else "") + "or collected"
    if tender.status in ("closed", "awarded"):
        visibility = "closed to new quotes"
    elif tender.visibility == "private":
        visibility = f"restricted to {_plural(invited, 'invited supplier')}" if invited else "restricted"
    else:
        visibility = "open to all suppliers"
    return {
        "title": title,
        "where": where,
        "incoterm": tender.incoterm_requested,
        "visibility": visibility,
        "status": tender.status,
    }


def related_order(tender, today, *, own_org_id=None) -> dict | None:
    """The most recent order placed from an earlier tender of this program for the same product, if any."""
    from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
    from connect_labs.supply_chain.models import Contract

    slugs = {line.get("commodity_slug") for line in tender.lines or [] if isinstance(line, dict)}
    candidates = (
        Contract.objects.filter(program_id=tender.program_id, tender__isnull=False)
        .exclude(tender=tender)
        .exclude(status="cancelled")
        .filter(tender__created_at__lte=tender.created_at)
        .select_related("supplier__org", "tender", "commodity")
        .order_by("-created_at")
    )
    contract = next((c for c in candidates if not slugs or c.commodity.slug in slugs), None)
    if contract is None:
        return None
    from connect_labs.supply_chain.standing import _order_rows

    row = next(iter(_order_rows(tender.program_id, today, None, own_org_id, only=[contract.pk], keep_done=True)), None)
    ours, _ = rules.contract_moves(contract, today, holds=holds_on_us(contract))
    return {
        "tender_label": contract.tender.label,
        "tender_url": reverse("supply_chain:procurement_tender_detail", args=[contract.tender_id]),
        "reference": contract.reference or f"Order {contract.pk}",
        "supplier": contract.supplier.name,
        "url": reverse("supply_chain:order_detail", args=[contract.pk]),
        "stage": row.stage if row is not None else "",
        "stage_parts": [
            p.strip() for p in (row.stage if row is not None else "").replace(" · ", ", ").split(",") if p.strip()
        ],
        "ours": ours,
    }


# ---- the comparison grid ---------------------------------------------------


def comparison_grid(
    tender, comparison: dict, quotes_by_id: dict, *, ai_quotes=(), awarded=(), draft_anchors=(), waiver_on_file=True
):
    """{"quotes": [...columns], "rows": [...facts]} from a tender_compare snapshot and the quotes themselves."""
    from connect_labs.supply_chain.procurement.services.pricing import buyer_imports
    from connect_labs.supply_chain.records import freight_and_duties_for_incoterm

    # One column order on every visit, by supplier: a quote turning comparable keeps its place.
    rows = sorted(
        [*comparison.get("comparable", []), *comparison.get("blocked", [])],
        key=lambda r: ((r.get("supplier_name") or "").lower(), r.get("quote_id") or 0),
    )
    columns, cells = [], {
        k: [] for k in ("price", "pack", "term", "imports", "freight", "clearing", "duty", "fx", "spec", "landed")
    }
    line_qty, line_unit = _tender_line(tender)
    per_unit = f" / {unit_noun(line_unit)}" if line_unit else ""
    tender_url = reverse("supply_chain:procurement_tender_detail", args=[tender.pk])
    for row in rows:
        quote = quotes_by_id.get(row.get("quote_id")) or _quote_from_row(row)
        gaps = [g for g in row.get("gaps") or []]
        src = AI if row["quote_id"] in ai_quotes else PERSON
        base, pack = row.get("base_unit") or "", row.get("pack_unit") or ""
        pack_gap = f"per {unit_noun(pack)}" if pack else "per pack"
        quote_url = reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]])
        anchor = f"draft-supplier-{row.get('supplier_id')}"
        # ONE gap list per quote: the header chips, the landed cell and the next steps all read it.
        # A quote costed on the waiver with no copy of it on file owes one more fact: ours.
        quote_gaps = quote_open_facts(tender, row, quote, waiver_on_file=waiver_on_file)
        waiver_gap = _WAIVER_DOC in quote_gaps
        our_gaps, supplier_gaps = split_gaps(quote_gaps)
        asked = asked_since_quote(quotes_by_id.get(row.get("quote_id")))
        supplier_owner = rules.SUPPLIERS if asked else rules.TO_ASK
        # The quote's status chips (one per party owing facts), and one action per open gap, ours first.
        chips, actions = [], []
        if row["quote_id"] in awarded:
            chips.append({"label": "Awarded", "tone": PRIMARY})
        elif row.get("is_comparable"):
            chips.append({"label": "Comparable", "tone": PRIMARY})
        if row["quote_id"] not in awarded:
            if our_gaps:
                chips.append({"label": rules.facts_chip(our_gaps, rules.US), "tone": OURS})
            if supplier_gaps:
                chips.append(
                    {"label": rules.facts_chip(supplier_gaps, supplier_owner), "tone": THEIRS if asked else OURS}
                )
            for g in our_gaps:
                if g == _ROUND_DUTY:
                    actions.append(
                        {"label": "Settle duty terms", "href": "#duty-terms", "owner": rules.US, "terms": True}
                    )
                elif g in _ESTIMATE_GAPS:
                    actions.append(
                        {"label": f"Record {g}", "href": f"{tender_url}#import-estimates", "owner": rules.US}
                    )
                elif g == _WAIVER_DOC:
                    actions.append(
                        {
                            "label": f"Attach {_WAIVER_DOC}",
                            "href": "#duty-terms",
                            "owner": rules.US,
                            # Tender-level: attached once on the duty line, not per quote.
                            "terms": True,
                        }
                    )
                else:
                    actions.append({"label": f"Record {g}", "href": quote_url, "owner": rules.US})
            for g in supplier_gaps:
                # Not yet asked: asking is ours, under To do; once asked, the supplier's, under Waiting.
                actions.append(
                    {
                        "label": f"Ask for {g}",
                        "href": f"{tender_url}#{anchor}" if anchor in draft_anchors else quote_url,
                        "owner": supplier_owner,
                    }
                )
            # No Award here: the next steps are open facts only. The award is the
            # fold under the grid, one per comparable quote -- the single place it is offered.
        if not chips:
            chips.append({"label": "Not comparable", "tone": NEUTRAL})
        columns.append(
            {
                "quote_id": row["quote_id"],
                "name": row.get("supplier_name"),
                "item": row.get("item_name") or "",
                "chip": chips[0],
                "chips": chips,
                "actions": actions,
                "ours_actions": [a for a in actions if a.get("owner") != rules.SUPPLIERS],
                "theirs_actions": [a for a in actions if a.get("owner") == rules.SUPPLIERS],
                "action": actions[0] if actions else None,
                "blocked_by_terms": _ROUND_DUTY in gaps,
                "href": reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]]),
                "comparable": bool(row.get("is_comparable")),
            }
        )

        why = {b.get("label"): b.get("fact") for b in row.get("blockers") or [] if isinstance(b, dict)}

        def gap(words="not stated", label=None, owner=rules.SUPPLIERS):
            reason = why.get(label) if label else next((f for k, f in why.items() if k and words and k in words), "")
            # A gap is a value nobody has recorded, so it carries no mark of where a value came from.
            return {"v": words, "gap": True, "src": "", "why": reason or "", "owner": owner}

        def fact(value, source=src):
            return {"v": value, "gap": False, "src": source}

        def blank(words="—"):
            return {"v": words, "gap": False, "mute": True, "src": ""}

        cells["price"].append(
            fact((row.get("as_quoted") or "").replace(" per ", " / ")) if row.get("as_quoted") else gap("no price")
        )
        pack_label = next((g for g in gaps if g.endswith(pack_gap)), None)
        if pack_label:
            cells["pack"].append(gap(f"{pack_label}: not stated", label=pack_label))
        elif quote.base_per_pack_stated:
            grams = (
                f" × {quote.base_unit_grams_stated} g" if quote.base_unit_grams_stated else f" {unit_noun(base, 2)}"
            )
            cells["pack"].append(fact(f"{quote.base_per_pack_stated}{grams}"))
        else:
            cells["pack"].append(blank())
        cells["term"].append(fact(quote.incoterm.strip()) if (quote.incoterm or "").strip() else gap())
        if quote.delivery_mode == "pickup":
            cells["imports"].append(fact("Us (we collect)", CALC))
        elif quote.incoterm or quote.duties_basis in ("included", "excluded"):
            term_code = ((quote.incoterm or "").split() or [""])[0].upper()
            who = "Us" if buyer_imports(quote) else "Supplier"
            cells["imports"].append(fact(f"{who} · {term_code}" if term_code else who, CALC))
        else:
            cells["imports"].append(gap("not known"))
        freight_basis = quote.freight_basis
        source = src
        if freight_basis not in ("included", "excluded"):
            freight_basis = freight_and_duties_for_incoterm(quote.incoterm)[0]
            source = CALC
        freight_label = next((g for g in gaps if g.startswith("freight")), None)
        if row.get("freight_ours") == "open":
            cells["freight"].append(gap("ours: estimate not recorded", label="freight estimate", owner=rules.US))
        elif row.get("freight_ours") == "estimate":
            cells["freight"].append(
                fact(f"ours · USD {money_digits(tender.freight_estimate_per_unit)}{per_unit} (our estimate)", PERSON)
            )
        elif freight_label:
            cells["freight"].append(gap(label=freight_label))
        elif quote.delivery_mode == "pickup":
            cells["freight"].append(
                fact(f"our transport {money_digits(quote.buyer_transport_amount)}", PERSON)
                if quote.buyer_transport_amount is not None
                else gap("not recorded")
            )
        elif freight_basis == "included":
            cells["freight"].append(fact("included", source))
        elif quote.freight_amount is not None:
            cells["freight"].append(fact(f"{quote.as_quoted_currency} {money_digits(quote.freight_amount)} added"))
        else:
            cells["freight"].append(blank())
        if row.get("clearing") == "estimate":
            cells["clearing"].append(
                fact(f"USD {money_digits(tender.clearing_estimate_per_unit)}{per_unit} (our estimate)", PERSON)
            )
        elif row.get("clearing") == "open":
            cells["clearing"].append(gap("ours: estimate not recorded", label="clearing estimate", owner=rules.US))
        else:
            cells["clearing"].append(blank("supplier's (it imports)" if quote.delivery_mode != "pickup" else "—"))
        duty = _duty_cell(tender, quote, gaps, src)
        if waiver_gap:
            duty["pending"] = document_not_on_file("duty_exemption")
            duty["pending_owner"] = rules.US
        cells["duty"].append(duty)
        if (quote.as_quoted_currency or "USD") == "USD":
            cells["fx"].append(blank("n/a · quoted in USD"))
        elif quote.fx_rate_to_usd is not None:
            cells["fx"].append(
                fact(f"1 {quote.as_quoted_currency} = {quote.fx_rate_to_usd.normalize():f} USD", PERSON)
            )
        else:
            cells["fx"].append(gap("not recorded", label="exchange rate", owner=rules.US))
        landed = _landed_per_unit(row, line_qty) if line_qty else {}
        if not landed:
            landed = (row.get("figures") or {}).get("usd_per_pack_normalized") or {}
        if not row.get("is_comparable") and row["quote_id"] not in awarded:
            # Not comparable: no figure, only what blocks it, each with whose it is.
            cells["landed"].append(
                {
                    "v": "not comparable",
                    "gap": False,
                    "mute": True,
                    "src": "",
                    "blocked": [{"label": _gap_word(g), "owner": gap_owner(g)} for g in quote_gaps],
                }
            )
        elif isinstance(landed, dict) and landed.get("amount") not in (None, "") and not landed.get("unconfirmed"):
            figure = fact(f"{landed.get('currency') or 'USD'} {money_digits(landed['amount'])}", CALC)
            if row.get("clearing") == "open":
                figure["qualifier"] = "excl. clearing"
            # Nil duty on a waiver no document on file shows: the order's own mark, pricing.relief_unevidenced.
            if row.get("relief_unevidenced"):
                figure["unconfirmed"] = True
            figure["open"] = [{"label": _gap_word(g), "owner": gap_owner(g)} for g in quote_gaps]
            cells["landed"].append(figure)
        else:
            cells["landed"].append(blank())
        spec = row.get("specification") or {}
        if not spec:
            cells["spec"].append(blank())
        elif spec.get("outcome") == "pass":
            met = spec.get("met") or []
            cells["spec"].append(
                fact(
                    (
                        f"Meets {'; '.join(m[:1].lower() + m[1:] for m in met)}"
                        if met
                        else spec.get("summary") or "meets"
                    )
                    + (f" ({len(met)} of {spec.get('requirement_count') or len(met)} met)" if len(met) > 1 else ""),
                    CALC,
                )
            )
        else:
            cells["spec"].append(
                {
                    "v": spec.get("summary") or "",
                    "gap": True,
                    "src": CALC,
                    "why": "; ".join(spec.get("failures") or []),
                    "owner": rules.SUPPLIERS,
                }
            )
        # A supplier's fact nobody has asked it for is ours to ask: every cell of this column says so.
        if not asked:
            for column_cells in cells.values():
                cell = column_cells[-1] if column_cells else None
                for item in [cell, *((cell or {}).get("open") or []), *((cell or {}).get("blocked") or [])]:
                    if isinstance(item, dict) and item.get("owner") == rules.SUPPLIERS:
                        item["owner"] = rules.TO_ASK
    pack_label = (
        unit_noun(line_unit) if line_qty else f"{unit_noun(rows[0].get('pack_unit') or 'pack')}" if rows else "pack"
    )
    facts = [
        ("landed", f"Landed per {pack_label}", "calculated"),
        ("price", "Quoted price", "as quoted"),
        ("pack", "Pack", "as quoted"),
        ("term", "Delivery term", "as quoted"),
        ("imports", "Who imports", "from the term"),
        ("freight", "Freight", "quote or term"),
        ("clearing", "Clearing & forwarding", "our estimate"),
        ("duty", "Import duty", "tender terms"),
        ("fx", "Exchange rate", "recorded by us"),
        ("spec", "Specification", "checked"),
    ]
    # Each cell names its quote, so a page (or a recorder) can find one quote's fact.
    for key, *_ in facts:
        for column, cell in zip(columns, cells[key]):
            cell["quote_id"] = column["quote_id"]
    # Quotes we import are shown without clearing beside quotes the supplier imports:
    # their totals are not like for like until the clearing estimate is recorded.
    return {
        "like_for_like": comparison.get("like_for_like", True),
        "quotes": columns,
        "rows": [
            {"key": key, "label": label, "src_label": note, "cells": cells[key], "total": key == "landed"}
            for key, label, note in facts
        ],
    }


def _tender_line(tender) -> tuple:
    """(quantity, unit) of the tender's line when it buys one product, else (None, None)."""
    lines = [line for line in getattr(tender, "lines", None) or [] if isinstance(line, dict)]
    if len(lines) != 1:
        return None, None
    try:
        quantity = Decimal(str(lines[0].get("quantity")))
    except (InvalidOperation, TypeError, ValueError):
        return None, None
    return (quantity, lines[0].get("quantity_unit") or "") if quantity > 0 else (None, None)


def _landed_per_unit(row, quantity) -> dict:
    """The landed total for the tender's quantity, per unit of its line: what "Landed per carton" reads."""
    total = (row.get("figures") or {}).get("landed_total_for_tender_quantity") or {}
    if not isinstance(total, dict) or total.get("unconfirmed") or total.get("amount") in (None, ""):
        return {}
    try:
        amount = Decimal(str(total["amount"])) / quantity
    except (InvalidOperation, TypeError, ValueError):
        return {}
    return {"amount": str(amount.quantize(Decimal("0.01"))), "currency": total.get("currency") or "USD"}


def _quote_from_row(row):
    """The few quote facts the grid reads, from a comparison row alone (no quote record to hand)."""
    from types import SimpleNamespace

    as_quoted = row.get("as_quoted") or ""
    currency = as_quoted[:3] if as_quoted[:3].isalpha() and as_quoted[:3].isupper() else "USD"
    return SimpleNamespace(
        base_per_pack_stated=None,
        base_unit_grams_stated=None,
        incoterm="",
        delivery_mode="pickup" if str(row.get("delivery") or "").startswith("collected") else "delivered",
        duties_basis="",
        freight_basis="",
        freight_amount=None,
        duties_amount=None,
        buyer_transport_amount=None,
        as_quoted_currency=currency,
        fx_rate_to_usd=None,
    )


def _duty_cell(tender, quote, gaps, src) -> dict:
    from connect_labs.supply_chain.procurement.services.pricing import buyer_imports

    terms = tender.duty_terms or ""
    if _ROUND_DUTY in gaps:
        return {"v": "our terms: not settled", "gap": True, "src": "", "owner": rules.US}
    if not buyer_imports(quote):
        if quote.duties_basis == "included" or (quote.incoterm or "").upper().startswith("DDP"):
            return {"v": "in price (supplier)", "gap": False, "src": src}
        if quote.duties_amount is not None:
            return {
                "v": f"{quote.as_quoted_currency} {money_digits(quote.duties_amount)} (supplier)",
                "gap": False,
                "src": src,
            }
        return {"v": "not stated", "gap": True, "src": "", "owner": rules.SUPPLIERS}
    if terms == "buyer_waiver":
        return {"v": "waived (our import)", "gap": False, "src": CALC}
    if terms == "buyer_pays":
        if tender.duty_estimate_percent is None:
            return {"v": "our estimate: not recorded", "gap": True, "src": "", "owner": rules.US}
        from decimal import Decimal

        percent = Decimal(str(tender.duty_estimate_percent)).normalize()
        return {"v": f"our estimate {percent:f}%", "gap": False, "src": CALC}
    if any(g.startswith("duties") for g in gaps):
        return {"v": "not stated", "gap": True, "src": "", "owner": rules.SUPPLIERS}
    return {"v": "—", "gap": False, "mute": True, "src": ""}
