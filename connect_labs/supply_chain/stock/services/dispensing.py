"""Dispensing rules: what a visit's form answers say left the worker's bag.

Pure functions over a rule's `lines`. No database: the reader owns
persistence, so every rule the domain applies to a form answer can be tested
with a dict.

Three kinds of line (design 2026-09-28 §3.1, and the plan's addendum "Resolved
from the released app"):

- **stated** -- read a number the worker entered, e.g.
  `form.rutf_dispensing.rutf_sachets_dispensed`. The first path that answers
  wins, so one line covers the same question on two forms.
- **protocol** -- the form records only THAT something was given
  (`va_delivered = child_fine`); the quantity comes from the protocol. An
  optional `requires_paths` names answers that must ALSO be present (the dose
  question that says which strength was given).
- **value_map** -- the form records WHICH dose the app chose, as text; `map`
  turns each answer into a quantity. The first path that answers wins.

Anything a protocol or value_map line contributes is ESTIMATED, everywhere it
appears. Every line may carry `forms`: the form xmlns or names it applies to.

Every path is a `form_json` path and so starts `form.` -- `/data/x` in the
app is `form.x` in a submission. A path that does not is refused at save,
because reading the top-level visit dict (the bug extract_rows had) returns
nothing and looks exactly like "nobody answered".
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from connect_labs.labs.analysis.utils import extract_json_path
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.values import Unconfirmed, decimal_string


def base_unit(item) -> str:
    """The single unit a worker counts this item in: sachet, capsule, tablet."""
    unit = ledger._pack_spec(item)[1]
    if not unit:
        raise ValueError(
            f"{item.name} states no single unit (base_unit), so nothing given at a visit can be counted in it"
        )
    return unit


def _paths(values, what) -> list[str]:
    if not values:
        raise ValueError(f"{what} names no form path")
    for path in values:
        if not isinstance(path, str) or not path.startswith("form."):
            raise ValueError(
                f"{what}: {path!r} is not a form_json path -- it must start with 'form.' "
                "(the app's /data/x is form.x in a submission)"
            )
    return list(values)


def _quantity(value, what) -> str:
    try:
        amount = Decimal(str(value))
    except ArithmeticError:
        raise ValueError(f"{what}: {value!r} is not a number") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{what}: a quantity must be greater than zero")
    return decimal_string(amount)


def _forms(values, what) -> list[str]:
    names = [name.strip() for name in values]
    if not all(names):
        raise ValueError(f"{what}: a form name cannot be blank")
    return names


def validate_lines(lines, item) -> list[dict]:
    """The lines as they will be stored, or ValueError naming the first problem."""
    to_unit = base_unit(item)
    clean = []
    for number, line in enumerate(lines, start=1):
        what = f"line {number}"
        unit = line["unit"]
        if isinstance(ledger.convert(Decimal(1), unit, to_unit, item), Unconfirmed):
            raise ValueError(f"{what} is in {unit}s, and {item.name} gives no way to count {unit}s as {to_unit}s")
        kind = line["kind"]
        if kind == "stated":
            out = {"kind": "stated", "paths": _paths(line.get("paths"), what), "unit": unit}
        elif kind == "protocol":
            if line.get("given_values") == []:
                raise ValueError(
                    f"{what}: given_values is empty, so no answer would ever count as given; "
                    "leave it out to accept any answer"
                )
            out = {
                "kind": "protocol",
                "given_paths": _paths(line.get("given_paths"), what),
                "given_values": line.get("given_values"),
                "quantity": _quantity(line["quantity"], what),
                "unit": unit,
            }
        elif kind == "value_map":
            out = {
                "kind": "value_map",
                "paths": _paths(line.get("paths"), what),
                "map": {
                    answer: _quantity(quantity, f"{what}, answer {answer!r}")
                    for answer, quantity in line["map"].items()
                },
                "unit": unit,
            }
        else:
            raise ValueError(f"{what}: unknown line kind {kind!r}")
        if line.get("requires_paths"):
            out["requires_paths"] = _paths(line["requires_paths"], f"{what}'s required answers")
        if line.get("forms"):
            out["forms"] = _forms(line["forms"], what)
        clean.append(out)
    return clean


def validate_reports(reports) -> dict:
    """What the worker's own app reports: its running balance, and receipts."""
    if not reports:
        return {}
    clean = {}
    if reports.get("balance_paths"):
        clean["balance_paths"] = _paths(reports["balance_paths"], "the balance report")
    if reports.get("receipt"):
        receipt = reports["receipt"]
        clean["receipt"] = {
            "quantity_paths": _paths(receipt.get("quantity_paths"), "the receipt quantity"),
            "date_paths": _paths(receipt.get("date_paths"), "the receipt date"),
        }
    return clean


DISPENSED = "dispensed"
NOTHING_GIVEN = "nothing_given"
NO_ANSWER = "no_answer"
UNMAPPED = "unmapped"
UNIT_REFUSED = "unit_refused"
NOT_APPLICABLE = "not_applicable"

# 1e6 of anything at one visit is a typo, not a ration.
_MOST_AT_ONE_VISIT = Decimal("1000000")


@dataclass(frozen=True)
class Dispensed:
    """What one visit gave out of one item, in the item's single unit.

    `outcome` is one of the constants above. `unmapped` is ((path, answer), ...)
    for a value_map answer the map does not know: reported, never zero.
    """

    outcome: str
    quantity: Decimal = Decimal(0)
    unit: str = ""
    estimated: bool = False
    answers: dict = field(default_factory=dict)
    reasons: tuple = ()
    unmapped: tuple = ()


def read_number(value) -> Decimal | None:
    """A form answer as a non-negative quantity, or None when it is not one."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0 or number > _MOST_AT_ONE_VISIT:
        return None
    return number


def read_date(value) -> date | None:
    """A day from a visit or a form answer, or None when there is none.

    The one reader of Connect's dates in the stock-from-visits code: a visit's
    `visit_date`, its `status_modified_date` (a timestamp, read as its day),
    and a form's own date answers. Anything that is not a day is None --
    never today, never a guess.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def read_reports(reports, form_json) -> dict:
    """What the worker's app says on this visit: its balance, and any receipt.

    `balance` is None when the balance is unanswered (a stated 0 is a stockout
    and is kept). `received` is set only for a positive quantity; `received_on`
    is the form's own date answer, None when it gave none.
    """
    found = {"balance": None, "received": None, "received_on": None}
    if not reports:
        return found
    if reports.get("balance_paths"):
        path, raw = first_answer(form_json, reports["balance_paths"])
        found["balance"] = read_number(raw) if path else None
    receipt = reports.get("receipt")
    if receipt:
        path, raw = first_answer(form_json, receipt["quantity_paths"])
        quantity = read_number(raw) if path else None
        if quantity is not None and quantity > 0:
            found["received"] = quantity
            date_path, date_raw = first_answer(form_json, receipt["date_paths"])
            found["received_on"] = read_date(date_raw) if date_path else None
    return found


def _answer(form_json, path):
    value = extract_json_path(form_json, path)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def first_answer(form_json, paths):
    """(path, raw answer) for the first path answered, or (None, None)."""
    for path in paths:
        value = _answer(form_json, path)
        if value is not None:
            return path, value
    return None, None


def _given(value, given_values) -> bool:
    if given_values is None:
        return True
    # A CommCare multiple-choice answer is its choices joined by spaces.
    return any(word in given_values for word in str(value).split())


def _form_identity(form_json) -> set:
    form = form_json.get("form") if isinstance(form_json, dict) else None
    form = form if isinstance(form, dict) else {}
    return {str(v).strip() for v in (form.get("@xmlns"), form.get("@name")) if v}


def _applies(line, rule_forms, identity) -> bool:
    wanted = line.get("forms") or rule_forms
    return not wanted or bool(identity & set(wanted))


def _refused(to_unit, answers, reasons):
    return Dispensed(UNIT_REFUSED, Decimal(0), to_unit, False, answers, tuple(reasons))


def evaluate(lines, form_json, item, rule_forms=()) -> Dispensed:
    """Sum a rule's lines over one visit's answers.

    `rule_forms` is the rule's default form filter; a line's own `forms` wins.
    A visit whose form no line applies to is NOT_APPLICABLE (no movement, not
    a missing answer). Otherwise: stated lines read a number (absent lines add
    nothing; an answered-but-unreadable one makes the item NO_ANSWER, as does
    every stated line being unanswered when nothing else contributed);
    protocol and value_map lines whose given-path is absent are simply not
    given; an answer a value_map does not know is UNMAPPED. Units must all
    convert to the item's unit, else UNIT_REFUSED.
    """
    to_unit = base_unit(item)
    identity = _form_identity(form_json)
    lines = [line for line in lines if _applies(line, rule_forms, identity)]
    if not lines:
        return Dispensed(NOT_APPLICABLE, unit=to_unit, answers={})

    total = Decimal(0)
    estimated = stated_seen = stated_answered = False
    answers: dict = {}
    reasons: list[str] = []
    unmapped: list[tuple] = []
    for line in lines:
        kind = line["kind"]
        if kind == "stated":
            stated_seen = True
            path, raw = first_answer(form_json, line["paths"])
            if path is None:
                continue
            answers[path] = raw
            stated_answered = True
            quantity = read_number(raw)
            if quantity is None:
                reasons.append(f"{path} answered {raw!r}, which is not a quantity")
                continue
        else:
            paths = line["given_paths"] if kind == "protocol" else line["paths"]
            path, raw = first_answer(form_json, paths)
            if path is None:
                continue
            answers[path] = raw
            required = line.get("requires_paths") or []
            missing = [r for r in required if _answer(form_json, r) is None]
            for r in required:
                if r not in missing:
                    answers[r] = _answer(form_json, r)
            if missing:
                continue
            if kind == "protocol":
                if not _given(raw, line.get("given_values")):
                    continue
                quantity = Decimal(line["quantity"])
            else:
                text = str(raw).strip()
                if text not in line["map"]:
                    unmapped.append((path, text))
                    continue
                quantity = Decimal(line["map"][text])
            if quantity > 0:
                estimated = True
        converted = ledger.convert(quantity, line["unit"], to_unit, item)
        if isinstance(converted, Unconfirmed):
            return _refused(to_unit, answers, converted.reasons)
        total += converted.amount

    if unmapped:
        reasons += [f"{path} answered {text!r}, which the rule does not know" for path, text in unmapped]
        return Dispensed(UNMAPPED, Decimal(0), to_unit, False, answers, tuple(reasons), tuple(unmapped))
    if reasons or (stated_seen and not stated_answered and total == 0):
        return Dispensed(NO_ANSWER, Decimal(0), to_unit, False, answers, tuple(reasons))
    if total == 0:
        return Dispensed(NOTHING_GIVEN, Decimal(0), to_unit, False, answers)
    return Dispensed(DISPENSED, total.quantize(ledger.QUANTITY_SCALE), to_unit, estimated, answers)
