"""Edit one table cell in place: a value typed into a cell, written through its operation.

A supply table used to be read-only, with every change a trip to a form page.
Now a cell that holds one stored fact can be clicked and typed into, the way a
spreadsheet is. What stays the same is the write path: each editable cell maps
to the operation a form for that record already calls (`quote_correct`,
`outreach_update`, `contract_update` ...), through `call_operation`, so a cell
edit is validated by the same schema, recorded as the same attributed
OperationCall and Revision, and refused for the same reasons as the form.
Nothing here writes a model.

The registry below is the whole contract: which record kinds and fields a
table may make editable, what each one is called, and what kind of input it
takes. A template marks a cell with `{% edit_cell kind id field value %}`
(templatetags/supply_chain_extras.py); `static/supply_chain/cell_edit.js` turns
a marked cell into an input and posts here; this module coerces the typed text
to the field's type and runs the operation.

A quote is the one record a cell does not overwrite: `quote_correct` writes a
new version and keeps the old one, so the edit carries a reason naming the
field and both values, and the response names the NEW quote's cell so the page
can mark it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import jsonschema
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

from connect_labs.supply_chain import records
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.identity import IdentityUnresolved
from connect_labs.supply_chain.operations import call_operation

TEXT, MONEY, NUMBER, INT, DATE, CHOICE = "text", "money", "number", "int", "date", "choice"


@dataclass(frozen=True)
class Cell:
    label: str
    type: str = TEXT
    choices: tuple = ()
    # Whether an emptied cell clears the value (sends null) rather than being refused.
    clearable: bool = False


@dataclass(frozen=True)
class Kind:
    operation: str
    id_arg: str
    fields: dict = field(default_factory=dict)


def _pairs(values, words=None) -> tuple:
    return tuple((v, (words or {}).get(v) or v.replace("_", " ")) for v in values)


KINDS: dict[str, Kind] = {
    "quote": Kind(
        "quote_correct",
        "quote_id",
        {
            "as_quoted_amount": Cell("price", MONEY),
            "as_quoted_currency": Cell("currency"),
            "incoterm": Cell("delivery term"),
            "base_per_pack_stated": Cell("pack size", INT, clearable=True),
            "base_unit_grams_stated": Cell("grams each", INT, clearable=True),
            "lead_time_days": Cell("lead time (days)", INT, clearable=True),
            "validity_until": Cell("valid until", DATE, clearable=True),
            "received_on": Cell("received", DATE),
            "fx_rate_to_usd": Cell("exchange rate to USD", MONEY, clearable=True),
            "freight_amount": Cell("freight", MONEY, clearable=True),
            "supplier_reference": Cell("supplier's reference"),
            "payment_terms": Cell("payment terms"),
        },
    ),
    "outreach": Kind(
        "outreach_update",
        "outreach_id",
        {
            "sent_on": Cell("asked", DATE, clearable=True),
            "responded_on": Cell("replied", DATE, clearable=True),
            "last_reminder_on": Cell("last chased", DATE, clearable=True),
            "response_kind": Cell(
                "reply",
                CHOICE,
                _pairs(
                    ("quote", "declined", "needs_info", "no_reply"),
                    {"needs_info": "questions for us", "no_reply": "no reply"},
                ),
            ),
            "notes": Cell("notes", clearable=True),
        },
    ),
    "tender": Kind(
        "tender_update",
        "tender_id",
        {
            "label": Cell("name"),
            "response_deadline": Cell("deadline", DATE, clearable=True),
            # Each of these has its own operation, so it is attributed the way its form is.
            "duty_terms": Cell("import duty", CHOICE, tuple(records.DUTY_TERMS_LABELS.items())),
            "clearing_estimate_per_unit": Cell("clearing estimate (USD per unit)", MONEY, clearable=True),
            "freight_estimate_per_unit": Cell("freight estimate (USD per unit)", MONEY, clearable=True),
        },
    ),
    "contract": Kind(
        "contract_update",
        "contract_id",
        {
            "reference": Cell("reference"),
            "quantity": Cell("quantity", NUMBER),
            "unit_price": Cell("unit price", MONEY),
            "currency": Cell("currency"),
            "status": Cell("status", CHOICE, _pairs(records.CONTRACT_STATUSES)),
            "signed_on": Cell("signed", DATE, clearable=True),
            "promised_lead_time_days": Cell("promised lead time (days)", INT),
        },
    ),
    "invoice": Kind(
        "invoice_update",
        "invoice_id",
        {
            "reference": Cell("invoice number"),
            "issued_on": Cell("issued", DATE),
            "amount": Cell("amount", MONEY),
            "quantity_billed": Cell("quantity billed", NUMBER),
            # "queried" reads "disputed" everywhere an invoice is shown; the list says so too.
            "status": Cell("status", CHOICE, _pairs(records.INVOICE_STATUSES, {"queried": "disputed"})),
        },
    ),
    # A shipment's lines (batch, expiry, quantity) are one list sent whole, so they are edited on
    # the shipment's own screen; the consignment's own facts edit here.
    "shipment": Kind(
        "shipment_update",
        "shipment_id",
        {
            "reference": Cell("reference"),
            "status": Cell("status", CHOICE, _pairs(records.SHIPMENT_STATUSES)),
            "expected_on": Cell("expected", DATE),
            "carrier": Cell("carrier"),
        },
    ),
    "supplier": Kind(
        "supplier_update",
        "supplier_id",
        {
            "name": Cell("name"),
            "country": Cell("country (2 letters)"),
            "city": Cell("city", clearable=True),
            "website": Cell("website", clearable=True),
            "type": Cell("type", CHOICE, _pairs(records.SUPPLIER_TYPES)),
        },
    ),
}


def cell_spec(kind: str, name: str) -> Cell | None:
    spec = KINDS.get(kind)
    return spec.fields.get(name) if spec else None


# ---- typed text → a value the operation's schema takes --------------------


def coerce(cell: Cell, text) -> object:
    """The value to send for what was typed. Raises ValueError with words a person can act on."""
    text = "" if text is None else str(text).strip()
    if text == "":
        if cell.clearable:
            return None
        raise ValueError(f"{cell.label.capitalize()} cannot be empty.")
    if cell.type == MONEY or cell.type == NUMBER:
        cleaned = text.replace(",", "")
        for prefix in ("USD", "US$", "$", "EUR", "€", "NGN", "₦"):
            cleaned = cleaned.removeprefix(prefix).strip()
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            raise ValueError(f"{cell.label.capitalize()} must be a number, not {text!r}.") from None
        if amount < 0:
            raise ValueError(f"{cell.label.capitalize()} cannot be negative.")
        # A decimal string, never a float: money is Decimal throughout (operations.MONEY).
        return format(amount.normalize(), "f")
    if cell.type == INT:
        try:
            whole = int(Decimal(text.replace(",", "")))
        except (InvalidOperation, ValueError):
            raise ValueError(f"{cell.label.capitalize()} must be a whole number, not {text!r}.") from None
        if whole < 0:
            raise ValueError(f"{cell.label.capitalize()} cannot be negative.")
        return whole
    if cell.type == DATE:
        return _date(text, cell).isoformat()
    if cell.type == CHOICE:
        # The page carries a choice's position, never its stored code (cells never put a raw
        # code like "part_received" in front of anyone): position N is cell.choices[N].
        try:
            return cell.choices[int(text)][0]
        except (ValueError, IndexError):
            raise ValueError(f"Choose one of the listed values for {cell.label}.") from None
    return text


def _date(text: str, cell: Cell) -> date:
    # "6 Oct" -- the page's own short form -- is this year's.
    candidates = (text, f"{text} {date.today().year}")
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y"):
        for candidate in candidates:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"{cell.label.capitalize()} must be a date, e.g. {date.today():%-d %b %Y}.")


# ---- the write -----------------------------------------------------------


def _shown(value) -> str:
    return "blank" if value in (None, "") else str(value)


def apply(access, kind: str, record_id: int, name: str, text, *, was=None) -> dict:
    """Run the cell's operation. Returns {"key": the cell's key after the write}."""
    spec = KINDS.get(kind)
    cell = cell_spec(kind, name)
    if spec is None or cell is None:
        raise ValueError(f"{kind}.{name} is not editable in a table.")
    value = coerce(cell, text)

    if kind == "tender" and name == "duty_terms":
        call_operation("tender_set_duty_terms", access, {"tender_id": record_id, "duty_terms": value})
        return {"key": key(kind, record_id, name)}
    if kind == "tender" and name in ("clearing_estimate_per_unit", "freight_estimate_per_unit"):
        # The operation reads an omitted value as "keep it" and '' as "clear it".
        call_operation(
            "tender_set_import_estimates", access, {"tender_id": record_id, name: "" if value is None else value}
        )
        return {"key": key(kind, record_id, name)}

    data = {name: value}
    if kind == "outreach":
        # A reply's day or kind says whether there was a reply at all; the row's flag follows.
        if name == "responded_on" and value is not None:
            data["responded"] = True
        if name == "response_kind":
            data["responded"] = value != "no_reply"
    if kind == "quote" and name in ("base_per_pack_stated", "base_unit_grams_stated") and value is not None:
        # A pack figure typed in is the supplier's, read off their quote.
        data["pack_spec_source"] = "stated_on_quote"

    payload = {spec.id_arg: record_id, "data": data}
    if kind == "quote":
        payload["reason"] = f"Corrected in a table: {cell.label} {_shown(was)} → {_shown(value)}"
    result = call_operation(spec.operation, access, payload)
    new_id = (result or {}).get("id", record_id) if isinstance(result, dict) else record_id
    return {"key": key(kind, new_id, name)}


def key(kind: str, record_id, name: str) -> str:
    return f"{kind}:{record_id}:{name}"


@method_decorator(login_required, name="dispatch")
class CellEditView(View):
    """POST {"cell": "<kind>:<id>:<field>", "value": "...", "was": "..."} → {"ok", "key"} or {"error"}."""

    def post(self, request):
        if getattr(request, "supply_as_of", None):
            return JsonResponse({"error": "This page shows the past; it cannot be edited."}, status=400)
        if not has_program_context(request):
            return JsonResponse({"error": "Choose a program first."}, status=400)
        try:
            body = json.loads(request.body or b"{}")
            kind, record_id, name = str(body.get("cell", "")).split(":")
            record_id = int(record_id)
        except (ValueError, TypeError):
            return JsonResponse({"error": "Not a cell this page can edit."}, status=400)
        try:
            result = apply(_access(request), kind, record_id, name, body.get("value"), was=body.get("was"))
        except IdentityUnresolved as exc:
            # Worded for a browser, as the form screens word it (form_views.py).
            return JsonResponse({"error": f"{exc} There is a screen for that: Suppliers → Organisations."}, status=400)
        except jsonschema.ValidationError as exc:
            return JsonResponse({"error": exc.message}, status=400)
        except (ValueError, TypeError, PermissionError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse({"ok": True, **result})
