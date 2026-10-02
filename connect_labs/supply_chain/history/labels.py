"""History as a program manager would say it.

A `Revision` stores a change as `{attname: [old, new]}`; nobody reads that.
This module turns one into a sentence -- "ETA 5 Sep → 19 Sep", "Quote
recorded: 42.50 USD per carton (basis not specified), from Northwind Foods" --
and an `OperationCall` into who told us: "Sophie Bello", "via AI · Sophie",
"ACE (agent)", "Supplier · Northwind Foods". See docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md
§3.4 and §4.3.

Numbers go through the app's one money and quantity rules (`values.py`), and
vocabulary codes through the same `words` mapping every supply template uses,
so a timeline line and the panel above it cannot word one fact two ways.
"""

import datetime
from decimal import Decimal

from django.db import models
from django.utils.dateformat import format as date_format

from connect_labs.supply_chain.templatetags.supply_chain_extras import VOCAB_LABELS, words
from connect_labs.supply_chain.values import money_digits, quantity_digits, unit_noun

# A field whose own name is not what a person calls it on this record.
FIELD_LABELS = {
    ("Shipment", "expected_on"): "ETA",
    ("Shipment", "dispatched_on"): "Dispatched",
    ("Receipt", "received_on"): "Received",
    ("Payment", "paid_on"): "Paid",
    ("Quote", "voided"): "Voided",
    ("Quote", "base_per_pack_stated"): "Units per pack",
    ("Quote", "base_unit_grams_stated"): "Grams per unit",
    ("Quote", "shelf_life_months_stated"): "Shelf life",
    ("Quote", "as_quoted_amount"): "Price",
    ("Quote", "as_quoted_unit"): "Priced",
    ("Quote", "as_quoted_currency"): "Currency",
    ("Tender", "status"): "Status",
    # What the outreach table heads the column: the day a reminder last went.
    ("Outreach", "last_reminder_on"): "Last chased",
}

# What the pages call a model, where that differs from its verbose name: the
# supply screens say "order", never "contract".
MODEL_LABELS = {
    "Contract": "Order",
    "AwardApproval": "Approval request",
    # Something we owe a counterparty: their question, or our promise.
    "Commitment": "Owed",
}

# Vocabulary codes held in a plain CharField (no `choices`), which would
# otherwise read verbatim as the free text beside them does.
_CODED_FIELDS = {("Quote", "pack_spec_source"), ("Quote", "freight_basis"), ("Quote", "duties_basis")}

# Never worth a clause: the record's own identity and its program. The rest of
# what a change touched is said, provenance fields included, because in an
# update they only appear when they actually changed.
HIDDEN_FIELDS = {"id", "program_id", "scope_key", "entered_by_user_id"}

# Decimal fields that hold money. Money and quantities share a column shape
# (18, 4), so the name is what tells them apart.
_MONEY_WORDS = ("amount", "price")

_DAY = "j M"
_LONG_TEXT = 80


# ---- who --------------------------------------------------------------


def is_ai(call) -> bool:
    """Whether a write came through an AI: an agent account, or a person driving MCP or the API."""
    return call is not None and (call.actor_is_agent or call.channel in ("mcp", "api"))


def _full_name(user) -> str:
    return (getattr(user, "name", "") or "").strip()


def _first_name(user) -> str:
    name = _full_name(user)
    return name.split()[0] if name else user.username


def actor_label(call, lookup=None) -> str:
    """Who told us, as the timeline's pill reads it.

    `lookup` (a `Lookup`) caches the organisation a supplier-channel call names,
    so a timeline of one supplier's reports reads its name once.
    """
    if call is None:
        return "System"
    actor = call.actor
    if call.actor_is_agent:
        email = (getattr(actor, "email", "") or "").strip()
        if email.lower().startswith("ace@"):
            return "ACE (agent)"
        return f"{_full_name(actor) or email or 'An agent'} (agent)"
    if call.channel in ("mcp", "api"):
        return f"via AI · {_first_name(actor)}" if actor is not None else "via AI"
    if call.channel == "web":
        return (_full_name(actor) or actor.username) if actor is not None else "Someone"
    if call.channel == "supplier":
        who = _acting_org_name(call, lookup) or _full_name(actor) or getattr(actor, "username", "")
        return f"Supplier · {who or 'unknown'}"
    if call.channel == "command":
        return "Imported"
    return "System"


def _acting_org_name(call, lookup=None) -> str:
    """The organisation a supplier-channel call was made for, by name."""
    if not call.acting_org_id:
        return ""
    from connect_labs.labs.models import LabsOrg

    org = (lookup or Lookup()).row(LabsOrg, call.acting_org_id)
    return (org.name or "") if org is not None else ""


# ---- what --------------------------------------------------------------


class Lookup:
    """Rows a sentence names -- a supplier, an organisation, a commodity -- read once each.

    A timeline names the same supplier on many lines; this keeps that to one
    query per record rather than one per line.
    """

    def __init__(self):
        self._rows = {}

    def prime(self, model, pks):
        wanted = {int(pk) for pk in pks} - {pk for (m, pk) in self._rows if m is model}
        if wanted:
            found = model._base_manager.in_bulk(wanted)
            for pk in wanted:
                self._rows[(model, pk)] = found.get(pk)

    def row(self, model, pk):
        if pk in (None, ""):
            return None
        key = (model, int(pk))
        if key not in self._rows:
            self._rows[key] = model._base_manager.filter(pk=key[1]).first()
        return self._rows[key]

    def name(self, model, pk) -> str:
        """The target's own name when it has one, else "#id"."""
        if pk in (None, ""):
            return ""
        obj = self.row(model, pk)
        if obj is None:
            return f"#{pk}"
        if type(obj).__str__ is not models.Model.__str__:
            return str(obj)
        identity = _identity(type(obj), _values_of(obj), self)
        return identity or f"#{pk}"


def model_label(model) -> str:
    if model._meta.auto_created:
        return "Invitation"
    label = MODEL_LABELS.get(model.__name__) or str(model._meta.verbose_name)
    return label[:1].upper() + label[1:]


def _fields(model):
    return {f.attname: f for f in model._meta.concrete_fields}


def field_label(model, attname) -> str:
    if (model.__name__, attname) in FIELD_LABELS:
        return FIELD_LABELS[(model.__name__, attname)]
    field = _fields(model).get(attname)
    label = str(field.verbose_name) if field is not None else attname
    if field is not None and field.is_relation and attname.endswith("_id"):
        label = field.name.replace("_", " ")
    return label[:1].upper() + label[1:]


def _quote_units(values, lookup) -> tuple[str, str]:
    """(base unit, pack unit) of the quote these values are, from its trade item or its commodity."""
    from connect_labs.supply_chain.models import Commodity, Item

    base = pack = ""
    for model, key in ((Item, "item_id"), (Commodity, "commodity_id")):
        row = lookup.row(model, (values or {}).get(key))
        if row is not None:
            base = base or row.base_unit
            pack = pack or row.pack_unit
    return base, pack


def quote_field_label(attname, values, lookup) -> str:
    """A quote's pack figure in the commodity's own units: "Sachets per carton", not "Units per pack".

    Falls back to the generic label when the commodity names no units.
    """
    base, pack = _quote_units(values, lookup)
    if attname == "base_per_pack_stated" and base and pack:
        label = f"{unit_noun(base, 2)} per {unit_noun(pack)}"
    elif attname == "base_unit_grams_stated" and base:
        label = f"grams per {unit_noun(base)}"
    else:
        return field_label(_quote_model(), attname)
    return label[:1].upper() + label[1:]


def _quote_model():
    from connect_labs.supply_chain.models import Quote

    return Quote


def _day(value) -> str:
    if isinstance(value, str):
        try:
            value = datetime.date.fromisoformat(value[:10])
        except ValueError:
            return value
    return date_format(value, _DAY)


def value_text(model, attname, value, lookup) -> str:
    """One stored value as it reads on the page: "5 Sep", "Northwind Foods", "42.50"."""
    if value is None or value == "":
        return ""
    field = _fields(model).get(attname)
    if field is None:
        return str(value)
    if field.is_relation:
        return lookup.name(field.related_model, value)
    if isinstance(field, models.DateField):  # DateTimeField included
        return _day(value)
    if isinstance(field, models.DecimalField):
        return money_digits(value) if any(w in attname for w in _MONEY_WORDS) else quantity_digits(value)
    if isinstance(field, models.BooleanField):
        return "yes" if value else "no"
    # Only a vocabulary code is put into words; free text -- a reference
    # "PO_7", a title -- is what somebody typed and reads verbatim.
    coded = isinstance(value, str) and (
        field.choices or value in VOCAB_LABELS or (model.__name__, attname) in _CODED_FIELDS
    )
    text = str(words(value)) if coded else str(value)
    return text if len(text) <= _LONG_TEXT else text[: _LONG_TEXT - 1].rstrip() + "…"


def _clause(model, attname, old, new, lookup) -> str:
    """One changed field: "ETA 5 Sep → 19 Sep", "Reference: PO-7", "Voided"."""
    special = _SPECIAL_CLAUSES.get((model.__name__, attname))
    if special is not None:
        return special(old, new)
    field = _fields(model).get(attname)
    if field is None or attname in HIDDEN_FIELDS:
        return ""
    label = field_label(model, attname)
    if isinstance(field, models.JSONField):
        return f"{label} changed"
    if isinstance(field, models.BooleanField):
        return label if new else f"{label}: no"
    before, after = value_text(model, attname, old, lookup), value_text(model, attname, new, lookup)
    if not before:
        return f"{label}: {after}"
    if not after:
        return f"{label} cleared"
    return f"{label} {before} → {after}"


_SPECIAL_CLAUSES = {
    # A correction supersedes the old version rather than editing it.
    ("Quote", "superseded_by_id"): lambda old, new: "Replaced by a corrected version" if new else "",
    ("Quote", "version"): lambda old, new: "",
}

# How a program manager would say what a supplier's reply was, from the
# vocabulary `outreach_update`'s schema restricts `response_kind` to
# (operations.py's `_OUTREACH_DATA`). An unknown code -- there should never be
# one, since the schema is closed -- falls back to the plain vocabulary word
# rather than raising, so a future addition here shows up as a stopgap
# sentence instead of a broken timeline.
_OUTREACH_REPLY_TEXT = {
    "quote": "Replied with a quote",
    "declined": "Declined to quote",
    "needs_info": "Replied asking for more information",
    "no_reply": "Marked as no reply",
}


def _outreach_reply_clause(kind) -> str:
    return _OUTREACH_REPLY_TEXT.get(kind) or f"Replied: {words(kind)}"


def _short(text) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _LONG_TEXT else text[: _LONG_TEXT - 1].rstrip() + "…"


def _values_of(obj) -> dict:
    return {f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields}


def _qty(values, amount_key, unit_key) -> str:
    amount = values.get(amount_key)
    if amount in (None, ""):
        return ""
    unit = values.get(unit_key) or ""
    return f"{quantity_digits(amount)} {unit_noun(unit, amount)}".strip()


def _cash(values, key="amount") -> str:
    amount = values.get(key)
    if amount in (None, ""):
        return ""
    # Currency first, as every table on the page writes it: "EUR 0.31", not "0.31 EUR".
    return f"{values.get('currency') or ''} {money_digits(amount)}".strip()


def _quote_price(values, lookup) -> str:
    """ "USD 42.50 per carton (basis not specified)": the price as the supplier stated it."""
    from connect_labs.supply_chain.models import Commodity, Item

    amount = values.get("as_quoted_amount")
    if amount in (None, ""):
        price = "no price"
    else:
        price = f"{values.get('as_quoted_currency') or 'USD'} {money_digits(amount)}"
    basis = values.get("as_quoted_unit") or ""
    field = {"per_base_unit": "base_unit", "per_pack": "pack_unit"}.get(basis)
    unit = ""
    if field:
        for model, key in ((Item, "item_id"), (Commodity, "commodity_id")):
            row = lookup.row(model, values.get(key))
            unit = unit or (getattr(row, field, "") if row is not None else "")
    per = f"per {unit_noun(unit)}" if unit else (words(basis) if basis else "")
    return " ".join(part for part in (price, per, _quote_terms(values)) if part)


def _quote_terms(values) -> str:
    """The terms a price was stated on: "(freight included)", "(DDP Kano: freight and duty included)".

    The Incoterm leads when the supplier stated one, because it is what they
    wrote; the freight and duty bases are what it was read as.
    """
    freight = values.get("freight_basis") or "not_specified"
    incoterm = (values.get("incoterm") or "").strip()
    if not incoterm:
        return "(basis not specified)" if freight == "not_specified" else f"(freight {freight})"
    duties = values.get("duties_basis") or "not_specified"
    if freight != "not_specified" and freight == duties:
        basis = f"freight and duty {freight}"
    else:
        basis = ", ".join(
            f"{what} {how}" for what, how in (("freight", freight), ("duty", duties)) if how != "not_specified"
        )
    return f"({incoterm}: {basis})" if basis else f"({incoterm})"


def _identity(model, values, lookup) -> str:
    """What names one record among its siblings: a supplier, a reference, a title."""
    from connect_labs.supply_chain import models as m

    def name(target, key):
        return lookup.name(target, values.get(key))

    by_model = {
        "Tender": lambda: values.get("label") or "",
        "Outreach": lambda: name(m.Supplier, "supplier_id"),
        "Quote": lambda: name(m.Supplier, "supplier_id"),
        "Award": lambda: name(m.Supplier, "supplier_id"),
        "AwardApproval": lambda: name_org(values.get("approver_org_id")),
        "Contract": lambda: values.get("reference") or "",
        "Shipment": lambda: values.get("reference") or "",
        "Receipt": lambda: values.get("reference") or "",
        "Invoice": lambda: values.get("reference") or "",
        "Charge": lambda: words(values.get("kind") or "").capitalize() if values.get("kind") else "",
        "Payment": lambda: _cash(values),
        "Commitment": lambda: name_org(values.get("owed_to_org_id")),
        "Document": lambda: values.get("title")
        or (words(values.get("kind")).capitalize() if values.get("kind") else ""),
        "ShipmentLine": lambda: _qty(values, "quantity", "quantity_unit"),
        "ReceiptLine": lambda: _qty(values, "quantity_accepted", "quantity_unit"),
    }

    def name_org(pk):
        from connect_labs.labs.models import LabsOrg

        return lookup.name(LabsOrg, pk)

    if model._meta.auto_created:
        return name_org(values.get("labsorg_id"))
    resolve = by_model.get(model.__name__)
    return str(resolve() or "") if resolve else ""


def _create_facts(model, values, lookup) -> list[str]:
    """The key facts of a new record, after "{Model} recorded: "."""
    identity = _identity(model, values, lookup)
    named = model.__name__
    if named == "Quote":
        return [_quote_price(values, lookup) + (f", from {identity}" if identity else "")]
    facts = [identity]
    if named == "Outreach" and values.get("sent_on"):
        facts.append(f"sent {_day(values['sent_on'])}")
    elif named == "Award" and values.get("decided_on"):
        facts.append(f"decided {_day(values['decided_on'])}")
    elif named == "AwardApproval" and values.get("role"):
        facts.append(words(values["role"]))
    elif named == "Contract":
        facts.append(_qty(values, "quantity", "quantity_unit"))
    elif named == "Shipment":
        if values.get("dispatched_on"):
            facts.append(f"dispatched {_day(values['dispatched_on'])}")
        if values.get("expected_on"):
            facts.append(f"ETA {_day(values['expected_on'])}")
    elif named == "ShipmentLine" and values.get("batch"):
        facts.append(f"batch {values['batch']}")
    elif named == "Charge":
        facts.append(_cash(values))
    elif named == "Receipt" and values.get("received_on"):
        facts.append(f"received {_day(values['received_on'])}")
    elif named == "ReceiptLine":
        facts = [f"{identity} accepted" if identity else ""]
        rejected = values.get("quantity_rejected")
        if rejected not in (None, "") and Decimal(str(rejected)) > 0:
            facts.append(f"{_qty(values, 'quantity_rejected', 'quantity_unit')} rejected")
    elif named == "Invoice":
        facts.append(_cash(values))
    elif named == "Payment" and values.get("paid_on"):
        facts.append(f"paid {_day(values['paid_on'])}")
    elif named == "Commitment" and values.get("text"):
        said = "they asked" if values.get("kind") == "question" else "we promised"
        facts.append(f"{said}: {_short(values['text'])}")
    return [f for f in facts if f]


def create_what(model, values, lookup) -> str:
    """What a new record's line says after its entity and name: "recorded: ETA 5 Sep".

    The same facts `sentence` gives a create, less the record's own name --
    the line already leads with it ("Shipment · SH-1 · recorded: ETA 5 Sep").
    """
    identity = _identity(model, values, lookup)
    if model.__name__ == "Quote":
        facts = [_quote_price(values, lookup)]
    else:
        facts = _create_facts(model, values, lookup)
        if identity and facts and facts[0] == identity:
            facts = facts[1:]
    text = "recorded" + (f": {', '.join(facts)}" if facts else "")
    if model.__name__ == "Award" and (values.get("rationale") or "").strip():
        text += f" — why: {values['rationale'].strip()}"
    return text


def identity(model, values, lookup) -> str:
    """What names one record among its siblings, for a timeline line: "SH-1", "Northwind Foods"."""
    return _identity(model, values or {}, lookup)


def sender(model, values, lookup) -> str:
    """Who a quoted source on this record came from, when the record says so; "" otherwise.

    Read off the record only for the write that CREATED it: a quote's source
    is its supplier's reply, and a fulfilment record the supplier reported
    (`source == "supplier_reported"`) is the order's supplier's word. A
    shipment's carrier is not its sender -- the rehearsal's dispatch notice
    from the supplier read "Email from Crescent Freight" -- and an update's
    sender is never the record's first teller: the call names it itself
    (`OperationCall.source_sender`, see entry_for). Never invented; anything
    else reads only as its kind ("Email, recorded by ...").
    """
    from connect_labs.supply_chain.models import Contract, Supplier

    values = values or {}
    if model.__name__ == "Quote":
        return lookup.name(Supplier, values.get("supplier_id")) if values.get("supplier_id") else ""
    if model.__name__ in ("Shipment", "Invoice", "Receipt"):
        if values.get("source") != "supplier_reported" or not values.get("contract_id"):
            return ""
        contract = lookup.row(Contract, values.get("contract_id"))
        return lookup.name(Supplier, contract.supplier_id) if contract is not None else ""
    return ""


def sentence(model, action, changes, lookup) -> str:
    """One revision in plain words. "" when nothing it changed is worth saying."""
    label = model_label(model)
    if model._meta.auto_created:
        values = changes if action == "delete" else {k: v[1] for k, v in changes.items()}
        org = _identity(model, values, lookup)
        return f"Invitation to {org} withdrawn" if action == "delete" else f"Invited {org}"
    if action == "create":
        values = {k: v[1] for k, v in changes.items()}
        facts = _create_facts(model, values, lookup)
        text = f"{label} recorded" + (f": {', '.join(facts)}" if facts else "")
        if model.__name__ == "Award" and (values.get("rationale") or "").strip():
            # The decision's reason is what a reader of the award wants next.
            text += f" — why: {values['rationale'].strip()}"
        return text
    if action == "delete":
        identity = _identity(model, changes, lookup)
        return f"{label} removed" + (f": {identity}" if identity else "")
    changes = dict(changes)
    lead = []
    if model.__name__ == "Quote" and (changes.get("voided") or [None, False])[1]:
        # "Voided: duplicate of an earlier email" -- the reason is the void's, not a second change.
        changes.pop("voided")
        reason = (changes.pop("void_reason", None) or [None, ""])[1]
        lead.append(f"Voided: {reason}" if reason else "Voided")
    if model.__name__ == "Outreach" and (changes.get("response_kind") or [None, ""])[1]:
        # "Replied with a quote", not "Responded; Response kind: quote" -- the
        # kind of reply already says a reply happened, so the boolean flip
        # that always comes with it is not a second fact worth a clause of
        # its own.
        kind = changes.pop("response_kind")[1]
        changes.pop("responded", None)
        lead.append(_outreach_reply_clause(kind))
    if model.__name__ == "Commitment" and (changes.get("resolution") or [None, ""])[1]:
        # "answered: one warehouse in Kano" -- the day it was answered is the
        # line's own date, so it is not a second clause.
        lead.append(f"answered: {_short(changes.pop('resolution')[1])}")
        changes.pop("resolved_on", None)
    clauses = lead + [_clause(model, attname, old, new, lookup) for attname, (old, new) in changes.items()]
    return "; ".join(c for c in clauses if c)


# What a correction writes about itself rather than about the quote.
_CORRECTION_BOOKKEEPING = {"version", "superseded_by_id", "correction_reason", "created_at", "updated_at"}
# A stated pack figure already says it was stated: the source flag that flips
# with it is not a second fact worth a clause (it stays in the attnames).
_PACK_FIGURES = {"base_per_pack_stated", "base_unit_grams_stated"}


def correction_sentence(model, old_values, new_values, lookup, *, with_before=True):
    """A corrected version against the one it replaced, as (sentence, attnames).

    "Quote corrected: sachets per carton 150 (was not stated)" -- a pack
    figure in the commodity's own units, when it names them.

    Names every field whose value reads differently now, old value in
    brackets; a value that was blank reads "not stated". `with_before=False`
    leaves the brackets off, for a one-line note that says only what the
    quote says now: "sachets per carton 150".
    """
    clauses, fields = [], []
    for attname, new in new_values.items():
        if attname in HIDDEN_FIELDS or attname in _CORRECTION_BOOKKEEPING or attname not in _fields(model):
            continue
        before = value_text(model, attname, old_values.get(attname), lookup)
        after = value_text(model, attname, new, lookup)
        if before == after:
            continue
        label = (
            quote_field_label(attname, {**old_values, **new_values}, lookup)
            if model.__name__ == "Quote"
            else field_label(model, attname)
        )
        # "sachets per carton", but "ETA" stays as it is.
        if not label[:2].isupper():
            label = label[:1].lower() + label[1:]
        clause = f"{label} {after or 'not stated'}"
        if with_before:
            clause += f" (was {before or 'not stated'})"
        clauses.append((attname, clause))
        fields.append(attname)
    if _PACK_FIGURES & set(fields):
        clauses = [c for c in clauses if c[0] != "pack_spec_source"]
    clauses = [text for _, text in clauses]
    text = f"{model_label(model)} corrected"
    return (f"{text}: {'; '.join(clauses)}" if clauses else text), tuple(fields)


def subject(model, values, lookup) -> str:
    """Which record an update is on: "Shipment · SH-1". The model alone when nothing names it."""
    identity = _identity(model, values or {}, lookup) if values is not None else ""
    return f"{model_label(model)} · {identity}" if identity else model_label(model)


def line_summary(model, changes, lookup) -> str:
    """A new line row's facts, to hang off its parent's sentence: "300 cartons, batch B1"."""
    return ", ".join(_create_facts(model, {k: v[1] for k, v in changes.items()}, lookup))
