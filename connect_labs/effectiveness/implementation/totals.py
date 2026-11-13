"""What a set of opportunities achieved together: a portfolio, a programme, a partner or a country."""

from __future__ import annotations

from connect_labs.effectiveness.implementation.catalog import STATUS_MODELLED


def rollup(rows: list[dict]) -> dict:
    """What a set of opportunities achieved together: the portfolio, a programme, a partner or a country.

    The one place a group's figures are added up, so the page's tiles, the programme table, the JSON and
    the agent tool cannot disagree. Each dollar counts once (a pooled row's spend is already inside its
    delivery rows), and the multiple is total benefit over total cost, so it is spend-weighted.
    """
    modelled = [r for r in rows if r["status"] == STATUS_MODELLED]
    spend_modelled = sum(r["total_usd"] for r in modelled)
    # What the multiple is weighted by: the cost a row was valued at. That is its actual cost unless a what-if
    # re-priced it (extra cost, or the models' own prices), which says so in ``cost_used``.
    cost = sum(r.get("cost_used") or r["total_usd"] for r in modelled)
    deaths = sum(r["deaths_averted"] for r in modelled)
    return {
        "opportunities": len(rows),
        "modelled": len(modelled),
        "clearing_bar": sum(1 for r in modelled if r["clears_bar"]),
        "spend": round(sum(r["own_usd"] for r in rows), 2),
        "spend_modelled": round(spend_modelled, 2),
        "people": sum(r["reached"] for r in modelled),
        "deaths_averted": round(deaths, 2),
        "cost_per_death": (cost / deaths) if deaths else None,
        "portfolio_x": (
            (sum(r["x"] * (r.get("cost_used") or r["total_usd"]) for r in modelled) / cost) if cost else None
        ),
    }


def programmes(rows: list[dict]) -> list[dict]:
    """The same roll-up per Connect programme, biggest spend first.

    A programme is Connect's own grouping (``PulseProgram``); opportunities with none are gathered as
    "No programme". Its partners and the models that value it are listed so a reader sees what is in it.
    """
    from connect_labs.pulse.models import PulseProgram

    names = dict(
        PulseProgram.objects.filter(program_id__in={r["program_id"] for r in rows}).values_list("program_id", "name")
    )
    groups: dict[int | None, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["program_id"], []).append(r)
    out = []
    for pid, members in groups.items():
        out.append(
            {
                "program_id": pid,
                "name": names.get(pid) or (f"Programme {pid}" if pid is not None else "No programme"),
                "partners": sorted({r["org_slug"] for r in members if r["org_slug"]}),
                "models": sorted({r["model"] for r in members if r.get("model")}),
                "opportunity_ids": [r["id"] for r in members],
                **rollup(members),
            }
        )
    out.sort(key=lambda g: -g["spend"])
    return out
