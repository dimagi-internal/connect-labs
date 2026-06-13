"""Build user_data.json (the FLW roster) from manifest personas."""

from __future__ import annotations

from typing import Any

from .manifest import FlwPersona


def build_user_data(
    personas: list[FlwPersona],
    visits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    last_active: dict[str, str] = {}
    for v in visits:
        u = v["username"]
        d = v["visit_date"]
        if u not in last_active or d > last_active[u]:
            last_active[u] = d

    rows: list[dict[str, Any]] = []
    for p in personas:
        rows.append(
            {
                "username": p.id,
                "name": p.display_name or p.id,
                "last_active": last_active.get(p.id),
                "archetype": p.archetype,
            }
        )
    return rows
