"""How much to buy for each partner: target cases x the course, plus a buffer.

Sophie Feintuch's RUTF feedback, "Help working out how much to buy": nothing helped size a purchase per
partner. This is arithmetic on three inputs the programme names (target cases, the course in base units,
a buffer) and is shown step by step so a person can check it. The course defaults to the forecast's
expected course once enough children have finished, else the protocol's (stock.services.forecast.course_size);
a person can override it. It does not net off stock already held: say so, or subtract it.
"""

from __future__ import annotations

import math
from decimal import Decimal

DEFAULT_BUFFER_PERCENT = Decimal("10")


def size_purchase(partners, course_units, buffer_percent=DEFAULT_BUFFER_PERCENT, base_per_pack=None) -> dict:
    """Per partner: target cases x course, plus the buffer, in base units and (when the pack is known) in packs.

    `partners` are {org_id, org_name, target_cases, buffer_percent?}. Raises on a missing or negative figure
    rather than assuming one.
    """
    course = Decimal(str(course_units))
    if course <= 0:
        raise ValueError("the course must be more than zero base units")
    rows, total_units, total_packs = [], Decimal(0), 0
    for partner in partners:
        cases = Decimal(str(partner["target_cases"]))
        if cases < 0:
            raise ValueError(f"{partner.get('org_name') or partner.get('org_id')}: target cases cannot be negative")
        buffer = Decimal(str(partner.get("buffer_percent", buffer_percent)))
        if buffer < 0:
            raise ValueError("the buffer cannot be negative")
        units = (cases * course * (1 + buffer / 100)).to_integral_value(rounding="ROUND_CEILING")
        packs = math.ceil(units / Decimal(base_per_pack)) if base_per_pack else None
        total_units += units
        total_packs += packs or 0
        rows.append(
            {
                "org_id": partner.get("org_id"),
                "org_name": partner.get("org_name", ""),
                "target_cases": str(cases.normalize() if cases != cases.to_integral() else int(cases)),
                "course_units": str(course),
                "buffer_percent": str(buffer),
                "units": str(units),
                "packs": packs,
                "working": (
                    f"{int(cases):,} cases x {course:g} + {buffer:g}% buffer = {int(units):,} base units"
                    + (f" = {packs:,} packs of {int(base_per_pack)}" if packs is not None else "")
                    if cases == cases.to_integral()
                    else ""
                ),
            }
        )
    return {
        "partners": rows,
        "total_units": str(total_units),
        "total_packs": total_packs if base_per_pack else None,
        "base_per_pack": base_per_pack,
    }


SETTING = "purchase_packs_per_child"
DEFAULT_PACKS_PER_CHILD = Decimal("1")


def packs_per_child(program_id) -> Decimal:
    """How many packs (cartons) one child is sized at: the programme's own setting, else one.

    Sophie Feintuch's round-2 walk, "Purchase sizing": the default was the measured course (40 sachets in the
    demo), which is a floor because the fastest recoveries finish first. A carton a child (150 sachets) is the
    default, and the programme may change it in its Supply settings.
    """
    from connect_labs.scope_config.models import ScopeConfig
    from connect_labs.supply_chain.config import SUPPLY

    row = ScopeConfig.objects.filter(scope_type="program", scope_key=str(program_id), namespace=SUPPLY.key).first()
    raw = (row.data if row else {}).get(SETTING)
    try:
        value = Decimal(str(raw)) if raw is not None else DEFAULT_PACKS_PER_CHILD
    except Exception:  # noqa: BLE001 -- a malformed setting falls back to the default
        return DEFAULT_PACKS_PER_CHILD
    return value if value > 0 else DEFAULT_PACKS_PER_CHILD
