"""What the Distribution page shows: handouts planned and done, and the deliveries to stores before them.

Reads only through operations, so the page sees what an API or MCP caller sees.
"""

from decimal import Decimal


def _dec(value):
    return Decimal(str(value)) if value not in (None, "") else None


def _line_view(line, points):
    planned = _dec(line["quantity"])
    given = _dec(line.get("quantity_given"))
    received = _dec(line.get("quantity_received"))
    place = points.get(line["to_supply_point_id"]) or {}
    return {
        **line,
        "name": place.get("name") or line.get("connect_username") or f"place {line['to_supply_point_id']}",
        # The worker's name and their username are often the same word: say it once.
        "show_username": bool(line.get("connect_username")) and line.get("connect_username") != place.get("name"),
        "planned_qty": planned,
        "given_qty": given,
        "received_qty": received,
        # A planned quantity of 0 is a handout nobody planned.
        "unplanned": planned == 0 and given is not None,
        "short_given": given is not None and planned and given < planned,
        "over_given": given is not None and planned and given > planned,
        "short_received": received is not None and given is not None and received < given,
        "over_received": received is not None and given is not None and received > given,
        "awaiting_receipt": given is not None and given > 0 and received is None,
    }


def handouts(runs, points, partner_of=None) -> dict:
    """Split runs into those still to be confirmed and those done, with each line's differences."""
    waiting, done = [], []
    for run in runs:
        lines = [_line_view(line, points) for line in run["lines"]]
        view = {
            **run,
            "lines": lines,
            "partner_name": (partner_of or {}).get(run["supply_point_id"]),
            "awaiting_receipt": sum(1 for line in lines if line["awaiting_receipt"]),
            "differences": sum(1 for line in lines if line["short_given"] or line["over_given"] or line["unplanned"]),
            "not_received": sum(1 for line in lines if line["short_received"] or line["over_received"]),
            "to_confirm": sum(1 for line in lines if line["given_qty"] is None),
        }
        (waiting if run["status"] != "confirmed" else done).append(view)
    # Soonest handout day first among the waiting; most recent first among the done.
    waiting.sort(key=lambda r: r["distributed_on"] or "")
    return {"waiting": waiting, "done": done}


def deliveries(consignments, transfers, points) -> list[dict]:
    """Stock moved from a store to a facility or another store (not to a field worker), newest first.

    Consignments are the ones with a journey (dispatched, then received, perhaps short); plain
    transfers are stock that moved with no journey recorded. Both are the same step in the chain:
    warehouse to facility.
    """
    rows = []
    for c in consignments:
        rows.append(
            {
                "kind": "consignment",
                "id": c["id"],
                "date": c["dispatched_on"],
                "from": points.get(c["from_supply_point_id"], {}).get("name", ""),
                "to": points.get(c["to_supply_point_id"], {}).get("name", ""),
                "quantity": c["quantity"],
                "unit": c["quantity_unit"],
                "status": c["status"],
                "expected_on": c["expected_on"],
                "received_on": c["received_on"],
                "quantity_received": c["quantity_received"],
                "short": (c["quantity_received"] is not None and _dec(c["quantity_received"]) < _dec(c["quantity"])),
                "source": c["source"],
                "witnessed": c["witnessed"],
            }
        )
    for m in transfers:
        origin = points.get(m["from_supply_point_id"]) or {}
        target = points.get(m["to_supply_point_id"]) or {}
        if not origin or not target or target.get("kind") == "user_held" or origin.get("kind") == "user_held":
            continue
        rows.append(
            {
                "kind": "transfer",
                "id": m["id"],
                "date": m["occurred_on"],
                "from": origin["name"],
                "to": target["name"],
                "quantity": m["quantity"],
                "unit": m["quantity_unit"],
                "status": "moved",
                "expected_on": None,
                "received_on": None,
                "quantity_received": None,
                "short": False,
                "source": m["source"],
                "witnessed": m["witnessed"],
            }
        )
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    return rows
