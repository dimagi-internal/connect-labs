"""Build completed_works.json and completed_module.json from synthetic visits."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _payment_unit_for_deliver(deliver_unit_id: int, payment_units: list[dict]) -> int | None:
    for pu in payment_units:
        if deliver_unit_id in pu.get("deliver_units", []):
            return pu["id"]
    return None


def build_works_and_modules(
    visits: list[dict[str, Any]],
    payment_units: list[dict[str, Any]],
) -> tuple[list[dict], list[dict]]:
    works: list[dict] = []
    seen_modules: set[tuple[str, int]] = set()
    modules: list[dict] = []
    counts: dict[tuple[str, int], int] = defaultdict(int)

    for v in visits:
        if v.get("status") != "approved":
            continue
        deliver_unit_id = v.get("deliver_unit_id")
        if deliver_unit_id is None:
            continue
        pu_id = _payment_unit_for_deliver(deliver_unit_id, payment_units)
        if pu_id is None:
            continue
        works.append(
            {
                "id": f"{v['id']}-cw",
                "username": v["username"],
                "payment_unit_id": pu_id,
                "completed_at": v["visit_date"],
                "approved": True,
            }
        )
        key = (v["username"], pu_id)
        counts[key] += 1
        if key not in seen_modules:
            seen_modules.add(key)
            modules.append(
                {
                    "id": f"{v['username']}-{pu_id}-cm",
                    "username": v["username"],
                    "payment_unit_id": pu_id,
                    "completed": True,
                }
            )

    for m in modules:
        m["count"] = counts[(m["username"], m["payment_unit_id"])]

    return works, modules
