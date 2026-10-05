import re
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from django import template
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

from connect_labs.supply_chain.values import (
    _as_decimal,
    day_text,
    is_counted_unit,
    money_digits,
    possessive,
    quantity_digits,
    unit_noun,
)

register = template.Library()


@register.filter(name="possessive")
def possessive_filter(value):
    """`possessive` for a template: the name escaped, its apostrophe left as an apostrophe."""
    text = str(value or "").strip()
    if not text:
        return ""
    return mark_safe(escape(text) + possessive(text)[len(text) :])


@register.filter
def supplies(owner):
    """Whose step a quote's missing fact is, as its chip: "to do" or "waiting" (moves.OWNER_CHIP)."""
    from connect_labs.supply_chain.moves import OWNER_CHIP, SUPPLIERS

    return OWNER_CHIP.get(owner) or OWNER_CHIP[SUPPLIERS]


@register.filter
def document_kind(kind):
    """A document kind as every page names it (records.DOCUMENT_KIND_LABELS): "duty exemption"."""
    from connect_labs.supply_chain.records import document_kind_label

    return document_kind_label(kind)


@register.filter
def not_on_file(kind):
    """The chip a figure carries while its document is missing: "duty exemption not on file"."""
    from connect_labs.supply_chain.records import document_not_on_file

    return document_not_on_file(kind)


@register.filter
def dictkey(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def questions_for(questions, audience):
    """Split a row's outstanding questions by who they are for -- design doc
    section 7's audience split. A comparison row's `questions` list mixes
    supplier-facing facts with our own internal gaps (a misconfigured tender,
    a missing course definition); this is what lets the comparison screen
    show "ask the supplier" and "our own outstanding work" as two lists
    instead of one undifferentiated one.
    """
    return [q for q in (questions or []) if q.get("audience") == audience]


@register.filter
def also_confirm(row):
    """A blocked row's questions without the ones that clear its blocks -- the
    rest of the checklist, once those questions have been asked on their own."""
    row = row or {}
    asked = {((b or {}).get("question") or {}).get("key") for b in _blockers(row)}
    return [q for q in (row.get("questions") or []) if q.get("key") not in asked]


def _blockers(row) -> list:
    """Every blocker on a row; a row serialised before `blockers` existed has its one `blocking`."""
    blockers = row.get("blockers")
    if blockers is None:
        blockers = [row["blocking"]] if row.get("blocking") else []
    return blockers


@register.filter
def asks_supplier(row):
    """Whether any blocker on a row is cleared by asking the supplier (not by our own work)."""
    return any(
        ((b or {}).get("question") or {}).get("audience") not in (None, "internal")
        for b in _blockers(row or {})
        if (b or {}).get("question")
    )


@register.filter
def all_missing_one(rows):
    """Whether every blocked row is kept out of the ranking by exactly one gap."""
    rows = list(rows or [])
    return bool(rows) and all(len((row or {}).get("gaps") or []) == 1 for row in rows)


@register.filter
def not_blocking(row):
    """The specification's unstated requirements a blocked row's blocker is not: ["shelf life"].

    Said once, muted, as "also not stated (not blocking)": an amber badge
    beside the blocker read as a second thing keeping the offer out.
    """
    row = row or {}
    blockers = {(b or {}).get("label") or "" for b in _blockers(row)}
    return [label for label in (row.get("specification") or {}).get("not_stated") or [] if label not in blockers]


@register.filter
def plain_reason(reason, row):
    """A pricing reason in the row's commodity's own words: "sachets per carton not stated on the quote"."""
    from connect_labs.supply_chain.procurement.services.comparison import plain_reason as plain

    row = row or {}
    return plain(str(reason or ""), row.get("base_unit") or "", row.get("pack_unit") or "")


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
    "shipment_overdue": "Shipment past its expected date",
    "shipment_documents_outstanding": "Documents it needs, not on file",
    "shipment_delivered_unevidenced": "Recorded as delivered, with no proof of delivery",
    "charge_paid_unevidenced": "A carrier was paid, with no proof of delivery",
    "shipment_quantity_unaccounted": "More was despatched than has arrived",
    "award_awaiting_approval": "Awarded, awaiting approval",
    "payment_unconfirmed": "Payment not confirmed by the payee",
    "invoice_above_contract": "Invoice above the contract",
    "commitment_open": "We owe an answer",
    "contract_delivery_overdue": "Delivery past the promised lead time",
    "consignment_overdue": "Consignment past its expected arrival",
}

AUDIENCE_LABELS = {
    "supplier": "Only the supplier can answer",
    "partner": "Only the buying partner can answer",
    "internal": "Ours to answer",
}

CATEGORY_LABELS = {
    "missing": "something nobody has told us yet",
    "conflict": "two records disagree",
    "threshold": "a figure past a limit you set",
}


# Where each kind of subject is answered. A check about a shipment is read on
# the shipment; one about a contract on its order. Subjects with no page of
# their own fall back to a record they belong to, named in the facts.
_SUBJECT_ROUTES = {
    "quote": "supply_chain:procurement_quote_detail",
    "contract": "supply_chain:order_detail",
    "shipment": "supply_chain:shipment_detail",
    "award": "supply_chain:award_detail",
    "item": "supply_chain:item_detail",
    "supplier": "supply_chain:supplier_detail",
}


@register.filter
def check_href(check):
    """The page a check is answered on, or "" when there is none."""
    subject = check.get("subject") or {}
    kind, subject_id = subject.get("type"), subject.get("id")
    facts = check.get("facts") or {}
    if kind in _SUBJECT_ROUTES and subject_id:
        return reverse(_SUBJECT_ROUTES[kind], args=[subject_id])
    if facts.get("contract_id"):
        return reverse("supply_chain:order_detail", args=[facts["contract_id"]])
    if kind == "supply_point":
        return reverse("supply_chain:stock")
    if kind == "commodity":
        return reverse("supply_chain:catalogue")
    return ""


_ENUM = re.compile(r"[a-z]+(?:_[a-z]+)+")
_DECIMAL = re.compile(r"-?\d+\.\d+")

# How a record came to be known, in the words the forms use to ask it.
SOURCE_LABELS = {
    "we_recorded": "we recorded it",
    "partner_reported": "a partner told us",
    "supplier_reported": "the supplier told us",
    "forwarder_reported": "the forwarder told us",
    "commcare_form": "a CommCare form",
    "connect_visit": "a Connect visit",
    "document": "a document",
}


@register.filter
def money(value):
    """An amount by the one money rule (values.money_digits): "410000" -> "410,000.00".

    Grouped, two decimals, more only when the stored figure has them -- so two
    different per-unit prices never read the same.
    """
    return money_digits(value)


@register.filter
def day(value):
    """A date by the one date rule (values.day_text): "2026-09-17" -> "17 Sep 2026".

    Operations hand screens ISO strings, which printed as they came -- one
    order page read "decided 2026-09-17" above "received 23 Sep 2026". Takes a
    date, a datetime or an ISO string; anything else passes through.
    """
    if value in (None, ""):
        return value
    if isinstance(value, str):
        from datetime import date

        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return day_text(value) or value


@register.filter
def days_ago(value, now):
    """When something happened, counted back from `now`: "today", "yesterday", "12 days ago".

    `now` is passed in rather than read from the clock so a page viewed as of
    a past date counts back from that date. Beyond a month the date itself
    reads better than a count.
    """
    if value is None or now is None:
        return ""
    from django.utils import timezone

    days = (timezone.localdate(now) - timezone.localdate(value)).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 31:
        return f"{days} days ago"
    return day_text(timezone.localdate(value)) or ""


@register.filter
def days_ago_and_day(value, now):
    """ "today · 26 Sep", "7 days ago · 19 Sep": the count back from `now`, and the day itself.

    A count alone cannot be checked against anything else on the page, and on
    a page viewed as of a past date "7 days ago" is from that date, not from
    today -- the day beside it says which day that was. Past a month the count
    is already the date, so it is said once.
    """
    relative = days_ago(value, now)
    if not relative:
        return ""
    from django.utils import timezone

    day = timezone.localdate(value)
    short = f"{day.day} {day.strftime('%b')}"
    return relative if relative == (day_text(day) or "") else f"{relative} · {short}"


@register.filter
def day_beside_count(value, now):
    """ "3 Aug": the day under a count back from `now` -- or "" past a month, where the count is already the day."""
    relative = days_ago(value, now)
    if not relative:
        return ""
    from django.utils import timezone

    day = timezone.localdate(value)
    return "" if relative == (day_text(day) or "") else f"{day.day} {day.strftime('%b')}"


@register.filter
def count_back(value, now):
    """ "today", "6 days ago", "2 months ago": always a count back from `now`, never a date.

    The overview's Last change column pairs it with `short_day` on every row, so
    the column reads one way -- a count, then the day -- however old the change.
    """
    relative = days_ago(value, now)
    if not relative:
        return ""
    from django.utils import timezone

    days = (timezone.localdate(now) - timezone.localdate(value)).days
    if days < 31:
        return relative
    months = max(1, round(days / 30.4))
    return f"{months} month{'s' if months != 1 else ''} ago"


@register.filter
def short_day(value, now):
    """ "29 Jul": the day under a count back, with its year only when it is not `now`'s year."""
    if value is None or now is None:
        return ""
    from django.utils import timezone

    day = timezone.localdate(value)
    text = f"{day.day} {day.strftime('%b')}"
    return text if day.year == timezone.localdate(now).year else f"{text} {day.year}"


@register.filter
def qty(value, unit=None):
    """A quantity and its unit as a person writes them: "3 jerry cans", "1 carton".

    `{{ line.quantity|qty:line.quantity_unit }}`. The number follows the one
    quantity rule (values.quantity_digits) and the unit is pluralised on the
    number as it displays. With no unit, just the number.
    """
    if value is None or value == "":
        return "—"
    return f"{quantity_digits(value)} {unit_noun(unit, value)}".strip()


# Incoterms 2020, as a buyer needs them read: who pays to move the goods, and
# to where. A three-letter code is a contract term most of the programme team
# cannot expand from memory; the order says it once, in words, beside the code.
INCOTERMS = {
    "EXW": ("ex works", "we collect from the supplier's premises and pay all the freight"),
    "FCA": ("free carrier", "the supplier hands the goods to our carrier at the named place"),
    "CPT": ("carriage paid to", "the supplier pays freight to the named place"),
    "CIP": ("carriage and insurance paid to", "the supplier pays freight and insurance to the named place"),
    "DAP": ("delivered at place", "the supplier pays freight to the named place"),
    "DPU": ("delivered at place unloaded", "the supplier pays freight to the named place and unloads there"),
    "DDP": ("delivered duty paid", "the supplier pays freight and import duties to the named place"),
    "FAS": ("free alongside ship", "the supplier delivers alongside our vessel at the named port"),
    "FOB": ("free on board", "the supplier loads the goods onto our vessel at the named port"),
    "CFR": ("cost and freight", "the supplier pays sea freight to the named port"),
    "CIF": ("cost, insurance and freight", "the supplier pays sea freight and insurance to the named port"),
}


@register.filter
def incoterm_words(incoterm):
    """An Incoterm with what it means: "DAP — delivered at place: the supplier pays freight to the named place".

    The code may carry its named place ("DAP Kano"), which is kept as written.
    A term that is not one of the eleven is shown as typed, never guessed at.
    """
    text = str(incoterm or "").strip()
    if not text:
        return ""
    code = text.split()[0].upper().strip(".,")
    if code not in INCOTERMS:
        return text
    name, meaning = INCOTERMS[code]
    return f"{text} — {name}: {meaning}"


@register.filter
def incoterm_name(incoterm):
    """The Incoterm's name alone -- "carriage paid to" for "CPT Kano" -- or "" for a term not of the eleven.

    Set beside the term's chip, whose hover carries what it means (`incoterm_words`):
    the meaning written out after a dash read as a sentence on a page of facts.
    """
    text = str(incoterm or "").strip()
    code = text.split()[0].upper().strip(".,") if text else ""
    return INCOTERMS[code][0] if code in INCOTERMS else ""


@register.filter
def place_text(point):
    """A delivery point as a person would write the address.

    `{{ tender.delivery_point|place_text }}`.

    Three templates used to join these parts by hand and each got it wrong
    in its own way: the programme's own tender list printed a leading comma
    when the name was blank (", Kano"), and the SUPPLIER-FACING marketplace
    printed "(not stated), Kano" -- telling an outside reader the place was
    unknown in the same breath as naming the city. That page is the one
    people outside the programme read, which is why this is one function now
    rather than three expressions.

    The rule: say the parts that are there, in order, and say "not stated"
    only when none of them is. The two-letter country code is used only when
    the country's name is missing -- a reader wants "Nigeria", but "NG" beats
    nothing.
    """
    point = point or {}
    parts = [
        str(point.get("name") or "").strip(),
        str(point.get("city") or "").strip(),
        str(point.get("country_name") or "").strip() or str(point.get("country") or "").strip(),
    ]
    return ", ".join(part for part in parts if part) or "not stated"


@register.filter
def unit_words(unit):
    """A unit on its own, as a word: "per {{ unit|unit_words }}" -> "per jerry can"."""
    return unit_noun(unit)


@register.filter
def unit_plural(unit):
    """A unit as a plural noun: "{{ unit|unit_plural }} per course" -> "sachets per course"."""
    return unit_noun(unit, 2)


@register.filter
def source_label(source):
    return SOURCE_LABELS.get(source, str(source or "").replace("_", " "))


def _fact_text(value):
    if isinstance(value, dict):
        if "kind" in value and isinstance(value.get("owed_by"), dict):
            # A document a consignment still needs: what it is, and who to ask.
            kind = words(value["kind"]).capitalize()
            return f"{kind} — owed by {value['owed_by'].get('name') or '—'}"
        if "name" in value:
            return str(value["name"])
        if "question" in value:
            return str(value["question"])
        if "commodity_slug" in value and "verdict" in value:
            return f"{value['commodity_slug']}: {value['verdict']}"
        return ", ".join(f"{str(k).replace('_', ' ')} {_fact_text(v)}" for k, v in value.items())
    if isinstance(value, list | tuple):
        return "; ".join(_fact_text(v) for v in value) or "none"
    if value is None or value == "":
        return "—"
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        # A derived quantity ("83.7209") by the one quantity rule. Only a
        # value with a point: whole numbers here are as often ids as counts,
        # and "contract 1,157" would be wrong.
        return quantity_digits(value)
    if isinstance(value, str) and _ENUM.fullmatch(value):
        # A stored enum ("at_customs") reached the checks list as the raw
        # value; say it in words. Only snake_case lowercase is touched, so a
        # name or a reference is never rewritten.
        return words(value)
    return str(value)


@register.filter
def fact_rows(facts):
    """A check's facts as (label, text) rows, for reading rather than parsing.

    Generic on purpose: the facts are the domain's structured data, and this
    only spells them out. A client wanting to word them does so itself.
    """
    return [(str(key).replace("_", " "), _fact_text(value)) for key, value in (facts or {}).items()]


# A check's facts as a person reads them on the checks page. Keyed by the fact's
# name; a key not here is spelled out by `humanise` rather than dropped, so a
# new fact still reaches the page. Each is a label for a fact, never a verdict.
CHECK_FACT_LABELS = {
    "signed_on": "Signed",
    "promised_lead_time_days": "Promised lead time",
    "supplier": "Supplier",
    "status": "Status",
    "outstanding": "Outstanding",
    "over_invoiced": "Billed beyond what arrived",
    "amount": "Amount",
    "required": "Documents required",
    "balance": "Balance",
    "kind": "Kind of site",
    "months_of_stock": "Months of stock",
    "min_months_of_stock": "Its own minimum",
    "ledger": "Ledger",
    "reported": "Counted",
    "variance": "Difference",
    "reconcilable": "Can be reconciled",
    "reported_kind": "How it was counted",
    "reasons": "Why",
    "missing": "Not stated",
    "blocks": "Cannot compute",
    "buyer_of_record": "Buyer of record",
    "provisional": "Provisional ranking",
    "approver": "Approver",
    "role": "As",
    "requested_on": "Asked",
    "verdict": "Verdict",
    "fails": "Fails",
    "requirements": "Required",
    "stated": "Stated",
    "components": "Parts",
    "components_in_each": "Parts in each",
    "connect_username": "Connect username",
    "holds_stock": "Holds stock",
    "commodity": "Product",
    "trade_item": "Trade item",
}

# Fact keys that reference another record: the label, the key into the view's
# `refs` ({id: name}), and the page that record is read on. "tender id 33" and
# "supplier id 88" were the raw facts; the page has the names one list call away.
# `contract_id` is not here: the card already goes to that order -- it is the
# card's own link when the subject has no page, and a shipment's page opens
# with its order.
_FACT_REFERENCES = {
    "tender_id": ("Tender", "tender", "supply_chain:procurement_tender_detail"),
    "supplier_id": ("Supplier", "supplier", "supply_chain:supplier_detail"),
}

# Quantities and the key their unit rides in, so "400" and "jerry_can" read as
# one fact, "400 jerry cans", rather than two rows.
_CHECK_FACT_UNITS = {
    "outstanding": "unit",
    "over_invoiced": "unit",
    "balance": "unit",
    "variance": "unit",
    "ledger": "ledger_unit",
    "reported": "reported_unit",
}

# Figures stated in months or days, where the key names the period.
_CHECK_FACT_PERIODS = {"promised_lead_time_days": "day", "min_months_of_stock": "month"}

# A check against a bound, and the two facts that say it in one line.
_READOUTS = {"stock_below_minimum": ("months_of_stock", "min_months_of_stock")}


class FactRow(tuple):
    """One fact on a check card: (label, text), and the page it links to, if any.

    A plain two-tuple to anything that unpacks or compares it; `href` rides
    alongside so the template can link a referenced record by its name.
    """

    def __new__(cls, label, text, href=None):
        row = super().__new__(cls, (label, text))
        row.href = href
        return row

    @property
    def label(self):
        return self[0]

    @property
    def text(self):
        return self[1]


@register.filter
def check_readout(check):
    """A threshold check as one plain line: "2.8 months of stock · minimum 3"."""
    keys = _READOUTS.get((check or {}).get("kind"))
    facts = (check or {}).get("facts") or {}
    if not keys or facts.get(keys[0]) in (None, ""):
        return ""
    value, bound = (facts.get(key) for key in keys)
    line = f"{quantity_digits(value)} months of stock"
    return f"{line} · minimum {quantity_digits(bound)}" if bound not in (None, "") else line


def _check_fact_value(key, value, facts):
    if isinstance(value, bool):
        return "yes" if value else "no"
    if key in _CHECK_FACT_UNITS and not isinstance(value, list | tuple | dict):
        unit = facts.get(_CHECK_FACT_UNITS[key]) or facts.get("unit")
        return qty(value, unit) if unit else _fact_text(value)
    if key in _CHECK_FACT_PERIODS and value not in (None, ""):
        return qty(value, _CHECK_FACT_PERIODS[key])
    if key == "amount" and facts.get("currency"):
        return f"{facts['currency']} {money_digits(value)}"
    if key == "buyer_of_record":
        return buyer_label(value)
    if key in ("role", "kind", "reported_kind", "status") and isinstance(value, str):
        return words(value)
    if key == "blocks" and isinstance(value, list | tuple):
        return "; ".join(words(v) for v in value) or "none"
    return _fact_text(value)


@register.filter
def check_facts(check, refs=None):
    """A check's facts as labelled rows, worded for the checks page.

    Each row is a `FactRow` -- (label, text) plus an `href` when the fact is
    another record: a tender or supplier reads by its name (from `refs`, which
    maps "tender" / "supplier" to {id: name}) and links to it.

    Leaves out what the card already says: the age and the date it counts
    from (the header's "90 days since 2026-06-26" is `days_late` and
    `expected_on` again), a threshold the readout line states, a supplier the
    subject's label already names, a record the card itself links to, and
    ids with no page of their own. A quantity is read with its unit and a
    period with its noun. Nothing is ranked or added: every row is one of the
    check's own facts, in words.
    """
    check = check or {}
    refs = refs or {}
    facts = check.get("facts") or {}
    since = check.get("since")
    label = ((check.get("subject") or {}).get("label")) or ""
    card_href = check_href(check)
    said = set(_READOUTS.get(check.get("kind"), ())) if check_readout(check) else set()
    units = set(_CHECK_FACT_UNITS.values()) | {"currency"}
    rows = []
    for key, value in facts.items():
        if key in said or key in units:
            continue
        if key in _FACT_REFERENCES:
            if value in (None, ""):
                continue
            row_label, ref_key, url_name = _FACT_REFERENCES[key]
            href = reverse(url_name, args=[value])
            if href == card_href:
                continue
            name = (refs.get(ref_key) or {}).get(value)
            rows.append(FactRow(row_label, name or f"{row_label.lower()} {value}", href))
            continue
        if key == "id" or str(key).endswith("_id"):
            continue
        if key == "days_late" and value == check.get("days_open"):
            continue
        if since and value == since and key != "signed_on":
            continue
        if key == "supplier" and isinstance(value, dict):
            if value.get("name") and value["name"] in label:
                continue
            if value.get("id"):
                href = reverse("supply_chain:supplier_detail", args=[value["id"]])
                if href != card_href:
                    rows.append(FactRow("Supplier", _fact_text(value), href))
                continue
        text = _check_fact_value(key, value, facts)
        rows.append(FactRow(CHECK_FACT_LABELS.get(key) or humanise(key).capitalize(), text))
    return rows


@register.filter
def requirement_text(requirement):
    """A specification requirement in words: "Range maximum at least 2.0 mg/L"."""
    from connect_labs.supply_chain.procurement.services.compliance import requirement_text as text

    return text(requirement or {})


@register.filter
def check_label(kind):
    return CHECK_LABELS.get(kind, kind.replace("_", " "))


@register.filter
def audience_label(audience):
    return AUDIENCE_LABELS.get(audience, audience)


# What a check's age is counted from, by kind. The header used to read "19
# days since 2026-09-05" for a late shipment and "40 days since 2026-08-15"
# for its missing papers, leaving the reader to guess which date was which.
# Every `since=` a check in checks.py sets has its phrase here; a kind without
# one falls back to the bare "since".
AGE_FROM = {
    "quote_not_comparable": "since the quote arrived",
    "award_not_contracted": "since the award",
    "award_awaiting_approval": "since approval was asked",
    "contract_reference_unknown": "since the order was signed",
    "duty_relief_unevidenced": "since the order was signed",
    "shipment_without_certificate": "since dispatch",
    "shipment_documents_outstanding": "since dispatch",
    "shipment_delivered_unevidenced": "since dispatch",
    "charge_paid_unevidenced": "since it was paid",
    "shipment_quantity_unaccounted": "since dispatch",
    "shipment_overdue": "past the expected date",
    "contract_delivery_overdue": "past the expected date",
    "consignment_overdue": "past the expected date",
    "payment_unconfirmed": "since payment",
}


@register.filter
def check_age(check):
    """ "19 days past the expected date, 2026-09-05" -- the age and what it counts from."""
    days = check.get("days_open")
    if days is None:
        return ""
    phrase = AGE_FROM.get(check.get("kind"))
    noun = "day" if days == 1 else "days"
    if phrase:
        return f"{days} {noun} {phrase}, {check.get('since')}"
    return f"{days} {noun} since {check.get('since')}"


@register.filter
def check_audience(check):
    """Who can answer a check. A documents check is ours when several parties
    owe papers -- we are the ones chasing them, so it says so rather than
    claiming we can answer for papers somebody else holds."""
    if (
        check.get("kind") in ("shipment_documents_outstanding", "shipment_overdue")
        and check.get("audience") == "internal"
    ):
        return "Ours to chase"
    return audience_label(check.get("audience"))


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
    if "not_forecast" in cell:
        # Not a gap either: durable equipment is never consumed.
        return "durable — not forecast"
    amount = cell.get("amount")
    if cell.get("currency"):
        return money_text(cell)
    if amount in (None, ""):
        return "—"
    return qty(amount, cell.get("unit"))


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


def _awaiting_words(rfq) -> str:
    """ "3 awaiting a reply on 1 open tender": the silent suppliers the overview's table names, and where.

    Counted by the table's own rule (standing.awaiting_reply), and it says so:
    silent on rounds still being chased, not every unanswered invitation.
    """
    n = rfq.get("awaiting_reply") or 0
    rounds = rfq.get("awaiting_tenders") or 0
    if not n:
        return "none awaiting a reply on open tenders"
    return f"{n} awaiting a reply on {_plural(rounds, 'open tender')}"


def _open_and_awarded(evaluation):
    parts = evaluation.get("by_tender") or []
    return [p for p in parts if not p["awarded"]], [p for p in parts if p["awarded"]]


def evaluation_value(evaluation):
    """The Evaluation headline: quotes comparable of quotes received, on rounds still being evaluated.

    An awarded tender is not added in: "1 of 4" mixed one open tender's
    comparable count with another tender's awarded quote, and matched neither
    tender's own comparison page. With one open tender this is that page's "1 of 3".
    """
    open_rounds, awarded = _open_and_awarded(evaluation)
    if not open_rounds and not awarded:
        return f"{evaluation.get('comparable', 0)} of {evaluation.get('of', 0)}"
    if not open_rounds:
        return 0
    return f"{sum(p['comparable'] for p in open_rounds)} of {sum(p['of'] for p in open_rounds)}"


def evaluation_words(evaluation) -> str:
    """The Evaluation cell's caption: what the headline counts, round by round, and awarded tenders apart.

    "quotes comparable on RUTF round 2 · awarded: RUTF round 1". Several open
    rounds are broken down one by one ("RUTF round 3: 0 of 2 · RUTF round 2:
    1 of 3 quotes comparable"), each reading back to its own comparison page.
    """
    open_rounds, awarded = _open_and_awarded(evaluation)
    if not open_rounds and not awarded:
        return "quotes comparable"
    if len(open_rounds) == 1:
        head = f"quotes comparable on {open_rounds[0]['label']}"
    elif open_rounds:
        head = " · ".join(f"{p['label']}: {p['comparable']} of {p['of']}" for p in open_rounds) + " quotes comparable"
    else:
        head = "no tender open for evaluation"
    if awarded:
        head += " · awarded: " + ", ".join(p["label"] for p in awarded)
    return head


def quotation_words(quotations) -> str:
    """The Quotations caption: live quotes per tender, newest first, so each reads back to its tender."""
    parts = quotations.get("by_tender") or []
    if len(parts) < 2:
        return "live" + (f" on {parts[0]['label']}" if parts else "")
    return "live: " + " · ".join(f"{p['live']} on {p['label']}" for p in parts)


@register.filter
def source_stages(source):
    """chain_summary's source counts, as stage cells.

    The shaping lives here rather than in summary.py so the derivation stays
    free of presentation, and so a second client can lay the same numbers out
    differently without fighting a format baked into the data.
    """
    evaluation = source["evaluation"]
    sourcing = reverse("supply_chain:procurement_tender_board")

    return [
        _cell(
            "Demand",
            source["demand"]["tenders"],
            _plural(source["demand"]["open"], "open tender"),
            sourcing,
        ),
        _cell(
            "RFQ issued",
            source["rfq_issued"]["invitations"],
            _awaiting_words(source["rfq_issued"]),
            sourcing,
        ),
        _cell("Quotations", source["quotations"]["live"], quotation_words(source["quotations"]), sourcing),
        _cell("Evaluation", evaluation_value(evaluation), evaluation_words(evaluation), sourcing),
        _cell(
            "Award",
            source["award"]["count"],
            (
                f"{source['award']['provisional']} provisional — chosen before every quote could be compared"
                if source["award"]["provisional"]
                else None
            ),
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
            ", ".join(f"{n} bought by {buyer_label(buyer)}" for buyer, n in buyers.items()) or None,
            orders,
        ),
        _cell(
            "Orders without a PO number",
            order["contract"]["without_reference"],
            "we do not hold the PO number" if order["contract"]["without_reference"] else None,
            orders,
        ),
        _cell(
            "Dispatched",
            order["dispatched"]["shipments"],
            _dispatched_note(order["dispatched"]),
            orders,
        ),
        _cell("Received", order["received"]["receipts"], "goods received notes", orders),
        _cell(
            "Invoiced",
            order["invoiced"]["count"],
            _invoiced_note(order["invoiced"]) if order["invoiced"]["count"] else None,
            orders,
        ),
    ]


def _dispatched_note(dispatched) -> str:
    """ "1 at customs — held, waiting on us · not stock": where the goods on the road are, as the order says."""
    where = dispatched.get("whereabouts") or {}
    if not where:
        return f"{dispatched['in_transit']} in transit — not yet stock on hand"
    return ", ".join(f"{n} {words}" for words, n in where.items()) + " · not yet stock on hand"


def _invoiced_note(invoiced) -> str:
    """ "1 part paid", "2 unpaid · 1 part paid": what is still owed on the invoices, part payments apart."""
    unpaid, part = invoiced.get("unpaid") or 0, invoiced.get("part_paid") or 0
    parts = [f"{unpaid} unpaid" if unpaid else "", f"{part} part paid" if part else ""]
    return " · ".join(p for p in parts if p) or "0 unpaid"


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
            f"supply points · {deliver['network']['user_held']} field workers",
            stock,
        ),
        # A quantity needs a unit, and a unit needs a commodity -- so a cell
        # with neither says what would make it computable rather than showing
        # a bare dash, which reads as "zero" or "broken".
        _quantity_cell("On hand", deliver["on_hand"], "excludes goods in transit", stock),
        _quantity_cell("In transit", deliver["in_transit"], "not counted as stock on hand", stock),
        _cell("Distributed", deliver["distributions"]["runs"], "resupply runs", distribution),
        _quantity_cell("Dispensed", deliver["consumed"], "derived from visits", distribution),
        _cell(
            "Below reorder level",
            needs,
            f"supply points · {deliver['network']['never_reported']} never reported",
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


# Who the buyer of record is, in words. The enum is the domain's; the screen
# is a person's, and "as programme org" read like a database column.
BUYER_LABELS = {
    "programme_org": "the program",
    "partner_org": "a local partner",
    "agency": "a procurement agency",
}


@register.filter
def buyer_label(value):
    return BUYER_LABELS.get(value, humanise(value))


# Every vocabulary value that humanise() alone would word badly. The rest --
# "at_customs", "certificate_of_analysis", "customs_fee" -- read fine with the
# underscores taken out, and fall through to that. One mapping for every
# vocabulary in records.py, because a code means the same thing wherever it
# lands; `words` is the one filter a template uses to put any of them on a page.
VOCAB_LABELS = {
    **BUYER_LABELS,
    **SOURCE_LABELS,
    **CHECK_LABELS,
    # how a quote is priced
    "per_base_unit": "per single unit",
    "per_pack": "per pack",
    "per_lot_total": "for the whole lot",
    "per_metric_tonne": "per metric tonne",
    "trade_item_confirmed": "confirmed from the trade item",
    # included / excluded / not stated
    "not_specified": "not stated",
    # what is given for the goods
    "priced": "bought",
    "in_kind": "donated in kind",
    "bundled": "bundled in another cost",
    # when it is paid
    "on_delivery": "on delivery",
    "advance": "in advance",
    # where stock rests
    "user_held": "held by a field worker",
    "supplier_site": "supplier's site",
    # how a count was taken
    "self_reported": "self-reported",
    # a three-way match
    "over_invoiced": "billed beyond what arrived",
    "part_received": "part received",
    "part_paid": "part paid",
}


@register.filter
def words(value):
    """Any vocabulary value as words: "programme_org" -> "the programme",
    "stock_below_minimum" -> "Below its own minimum", "at_customs" -> "at customs".

    Use `|words|capfirst` where it starts a sentence or a cell.
    """
    if value is None or value == "":
        return ""
    return VOCAB_LABELS.get(value, humanise(value))


@register.filter
def money_text(cell, max_places=None):
    """A money cell as a person writes money: "USD 18,000.00", never "18000.0000".

    `max_places` caps the decimals where a column reads at a glance: the
    comparison's per-sachet figure is "0.273", not "0.2733".
    """
    if not isinstance(cell, dict) or cell.get("amount") in (None, ""):
        return figure_text(cell)
    amount = cell["amount"]
    if max_places not in (None, ""):
        number = _as_decimal(amount)
        if number is not None and number.is_finite():
            amount = number.quantize(Decimal(1).scaleb(-int(max_places)), rounding=ROUND_HALF_UP)
    return f"{cell.get('currency') or ''} {money_digits(amount)}".strip()


_ARROW_VALUE = re.compile(r"→ ([^;]+)")


@register.filter
def bold_after_arrow(text):
    """A change line with each new value in bold: "ETA 5 Sep → <strong>19 Sep</strong>".

    What a field changed TO is the news on the line; the value it left is context.
    """
    text = str(text or "")
    out, at = [], 0
    # Matched on the raw text and each piece escaped on its own: an escaped
    # "&amp;" carries the ";" that ends a clause.
    for match in _ARROW_VALUE.finditer(text):
        out.append(escape(text[at : match.start()]))
        out.append(f'→ <strong class="font-semibold">{escape(match.group(1))}</strong>')
        at = match.end()
    out.append(escape(text[at:]))
    return mark_safe("".join(str(part) for part in out))


@register.filter
def record_kind_lead(text, sender=""):
    """A record line under an email with its kind set apart: "<b>Outreach</b> · Replied ...".

    Under an email's excerpt, what was recorded from it read at the same weight
    as the evidence; the record's kind in semibold marks the line as the record.
    The rest reads as `bold_after_arrow` reads it. The record's name is left out
    when it is the sender the event is already headed by ("Email from Amadou
    Issoufou, Sahel Nutrition Industries"): said on every line under it, it
    repeated three times per email.
    """
    text = str(text or "")
    kind, sep, rest = text.partition(" · ")
    if not sep or len(kind) > 24:
        return bold_after_arrow(text)
    name, named, after = rest.partition(" · ")
    if named and name.strip() and sender and str(sender).rstrip().endswith(name.strip()):
        rest = after
    lead = f'<span data-testid="record-kind" class="font-semibold">{escape(kind)}</span> · '
    if kind == "Quote" and rest.startswith("recorded: "):
        return mark_safe(lead + _quote_term_chips(rest[len("recorded: ") :]))
    # "Owed · recorded: they asked: ..." stacked two colons; under the email it came
    # from, the record's kind already says it was recorded.
    for said in ("recorded: they asked: ", "recorded: we promised: "):
        if rest.startswith(said):
            rest = said[len("recorded: ") :] + rest[len(said) :]
            break
    return mark_safe(lead + str(_terms_kept_whole(rest)))


# What each recorded quote term is called on its chip, by how the line words it.
_QUOTE_TERM_LABELS = (
    ("valid to ", "Valid to"),
    ("lead time ", "Lead time"),
    ("minimum order ", "Minimum order"),
    ("shelf life ", "Shelf life"),
)


# The fixed order of a quote card's terms, two to a row.
_QUOTE_TERM_ORDER = ("Incoterm", "Pack", "Valid to", "Lead time", "Minimum order", "Shelf life")


def _quote_term_chips(text):
    """A recorded quote's terms as labelled chips: Price · Incoterm · Pack · Valid to · ...

    The line ran every term together ("EUR 0.31 per sachet (EXW Niamey: freight
    and duty excluded) — you asked CPT Kano · valid to 2 Nov 2026 · ...") and
    wrapped mid-phrase; each term now reads as its own labelled chip, in the
    order the supplier's email gives them.
    """
    chips = []
    pieces = str(text).split(" · ")
    price, _, basis = pieces[0].partition(" (")
    chips.append(("Price", price))
    if basis:
        terms, _, asked = basis.partition(")")
        asked = asked.strip().lstrip("—").strip()
        chips.append(("Incoterm", terms + (f" — {asked}" if asked else "")))
    for piece in pieces[1:]:
        for prefix, label in _QUOTE_TERM_LABELS:
            if piece.startswith(prefix):
                chips.append((label, piece[len(prefix) :]))
                break
        else:
            chips.append(("Pack" if " per " in piece else "", piece))
    # The price first, at a weight of its own, then the other terms as a two-column
    # list of label and value: a run of equal-weight chips gave the price no lead.
    # Every card lays its terms out in one fixed order, a term the supplier did not
    # give marked "not stated", so the cells line up from one card to the next.
    (_, price), rest = chips[0], chips[1:]
    head = f'<span data-testid="quote-price" class="text-base font-semibold text-gray-900">{escape(price)}</span>'
    if not rest:
        return head
    given = {}
    extra = []
    for label, value in rest:
        if label in _QUOTE_TERM_ORDER and label not in given:
            given[label] = value
        else:
            extra.append((label, value))
    rendered = []
    for label in _QUOTE_TERM_ORDER:
        value = given.get(label)
        shown = (
            f"<span>{escape(value)}</span>"
            if value
            else '<span class="text-gray-600" data-testid="quote-term-missing">not stated</span>'
        )
        rendered.append(
            f'<span data-testid="quote-term" data-term="{escape(label)}" class="block">'
            f'<span class="text-gray-600">{escape(label)}</span> {shown}</span>'
        )
    for label, value in extra:
        name = f'<span class="text-gray-600">{escape(label)}</span> ' if label else ""
        rendered.append(f'<span data-testid="quote-term" class="block">{name}<span>{escape(value)}</span></span>')
    return head + '<span class="mt-0.5 grid grid-cols-2 gap-x-6 gap-y-0.5">' + "".join(rendered) + "</span>"


def _terms_kept_whole(text):
    """A record's " · "-separated terms, each short one kept on one line.

    "valid to 2 Nov 2026 · lead time 5 weeks · shelf life 24 months" broke
    inside "shelf / life" at the line's end; a short term now moves down whole.
    A long one (the price with its basis) may still wrap, or it would overflow.
    """
    pieces = []
    for piece in str(text).split(" · "):
        rendered = bold_after_arrow(piece)
        pieces.append(f'<span class="whitespace-nowrap">{rendered}</span>' if len(piece) <= 32 else str(rendered))
    return mark_safe(" · ".join(pieces))


@register.filter
def quantity_text(value):
    """A derived quantity (a consumption rate, a balance) to at most two places.

    "3033.3333 co-pack a month" is false precision on a figure averaged over
    counted cartons; "3,033.33" says the same thing a reader can take in.
    """
    if isinstance(value, dict) and value.get("amount") not in (None, ""):
        return qty(value["amount"], value.get("unit"))
    return derived_text(value)


@register.filter
def demand_text(value):
    """A demand rate with no more precision than the data has.

    A rate averaged over counted units reads in whole units -- "3,033 co-packs",
    not "3,033.33"; "84 jerry cans", not "83.72" -- because there is no
    meaningful fraction of a co-pack. A measure ("L", "kg") keeps one place.
    A rate is never rounded to nothing: under one whole unit it keeps one place.
    """
    if isinstance(value, dict) and value.get("amount") not in (None, ""):
        amount = _as_decimal(value["amount"])
        unit = value.get("unit")
        if amount is not None and amount.is_finite():
            whole = amount.quantize(Decimal(1), rounding=ROUND_HALF_UP)
            if is_counted_unit(unit) and (whole or not amount):
                return qty(whole, unit)
            return qty(amount.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), unit)
    return quantity_text(value)


@register.filter
def send_text(value):
    """What to send or reorder, rounded UP to a whole unit wherever the unit is counted.

    Nobody can send 0.88 of a jerry can, and rounding down would send less
    than the band asks for. The operation's figure stays exact; only what the
    page asks a person to do is whole. A measure ("L") is shown as it is.
    """
    if isinstance(value, dict) and value.get("amount") not in (None, ""):
        amount = _as_decimal(value["amount"])
        unit = value.get("unit")
        if amount is not None and amount.is_finite() and is_counted_unit(unit):
            return qty(amount.quantize(Decimal(1), rounding=ROUND_CEILING), unit)
    return derived_text(value)


@register.filter
def told_by(row, orgs):
    """Who told us this: the organisation, when the row names one; else how we know.

    The row's `source` is an enum ("supplier_reported"); the organisation that
    reported it is the fact a reader wants, and the update links set it.
    """
    row = row or {}
    org = (orgs or {}).get(row.get("recorded_by_org_id")) if row.get("recorded_by_org_id") else None
    if org and org.get("name"):
        return org["name"]
    return source_label(row.get("source"))


@register.simple_tag
def told_by_for(row, orgs, tellers):
    """Who told us, naming the party whose word it is when somebody else wrote it down.

    `tellers` maps a reported source to the organisation that reports it on
    this record ({"partner_reported": "SCHI", ...}). A receipt the programme
    took down from a partner read just "Dimagi", which hid that it was the
    partner's word: it now reads "Dimagi, for SCHI (they told us)".
    """
    row = row or {}
    recorded = told_by(row, orgs)
    teller = (tellers or {}).get(row.get("source"))
    org = (orgs or {}).get(row.get("recorded_by_org_id")) if row.get("recorded_by_org_id") else None
    if teller and org and org.get("name") and org["name"] != teller:
        return f"{org['name']}, for {teller} (they told us)"
    return recorded


@register.filter
def buyer_comparison(by_buyer):
    """What the per-buyer landed totals actually establish, and no more.

    {"state": "unknown" | "differ" | "same", "unconfirmed": [buyer, ...]}.

    `unknown` whenever ANY buyer's total is unconfirmed: a missing figure
    cannot be said to equal or differ from the others, and "none are payable"
    is exactly what an unconfirmed duty does not tell you (CodeRabbit on
    #1975). `differ` only when two CONFIRMED totals differ; `same` only when
    every total is confirmed and they all agree -- which says they cost the
    same, not why.
    """
    cells = dict(by_buyer or {})
    unconfirmed = [buyer for buyer, cell in cells.items() if not isinstance(cell, dict) or "amount" not in cell]
    confirmed = {
        (cell.get("currency"), _as_number(cell.get("amount")))
        for buyer, cell in cells.items()
        if buyer not in unconfirmed
    }
    if len(confirmed) > 1:
        state = "differ"
    elif unconfirmed or not confirmed:
        state = "unknown"
    else:
        state = "same"
    return {"state": state, "unconfirmed": unconfirmed}


@register.filter
def same_total(by_buyer):
    """The one landed total when every buyer's is confirmed and equal, else None.

    So the page can say it once: three identical rows said one thing three times.
    """
    if buyer_comparison(by_buyer)["state"] != "same":
        return None
    return next(iter(dict(by_buyer).values()))


def _as_number(amount):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(amount))
    except (InvalidOperation, ValueError):
        return amount


@register.filter
def figure_label(key, commodity=None):
    """A derived figure's name, from the one place that names them.

    `{{ key|figure_label:commodity }}` fills the unit with the commodity's own
    noun, as the comparison's column headers do: "USD per jerry can". Without
    a commodity the placeholders read generically ("base unit", "pack") --
    "USD per sachet" on a page about cartons would be worse than the generic
    word.
    """
    from connect_labs.supply_chain.procurement.services.pricing import FIGURE_LABELS, figure_nouns

    label = FIGURE_LABELS.get(key, str(key).replace("_", " "))
    if isinstance(commodity, dict):
        return label.format(**figure_nouns(commodity.get("base_unit"), commodity.get("pack_unit")))
    return label.replace("{base_unit}", "base unit").replace("{pack_unit}", "pack")


# What the supplier stated, in the order a reader checks it. Empty rows are
# KEPT on purpose: "sachets per carton -- not stated" is the whole explanation
# for four of the six figures coming back unconfirmed, so hiding it would
# remove the answer to the question the page raises.
_STATED_ROWS = (
    ("Price", "_price"),
    ("Priced per", "as_quoted_unit"),
    ("Quantity priced", "_basis"),
    ("Single units per pack", "base_per_pack_stated"),
    ("Weight per single unit", "base_unit_grams_stated"),
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
    said = words(basis)
    # `if amount` is false for 0, so freight quoted at zero read as "amount
    # not given" -- an unknown. Free freight and a waived duty are real facts
    # with their own basis flag, and keeping a known zero apart from an
    # unknown is the thing this domain is built around.
    return f"{said}, {money_digits(amount)}" if amount is not None else f"{said}, amount not given"


@register.filter
def stated_rows(quote):
    """The quote as the supplier wrote it, one label-value row at a time."""
    quote = quote or {}
    amount, currency = quote.get("as_quoted_amount"), quote.get("as_quoted_currency")
    computed = {
        "_price": f"{currency} {money_digits(amount)}" if amount else "",
        "_basis": (
            qty(quote.get("quantity_basis"), quote.get("quantity_basis_unit")) if quote.get("quantity_basis") else ""
        ),
        "_freight": _basis_phrase(quote.get("freight_basis"), quote.get("freight_amount")),
        "_duties": _basis_phrase(quote.get("duties_basis"), quote.get("duties_amount")),
        "_moq": qty(quote.get("moq"), quote.get("moq_unit")) if quote.get("moq") else "",
    }
    rows = []
    for label, key in _STATED_ROWS:
        value = computed[key] if key.startswith("_") else quote.get(key)
        if key == "shelf_life_months_stated" and value:
            value = f"{value} months"
        if key == "lead_time_days" and value:
            value = f"{value} days"
        # The enum-valued fields read as words, not as identifiers. Fixed in
        # the tender table and missed here, which is why "not_stated" reached
        # the quote page.
        if key in ("as_quoted_unit", "pack_spec_source"):
            value = words(value)
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
    "declared": "Says they sell it",
}


@register.filter
def evidence_label(kind):
    return EVIDENCE_LABELS.get(kind, humanise(kind))


@register.filter
def has_field_errors(form) -> bool:
    """Whether any single field is marked, as opposed to only a form-wide refusal.

    A refusal about the record itself -- an award still awaiting approval --
    leaves nothing on the form to fix, and the hint that says to fix what is
    marked would send the reader hunting for a field that is fine.
    """
    return any(name != "__all__" for name in getattr(form, "errors", {}) or {})


# ---- stock from visits: what we believe each worker holds -------------------


def _nonzero_amount(cell):
    """The cell's amount as a Decimal when it is a nonzero number, else None."""
    if not isinstance(cell, dict) or cell.get("amount") in (None, ""):
        return None
    number = _as_decimal(cell["amount"])
    return number if number else None


def _unsettled_parts(parts) -> list[str]:
    """ "30 on visits not yet approved", "30 estimated": the dispensing that is not settled, in words."""
    parts = parts or {}
    said = []
    for key, words_for in (("unapproved", "on visits not yet approved"), ("estimated", "estimated")):
        part = parts.get(key)
        if isinstance(part, dict) and "unconfirmed" in part:
            said.append(f"an unknown part {words_for}")
            continue
        amount = _nonzero_amount(part)
        if amount is not None:
            said.append(f"{quantity_digits(amount)} {words_for}")
    return said


@register.filter
def on_hand_words(cell, parts):
    """An on-hand figure, and what the dispensing behind it rests on.

    "120 sachets on hand — of the 90 sachets dispensed, 30 on visits not yet
    approved, 30 estimated". `parts` is the row (or a store's subtree): its
    `dispensed`, `unapproved` and `estimated`, all in the figure's own unit.
    The parts are DISPENSING, never stock on hand, so they are said as parts
    of what was dispensed -- set beside on hand with no noun, "30 unapproved"
    read as though 30 of the sachets on hand were. Said ON the figure, never
    in a footnote (design 2026-09-28 §6); a figure resting on nothing
    unsettled is just "120 sachets on hand".
    """
    text = f"{figure_text(cell)} on hand"
    said = _unsettled_parts(parts)
    if not said:
        return text
    return f"{text} — of the {figure_text((parts or {}).get('dispensed'))} dispensed, {', '.join(said)}"


@register.filter
def dispensed_words(cell, parts):
    """A dispensed figure and its unsettled parts.

    "90 sachets dispensed — 30 on visits not yet approved, 30 estimated".
    """
    text = f"{figure_text(cell)} dispensed"
    said = _unsettled_parts(parts)
    return f"{text} — {', '.join(said)}" if said else text


@register.filter
def signed_figure(cell):
    """A difference with its sign always said: "−5 sachets", "+12 sachets", "0 sachets".

    For a variance, where the sign is the finding. A true minus sign, not a
    hyphen, so it does not vanish against the number.
    """
    if not isinstance(cell, dict) or "unconfirmed" in cell:
        return figure_text(cell)
    number = _as_decimal(cell.get("amount"))
    if number is None:
        return figure_text(cell)
    text = qty(abs(number), cell.get("unit"))
    if number > 0:
        return f"+{text}"
    if number < 0:
        return f"−{text}"
    return text


@register.filter
def days_text(value):
    """Days to stock-out as a reader wants it: "102 days", "under a day", or why there is no figure."""
    if isinstance(value, dict):
        if "unconfirmed" in value:
            return "unknown"
        if "not_forecast" in value:
            return "not forecast"
        return figure_text(value)
    number = _as_decimal(value)
    if number is None:
        return "—"
    if number < 1:
        return "under a day"
    whole = int(number)
    return f"{whole:,} day{'' if whole == 1 else 's'}"


@register.filter
def months_text(value):
    """Months of cover to one place, with its noun: "3.4 months of cover", or why there is no figure."""
    if isinstance(value, dict):
        if "unconfirmed" in value:
            return "cover unknown"
        if "not_forecast" in value:
            return "durable — not forecast"
        return figure_text(value)
    number = _as_decimal(value)
    if number is None:
        return "—"
    shown = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{shown.normalize():f} month{'' if shown == 1 else 's'} of cover"


# What the visit reader made of one visit for one item (WorkerVisit.outcomes),
# in words. "" is a visit whose form this item's rule does not read.
VISIT_OUTCOMES = {
    "dispensed": "Gave some out",
    "nothing_given": "Gave none",
    "no_answer": "Did not say",
    "unmapped": "Answer not in the rule's list",
    "unit_refused": "Unit could not be converted",
    "skipped": "Skipped",
    "reversed": "Reversed — stock put back",
    "not_counted": "Not counted — rejected before it was read",
    "": "Not read for this item",
}


@register.filter
def visit_outcome(outcome):
    return VISIT_OUTCOMES.get(outcome or "", humanise(outcome).capitalize())


@register.filter
def rests_on_unsettled(parts):
    """Whether any of a row's dispensing is unapproved or estimated (or could not be told)."""
    parts = parts or {}
    for key in ("unapproved", "estimated"):
        part = parts.get(key)
        if (isinstance(part, dict) and "unconfirmed" in part) or _nonzero_amount(part) is not None:
            return True
    return False


@register.filter
def lead_in(line):
    """A "Kind: detail" line with its kind in bold: "<strong>No reply</strong>: Plateau Mills".

    The overview's waiting-on lines lead with what is blocking ("us", "No
    reply", "Missing facts"); bold makes the kinds scannable down the column.
    The text is unchanged, and a line with no colon is returned as it is.
    """
    text = str(line or "")
    head, sep, rest = text.partition(":")
    if not sep or not head.strip():
        return escape(text)
    # "Us: import permit", with the same capitalised label as "No reply".
    return mark_safe(f"<strong>{escape(head[:1].upper() + head[1:])}</strong>:{nowrap_money(rest)}")


_MONEY = re.compile(r"\b([A-Z]{3}) (\d[\d,]*(?:\.\d+)?)")


@register.filter
def nowrap_money(text):
    """Escape `text`, keeping each "USD 3,550.00" on one line.

    A narrow column broke an amount between its currency and its figure
    ("(USD / 3,550.00 above)"). The words are unchanged; only the amount is
    wrapped so it cannot split.
    """
    return mark_safe(_MONEY.sub(r'<span class="whitespace-nowrap">\1 \2</span>', str(escape(str(text or "")))))


@register.filter
def email_events(timeline):
    """A history's lines with those recorded from one email folded into one event (history.timeline)."""
    from connect_labs.supply_chain.history.timeline import email_events as fold

    return fold(list(timeline or []))


@register.filter
def duty_terms_words(value) -> str:
    """ "we import, under the program's duty waiver", or "not settled": a tender's import-duty terms."""
    from connect_labs.supply_chain.records import DUTY_TERMS_LABELS

    return DUTY_TERMS_LABELS.get(value or "", DUTY_TERMS_LABELS[""])


@register.filter
def ai_words(actor) -> str:
    """ "AI assistant" for an agent's pill: "ACE (agent)" named an internal product.

    The buyer knows it as the AI; which agent it was goes in the pill's tooltip
    (`ai_agent_name`). Any other label is returned as it is.
    """
    text = str(actor or "")
    return "AI assistant" if text.endswith(" (agent)") else text


@register.filter
def ai_agent_name(actor) -> str:
    """ "ACE" from "ACE (agent)", for the AI pill's tooltip; "" for any other label."""
    text = str(actor or "")
    return text.removesuffix(" (agent)") if text.endswith(" (agent)") else ""


@register.filter
def in_sentence(name) -> str:
    """A product name inside running text, lower case unless it is an acronym ("of
    ready-to-use therapeutic food", "of RUTF") -- the drafted emails' own rule."""
    from connect_labs.supply_chain.procurement.services.render import in_sentence as _in_sentence

    return _in_sentence(name)


def _round_only(row) -> bool:
    """Whether every blocker on a row is ours to clear (the tender's own decision), none the supplier's."""
    blockers = _blockers(row or {})
    return bool(blockers) and all(((b or {}).get("question") or {}).get("audience") == "internal" for b in blockers)


@register.filter
def waits_only_on_round(rows):
    """The blocked rows held only by our own decision (e.g. the tender's duty terms): nothing the supplier owes."""
    return [row for row in rows or [] if _round_only(row)]


@register.filter
def owes_supplier_facts(rows):
    """The blocked rows with at least one fact still owed by the supplier -- what "Needs info" counts."""
    return [row for row in rows or [] if not _round_only(row)]


@register.filter
def dot_parts(value):
    """ "USD 50.10 / carton · CPT Kano" -> its " · " parts, so a template can keep each part whole on a line."""
    return [p for p in str(value or "").split(" · ") if p]
