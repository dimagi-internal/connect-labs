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

from decimal import Decimal

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
                "map": {answer: _quantity(quantity, f"{what}, answer {answer!r}") for answer, quantity in line["map"].items()},
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
