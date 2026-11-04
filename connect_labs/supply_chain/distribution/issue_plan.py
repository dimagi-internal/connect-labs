"""What to issue each field worker at the next handout, as a list of planned handout lines.

The ONE place a handout plan reads the stock forecast, so that when the plan itself
(targets, buffer, exits) is computed somewhere better this is the only function to
repoint. It asks the forecast for each worker's need over the coming week, takes off
what the worker already holds, and rounds up to a whole unit.
"""

from decimal import ROUND_CEILING, Decimal

from connect_labs.supply_chain.models import DispensingRule, Item

ZERO = Decimal("0")


def lines_for(access, commodity, opportunity_id, item_id=None) -> list[dict]:
    """Planned handout lines [{to_supply_point_id, quantity, quantity_unit}], one per worker who needs stock."""
    from connect_labs.supply_chain.stock.services import forecast

    program_id = access._require_program()
    if item_id is not None:
        item = access._resolve_item(item_id)
    else:
        rule = (
            DispensingRule.objects.filter(
                program_id=program_id, opportunity_id=opportunity_id, item__commodity=commodity, status="active"
            )
            .select_related("item")
            .first()
        )
        item = rule.item if rule else Item.objects.filter(commodity=commodity).first()
    if item is None:
        return []
    result = forecast.forecast(program_id, item, opportunity_ids=[opportunity_id], horizon_weeks=1)
    unit = result["unit"]
    lines = []
    for worker in result["workers"]:
        needed = Decimal(worker["weeks"][0]["need"] or "0")
        held = ZERO
        if worker["stock_known"]:
            held = Decimal(str((worker["on_hand"] or {}).get("amount") or "0"))
        to_issue = (needed - max(held, ZERO)).to_integral_value(rounding=ROUND_CEILING)
        if to_issue > 0:
            lines.append(
                {
                    "to_supply_point_id": worker["supply_point_id"],
                    "quantity": str(to_issue),
                    "quantity_unit": unit,
                    "item_id": item.pk,
                }
            )
    return lines
