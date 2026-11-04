"""What to issue each field worker at the next handout, as a list of planned handout lines.

The ONE place a handout plan reads "what to issue". It takes the saved plan's per-worker
what-to-issue list (`plan.service`, the same figures as the Plan page and `stock_plan_view`:
the week's follow-ups and expected new children, plus the buffer, less what Supply's records
say the worker holds) and turns each row that needs stock into a planned line. There is no
second calculation here: change the plan and the handout follows.
"""

from decimal import Decimal

from connect_labs.supply_chain.models import DispensingRule, Item


def lines_for(access, commodity, opportunity_id, item_id=None) -> list[dict]:
    """Planned handout lines (to_supply_point_id, quantity, quantity_unit, item_id), one per worker who needs stock."""
    from connect_labs.supply_chain.plan import service

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
    picture = service.compute(program_id, item, horizon_weeks=1, opportunity_ids=[opportunity_id])
    unit = picture["forecast"]["unit"]
    return [
        {
            "to_supply_point_id": row["supply_point_id"],
            "quantity": str(Decimal(row["to_issue"])),
            "quantity_unit": unit,
            "item_id": item.pk,
        }
        for row in picture["issue"]
        if row["to_issue"] > 0
    ]
