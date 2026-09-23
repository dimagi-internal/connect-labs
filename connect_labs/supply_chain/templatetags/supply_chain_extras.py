from django import template
from django.urls import reverse

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
    if "not_costed" in cell:
        # Not a gap: the goods were never bought, and the reason is the text.
        return cell["not_costed"]
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


def _cell(label, value, sub=None, href=None):
    """One stage of the chain.

    `href` is where the number is explained. A count with nowhere to go is a
    dead end: the chain is the map of this domain, so every figure on it is
    the way in to the records behind it. A cell with no destination renders
    as plain text rather than a link that goes nowhere.
    """
    return {"label": label, "value": value, "sub": sub, "href": href}


def _quantity_cell(label, cell, sub_when_known, href=None):
    """A derived quantity as a stage cell, never a bare word.

    An `Unconfirmed` shows its first reason as the sub-line. The reasons are
    user-facing by design -- they name the fact that is missing and who could
    supply it -- so rendering the word "Unconfirmed" alone would throw away
    the half of the value that makes it actionable.
    """
    if not cell:
        return _cell(label, "per commodity", "choose one above; units differ", href)
    reasons = cell.get("unconfirmed")
    if reasons:
        return _cell(label, "Unconfirmed", reasons[0], href)
    return _cell(label, figure_text(cell), sub_when_known, href)


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
    sourcing = reverse("supply_chain:procurement_round_board")
    return [
        _cell(
            "Demand",
            source["demand"]["rounds"],
            _plural(source["demand"]["open"], "open round"),
            sourcing,
        ),
        _cell(
            "RFQ issued",
            source["rfq_issued"]["invitations"],
            f"{source['rfq_issued']['awaiting_reply']} awaiting a reply",
            sourcing,
        ),
        _cell("Quotations", source["quotations"]["live"], "live, after corrections", sourcing),
        _cell(
            "Evaluation",
            f"{evaluation['comparable']} of {evaluation['of']}",
            "comparable" + (" · provisional" if evaluation["provisional"] else ""),
            sourcing,
        ),
        _cell(
            "Award",
            source["award"]["count"],
            f"{source['award']['provisional']} provisional" if source["award"]["provisional"] else None,
            sourcing,
        ),
    ]


@register.filter
def order_stages(order):
    buyers = order["contract"]["by_buyer_of_record"]
    orders = reverse("supply_chain:orders")
    return [
        _cell(
            "Contract",
            order["contract"]["count"],
            ", ".join(f"{n} {buyer.replace('_', ' ')}" for buyer, n in buyers.items()) or None,
            orders,
        ),
        _cell(
            "No PO reference",
            order["contract"]["without_reference"],
            "we do not hold it" if order["contract"]["without_reference"] else None,
            orders,
        ),
        _cell(
            "Dispatched",
            order["dispatched"]["shipments"],
            f"{order['dispatched']['in_transit']} in transit — not stock",
            orders,
        ),
        _cell("Received", order["received"]["receipts"], "goods received notes", orders),
        _cell(
            "Invoiced",
            order["invoiced"]["count"],
            f"{order['invoiced']['unpaid']} unpaid" if order["invoiced"]["count"] else None,
            orders,
        ),
    ]


@register.filter
def deliver_stages(deliver):
    cover = deliver["cover"]
    needs = cover.get("below_min", 0) + cover.get("stockout", 0) + cover.get("negative", 0)
    stock = reverse("supply_chain:stock")
    distribution = reverse("supply_chain:distribution")
    return [
        _cell(
            "Network",
            deliver["network"]["supply_points"],
            f"{deliver['network']['user_held']} field workers",
            stock,
        ),
        # A quantity needs a unit, and a unit needs a commodity -- so a cell
        # with neither says what would make it computable rather than showing
        # a bare dash, which reads as "zero" or "broken".
        _quantity_cell("On hand", deliver["on_hand"], "excludes goods in transit", stock),
        _quantity_cell("In transit", deliver["in_transit"], "real, but not cover", stock),
        _cell("Distributed", deliver["distributions"]["runs"], "resupply runs", distribution),
        _quantity_cell("Dispensed", deliver["consumed"], "derived from visits", distribution),
        _cell(
            "Under its band",
            needs,
            f"{deliver['network']['never_reported']} never reported",
            stock,
        ),
    ]


@register.filter
def humanise(value):
    """A slug or enum value as words: therapeutic_food -> therapeutic food.

    `|cut:"_"` was doing this and silently produced "therapeuticfood": cut
    REMOVES the character rather than replacing it. Django has no built-in
    replace filter, which is why the wrong one was reached for.
    """
    return str(value or "").replace("_", " ")


@register.filter
def figure_label(key):
    """A derived figure's name, from the one place that names them.

    The unit placeholders are left unfilled here: the template that shows a
    single quote has no commodity in scope, and "USD per {base_unit}" read as
    "USD per sachet" on a page about cartons would be worse than the generic
    word.
    """
    from connect_labs.supply_chain.procurement.services.pricing import FIGURE_LABELS

    label = FIGURE_LABELS.get(key, str(key).replace("_", " "))
    return label.replace("{base_unit}", "base unit").replace("{pack_unit}", "pack")


# What the supplier stated, in the order a reader checks it. Empty rows are
# KEPT on purpose: "sachets per carton -- not stated" is the whole explanation
# for four of the six figures coming back unconfirmed, so hiding it would
# remove the answer to the question the page raises.
_STATED_ROWS = (
    ("Price", "_price"),
    ("Priced per", "as_quoted_unit"),
    ("Quantity priced", "_basis"),
    ("Sachets per pack", "base_per_pack_stated"),
    ("Weight per sachet", "base_unit_grams_stated"),
    ("Pack spec source", "pack_spec_source"),
    ("Freight", "_freight"),
    ("Duties and taxes", "_duties"),
    ("Exchange rate to USD", "fx_rate_to_usd"),
    ("Shelf life stated", "shelf_life_months_stated"),
    ("Minimum order", "_moq"),
    ("Lead time", "lead_time_days"),
    ("Valid until", "validity_until"),
    ("Incoterm", "incoterm"),
)


def _basis_phrase(basis, amount):
    """A basis flag and its amount as one readable fact.

    "excluded" with no figure and "excluded, $2,186.58" are different states
    and the difference is the whole point -- one is a cost we know, the other
    a cost we know we do not know.
    """
    if not basis or basis == "not_specified":
        return ""
    words = str(basis).replace("_", " ")
    # `if amount` is false for 0, so freight quoted at zero read as "amount
    # not given" -- an unknown. Free freight and a waived duty are real facts
    # with their own basis flag, and keeping a known zero apart from an
    # unknown is the thing this domain is built around.
    return f"{words}, {amount}" if amount is not None else f"{words}, amount not given"


@register.filter
def stated_rows(quote):
    """The quote as the supplier wrote it, one label-value row at a time."""
    quote = quote or {}
    amount, currency = quote.get("as_quoted_amount"), quote.get("as_quoted_currency")
    computed = {
        "_price": f"{currency} {amount}" if amount else "",
        "_basis": (
            f"{quote.get('quantity_basis')} {humanise(quote.get('quantity_basis_unit'))}"
            if quote.get("quantity_basis")
            else ""
        ),
        "_freight": _basis_phrase(quote.get("freight_basis"), quote.get("freight_amount")),
        "_duties": _basis_phrase(quote.get("duties_basis"), quote.get("duties_amount")),
        "_moq": f"{quote.get('moq')} {humanise(quote.get('moq_unit'))}" if quote.get("moq") else "",
    }
    rows = []
    for label, key in _STATED_ROWS:
        value = computed[key] if key.startswith("_") else quote.get(key)
        if key == "shelf_life_months_stated" and value:
            value = f"{value} months"
        if key == "lead_time_days" and value:
            value = f"{value} days"
        # The enum-valued fields read as words, not as identifiers. Fixed in
        # the round table and missed here, which is why "not_stated" reached
        # the quote page.
        if key in ("as_quoted_unit", "pack_spec_source"):
            value = humanise(value)
        rows.append({"label": label, "value": value})
    return rows


# What each kind of evidence in a derived supply base means, in words. Here
# rather than in the service for the same reason CHECK_LABELS is: the domain
# states the kind, a client words it. The wording is deliberately plain about
# how weak the weakest one is -- "named as manufacturer" is a string match on
# a free-text field, and a page that let it read like a trading relationship
# would be the invention the derivation exists to avoid.
EVIDENCE_LABELS = {
    "contracted": "Under contract",
    "awarded": "Awarded",
    "quoted": "Quoted",
    "quoted_superseded": "Quoted, since superseded",
    "quoted_voided": "Quoted, since withdrawn",
    "invited": "Invited",
    "named_as_manufacturer": "Named as the manufacturer",
}


@register.filter
def evidence_label(kind):
    return EVIDENCE_LABELS.get(kind, humanise(kind))
