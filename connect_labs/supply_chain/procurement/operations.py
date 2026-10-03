"""Procurement's capabilities, registered into the shared supply registry.

Handlers take a SupplyDataAccess first and return JSON-serialisable dicts.
"""

from datetime import date

from django.db.models import Q

from connect_labs.supply_chain import records
from connect_labs.supply_chain.operations import (
    _OUTREACH_DATA,
    _OUTREACH_DATA_CREATE,
    _QUOTE_DATA_CORRECTION,
    _QUOTE_DATA_CREATE,
    _TENDER_DATA,
    ID,
    _data_with,
    figure,
    obj,
    record,
    register_operation,
)
from connect_labs.supply_chain.procurement.services.comparison import COURSE_FIGURES, compare_tender
from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.procurement.services.pricing import compute_figures
from connect_labs.supply_chain.procurement.services.questions import SUPPLIER, missing_facts
from connect_labs.supply_chain.procurement.services.render import (
    Sender,
    render_followup,
    render_initial_request,
    render_reminder,
)
from connect_labs.supply_chain.procurement.services.supply_base import supply_base, wire
from connect_labs.supply_chain.values import day_text

# ---- tenders and outreach ----------------------------------------------


@register_operation(
    name="tender_list",
    summary="List quote tenders for this programme with their status and lines.",
    input_schema=obj({}),
)
def tender_list(access):
    return [record(r) for r in access.list_tenders()]


@register_operation(
    name="tender_get",
    summary="Fetch one tender by id, with its commodity lines, delivery places and whether it accepts collection.",
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
)
def tender_get(access, tender_id):
    tender = access.get_tender(tender_id)
    return record(tender) if tender else None


@register_operation(
    name="tender_create",
    summary=(
        "Create a tender (a request for quotes) in draft. Needs lines (commodity_slug, "
        "quantity, quantity_unit), and delivery_points (one or more places) or "
        "pickup_accepted=true, before it can be opened."
    ),
    input_schema=obj({"data": _TENDER_DATA}, required=("data",)),
    is_write=True,
)
def tender_create(access, data):
    return record(access.create_tender(data))


@register_operation(
    name="tender_update",
    summary="Update a tender's label, lines, delivery places, collection, deadline or notes.",
    input_schema=obj({"tender_id": ID, "data": _TENDER_DATA}, required=("tender_id", "data")),
    is_write=True,
)
def tender_update(access, tender_id, data):
    return record(access.update_tender(tender_id, data))


@register_operation(
    name="tender_open",
    summary=(
        "Open a tender for quotes. Refused unless the tender names at least one "
        "delivery place or accepts collection from the supplier, because suppliers "
        "will not quote without knowing where the goods go."
    ),
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
    is_write=True,
)
def tender_open(access, tender_id):
    return record(access.open_tender(tender_id))


@register_operation(
    name="tender_invite_org",
    summary=(
        "Put a supplier organisation on a tender's invited list, so it can see and bid on the tender "
        "while it is restricted. Only organisations registered as suppliers can be invited."
    ),
    input_schema=obj({"tender_id": ID, "org_id": ID}, required=("tender_id", "org_id")),
    is_write=True,
)
def tender_invite_org(access, tender_id, org_id):
    return record(access.invite_org_to_tender(tender_id, org_id))


@register_operation(
    name="tender_uninvite_org",
    summary="Take an organisation off a tender's invited list. A restricted tender disappears for it.",
    input_schema=obj({"tender_id": ID, "org_id": ID}, required=("tender_id", "org_id")),
    is_write=True,
)
def tender_uninvite_org(access, tender_id, org_id):
    return record(access.uninvite_org_from_tender(tender_id, org_id))


@register_operation(
    name="tender_close",
    summary="Close a tender to further quotes.",
    input_schema=obj({"tender_id": ID}, required=("tender_id",)),
    is_write=True,
)
def tender_close(access, tender_id):
    return record(access.close_tender(tender_id))


@register_operation(
    name="request_render",
    summary=(
        "Draft the quote-request email for one supplier on one tender: subject, text, and the "
        "supplier's address when one is on file. Asks for exactly the facts needed to make the reply "
        "comparable, greets the supplier's named contact, gives the tender's reply-by date when it has "
        "one, and is signed by the caller. Nothing is sent: the person sends it from their own mailbox."
    ),
    input_schema=obj(
        {"tender_id": ID, "supplier_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "supplier_id", "commodity_slug"),
    ),
)
def request_render(access, tender_id, supplier_id, commodity_slug):
    tender = access.get_tender(tender_id)
    supplier = access.get_supplier(supplier_id)
    commodity = access.get_commodity(commodity_slug)
    return render_initial_request(commodity, tender, supplier, sender=_sender(access)).as_dict()


@register_operation(
    name="followup_render",
    summary=(
        "Draft a follow-up email for a quote -- subject and text -- naming the quote by its date and "
        "as-quoted price and asking only for the facts still missing before it can be compared."
    ),
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def followup_render(access, quote_id):
    quote = access.get_quote(quote_id)
    tender = access.get_tender(quote.tender_id)
    commodity = access.get_commodity(quote.commodity_slug)
    supplier = access.get_supplier(quote.supplier_id)
    item = access.get_item(quote.item_id) if quote.item_id else None
    return render_followup(quote, commodity, tender, supplier, item=item, sender=_sender(access)).as_dict()


_TODAY = {
    "type": "string",
    "format": "date",
    "description": "The day to draft for, which decides what is overdue. Defaults to today.",
}


@register_operation(
    name="reminder_render",
    summary=(
        "Draft a polite reminder to a supplier who has not answered a quote request: it names the day "
        "we asked and what we asked for, and repeats the questions. Takes the outreach row the request "
        "was logged on. Once the person has sent it, mark it sent with outreach_update setting "
        "last_reminder_on (the result's `mark_sent` is that call), which restarts the reminder interval."
    ),
    input_schema=obj(
        {"outreach_id": ID, "commodity_slug": {"type": "string"}, "today": _TODAY},
        required=("outreach_id",),
    ),
)
def reminder_render(access, outreach_id, commodity_slug=None, today=None):
    outreach = access.get_outreach(outreach_id)
    if outreach is None:
        raise ValueError(f"outreach {outreach_id} not found")
    if not outreach.sent_on:
        raise ValueError(
            f"outreach {outreach_id} has no send date: the request was logged but never sent, so there is "
            "nothing to remind them of. Draft the request itself with request_render."
        )
    tender = outreach.tender
    commodity = _line_commodity(access, tender, commodity_slug)
    day = _day(today)
    draft = render_reminder(
        commodity,
        tender,
        outreach.supplier,
        sent_on=outreach.sent_on,
        last_reminder_on=outreach.last_reminder_on,
        reminders_sent=_reminders_sent(access, [outreach]),
        sender=_sender(access),
        today=day,
    )
    return {
        **draft.as_dict(),
        "outreach_id": outreach.pk,
        "tender_id": tender.pk,
        "supplier_id": outreach.supplier_id,
        "supplier_name": outreach.supplier.name,
        "commodity_slug": commodity.slug,
        "mark_sent": _mark_sent(outreach.pk, day),
    }


DEFAULT_REMINDER_INTERVAL_DAYS = 7

# The order drafts are listed in: the order a round runs, then by name.
# Deliberately not an urgency ranking (design doc section 22).
# A clarification of the round's terms comes last: it goes to every invited
# supplier, so it never takes the place of a supplier's own reminder or follow-up.
_KIND_ORDER = {"reply": 0, "request": 1, "reminder": 2, "followup": 3, "clarification": 4}


@register_operation(
    name="tender_drafts_render",
    summary=(
        "Every email due on a tender now, drafted: a `request` for each invitation logged but never "
        "sent; a `reminder` for each supplier who was asked, has neither replied nor quoted, and has "
        "waited at least the tender's reminder_interval_days since the request or the last reminder "
        "(7 days when the tender sets none -- the result says which applied); and a `followup` for each "
        "live quote with questions still outstanding for the supplier. Each draft carries supplier, "
        "kind, subject, text, address and `why` it is due. No requests or reminders once the tender is "
        "closed or awarded; no follow-ups once it is awarded. And a `reply` to each supplier whose questions "
        "to us are still open (commitment_record), listing them for the person to answer -- at any stage, "
        "because an answer owed does not lapse with the award. Once the round's import duty terms are set "
        "(tender_set_duty_terms, or an answer that sets them), and while the round still takes quotes, a "
        "`clarification` to every invited supplier stating those terms, so all quote on the same basis. "
        "Writes nothing."
    ),
    input_schema=obj({"tender_id": ID, "today": _TODAY}, required=("tender_id",)),
)
def tender_drafts_render(access, tender_id, today=None):
    tender = access.get_tender(tender_id)
    if tender is None:
        raise ValueError(f"tender {tender_id} not found")
    day = _day(today)
    interval = tender.reminder_interval_days
    is_default = interval is None
    if is_default:
        interval = DEFAULT_REMINDER_INTERVAL_DAYS
    sender = _sender(access)
    commodities = _line_commodities(access, tender)
    quotes = [q for q in access.list_quotes(tender_id=tender_id) if q.is_live]
    accepting = tender.status not in ("closed", "awarded")

    drafts = []
    if accepting:
        drafts += _requests_and_reminders(access, tender, commodities, quotes, day, interval, is_default, sender)
    if tender.status != "awarded":
        drafts += _followups(access, tender, commodities, quotes, day, sender)
    # What we owe comes first and outlives the award: a supplier who asked us
    # something is owed an answer whether or not it won.
    drafts += _replies(access, tender, day, sender)
    if accepting:
        drafts += _clarifications(access, tender, sender)
    drafts.sort(key=lambda d: (_KIND_ORDER[d["kind"]], d["supplier_name"].lower(), d["commodity_slug"]))

    result = {
        "tender_id": tender.pk,
        "today": day.isoformat(),
        "reminder_interval_days": interval,
        "reminder_interval_is_default": is_default,
        "reminder_interval_note": f"A reminder falls due {_days(interval)} after the request or the last "
        "reminder"
        + (
            " -- the default, because this tender sets no reminder interval."
            if is_default
            else ", as this tender sets."
        ),
        "accepting_quotes": accepting,
        "drafts": drafts,
    }
    if not accepting:
        result["note"] = f"This tender is {tender.status}, so no requests or reminders are drafted."
    return result


def _reminders_sent(access, rows) -> int:
    """How many reminders went to these invitations, from the history, floored at 1
    when one carries a last-reminded day -- the count the tender's drafts panel reads."""
    from connect_labs.supply_chain.history.timeline import reminders_for_outreach

    counts = reminders_for_outreach([r.pk for r in rows], program_id=access.program_id)
    total = max((counts.get(r.pk, 0) for r in rows), default=0)
    if any(getattr(r, "last_reminder_on", None) for r in rows):
        total = max(total, 1)
    return total


def _requests_and_reminders(access, tender, commodities, quotes, day, interval, is_default, sender):
    """A request for an invitation never sent; a reminder for a silence past the interval."""
    quoted = {q.supplier_id for q in quotes}
    rows_by_supplier = {}
    for row in access.list_outreach(tender_id=tender.pk):
        rows_by_supplier.setdefault(row.supplier_id, []).append(row)
    drafts = []
    for supplier_id, rows in rows_by_supplier.items():
        if supplier_id in quoted or any(r.responded for r in rows):
            continue
        supplier = rows[0].supplier
        sent = [r for r in rows if r.sent_on]
        if not sent:
            why = (
                "An invitation is logged for this supplier with no send date, so the request has not gone "
                "out yet. Once it has, record the day it was sent on the invitation."
            )
            for commodity in commodities:
                draft = render_initial_request(commodity, tender, supplier, sender=sender, today=day)
                drafts.append(_draft_item(draft, "request", supplier, commodity, why=why, outreach_id=rows[0].pk))
            continue
        latest = max(sent, key=lambda r: r.sent_on)
        reminded = max((r.last_reminder_on for r in rows if r.last_reminder_on), default=None)
        since = max(latest.sent_on, reminded) if reminded else latest.sent_on
        if (day - since).days < interval:
            continue
        why = f"Asked on {day_text(latest.sent_on)} ({_days(day - latest.sent_on)} ago) and no reply is recorded"
        if reminded:
            why += f"; last reminded on {day_text(reminded)} ({_days(day - reminded)} ago)"
        why += f". A reminder is due every {_days(interval)}" + (
            " (the default: this tender sets no reminder interval)." if is_default else ", as this tender sets."
        )
        for commodity in commodities:
            draft = render_reminder(
                commodity,
                tender,
                supplier,
                sent_on=latest.sent_on,
                last_reminder_on=reminded,
                reminders_sent=_reminders_sent(access, rows) if reminded else 0,
                sender=sender,
                today=day,
            )
            drafts.append(
                _draft_item(
                    draft,
                    "reminder",
                    supplier,
                    commodity,
                    why=why,
                    outreach_id=latest.pk,
                    mark_sent=_mark_sent(latest.pk, day),
                )
            )
    return drafts


def _followups(access, tender, commodities, quotes, day, sender):
    """A follow-up for each live quote with a question only the supplier can answer."""
    by_slug = {c.slug: c for c in commodities}
    drafts = []
    for quote in quotes:
        commodity = by_slug.get(quote.commodity_slug) or access.get_commodity(quote.commodity_slug)
        if commodity is None:
            continue
        item = access.get_item(quote.item_id) if quote.item_id else None
        facts = [f for f in missing_facts(quote, commodity, tender, item=item) if f.audience == SUPPLIER]
        if not facts:
            continue
        draft = render_followup(quote, commodity, tender, quote.supplier, item=item, sender=sender, today=day)
        count = len(facts)
        why = (
            f"{count} question{'s' if count != 1 else ''} still outstanding for the supplier on this quote "
            "before it can be compared or accepted."
        )
        drafts.append(_draft_item(draft, "followup", quote.supplier, commodity, why=why, quote_id=quote.pk))
    return drafts


def _replies(access, tender, day, sender):
    """A reply to each counterparty on this tender whose questions to us are open or answered today.

    An answer marked today goes into the reply with what was said, so marking a
    question answered fills in the email that tells the supplier; an open one
    keeps its "[Your answer]" blank. A counterparty whose questions were all
    answered on an earlier day is owed no reply from here.
    """
    by_org = {}
    for commitment in access.list_commitments(tender_id=tender.pk, open_only=False):
        # An answer already marked sent has gone; it is owed no reply from here.
        if (
            commitment.kind == "question"
            and commitment.reply_sent_on is None
            and (commitment.resolved_on is None or commitment.resolved_on == day)
        ):
            by_org.setdefault(commitment.owed_to_org_id, []).append(commitment)
    suppliers = {s.org_id: s for s in access.list_suppliers()}
    drafts = []
    for org_id, questions in by_org.items():
        supplier = suppliers.get(org_id)
        name = supplier.name if supplier is not None else questions[0].owed_to_org.name
        address = ""
        for contact in (supplier.contacts if supplier is not None else None) or []:
            if isinstance(contact, dict) and contact.get("email"):
                address = contact["email"]
                break
        asked = min(q.raised_on for q in questions)
        lines = [
            f"{i}. {q.text}\n   {q.resolution if q.resolved_on is not None and q.resolution else '[Your answer]'}"
            for i, q in enumerate(questions, start=1)
        ]
        still_open = [q for q in questions if q.resolved_on is None]
        text = (
            f"Dear {name},\n\nThank you for your questions of {day_text(asked)} about {tender.label}. "
            "Our answers:\n\n"
            + "\n\n".join(lines)
            + f"\n\nKind regards,\n{sender.name}"
            + (f"\n{sender.organisation}" if sender.organisation else "")
        )
        drafts.append(
            {
                "kind": "reply",
                "supplier_id": supplier.pk if supplier is not None else None,
                "supplier_name": name,
                "commodity_slug": "",
                "subject": f"Re: {tender.label} — answers to your questions",
                "text": text,
                "to": address,
                "why": (
                    f"{len(still_open)} question{'s' if len(still_open) != 1 else ''} from {name} "
                    f"open since {day_text(asked)}: we owe the answer. Once sent, mark each question answered "
                    "under What we owe them."
                    + (
                        f" {len(questions) - len(still_open)} answered today, already written in."
                        if len(still_open) != len(questions)
                        else ""
                    )
                    if still_open
                    else f"Every question from {name} answered today: the answers are written in, ready to send."
                ),
                "commitment_ids": [q.pk for q in questions],
                # The answers written in, which the reply carries once sent (commitment_reply_sent).
                "answered_ids": [q.pk for q in questions if q.resolved_on is not None],
            }
        )
    return drafts


# What every invited supplier is told once the round's import duty terms are
# set, per terms: how to quote, and the freight still wanted where we import.
_CLARIFICATION_ASK = {
    "buyer_waiver": (
        "For this round we import, under the program's duty waiver; please quote excluding import duty "
        "and state freight to {where}."
    ),
    "buyer_pays": (
        "For this round we import and pay the import duty ourselves; please quote excluding import duty "
        "and state freight to {where}."
    ),
    "supplier_ddp": (
        "For this round the supplier delivers duty paid; please quote delivered duty paid to {where}, "
        "with the import duty included."
    ),
}


def _clarifications(access, tender, sender):
    """A clarification of the round's import duty terms to every supplier invited to it.

    An answer to one supplier ("we import, under the waiver") changes how every
    quote on the round is costed, so every invited supplier is told -- not only
    the one who asked.
    """
    template = _CLARIFICATION_ASK.get(tender.duty_terms or "")
    if template is None:
        return []
    from connect_labs.supply_chain.history.timeline import duty_terms_answer
    from connect_labs.supply_chain.values import destination_phrase

    ask = template.format(where=destination_phrase(tender.delivery_points or []))
    answer = duty_terms_answer(tender.pk, program_id=getattr(access, "program_id", None))
    set_on = (answer or {}).get("on") or tender.duty_terms_set_on
    why = "The round's import duty terms are set"
    if set_on:
        why += f" ({day_text(set_on)})"
    if answer and answer.get("owed_to"):
        why += f", from your answer to {answer['owed_to']}"
    why += "; every invited supplier is told, so all quote on the same basis."
    suppliers = {}
    for row in access.list_outreach(tender_id=tender.pk):
        suppliers.setdefault(row.supplier_id, row.supplier)
    drafts = []
    for supplier in suppliers.values():
        if supplier is None:
            continue
        address = ""
        for contact in getattr(supplier, "contacts", None) or []:
            if isinstance(contact, dict) and contact.get("email"):
                address = contact["email"]
                break
        text = (
            f"Dear {supplier.name},\n\nA clarification on {tender.label}, sent to every supplier invited to "
            f"quote: {ask}\n\nKind regards,\n{sender.name}"
            + (f"\n{sender.organisation}" if sender.organisation else "")
        )
        drafts.append(
            {
                "kind": "clarification",
                "supplier_id": supplier.pk,
                "supplier_name": supplier.name,
                "commodity_slug": "",
                "subject": f"{tender.label} — clarification: import duty terms",
                "text": text,
                "to": address,
                "why": why,
                "duty_terms": tender.duty_terms,
            }
        )
    return drafts


def _sender(access) -> Sender:
    """Who signs a draft: the signed-in person (over MCP, the token's user) and their organisation."""
    from connect_labs.supply_chain.identity import acting_org_name, person_name

    organisation = acting_org_name(access)
    if not organisation and getattr(access, "program_id", None):
        # Not resolvable from the caller alone, but the program's buyer of record
        # is known from its orders: the same name the Supply banner says.
        from connect_labs.supply_chain.banner import _buyer_of_record

        organisation = _buyer_of_record(access, access.program_id)
    return Sender(name=person_name(getattr(access, "user", None)), organisation=organisation)


def _day(value) -> date:
    return date.fromisoformat(str(value)) if value else date.today()


def _days(value) -> str:
    count = value.days if hasattr(value, "days") else int(value)
    return f"{count} day{'s' if count != 1 else ''}"


def _line_commodities(access, tender) -> list:
    slugs = [line.get("commodity_slug") for line in tender.lines or [] if isinstance(line, dict)]
    found = [access.get_commodity(slug) for slug in dict.fromkeys(s for s in slugs if s)]
    return [c for c in found if c is not None]


def _line_commodity(access, tender, commodity_slug):
    slugs = [line.get("commodity_slug") for line in tender.lines or [] if isinstance(line, dict)]
    slugs = [slug for slug in slugs if slug]
    if commodity_slug is None:
        if len(slugs) != 1:
            raise ValueError(
                f"tender {tender.pk} has {len(slugs)} lines ({', '.join(slugs) or 'none'}); name the "
                "commodity_slug the reminder is about"
            )
        commodity_slug = slugs[0]
    commodity = access.get_commodity(commodity_slug)
    if commodity is None:
        raise ValueError(f"commodity {commodity_slug!r} not found")
    return commodity


def _mark_sent(outreach_id, day) -> dict:
    """The call that records a reminder as sent, for a client to make once it has been."""
    return {"operation": "outreach_update", "outreach_id": outreach_id, "data": {"last_reminder_on": day.isoformat()}}


def _draft_item(draft, kind, supplier, commodity, *, why, **ids) -> dict:
    return {
        "kind": kind,
        "supplier_id": supplier.pk,
        "supplier_name": supplier.name,
        "commodity_slug": commodity.slug,
        **draft.as_dict(),
        "why": why,
        **ids,
    }


@register_operation(
    name="outreach_list",
    summary="List outreach rows — who was asked, when, and whether they replied.",
    input_schema=obj({"tender_id": ID}),
)
def outreach_list(access, tender_id=None):
    return [record(o) for o in access.list_outreach(tender_id=tender_id)]


@register_operation(
    name="outreach_log",
    summary="Record that a quote request was sent to a supplier on a tender.",
    input_schema=obj({"data": _OUTREACH_DATA_CREATE}, required=("data",)),
    is_write=True,
)
def outreach_log(access, data):
    return record(access.create_outreach(data))


@register_operation(
    name="outreach_update",
    summary=(
        "Update an outreach row -- typically to record that a supplier responded, and how, or that a "
        "reminder went out: set last_reminder_on to the day it was sent, which restarts the reminder "
        "interval tender_drafts_render counts from."
    ),
    input_schema=obj({"outreach_id": ID, "data": _OUTREACH_DATA}, required=("outreach_id", "data")),
    is_write=True,
)
def outreach_update(access, outreach_id, data):
    return record(access.update_outreach(outreach_id, data))


@register_operation(
    name="outreach_delete",
    summary=(
        "Delete an invitation recorded in error, with a reason. Unlike quote_void this removes the "
        "row: a quote is a supplier's stated fact worth keeping once superseded, an invitation we "
        "never sent is not. The reason is recorded in the write log."
    ),
    input_schema=obj(
        {"outreach_id": ID, "reason": {"type": "string", "minLength": 1}},
        required=("outreach_id", "reason"),
    ),
    is_write=True,
)
def outreach_delete(access, outreach_id, reason):
    return access.delete_outreach(outreach_id)


# ---- quotes ------------------------------------------------------------


@register_operation(
    name="quote_list",
    summary="List quotes, optionally for one tender. Includes voided and superseded versions.",
    input_schema=obj({"tender_id": ID}),
)
def quote_list(access, tender_id=None):
    return [record(q) for q in access.list_quotes(tender_id=tender_id)]


@register_operation(
    name="quote_get",
    summary=(
        "Fetch one quote with its as-quoted figures and basis flags, plus "
        "the derived figures and what is still missing."
    ),
    input_schema=obj({"quote_id": ID}, required=("quote_id",)),
)
def quote_get(access, quote_id):
    quote = access.get_quote(quote_id)
    if quote is None:
        return None
    tender = access.get_tender(quote.tender_id)
    commodity = access.get_commodity(quote.commodity_slug)
    item = access.get_item(quote.item_id) if quote.item_id else None
    figures = compute_figures(quote, commodity, tender, item=item).as_dict()
    missing = missing_facts(quote, commodity, tender, item=item)
    # The comparison's rule, applied to one quote: a category with no course
    # (a consumable, a dispenser, a test kit) has no per-course figure to be
    # unconfirmed and no treatment protocol for us to enter. Showing them here
    # after the comparison stopped reported the same absence as a gap on one
    # screen and not on the other.
    if not records.course_applies_to_category(commodity.category):
        figures = {key: value for key, value in figures.items() if key not in COURSE_FIGURES}
        missing = [f for f in missing if f.key != "course_definition"]
    return {
        "quote": record(quote),
        "item": record(item) if item else None,
        "figures": {key: figure(value) for key, value in figures.items()},
        "compliance": [
            {
                "field": r.field,
                "outcome": r.outcome,
                "message": r.message,
                "spec_origin": r.spec_origin,
                "claim_conflict": r.claim_conflict,
            }
            for r in check_compliance(quote, commodity, item=item)
        ],
        "missing": [f.as_dict() for f in missing],
    }


@register_operation(
    name="quote_record",
    summary=(
        "Record a quote as the supplier stated it, with the quantity_basis the price covers. Do NOT "
        "compute anything: leave freight_basis and duties_basis not_specified, and pack_spec_source "
        "not_stated, where the supplier was silent. Name an item_id with "
        "pack_spec_source=trade_item_confirmed when they identify a known trade item. Give received_on "
        "(the day the supplier's email was sent, not the day it was forwarded) and the supplier's own "
        "supplier_reference when the quote has one. Refused, naming the offer and its evidence, when "
        "this supplier already has a live quote for the product on this tender by the same delivery "
        "option: a forwarded copy is not a second offer. A real second offer names the one it stands "
        "beside in distinct_from_quote_ids; a revised offer is quote_correct."
    ),
    input_schema=obj(
        {"data": _QUOTE_DATA_CREATE, "distinct_from_quote_ids": {"type": "array", "items": ID}},
        required=("data",),
    ),
    is_write=True,
)
def quote_record(access, data, distinct_from_quote_ids=None):
    return record(access.create_quote(data, distinct_from=distinct_from_quote_ids or ()))


@register_operation(
    name="quote_correct",
    summary=(
        "Correct a quote by writing a new version that supersedes it. Needs "
        "a reason. The original stays readable so past comparisons remain "
        "reproducible."
    ),
    input_schema=obj(
        {"quote_id": ID, "data": _QUOTE_DATA_CORRECTION, "reason": {"type": "string", "minLength": 1}},
        required=("quote_id", "data", "reason"),
    ),
    is_write=True,
)
def quote_correct(access, quote_id, data, reason):
    return record(access.supersede_quote(quote_id, data, reason))


@register_operation(
    name="quote_void",
    summary=(
        "Void a quote with a reason — a duplicate, a mis-entry, or a "
        "withdrawn offer. It stays readable and drops out of comparisons. "
        "This is how a caller cleans up after itself."
    ),
    input_schema=obj(
        {"quote_id": ID, "reason": {"type": "string", "minLength": 1}},
        required=("quote_id", "reason"),
    ),
    is_write=True,
)
def quote_void(access, quote_id, reason):
    return record(access.void_quote(quote_id, reason))


@register_operation(
    name="tender_compare",
    summary=(
        "Compare every live quote on a tender for one commodity. Columns report "
        "rankable=false where any candidate is unconfirmed, with blocked_by "
        "naming the suppliers responsible — the honest answer to 'who is "
        "cheapest' is often 'not yet, ask these questions'."
    ),
    input_schema=obj(
        {"tender_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "commodity_slug"),
    ),
)
def tender_compare(access, tender_id, commodity_slug):
    tender = access.get_tender(tender_id)
    commodity = access.get_commodity(commodity_slug)
    quotes = [q for q in access.list_quotes(tender_id=tender_id) if q.commodity_slug == commodity_slug]
    suppliers = {s.id: s for s in access.list_suppliers()}
    return compare_tender(tender, commodity, quotes, suppliers, items_by_id=access.items_by_id()).to_snapshot()


@register_operation(
    name="tender_outstanding_questions",
    summary="Every outstanding question on a tender, grouped by supplier — the follow-up worklist.",
    input_schema=obj(
        {"tender_id": ID, "commodity_slug": {"type": "string"}},
        required=("tender_id", "commodity_slug"),
    ),
)
def tender_outstanding_questions(access, tender_id, commodity_slug):
    snapshot = tender_compare(access, tender_id, commodity_slug)
    return [
        {"supplier_name": row["supplier_name"], "quote_id": row["quote_id"], "questions": row["questions"]}
        for row in snapshot["all_rows"]
        if row["questions"]
    ]


@register_operation(
    name="award_create",
    summary=(
        "Award a tender to a quote. Requires a rationale and freezes the "
        "comparison as it stood at the moment of decision. Once every line of a draft or open tender "
        "has an award, the tender's status becomes awarded; a closed tender is left as it is."
    ),
    input_schema=obj(
        {
            "tender_id": ID,
            "quote_id": ID,
            "rationale": {"type": "string", "minLength": 1},
            "decided_by": {"type": "string"},
            # The day the decision was made, which is often not the day it
            # is recorded -- the meeting was in July, the entry is today. It
            # defaults to today, and may not be a day that has not come.
            "decided_on": {"type": "string", "format": "date"},
        },
        required=("tender_id", "quote_id", "rationale"),
    ),
    is_write=True,
)
def award_create(access, tender_id, quote_id, rationale, decided_by=None, decided_on=None):
    """The decision of record — validate at least as hard as every other write.

    Every other write operation reference-checks what it points at
    (_require_tender, _require_commodity); award_create is the one that
    freezes a comparison_snapshot into a permanent record, so a bad
    reference here is a permanent record of the wrong thing. Three checks
    a tender-trip through tender_compare would not itself catch:
      - the quote exists at all (a bad id would otherwise crash inside
        tender_compare on `quote.commodity_slug`, a confusing AttributeError
        instead of a 400 naming the missing quote);
      - the quote belongs to THIS tender -- without this, tender 1 could be
        awarded to a quote that only ever quoted on tender 2, and the frozen
        snapshot (built from tender_id's own comparison) would never contain
        the quote it claims to have chosen;
      - the quote is live -- a voided or superseded quote's row is already
        filtered out of compare_tender's snapshot by `_is_live`, so awarding
        one would freeze a comparison that does not even list the "winner".
    """
    quote = access.get_quote(quote_id)
    if quote is None:
        raise ValueError(f"quote {quote_id} not found")
    if quote.tender_id != tender_id:
        raise ValueError(
            f"quote {quote_id} belongs to tender {quote.tender_id}, not tender {tender_id} — "
            "a quote can only be awarded on the tender it was quoted for"
        )
    if quote.voided:
        raise ValueError(f"quote {quote_id} is voided and cannot be awarded")
    if quote.superseded_by_quote_id:
        raise ValueError(
            f"quote {quote_id} has been superseded by quote {quote.superseded_by_quote_id} — "
            "award the current version instead"
        )
    decided = _decided_on(decided_on)
    snapshot = tender_compare(access, tender_id, quote.commodity_slug)
    return record(
        access.create_award(
            {
                "tender_id": tender_id,
                "quote_id": quote_id,
                "rationale": rationale,
                "decided_by": decided_by,
                "decided_on": decided,
                "comparison_snapshot": snapshot,
            }
        )
    )


def _decided_on(value):
    """A parsed decision date: today when not given, never in the future."""
    from datetime import date

    if not value:
        return date.today()
    decided = date.fromisoformat(str(value))
    if decided > date.today():
        raise ValueError(f"an award cannot be dated {decided.isoformat()}: that is in the future")
    return decided


@register_operation(
    name="award_list",
    summary="List awards for this programme, each with its frozen comparison snapshot and rationale.",
    input_schema=obj({"tender_id": ID}),
)
def award_list(access, tender_id=None):
    return [record(a) for a in access.list_awards(tender_id=tender_id)]


@register_operation(
    name="commodity_supply_base",
    summary=(
        "Who we think can supply a commodity, and the record each belief rests on: contracted, "
        "awarded, quoted, invited, or merely named as the manufacturer of a trade item. Derived and "
        "dated, never stored. Pass item_id to narrow to one trade item."
    ),
    input_schema=obj(
        {"commodity_slug": {"type": "string"}, "item_id": ID},
        required=("commodity_slug",),
    ),
)
def commodity_supply_base(access, commodity_slug, item_id=None):
    claims = supply_base(
        commodity_slug=commodity_slug,
        item_id=item_id,
        suppliers=access.list_suppliers(),
        quotes=access.list_quotes(),
        awards=access.list_awards(),
        contracts=access.list_contracts(),
        outreach=access.list_outreach(),
        tenders=access.list_tenders(),
        items=access.list_items(),
        declared=_declared(access, commodity_slug),
    )
    return [wire(claim) for claim in claims]


def _offers_for(access, commodity_slug):
    """Every marketplace offering that matches this program's product, with how it matched."""
    from connect_labs.supply_chain.market.service import offering_match_kind
    from connect_labs.supply_chain.models import SupplierOffering

    commodity = access.get_commodity(commodity_slug)
    if commodity is None:
        return []
    items = [i for i in access.list_items() if i.commodity_id == commodity.pk]
    gtins = {g for i in items for g in (i.gtin_base, i.gtin_pack, i.gtin_case) if g}
    # Narrowed in the database to the offerings that could match at all, so a
    # product page does not read every offering on the marketplace.
    candidates = Q(gtin__in=gtins) if gtins else Q(pk__in=[])
    if commodity.category:
        candidates |= Q(category=commodity.category)
    if commodity.unicef_material_number:
        candidates |= Q(unicef_material_number=commodity.unicef_material_number)
    found = []
    for offering in SupplierOffering.objects.filter(candidates).select_related("profile__org"):
        match = offering_match_kind(offering, commodity, items)
        if match is not None:
            found.append((offering, match))
    return found


def _declared(access, commodity_slug):
    by_org = {s.org_id: s.pk for s in access.list_suppliers()}
    return [
        (by_org[offering.profile.org_id], offering, match)
        for offering, match in _offers_for(access, commodity_slug)
        if offering.profile.org_id in by_org
    ]


@register_operation(
    name="commodity_market_offers",
    summary=(
        "Companies on the supplier marketplace that say they sell this product and are NOT yet this "
        "program's suppliers -- who to approach next. Their own claim, dated, unverified; `match` says "
        "whether it matched exactly (UNICEF number or GTIN) or only by kind of product."
    ),
    input_schema=obj({"commodity_slug": {"type": "string"}}, required=("commodity_slug",)),
)
def commodity_market_offers(access, commodity_slug):
    ours = {s.org_id for s in access.list_suppliers()}
    return [
        {
            "org_id": offering.profile.org_id,
            "org_name": offering.profile.org.name,
            "country": offering.profile.org.country,
            "product_name": offering.product_name,
            "match": match,
            "certifications": offering.certifications,
            "updated_on": offering.updated_at.date().isoformat() if offering.updated_at else None,
        }
        for offering, match in _offers_for(access, commodity_slug)
        if offering.profile.org_id not in ours
    ]


# Purchases used to live here, as "what an LLO actually paid". They are gone:
# a commitment (Contract), a bill (Invoice) and a settlement (Payment) are
# three different facts with three different dates, and collapsing them into
# one row meant the system could not answer "what have we committed but not
# paid". See fulfilment/operations.py.


@register_operation(
    name="tracker_import",
    summary=(
        "Import a procurement tracker from a Google Sheet: suppliers, tenders, invitations and quotes. "
        "Records what the sheet STATES and refuses what it DERIVES — `refused` lists what it would "
        "not guess at, and is as much the point as `imported`. Idempotent on tender labels and "
        "supplier names. The sheet must be shared with the Drive service account. Use dry_run first."
    ),
    input_schema=obj(
        {
            "spreadsheet_id": {"type": "string"},
            "commodity_slug": {"type": "string"},
            "ensure_commodity": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
        }
    ),
    is_write=True,
    internal=True,
)
def tracker_import(access, spreadsheet_id=None, commodity_slug="rutf", ensure_commodity=False, dry_run=False):
    from connect_labs.supply_chain.procurement.services import tracker_import as service

    return service.import_tracker(
        access,
        spreadsheet_id=spreadsheet_id or service.SPREADSHEET_ID,
        commodity_slug=commodity_slug,
        ensure_commodity=ensure_commodity,
        dry_run=dry_run,
    )


# ---- approvals ----------------------------------------------------------
#
# Somebody other than the decider has to agree before an award becomes an
# order: a technical partner confirming the product, a funder approving a use
# of funds, a regulator. contract_create refuses an award with one pending or
# declined, and names it.

_DATE = {"type": "string", "format": "date"}


@register_operation(
    name="approval_request",
    summary=(
        "Record that an award needs a third party's agreement before it can be ordered: approver_org_id "
        "(an organisation from org_list) in the role technical, funder or regulatory. It starts as "
        "requested. While any approval on an award is requested or declined, contract_create against "
        "that award is refused. Attach the approver's letter with document_attach and approval_id."
    ),
    input_schema=obj(
        {
            "data": _data_with(
                ("award_id", "approver_org_id", "role"),
                award_id=ID,
                approver_org_id=ID,
                role={"enum": list(records.APPROVAL_ROLES)},
                requested_on=_DATE,
                note={"type": "string"},
                # A document already on file that the approval rests on -- a
                # product registration under a regulatory approval.
                rests_on_document_id=ID,
            )
        },
        required=("data",),
    ),
    is_write=True,
)
def approval_request(access, data):
    return record(access.request_approval(data))


@register_operation(
    name="approval_decide",
    summary=(
        "Record the approver's answer: approved or declined, on decided_on (today if omitted). A decision "
        "is final on its row; a reversal is a new approval_request, so the first answer stays on record."
    ),
    input_schema=obj(
        {
            "approval_id": ID,
            "status": {"enum": ["approved", "declined"]},
            "decided_on": _DATE,
            "note": {"type": "string"},
            "rests_on_document_id": ID,
            # Set only by the approver's own update link (update_links/service.py),
            # which runs with no user. It makes the answer the approver's word.
            "via_update_link_id": ID,
        },
        required=("approval_id", "status"),
    ),
    is_write=True,
)
def approval_decide(
    access, approval_id, status, decided_on=None, note=None, rests_on_document_id=None, via_update_link_id=None
):
    source, recorded_by = None, None
    if via_update_link_id is not None:
        # Refused for anyone signed in: a programme member recording the
        # answer is `we_recorded`, and letting them stamp it as the approver's
        # own word would undo the one thing the approver's link establishes.
        if getattr(access, "user", None) is not None or getattr(access, "request", None) is not None:
            raise ValueError("an answer is the approver's own word only through the approver's own link")
        from connect_labs.supply_chain.update_links.models import UpdateLink
        from connect_labs.supply_chain.update_links.service import scope_for

        link = UpdateLink.objects.filter(pk=via_update_link_id, program_id=access.program_id).first()
        # The link's scope as it stands now: named approvals on a listed link,
        # every approval asked of its organisation on one that follows it.
        if link is None or not link.is_usable or not scope_for(link).approvals.filter(pk=approval_id).exists():
            raise ValueError(f"update link {via_update_link_id} does not cover approval {approval_id}")
        source, recorded_by = "partner_reported", link.org_id
    return record(
        access.decide_approval(
            approval_id,
            status,
            decided_on=decided_on,
            note=note,
            rests_on_document_id=rests_on_document_id,
            source=source,
            recorded_by_org_id=recorded_by,
        )
    )


@register_operation(
    name="approval_list",
    summary="List approvals on this programme's awards, optionally for one award or in one status.",
    input_schema=obj({"award_id": ID, "status": {"enum": list(records.APPROVAL_STATUSES)}}),
)
def approval_list(access, award_id=None, status=None):
    return [record(a) for a in access.list_approvals(award_id=award_id, status=status)]


# ---- what we owe them -----------------------------------------------------
#
# A supplier that answers a request with questions, or a forwarder holding
# trucks for a document only we can supply, is waiting on us. These record it,
# so the overview can say "waiting on us" instead of blaming the other side,
# and the tender's drafts include the reply we owe.

_COMMITMENT_DATA = _data_with(
    ("kind", "text", "raised_on", "source"),
    kind={"enum": list(records.COMMITMENT_KINDS)},
    supplier_id=ID,
    owed_to_org_id=ID,
    tender_id=ID,
    contract_id=ID,
    text={"type": "string", "minLength": 1},
    raised_on=_DATE,
    due_on=_DATE,
    source={"enum": list(records.SOURCES)},
    recorded_by_org_id=ID,
    note={"type": "string"},
)


@register_operation(
    name="commitment_record",
    summary=(
        "Record something we owe a counterparty: kind=question for a question they asked us (one row per "
        "question, as they put it), kind=promise for something we promised them (a document, a decision, "
        "an answer by a date). Name who is waiting -- supplier_id for one of this program's suppliers, "
        "owed_to_org_id for anyone else (a forwarder; org_upsert it first) -- and the tender_id or "
        "contract_id it is about. raised_on is the day they asked or we promised, from the email. Open ones "
        "show as waiting on us on the overview, and a supplier's open questions are drafted as a reply."
    ),
    input_schema=obj({"data": _COMMITMENT_DATA}, required=("data",)),
    is_write=True,
)
def commitment_record(access, data):
    return record(access.record_commitment(data))


@register_operation(
    name="commitment_resolve",
    summary=(
        "Mark a question answered or a promise kept, with what was said or done and the day (today if "
        "omitted). The row stays on record. When the answer settles how the round's import duties are "
        "handled, pass duty_terms (see tender_set_duty_terms) and the tender's terms are set too."
    ),
    input_schema=obj(
        {
            "commitment_id": ID,
            "resolution": {"type": "string", "minLength": 1},
            "resolved_on": _DATE,
            "duty_terms": {"enum": list(records.DUTY_TERMS)},
        },
        required=("commitment_id", "resolution"),
    ),
    is_write=True,
)
def commitment_resolve(access, commitment_id, resolution, resolved_on=None, duty_terms=None):
    resolved = access.resolve_commitment(commitment_id, resolution, resolved_on=resolved_on)
    out = record(resolved)
    # An answer that settles how the round's import duties are handled ("We
    # import, under the program's duty waiver") sets them on the tender in the
    # same call, so the comparison reads what was just told the supplier.
    if duty_terms:
        if not resolved.tender_id:
            raise ValueError("duty_terms belong to a tender; this commitment is about an order")
        tender = access.set_tender_duty_terms(resolved.tender_id, duty_terms)
        out["tender_duty_terms"] = tender.duty_terms
    return out


@register_operation(
    name="tender_set_duty_terms",
    summary=(
        "Set how import duties are handled for a tender's round: buyer_waiver (we import under the "
        "program's duty waiver -- duty counts as zero and no supplier is asked for it), buyer_pays (we "
        "import and pay; a landed total needs duty_estimate_percent, our estimate as a percentage of the "
        "goods), supplier_ddp (the supplier delivers duty paid, as each quote states), or '' (not "
        "settled -- quotes are costed as they state duty). Idempotent."
    ),
    input_schema=obj(
        {
            "tender_id": ID,
            "duty_terms": {"enum": list(records.DUTY_TERMS)},
            "duty_estimate_percent": {"type": ["number", "string", "null"]},
            "set_on": _DATE,
        },
        required=("tender_id", "duty_terms"),
    ),
    is_write=True,
)
def tender_set_duty_terms(access, tender_id, duty_terms, duty_estimate_percent=None, set_on=None):
    return record(access.set_tender_duty_terms(tender_id, duty_terms, duty_estimate_percent, on=set_on))


@register_operation(
    name="commitment_reply_sent",
    summary=(
        "Record that the reply carrying these answered questions went out (sent_on, today if omitted). "
        "Until then an answer written into a reply draft reads as drafted, not answered. Idempotent."
    ),
    input_schema=obj(
        {"commitment_ids": {"type": "array", "items": ID, "minItems": 1}, "sent_on": _DATE},
        required=("commitment_ids",),
    ),
    is_write=True,
)
def commitment_reply_sent(access, commitment_ids, sent_on=None):
    return [record(c) for c in access.mark_reply_sent(commitment_ids, sent_on=sent_on)]


@register_operation(
    name="commitment_list",
    summary="What we owe counterparties -- their questions and our promises -- optionally for one tender or order.",
    input_schema=obj({"tender_id": ID, "contract_id": ID, "open_only": {"type": "boolean"}}),
)
def commitment_list(access, tender_id=None, contract_id=None, open_only=False):
    return [
        record(c) for c in access.list_commitments(tender_id=tender_id, contract_id=contract_id, open_only=open_only)
    ]


# ---- supplier performance ----------------------------------------------
#
# What a supplier promised against what they did. The domain stored both and
# compared them nowhere, so the supplier directory was a list of names.


@register_operation(
    name="supplier_performance",
    summary=(
        "On-time and in-full delivery per supplier for this programme, from what was promised "
        "(signed_on plus promised_lead_time_days) against what arrived. Orders with no promised "
        "lead time, and orders still on their way, are EXCLUDED from the rates and counted "
        "separately (no_promise, not_yet_due) -- scoring an unpromised order as punctual would "
        "reward never committing to a date. Every rate comes with the count behind it, and a rate "
        "out of no measurable orders is null rather than zero. In full means accepted: goods "
        "refused on arrival were not delivered. There is deliberately no single blended score."
    ),
    input_schema=obj({"supplier_id": ID}),
)
def supplier_performance(access, supplier_id=None):
    from connect_labs.supply_chain.procurement.services.performance import supplier_performance as service

    return service(access, supplier_id=supplier_id)
