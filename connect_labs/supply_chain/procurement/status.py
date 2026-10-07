"""The tender's status view and the comparison grid: facts, laid out, no prose.

`tender_status` builds the top of a tender's page -- header, the six-step
stage bar, four tiles, one row per supplier, the On us / On suppliers lists
(from `moves.py`, nothing else), the terms and a related earlier order.
`comparison_grid` builds the comparison: one row per quote, cheapest landed
price first, one column per fact, with a gap shown as a gap ("not stated")
rather than explained.

Every fact carries where it came from (`src`): "person" (recorded by
someone), "ai" (from an email, recorded through an AI) or "calc" (worked out
here). The page draws a small mark per source and a legend once.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.records import document_kind_label, document_not_on_file, waived_duty
from connect_labs.supply_chain.standing import STAGES, stage_bars
from connect_labs.supply_chain.values import money_digits, quantity_phrase, unit_noun

PERSON, AI, CALC = "person", "ai", "calc"

# Chip tones, by meaning (tailwind.css .status-chip--<tone>): done/primary, on us, on suppliers, neutral,
# and a quote's missing fact of ours -- outlined, never the moves' amber, since it is not a move.
PRIMARY, OURS, THEIRS, NEUTRAL, FACT = "primary", "ours", "theirs", "neutral", "fact"

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


# The stored values behind each quote fact a page marks with its source.
PRICE_FIELDS = ("as_quoted_amount", "as_quoted_currency", "as_quoted_unit", "quantity_basis", "quantity_basis_unit")
PACK_FIELDS = ("base_per_pack_stated", "base_unit_grams_stated")
TERM_FIELDS = ("incoterm",)


def quote_sources(quote_ids, *, program_id) -> dict:
    """{quote id: {attname: (version, source)}}: where each value of a live quote came from.

    Per value, not per version: a correction that types one figure marks that
    figure as a person's, and the figures it carried over keep the mark of the
    version that stated them (`timeline.quote_lineages`).
    """
    from connect_labs.supply_chain.history.timeline import quote_lineages

    return lineage_sources(quote_lineages(quote_ids, program_id=program_id))


def lineage_sources(lineages) -> dict:
    """`quote_sources` from lineages already read (`timeline.quote_lineages`)."""
    return {
        qid: {attname: (version, AI if ai else PERSON) for attname, (version, ai) in lineage.fields.items()}
        for qid, lineage in lineages.items()
    }


def value_src(sources, quote_id, fields, default=PERSON) -> str:
    """The source of the latest of `fields` to change on a quote; `default` when none is known."""
    known = [sources[quote_id][f] for f in fields if f in (sources or {}).get(quote_id, {})]
    return max(known, key=lambda pair: pair[0])[1] if known else default


def pack_text(per_pack, grams, base_unit="") -> str:
    """ "150 × 92 g", or "150 sachets" when no weight was stated: one wording on every sheet."""
    if not per_pack:
        return ""
    if grams:
        return f"{per_pack} × {grams} g"
    noun = unit_noun(base_unit, 2) if base_unit else ""
    return f"{per_pack} {noun}" if noun else str(per_pack)


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


def quote_count_words(compared) -> str:
    """ "3 quotes · 2 with a landed price": how many live quotes, and how many a landed price could be
    computed for. Data, not a verdict on which quotes may be compared -- that is for the buyer reading
    the comparison. Read off the same rows the comparison draws. "" when no quote has come in.
    """
    rows = [row for c in compared for row in (*c.comparable, *c.blocked)]
    if not rows:
        return ""
    priced = sum(1 for row in rows if row.has_landed_price)
    return _plural(len(rows), "quote") + (f" · {priced} with a landed price" if priced else "")


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
    the tender's Suppliers table and Quotes tile, and the overview's Quote gaps all read it.

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
    from connect_labs.supply_chain.history.timeline import duty_terms_set_by
    from connect_labs.supply_chain.models import Award, Commitment, Contract, Outreach, Quote, Receipt

    outreach = list(Outreach.objects.filter(tender=tender).select_related("supplier__org"))
    quotes = list(
        Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item").order_by("pk")
    )
    live = [q for q in quotes if q.is_live]
    commitments = list(
        Commitment.objects.filter(tender=tender, resolved_on__isnull=True).select_related("owed_to_org")
    )
    closed = list(Commitment.objects.filter(tender=tender, kind="question", resolved_on__isnull=False))
    answered = rules.questions_answered_on(closed)
    contract = Contract.objects.filter(tender=tender).exclude(status="cancelled").order_by("pk").first()
    award = Award.objects.filter(tender=tender).select_related("quote__supplier__org").first()
    provisional = bool(award and award.provisional)
    ours, theirs = rules.tender_moves(
        tender,
        today,
        outreach=outreach,
        quotes=quotes,
        commitments=commitments,
        answered=answered,
        sent=rules.replies_sent_on(closed),
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
    quoted = sum(len(c.comparable) + len(c.blocked) for c in compared)
    # How many a landed price could be computed for: a count of a figure, not a verdict.
    priced = sum(c.priced_count for c in compared)
    sources = quote_sources([q.pk for q in live], program_id=program_id)

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
    # Replied without a quote, their questions answered: waiting on their quote (rule e).
    awaiting = {m.supplier_id for m in theirs if m.state == rules.AWAITING_QUOTE}
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
        # The stored values behind the Suppliers sheet's cells, each editable in place (cells.py).
        row["asked_on"], row["chased_on"], row["reminders"] = asked, chased, 0
        # Last chased reads the same chase record as Invitations, replied or not.
        count = max(((reminder_counts or {}).get(o.pk, 0) for o in mine), default=0)
        if chased:
            count = max(count, 1)
        row["chased"] = (_day(chased) + (f" · {_ordinal(count)} reminder" if count else "")) if chased else ""
        row["reminders"] = f"{_ordinal(count)} reminder" if count else ""
        if theirs_quotes:
            quote = theirs_quotes[-1]
            compared_row = rows_by_quote.get(quote.pk)
            late = bool(quote.received_on and deadline and quote.received_on > deadline)
            row["chip"] = {"label": "Quote, late" if late else "Quote", "tone": PRIMARY}
            row["quote"] = _quote_summary(quote, compared_row)
            row["live_quote"] = quote
            price, _, per = row["quote"].split(" · ")[0].partition(" / ")
            row["price"], row["price_per"] = price, per
            row["pack"] = pack_text(
                quote.base_per_pack_stated,
                quote.base_unit_grams_stated,
                getattr(compared_row, "base_unit", "") or getattr(quote.commodity, "base_unit", ""),
            )
            # Each value's own source: a corrected pack is a person's, the AI-read price beside it stays the AI's.
            row["quote_src"] = value_src(sources, quote.pk, PRICE_FIELDS)
            row["pack_src"] = value_src(sources, quote.pk, PACK_FIELDS)
            row["term_src"] = value_src(sources, quote.pk, TERM_FIELDS)
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
        elif sid in awaiting:
            row["chip"] = {"label": rules.AWAITING_QUOTE_CHIP, "tone": THEIRS}
            row["missing"], row["on_us"] = [], ""
            row["action"] = {
                "label": rules.supplier_action(rules.ACTION_RECORD_QUOTE)["label"],
                "href": reverse("supply_chain:procurement_quote_entry") + f"?tender={tender.pk}",
            }
        else:
            kind = next((o.response_kind for o in mine if o.responded and o.response_kind), "")
            # One word for a reply that was questions, here and in the comparison: questions for us --
            # until they are answered, when they are no longer ours.
            if kind == "needs_info" and getattr(supplier, "org_id", None) in answered:
                row["chip"] = {"label": "Questions answered", "tone": NEUTRAL}
            elif kind == "needs_info":
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
        if m.state == rules.AWAITING_QUOTE:
            # Named, with its own chip; no reminder is drafted to a supplier that replied.
            m.text = name
            continue
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
    split = {qid: split_gaps(facts) for qid, facts in open_facts.items()}
    waiting_us = sum(1 for ours_g, _ in split.values() if ours_g)
    ours_word = rules.gap_chip_word(rules.US, [g for ours_g, _ in split.values() for g in ours_g])
    # A supplier's fact is ours to ask until we chase after its quote, then theirs: the tile says which.
    asked = {qid: asked_since_quote(quote_by_id.get(qid), outreach) for qid in split}
    to_ask = sum(len(theirs_g) for qid, (_, theirs_g) in split.items() if not asked[qid])
    waiting = sum(len(theirs_g) for qid, (_, theirs_g) in split.items() if asked[qid])
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
            "label": "Quotes",
            "value": str(quoted),
            "sub": " · ".join(
                p
                for p in (
                    f"{priced} with a landed price" if quoted else "",
                    f"{ours_word} on {_plural(waiting_us, 'quote')}" if waiting_us else "",
                    f"{_plural(to_ask, 'fact')} to ask" if to_ask else "",
                    f"{_plural(waiting, 'fact')} waiting" if waiting else "",
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
    # The bar says where the tender is -- when it was asked, how long it has left -- and
    # leaves the counts (answered, quotes, suppliers asked) to the tiles beneath it.
    collecting_note = ""
    if tender.status == "open" and deadline:
        ahead = (deadline - today).days
        collecting_note += (" · " if collecting_note else "") + (
            "deadline passed"
            if ahead < 0
            else "deadline today" if ahead == 0 else f"deadline in {_plural(ahead, 'day')}"
        )
    notes = [
        ((_day(first_ask) if first_ask else "not yet sent") if tender.status != "draft" else "not yet sent"),
        collecting_note or "—",
        "—",
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
        "quoted": quoted,
        "priced": priced,
        "primary": _primary_action(tender, ours, quoted),
    }


def _primary_action(tender, ours, quoted) -> str:
    """The header's filled button follows the stage: "open" for a draft, "decide" while
    the tender is ours to decide (deadline passed) with no quote in yet, else "compare" --
    any quote can be read on the comparison, whatever facts it still lacks."""
    if tender.status == "draft":
        return "open"
    if quoted == 0 and any(m.rule == rules.RULE_DEADLINE for m in ours):
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
    tender,
    comparison: dict,
    quotes_by_id: dict,
    *,
    ai_quotes=(),
    sources=None,
    awarded=(),
    draft_anchors=(),
    waiver_on_file=True,
):
    """{"quotes": [...columns], "rows": [...facts]} from a tender_compare snapshot and the quotes themselves.

    `sources` ({quote id: {attname: (version, source)}}, from `quote_sources`)
    marks each value by the version that last changed it; a quote without it
    falls back to `ai_quotes`, whole quote at a time.
    """
    from connect_labs.supply_chain.procurement.services.pricing import buyer_imports
    from connect_labs.supply_chain.records import freight_and_duties_for_incoterm

    line_qty, line_unit = _tender_line(tender)
    # Cheapest first: every quote a landed price could be computed for, lowest first, then the rest
    # by supplier. The landed price is the one the Landed column shows, so the order is the column's
    # order. No quote is held out of it for a verdict: the figures are shown, the buyer judges them.
    priced = {}
    for row in [*comparison.get("comparable", []), *comparison.get("blocked", [])]:
        amount = _landed_amount(row, line_qty)
        if amount is not None:
            priced[row.get("quote_id")] = amount
    rows = sorted(
        [*comparison.get("comparable", []), *comparison.get("blocked", [])],
        key=lambda r: (
            r.get("quote_id") not in priced,
            priced.get(r.get("quote_id"), Decimal(0)),
            (r.get("supplier_name") or "").lower(),
            r.get("quote_id") or 0,
        ),
    )
    # One landed price is not a comparison: "lowest" and the difference need two.
    lowest = min(priced.values()) if len(priced) > 1 else None
    columns, cells = [], {
        k: []
        for k in (
            "price",
            "pack",
            "term",
            "imports",
            "freight",
            "clearing",
            "duty",
            "fx",
            "spec",
            "landed",
            "vs_lowest",
        )
    }
    per_unit = f" / {unit_noun(line_unit)}" if line_unit else ""
    tender_url = reverse("supply_chain:procurement_tender_detail", args=[tender.pk])
    for row in rows:
        quote = quotes_by_id.get(row.get("quote_id")) or _quote_from_row(row)
        gaps = [g for g in row.get("gaps") or []]
        src = AI if row["quote_id"] in ai_quotes else PERSON

        def src_of(*fields, _qid=row["quote_id"], _src=src):
            return value_src(sources, _qid, fields, _src)

        base, pack = row.get("base_unit") or "", row.get("pack_unit") or ""
        pack_gap = f"per {unit_noun(pack)}" if pack else "per pack"
        quote_url = reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]])
        anchor = f"draft-supplier-{row.get('supplier_id')}"
        # ONE gap list per quote: the header chips, the landed cell and the next steps all read it.
        # A quote costed on the waiver with no copy of it on file owes one more fact: ours.
        quote_gaps = quote_open_facts(tender, row, quote, waiver_on_file=waiver_on_file)
        waiver_gap = _WAIVER_DOC in quote_gaps
        our_gaps, supplier_gaps = split_gaps(quote_gaps)
        # The exemption document is the tender's, attached once: the duty line above the grid
        # and each quote's duty cell say it; the quote's own cell keeps to what is the quote's.
        our_gaps = [g for g in our_gaps if g != _WAIVER_DOC]
        asked = asked_since_quote(quotes_by_id.get(row.get("quote_id")))
        supplier_owner = rules.SUPPLIERS if asked else rules.TO_ASK
        # The quote's status chips (one per party owing facts), and one action per open gap, ours first.
        chips, actions = [], []
        if row["quote_id"] in awarded:
            chips.append({"label": "Awarded", "tone": PRIMARY})
        if row["quote_id"] not in awarded:
            if our_gaps:
                chips.append({"label": rules.facts_chip(our_gaps, rules.US), "tone": FACT})
            if supplier_gaps:
                chips.append(
                    {"label": rules.facts_chip(supplier_gaps, supplier_owner), "tone": THEIRS if asked else FACT}
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
            # No Award here: the next steps are open facts only. The award is ONE block under
            # the grid, which each quote's Award link opens with that quote chosen.
        columns.append(
            {
                "quote_id": row["quote_id"],
                "name": row.get("supplier_name"),
                "item": row.get("item_name") or "",
                "chip": chips[0] if chips else None,
                "chips": chips,
                "actions": actions,
                "ours_actions": [a for a in actions if a.get("owner") != rules.SUPPLIERS],
                "theirs_actions": [a for a in actions if a.get("owner") == rules.SUPPLIERS],
                "action": actions[0] if actions else None,
                "blocked_by_terms": _ROUND_DUTY in gaps,
                "href": reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]]),
                # Every live quote can be awarded on the one award block below the grid -- whether the
                # facts behind it are enough is the buyer's call; an awarded quote is not offered again.
                "awardable": row["quote_id"] not in awarded,
            }
        )

        why = {b.get("label"): b.get("fact") for b in row.get("blockers") or [] if isinstance(b, dict)}

        def gap(words="not stated", label=None, owner=rules.SUPPLIERS):
            reason = why.get(label) if label else next((f for k, f in why.items() if k and words and k in words), "")
            # A gap is a value nobody has recorded, so it carries no mark of where a value came from.
            return {"v": words, "gap": True, "src": "", "why": reason or "", "owner": owner, "label": label}

        def fact(value, source=src):
            return {"v": value, "gap": False, "src": source}

        def blank(words="—"):
            return {"v": words, "gap": False, "mute": True, "src": ""}

        if row.get("as_quoted"):
            # The amount on the line, what it is per beneath it: the column stays as narrow as a figure.
            amount, _, per = row["as_quoted"].partition(" per ")
            cells["price"].append({**fact(amount, src_of(*PRICE_FIELDS)), "per": f"per {per}" if per else ""})
        else:
            cells["price"].append(gap("no price"))
        pack_label = next((g for g in gaps if g.endswith(pack_gap)), None)
        if pack_label:
            cells["pack"].append(gap(f"{pack_label}: not stated", label=pack_label))
        elif quote.base_per_pack_stated:
            cells["pack"].append(
                fact(pack_text(quote.base_per_pack_stated, quote.base_unit_grams_stated, base), src_of(*PACK_FIELDS))
            )
        else:
            cells["pack"].append(blank())
        cells["term"].append(
            fact(quote.incoterm.strip(), src_of(*TERM_FIELDS)) if (quote.incoterm or "").strip() else gap()
        )
        # Who imports follows from the term, so it reads under the term, in the same cell.
        if quote.delivery_mode == "pickup":
            cells["imports"].append(fact("we collect", CALC))
        elif quote.incoterm or quote.duties_basis in ("included", "excluded"):
            cells["imports"].append(fact("we import" if buyer_imports(quote) else "supplier imports", CALC))
        else:
            cells["imports"].append(gap("importer not known"))
        freight_basis = quote.freight_basis
        source = src_of("freight_basis")
        if freight_basis not in ("included", "excluded"):
            freight_basis = freight_and_duties_for_incoterm(quote.incoterm)[0]
            source = CALC
        freight_label = next((g for g in gaps if g.startswith("freight")), None)
        # Ex works, the goods are ours at the supplier's door: export clearance and loading at origin
        # are ours too, not only the main carriage, so the freight we estimate is from there.
        from_origin = ((quote.incoterm or "").split() or [""])[0].upper() == "EXW"
        freight_scope = "ours from origin, incl. export clearance" if from_origin else "ours"
        if row.get("freight_ours") == "open":
            cells["freight"].append(
                gap(f"{freight_scope}: estimate not recorded", label="freight estimate", owner=rules.US)
            )
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
            cells["freight"].append(
                fact(
                    f"{quote.as_quoted_currency} {money_digits(quote.freight_amount)} added", src_of("freight_amount")
                )
            )
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
        duty = _duty_cell(tender, quote, gaps, src_of("duties_basis", "incoterm"))
        if waiver_gap:
            # One shape wherever this nil duty shows (here and the order's duty line): the
            # figure, and one chip naming the document it waits on.
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
        # Which stored value each fact cell is, so the grid can edit it in place (cells.py):
        # the supplier's own figures on its quote, ours (estimates, duty terms) on the tender.
        qid = row["quote_id"]
        edits = {
            "price": ("quote", qid, "as_quoted_amount", getattr(quote, "as_quoted_amount", None)),
            "pack": ("quote", qid, "base_per_pack_stated", quote.base_per_pack_stated),
            "term": ("quote", qid, "incoterm", quote.incoterm),
        }
        if row.get("freight_ours") in ("open", "estimate"):
            edits["freight"] = ("tender", tender.pk, "freight_estimate_per_unit", tender.freight_estimate_per_unit)
        elif quote.freight_amount is not None:
            edits["freight"] = ("quote", qid, "freight_amount", quote.freight_amount)
        if row.get("clearing") in ("open", "estimate"):
            edits["clearing"] = ("tender", tender.pk, "clearing_estimate_per_unit", tender.clearing_estimate_per_unit)
        if quote.delivery_mode == "pickup" or buyer_imports(quote):
            edits["duty"] = ("tender", tender.pk, "duty_terms", tender.duty_terms)
        if (quote.as_quoted_currency or "USD") != "USD":
            edits["fx"] = ("quote", qid, "fx_rate_to_usd", quote.fx_rate_to_usd)
        # Only a quote record to hand can be corrected; a bare comparison row cannot.
        for fact_key, edit in edits.items() if qid in quotes_by_id else ():
            if cells[fact_key]:
                cells[fact_key][-1]["edit"] = edit
        landed = _landed_figure(row, line_qty)
        if row["quote_id"] not in priced:
            # No landed figure: the inputs it still needs, as data, each with whose it is.
            needs = _landed_needs(row, line_qty)
            cells["landed"].append(
                {
                    "v": f"needs {', '.join(needs)}" if needs else "not computed",
                    "gap": False,
                    "mute": True,
                    "src": "",
                    "needs": [{"label": word, "owner": gap_owner(word)} for word in needs],
                }
            )
        elif isinstance(landed, dict) and landed.get("amount") not in (None, "") and not landed.get("unconfirmed"):
            figure = fact(f"{landed.get('currency') or 'USD'} {money_digits(landed['amount'])}", CALC)
            if lowest is not None and priced.get(row["quote_id"]) == lowest:
                figure["lowest"] = True
            if row.get("clearing") == "open":
                figure["qualifier"] = "excl. clearing"
            # Nil duty on a waiver no document on file shows: the order's own mark, pricing.relief_unevidenced.
            if row.get("relief_unevidenced"):
                figure["unconfirmed"] = True
            figure["open"] = [{"label": _gap_word(g), "owner": gap_owner(g)} for g in quote_gaps]
            cells["landed"].append(figure)
            columns[-1]["landed"] = figure["v"]
        else:
            cells["landed"].append(blank())
        # How far above the lowest landed price, per the same unit: the gap the award has to justify.
        amount = priced.get(row["quote_id"])
        if lowest is None or amount is None:
            cells["vs_lowest"].append(blank())
        elif amount == lowest:
            cells["vs_lowest"].append(blank())
        else:
            currency = (landed.get("currency") if isinstance(landed, dict) else None) or "USD"
            cells["vs_lowest"].append(fact(f"+ {currency} {money_digits(amount - lowest)}", CALC))
        # What a click on a column's header sorts the rows by (sheet_sort.js): the figure itself,
        # not its words; "" is a missing figure, which sorts last either way.
        cells["landed"][-1]["sort"] = str(amount) if amount is not None else ""
        cells["vs_lowest"][-1]["sort"] = str(amount - lowest) if amount is not None and lowest is not None else ""
        price_amount = getattr(quote, "as_quoted_amount", None)
        cells["price"][-1]["sort"] = str(price_amount) if price_amount is not None else ""
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
                for item in [cell, *((cell or {}).get("open") or []), *((cell or {}).get("needs") or [])]:
                    if isinstance(item, dict) and item.get("owner") == rules.SUPPLIERS:
                        item["owner"] = rules.TO_ASK
    pack_label = (
        unit_noun(line_unit) if line_qty else f"{unit_noun(rows[0].get('pack_unit') or 'pack')}" if rows else "pack"
    )
    facts = [
        ("landed", f"Landed per {pack_label}", "calculated"),
        ("price", "Quoted price", "as quoted"),
        ("pack", "Pack", "as quoted"),
        ("term", "Delivery term", "as quoted · who imports"),
        ("freight", "Freight", "quote or term"),
        ("clearing", "Clearing & forwarding", "our estimate"),
        ("duty", "Import duty", "tender terms"),
        ("fx", "Exchange rate", "recorded by us"),
    ]
    if lowest is not None:
        facts.insert(1, ("vs_lowest", "Above lowest", f"per {pack_label}"))
    # The answer stays in view: the landed price (and how far above the lowest) is held beside
    # the pinned supplier while the facts behind it scroll -- an edit far right never hides it.
    pins = {"landed": "sheet-pin-2", "vs_lowest": "sheet-pin-3"}
    # Each cell names its quote, so a page (or a recorder) can find one quote's fact.
    for key in [*(k for k, *_ in facts), "spec", "imports"]:
        for column, cell in zip(columns, cells[key]):
            cell["quote_id"] = column["quote_id"]
            cell["fact"] = key
            cell["pin"] = pins.get(key, "")
            # A missing value's chip, in the one quote-gap vocabulary (moves.gap_chip_word).
            if cell.get("gap") and cell.get("owner"):
                cell["chip"] = rules.gap_chip_word(cell["owner"], [cell["label"]] if cell.get("label") else [])
    # The specification check is one verdict per quote, not a figure beside the others:
    # it rides on the quote's own cell, with its status, not as a column of its own.
    for column, cell in zip(columns, cells["spec"]):
        column["spec"] = _spec_chip(cell)
    for term, imports in zip(cells["term"], cells["imports"]):
        term["imports"] = imports
    # The page reads it the other way round: a quote to a row, a fact to a column. Each
    # quote carries its own cells, in the facts' order, for its row.
    for index, column in enumerate(columns):
        column["cells"] = [cells[key][index] for key, *_ in facts]
    # Quotes we import are shown without clearing beside quotes the supplier imports: the page
    # says the clearing estimate is not recorded, a fact about the figures, not a verdict on them.
    return {
        "like_for_like": comparison.get("like_for_like", True),
        "quotes": columns,
        "rows": [
            {
                "key": key,
                "label": label,
                "src_label": note,
                "cells": cells[key],
                "total": key == "landed",
                "pin": pins.get(key, ""),
            }
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


def _spec_chip(cell) -> dict | None:
    """A quote's specification check as one chip: "spec met · 3 of 3", or what fails, by whose it is."""
    if not cell or cell.get("mute"):
        return None
    if not cell.get("gap"):
        met = cell["v"].rsplit(" (", 1)
        count = met[1].removesuffix(" met)") if len(met) == 2 and met[1].endswith(" met)") else ""
        return {"label": f"spec met · {count}" if count else "spec met", "tone": PRIMARY, "detail": cell["v"]}
    return {
        "label": f"spec: {cell['v']}" if cell.get("v") else "spec not met",
        "tone": FACT if cell.get("owner") in (rules.US, rules.TO_ASK) else THEIRS,
        "detail": cell.get("why") or "",
        "owner": cell.get("owner") or "",
    }


def _landed_figure(row, quantity) -> dict:
    """The figure the grid's Landed column reads: the landed total per unit of the tender's line, or,
    with no single line quantity to divide by, the landed price per pack. {} when neither is computed.

    The per-pack price stands in only when the tender has no line quantity: with one, a quote whose
    landed total could not be computed shows what it needs, never its bare price under "Landed".
    """
    if quantity:
        return _landed_per_unit(row, quantity)
    landed = (row.get("figures") or {}).get("usd_per_pack_normalized") or {}
    return landed if isinstance(landed, dict) else {}


def _landed_needs(row, quantity) -> list:
    """What the landed figure still needs, in the grid's gap words: "exchange rate", "freight estimate".

    Read off the figure's own Unconfirmed reasons, so the cell names the inputs of THIS figure; a row
    whose figure carries no reasons falls back on the quote's gap list.
    """
    from connect_labs.supply_chain.procurement.services.comparison import gap_word

    key = "landed_total_for_tender_quantity" if quantity else "usd_per_pack_normalized"
    figure = (row.get("figures") or {}).get(key) or {}
    reasons = figure.get("unconfirmed") if isinstance(figure, dict) else None
    base, pack = row.get("base_unit") or "", row.get("pack_unit") or ""
    words = [gap_word(reason, base, pack) for reason in reasons or []] or list(row.get("gaps") or [])
    return list(dict.fromkeys(_gap_word(word) for word in words if word))


def _landed_amount(row, quantity):
    """The landed price the grid's Landed column shows for a comparison row, as a Decimal; None without one."""
    landed = _landed_figure(row, quantity)
    if not isinstance(landed, dict) or landed.get("unconfirmed") or landed.get("amount") in (None, ""):
        return None
    try:
        return Decimal(str(landed["amount"]))
    except (InvalidOperation, TypeError, ValueError):
        return None


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
        return {"v": "our terms: not settled", "gap": True, "src": "", "owner": rules.US, "label": _ROUND_DUTY}
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
        return {"v": waived_duty(f"USD {money_digits(0)}"), "gap": False, "src": CALC}
    if terms == "buyer_pays":
        if tender.duty_estimate_percent is None:
            return {"v": "our estimate: not recorded", "gap": True, "src": "", "owner": rules.US}
        from decimal import Decimal

        percent = Decimal(str(tender.duty_estimate_percent)).normalize()
        return {"v": f"our estimate {percent:f}%", "gap": False, "src": CALC}
    if any(g.startswith("duties") for g in gaps):
        return {"v": "not stated", "gap": True, "src": "", "owner": rules.SUPPLIERS}
    return {"v": "—", "gap": False, "mute": True, "src": ""}
