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

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
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


def _blocked_by_terms(row) -> bool:
    return _ROUND_DUTY in (row.gaps or [])


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
        anchor = f"draft-supplier-{sid}"
        row = {"name": supplier.name, "supplier_id": sid, "href": reverse("supply_chain:supplier_detail", args=[sid])}
        if theirs_quotes:
            quote = theirs_quotes[-1]
            compared_row = rows_by_quote.get(quote.pk)
            late = bool(quote.received_on and deadline and quote.received_on > deadline)
            row["chip"] = {"label": "Quote, late" if late else "Quote", "tone": PRIMARY}
            row["quote"] = _quote_summary(quote, compared_row)
            row["quote_src"] = AI if quote.pk in ai_quotes else PERSON
            gaps = [g for g in (compared_row.gaps if compared_row else []) if g != _ROUND_DUTY]
            row["missing"] = gaps
            if gaps and anchor in draft_anchors:
                row["action"] = {"label": "Ask", "href": f"#{anchor}"}
            else:
                row["action"] = {
                    "label": "Open quote",
                    "href": reverse("supply_chain:procurement_quote_detail", args=[quote.pk]),
                }
        elif questions:
            row["chip"] = {"label": "Questions for us", "tone": OURS}
            row["missing"] = [f"{_plural(len(questions), 'answer')} from us"]
            reply = f"draft-reply-{sid}"
            row["action"] = {"label": "Reply", "href": f"#{reply}" if reply in draft_anchors else "#owed"}
        elif sid in silent:
            days = (today - asked).days if asked else None
            row["chip"] = {"label": f"Silent {_plural(days, 'day')}" if days is not None else "Silent", "tone": THEIRS}
            count = max(((reminder_counts or {}).get(o.pk, 0) for o in mine), default=0)
            if chased:
                count = max(count, 1)
            row["missing"] = [f"chased {_day(chased)}" + (f" ({_ordinal(count)})" if count else "")] if chased else []
            if anchor in draft_anchors:
                row["action"] = {"label": "Remind", "href": f"#{anchor}"}
            elif mine:
                row["action"] = {
                    "label": "Record reply",
                    "href": reverse("supply_chain:procurement_outreach_reply", args=[mine[0].pk]),
                }
        else:
            kind = next((o.response_kind for o in mine if o.responded and o.response_kind), "")
            row["chip"] = {"label": (kind.replace("_", " ") or "replied").capitalize(), "tone": NEUTRAL}
            row["missing"] = []
        supplier_rows.append(row)

    # Tiles.
    with_questions = sum(1 for sid in invited if owed_by_org.get(getattr(suppliers[sid], "org_id", None)))
    silent_days = [
        (today - max(o.sent_on for o in rows if o.sent_on)).days
        for rows in silent.values()
        if any(o.sent_on for o in rows)
    ]
    chased_silent = sum(1 for rows in silent.values() if any(o.last_reminder_on for o in rows))
    blocked_terms = sum(1 for c in compared for row in c.blocked if _blocked_by_terms(row))
    blocked_other = sum(1 for c in compared for row in c.blocked if not _blocked_by_terms(row))
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
            "sub": (
                "blocked by our duty terms"
                if blocked_terms and not blocked_other
                else " · ".join(
                    p
                    for p in (
                        f"{blocked_terms} on our duty terms" if blocked_terms else "",
                        (
                            (f"{blocked_other} missing facts" if blocked_other > 1 else "1 missing a fact")
                            if blocked_other
                            else ""
                        ),
                    )
                    if p
                )
            ),
            "tone": "",
        },
        {
            "label": "Moves on us",
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
        collecting_note += (" · " if collecting_note else "") + (
            "deadline passed" if deadline < today else f"until {_day(deadline)}"
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
    }


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
        "ours": ours,
    }


# ---- the comparison grid ---------------------------------------------------


def comparison_grid(tender, comparison: dict, quotes_by_id: dict, *, ai_quotes=(), awarded=(), draft_anchors=()):
    """{"quotes": [...columns], "rows": [...facts]} from a tender_compare snapshot and the quotes themselves."""
    from connect_labs.supply_chain.procurement.services.pricing import buyer_imports
    from connect_labs.supply_chain.records import freight_and_duties_for_incoterm

    rows = [*comparison.get("comparable", []), *comparison.get("blocked", [])]
    columns, cells = [], {
        k: [] for k in ("price", "pack", "term", "imports", "freight", "duty", "fx", "spec", "landed")
    }
    tender_url = reverse("supply_chain:procurement_tender_detail", args=[tender.pk])
    for row in rows:
        quote = quotes_by_id.get(row.get("quote_id")) or _quote_from_row(row)
        gaps = [g for g in row.get("gaps") or []]
        src = AI if row["quote_id"] in ai_quotes else PERSON
        base, pack = row.get("base_unit") or "", row.get("pack_unit") or ""
        pack_gap = f"per {unit_noun(pack)}" if pack else "per pack"
        supplier_gaps = [g for g in gaps if g != _ROUND_DUTY]
        # The quote's status, and one action.
        if row["quote_id"] in awarded:
            chip, action = {"label": "Awarded", "tone": PRIMARY}, None
        elif row.get("is_comparable"):
            chip = {"label": "Comparable", "tone": PRIMARY}
            action = {"label": "Award", "href": f"#award-{row['quote_id']}", "award": True}
        elif gaps and not supplier_gaps:
            chip = {"label": "Waiting on our duty terms", "tone": OURS}
            action = {"label": "Settle duty terms", "href": "#duty-terms"}
        else:
            chip = {"label": f"Missing {_plural(len(supplier_gaps), 'fact')}", "tone": THEIRS}
            anchor = f"draft-supplier-{row.get('supplier_id')}"
            action = {
                "label": f"Ask for {supplier_gaps[0]}" if supplier_gaps else "Open quote",
                "href": (
                    f"{tender_url}#{anchor}"
                    if anchor in draft_anchors
                    else reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]])
                ),
            }
        columns.append(
            {
                "quote_id": row["quote_id"],
                "name": row.get("supplier_name"),
                "item": row.get("item_name") or "",
                "chip": chip,
                "action": action,
                "href": reverse("supply_chain:procurement_quote_detail", args=[row["quote_id"]]),
                "comparable": bool(row.get("is_comparable")),
            }
        )

        why = {b.get("label"): b.get("fact") for b in row.get("blockers") or [] if isinstance(b, dict)}

        def gap(words="not stated", label=None):
            reason = why.get(label) if label else next((f for k, f in why.items() if k and words and k in words), "")
            return {"v": words, "gap": True, "src": src, "why": reason or ""}

        def fact(value, source=src):
            return {"v": value, "gap": False, "src": source}

        def blank():
            return {"v": "—", "gap": False, "mute": True, "src": ""}

        cells["price"].append(
            fact((row.get("as_quoted") or "").replace(" per ", " / ")) if row.get("as_quoted") else gap("no price")
        )
        pack_label = next((g for g in gaps if g.endswith(pack_gap)), None)
        if pack_label:
            cells["pack"].append(gap(label=pack_label))
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
            cells["imports"].append(fact("Us" if buyer_imports(quote) else "Supplier", CALC))
        else:
            cells["imports"].append(gap("not known"))
        freight_basis = quote.freight_basis
        source = src
        if freight_basis not in ("included", "excluded"):
            freight_basis = freight_and_duties_for_incoterm(quote.incoterm)[0]
            source = CALC
        freight_label = next((g for g in gaps if g.startswith("freight")), None)
        if freight_label:
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
        cells["duty"].append(_duty_cell(tender, quote, gaps, src))
        if (quote.as_quoted_currency or "USD") == "USD":
            cells["fx"].append(blank())
        elif quote.fx_rate_to_usd is not None:
            cells["fx"].append(
                fact(f"1 {quote.as_quoted_currency} = {quote.fx_rate_to_usd.normalize():f} USD", PERSON)
            )
        else:
            cells["fx"].append(gap("not recorded", label="exchange rate"))
        landed = (row.get("figures") or {}).get("usd_per_pack_normalized") or {}
        if isinstance(landed, dict) and landed.get("amount") not in (None, "") and not landed.get("unconfirmed"):
            cells["landed"].append(fact(f"{landed.get('currency') or 'USD'} {money_digits(landed['amount'])}", CALC))
        else:
            cells["landed"].append(blank())
        spec = row.get("specification") or {}
        if not spec:
            cells["spec"].append(blank())
        elif spec.get("outcome") == "pass":
            cells["spec"].append(fact(spec.get("summary") or "meets", CALC))
        else:
            cells["spec"].append(
                {
                    "v": spec.get("summary") or "",
                    "gap": True,
                    "src": CALC,
                    "why": "; ".join(spec.get("failures") or []),
                }
            )
    pack_label = f"{unit_noun(rows[0].get('pack_unit') or 'pack')}" if rows else "pack"
    facts = [
        ("price", "Quoted price", "as quoted"),
        ("pack", "Pack", "as quoted"),
        ("term", "Delivery term", "as quoted"),
        ("imports", "Who imports", "from the term"),
        ("freight", "Freight", "quote or term"),
        ("duty", "Import duty", "tender terms"),
        ("fx", "Exchange rate", "recorded by us"),
        ("spec", "Specification", "checked"),
        ("landed", f"Landed per {pack_label}", "calculated"),
    ]
    # Each cell names its quote, so a page (or a recorder) can find one quote's fact.
    for key, *_ in facts:
        for column, cell in zip(columns, cells[key]):
            cell["quote_id"] = column["quote_id"]
    return {
        "quotes": columns,
        "rows": [
            {"key": key, "label": label, "src_label": note, "cells": cells[key], "total": key == "landed"}
            for key, label, note in facts
        ],
    }


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
        return {"v": "our terms: not settled", "gap": True, "src": CALC}
    if not buyer_imports(quote):
        if quote.duties_basis == "included" or (quote.incoterm or "").upper().startswith("DDP"):
            return {"v": "in price (supplier)", "gap": False, "src": src}
        if quote.duties_amount is not None:
            return {
                "v": f"{quote.as_quoted_currency} {money_digits(quote.duties_amount)} (supplier)",
                "gap": False,
                "src": src,
            }
        return {"v": "not stated", "gap": True, "src": src}
    if terms == "buyer_waiver":
        return {"v": "waived (our import)", "gap": False, "src": CALC}
    if terms == "buyer_pays":
        if tender.duty_estimate_percent is None:
            return {"v": "our estimate: not recorded", "gap": True, "src": CALC}
        from decimal import Decimal

        percent = Decimal(str(tender.duty_estimate_percent)).normalize()
        return {"v": f"our estimate {percent:f}%", "gap": False, "src": CALC}
    if any(g.startswith("duties") for g in gaps):
        return {"v": "not stated", "gap": True, "src": src}
    return {"v": "—", "gap": False, "mute": True, "src": ""}
