"""When an award must say why (Sophie's RUTF feedback, Sourcing: award reason).

A reason is optional by default. A programme may set an amount in its Supply settings
(`award_reason_required_above_usd`); an award worth more than that must then give one.
The value of an award is the winning quote's landed total for the tender's quantity,
as the comparison froze it. When that total cannot be worked out and a limit is set,
the reason is required: we cannot show the award is under the limit.
"""

from decimal import Decimal, InvalidOperation

KEY = "award_reason_required_above_usd"


class AwardReasonRequired(ValueError):
    """An award above the programme's limit that gave no reason."""


def reason_limit(program_id) -> Decimal | None:
    """The programme's own setting, or None (no limit). Read from the programme layer."""
    from connect_labs.scope_config.models import ScopeConfig
    from connect_labs.supply_chain.config import SUPPLY

    row = ScopeConfig.objects.filter(scope_type="program", scope_key=str(program_id), namespace=SUPPLY.key).first()
    raw = (row.data if row else {}).get(KEY)
    try:
        return Decimal(str(raw)) if raw is not None else None
    except InvalidOperation:
        return None


def award_value_usd(snapshot, quote_id) -> Decimal | None:
    """The chosen quote's landed total in US dollars from a frozen comparison, or None if unknown."""
    for row in (snapshot or {}).get("all_rows") or []:
        if row.get("quote_id") == quote_id:
            cell = (row.get("figures") or {}).get("landed_total_for_tender_quantity") or {}
            if cell.get("amount") is not None and (cell.get("currency") or "USD") == "USD":
                return Decimal(str(cell["amount"]))
    return None


def reason_problem(program_id, snapshot, quote_id, rationale) -> str:
    """Why this award cannot be recorded without a reason, or "" when it can."""
    if (rationale or "").strip():
        return ""
    limit = reason_limit(program_id)
    if limit is None:
        return ""
    value = award_value_usd(snapshot, quote_id)
    if value is not None and value <= limit:
        return ""
    worth = f"worth about {value:,.0f} US dollars" if value is not None else "of a value we cannot work out yet"
    return (
        f"This award is {worth}; this programme asks for a reason above {limit:,.0f}. "
        "Add a short one, such as “lowest delivered price”."
    )
