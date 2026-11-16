"""A quality test's result: the product's own specification applied to what was measured.

No threshold lives here. A batch or dispenser sample is held to `Commodity.spec_requirements`, the
same requirements a trade item's specification sheet is checked against (`compliance`), so a
programme changes what passes by changing its product, in the Catalogue, not by asking for code.

    fail        a requirement was measured and not met
    incomplete  nothing failed, but a requirement was not measured, or the product sets none
    pass        every requirement was measured and met

A storage check fails on any "no".
"""

from decimal import Decimal, InvalidOperation

from connect_labs.supply_chain import records
from connect_labs.supply_chain.procurement.services.compliance import OPERATOR_WORDS, OPERATORS, figure_label
from connect_labs.supply_chain.values import decimal_string

# An average is kept to three decimal places: a 0.96-1.44 mg/L band is read to two.
_AVERAGE_PLACES = Decimal("0.001")


def _number(raw, name):
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name}: {raw!r} is not a number") from None
    if not value.is_finite():
        raise ValueError(f"{name}: {raw!r} is not a number")
    return value


def readings_from(readings: dict | None, trials: list | None) -> tuple[dict, list]:
    """(readings, trials) as stored: decimal strings, with the trials' average filled in.

    The average goes under `records.TRIALS_AVERAGE_FIELD` unless the caller gave that reading
    itself, in which case theirs stands: it is what they read off, and recomputing it would
    quietly change a recorded figure.
    """
    out = {str(name): decimal_string(_number(value, name)) for name, value in (readings or {}).items()}
    kept = [_number(value, f"trial {i}") for i, value in enumerate(trials or [], start=1)]
    if kept and records.TRIALS_AVERAGE_FIELD not in out:
        average = (sum(kept) / len(kept)).quantize(_AVERAGE_PLACES)
        out[records.TRIALS_AVERAGE_FIELD] = decimal_string(average)
    return out, [decimal_string(value) for value in kept]


def _measured(readings, field) -> bool:
    value = readings.get(field)
    if value is None or value == "":
        return False
    try:
        Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return True


def figure_words(field: str) -> str:
    """A specification figure as a person reads it: "pH", "Double-dilution average (mg/L)"."""
    if field == records.TRIALS_AVERAGE_FIELD:
        return "Double-dilution average"
    if field.lower() == "ph":
        return "pH"
    return figure_label(field)


def _sentence(requirement, measured=None) -> str:
    unit = requirement.get("unit") or ""
    rule = f"{OPERATOR_WORDS[requirement['operator']]} {requirement.get('value')} {unit}".strip()
    name = figure_words(requirement.get("field") or "")
    if measured is None:
        return f"{name} was not measured; it must be {rule}."
    return f"{name} {decimal_string(measured)} {unit}".rstrip() + f"; it must be {rule}."


def judge_sample(commodity, readings: dict) -> tuple[str, list[str]]:
    """(result, findings) for a batch or dispenser sample."""
    requirements = [r for r in (getattr(commodity, "spec_requirements", None) or []) if r.get("operator") in OPERATORS]
    if not requirements:
        name = getattr(commodity, "name", "This product")
        return "incomplete", [f"{name} has no specification to test against: add its requirements in the Catalogue."]
    fails, missing, unmeasured = [], [], set()
    for requirement in requirements:
        field = requirement.get("field")
        if not _measured(readings, field):
            if field not in unmeasured:
                unmeasured.add(field)
                missing.append(_sentence(requirement))
            continue
        measured = Decimal(str(readings[field]))
        try:
            required = Decimal(str(requirement.get("value")))
        except (InvalidOperation, ValueError):
            continue
        if not OPERATORS[requirement["operator"]](measured, required):
            fails.append(_sentence(requirement, measured))
    if fails:
        return "fail", fails + missing
    if missing:
        return "incomplete", missing
    return "pass", []


def judge_storage(storage: dict) -> tuple[str, list[str]]:
    """(result, findings) for a storage check: each "no" is a finding."""
    answered = {name: value for name, value in (storage or {}).items() if value is not None}
    if not answered:
        return "incomplete", ["Nothing was checked."]
    noes = [
        f"{records.STORAGE_CHECK_WORDS.get(name, name)}: no"
        for name in records.STORAGE_CHECKS
        if answered.get(name) is False
    ]
    if noes:
        return "fail", noes
    return "pass", []


def judge(kind, commodity, readings, storage) -> tuple[str, list[str]]:
    if kind == "storage":
        return judge_storage(storage)
    return judge_sample(commodity, readings)
