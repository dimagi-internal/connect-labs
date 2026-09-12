from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def questions_for(questions, audience):
    """Split a row's outstanding questions by who they are for -- design doc
    section 7's audience split. A comparison row's `questions` list mixes
    supplier-facing facts with our own internal gaps (a misconfigured round,
    a missing course definition); this is what lets the comparison screen
    show "ask the supplier" and "our own outstanding work" as two lists
    instead of one undifferentiated one.
    """
    return [q for q in (questions or []) if q.get("audience") == audience]


# A human name for each check kind. It lives HERE, in the presentation layer,
# and not in checks.py, because checks.py must not carry prose -- the whole
# point of section 22 is that the domain states facts and a client words them.
# A label is the mildest possible wording, and even that belongs to a client.
CHECK_LABELS = {
    "quote_not_comparable": "Quote cannot be compared",
    "contract_cost_unconfirmed": "Landed cost cannot be computed",
    "contract_reference_unknown": "No purchase-order reference",
    "duty_relief_unevidenced": "Duty relief claimed, not evidenced",
    "shipment_without_certificate": "No certificate on file",
    "commodity_course_undefined": "No ration table",
    "stock_unconfirmed": "Stock on hand cannot be computed",
    "stock_never_reported": "Never reported stock",
    "award_not_contracted": "Awarded, not contracted",
    "invoice_over_billed": "Billed for more than arrived",
    "stock_variance": "Ledger and count disagree",
    "stock_negative": "Dispensed more than was ever received",
    "stock_stockout": "No stock on hand",
    "stock_below_minimum": "Below its own minimum",
    "item_fails_specification": "Fails the commodity specification",
}

AUDIENCE_LABELS = {
    "supplier": "Only the supplier can answer",
    "partner": "Only the buying partner can answer",
    "internal": "Ours to answer",
}

CATEGORY_LABELS = {
    "missing": "a fact nobody supplied",
    "conflict": "two records disagree",
    "threshold": "past a bound you set",
}


@register.filter
def check_label(kind):
    return CHECK_LABELS.get(kind, kind.replace("_", " "))


@register.filter
def audience_label(audience):
    return AUDIENCE_LABELS.get(audience, audience)


@register.filter
def category_label(category):
    return CATEGORY_LABELS.get(category, category)


@register.filter
def figure_text(cell):
    """A derived figure as text: the number, or the reason there is not one.

    The one place a template turns a `{"amount"...}` / `{"unconfirmed": [...]}`
    cell into something readable, so no screen can accidentally render an
    Unconfirmed as a blank -- which is the failure the whole value type
    exists to prevent.
    """
    if not cell:
        return "—"
    if "unconfirmed" in cell:
        return "Unconfirmed"
    amount = cell.get("amount")
    unit = cell.get("unit") or cell.get("currency") or ""
    return f"{amount} {unit}".strip()


@register.filter
def unconfirmed_reasons(cell):
    """The reasons on an Unconfirmed cell, or none. Tolerates a plain value.

    A derived figure on the wire is either a {"amount"...} / {"unconfirmed"}
    cell or, for a bare ratio like months of stock, a plain string. A
    template cannot know which, and rendering the dict raw -- which is what
    happened before this -- puts Python repr on the screen.
    """
    if not isinstance(cell, dict):
        return []
    return cell.get("unconfirmed") or []


@register.filter
def derived_text(value):
    """Any derived figure as text, whether it is a cell or a plain number."""
    if value is None or value == "":
        return "—"
    if isinstance(value, dict):
        return figure_text(value)
    return value


def _cell(label, value, sub=None):
    return {"label": label, "value": value, "sub": sub}


def _quantity_cell(label, cell, sub_when_known):
    """A derived quantity as a stage cell, never a bare word.

    An `Unconfirmed` shows its first reason as the sub-line. The reasons are
    user-facing by design -- they name the fact that is missing and who could
    supply it -- so rendering the word "Unconfirmed" alone would throw away
    the half of the value that makes it actionable.
    """
    if not cell:
        return _cell(label, "per commodity", "choose one above; units differ")
    reasons = cell.get("unconfirmed")
    if reasons:
        return _cell(label, "Unconfirmed", reasons[0])
    return _cell(label, figure_text(cell), sub_when_known)


def _plural(n, noun):
    return f"{n} {noun}{'' if n == 1 else 's'}"


@register.filter
def source_stages(source):
    """chain_summary's source counts, as stage cells.

    The shaping lives here rather than in summary.py so the derivation stays
    free of presentation, and so a second client can lay the same numbers out
    differently without fighting a format baked into the data.
    """
    evaluation = source["evaluation"]
    return [
        _cell("Demand", source["demand"]["rounds"], _plural(source["demand"]["open"], "open round")),
        _cell(
            "RFQ issued",
            source["rfq_issued"]["invitations"],
            f"{source['rfq_issued']['awaiting_reply']} awaiting a reply",
        ),
        _cell("Quotations", source["quotations"]["live"], "live, after corrections"),
        _cell(
            "Evaluation",
            f"{evaluation['comparable']} of {evaluation['of']}",
            "comparable" + (" · provisional" if evaluation["provisional"] else ""),
        ),
        _cell(
            "Award",
            source["award"]["count"],
            f"{source['award']['provisional']} provisional" if source["award"]["provisional"] else None,
        ),
    ]


@register.filter
def order_stages(order):
    buyers = order["contract"]["by_buyer_of_record"]
    return [
        _cell(
            "Contract",
            order["contract"]["count"],
            ", ".join(f"{n} {buyer.replace('_', ' ')}" for buyer, n in buyers.items()) or None,
        ),
        _cell(
            "No PO reference",
            order["contract"]["without_reference"],
            "we do not hold it" if order["contract"]["without_reference"] else None,
        ),
        _cell(
            "Dispatched",
            order["dispatched"]["shipments"],
            f"{order['dispatched']['in_transit']} in transit — not stock",
        ),
        _cell("Received", order["received"]["receipts"], "goods received notes"),
        _cell(
            "Invoiced",
            order["invoiced"]["count"],
            f"{order['invoiced']['unpaid']} unpaid" if order["invoiced"]["count"] else None,
        ),
    ]


@register.filter
def deliver_stages(deliver):
    cover = deliver["cover"]
    needs = cover.get("below_min", 0) + cover.get("stockout", 0) + cover.get("negative", 0)
    return [
        _cell(
            "Network",
            deliver["network"]["supply_points"],
            f"{deliver['network']['user_held']} field workers",
        ),
        # A quantity needs a unit, and a unit needs a commodity -- so a cell
        # with neither says what would make it computable rather than showing
        # a bare dash, which reads as "zero" or "broken".
        _quantity_cell("On hand", deliver["on_hand"], "excludes goods in transit"),
        _quantity_cell("In transit", deliver["in_transit"], "real, but not cover"),
        _cell("Distributed", deliver["distributions"]["runs"], "resupply runs"),
        _quantity_cell("Dispensed", deliver["consumed"], "derived from visits"),
        _cell(
            "Under its band",
            needs,
            f"{deliver['network']['never_reported']} never reported",
        ),
    ]
