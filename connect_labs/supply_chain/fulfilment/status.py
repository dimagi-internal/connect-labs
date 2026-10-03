"""An order's status view: where its goods and money are, as facts, no prose.

`order_status` builds the top of an order's page from what the order view has
already read: the six-step stage bar (Awarded, Ordered, Dispatched, At
customs / In transit, Received, Paid), four tiles (received against ordered,
billed against agreed, paid, days late) and the status chip. The moves -- On
us and On the supplier or forwarder -- come from `moves.contract_moves` and
nothing else, so the order page and the overview cannot disagree.

Every input is the operations' dict shape (`contract_get`, `contract_match`,
`shipment_list`, ...), so the page needs no second read of its own.
"""

from datetime import date

from connect_labs.supply_chain.records import IN_TRANSIT_STATUSES, latest_moving_shipment
from connect_labs.supply_chain.values import day_text, money_digits, quantity_digits, unit_noun

# Chip tones, as the tender page's (tailwind.css .status-chip--<tone>).
PRIMARY, OURS, THEIRS, NEUTRAL, ERROR = "primary", "ours", "theirs", "neutral", "error"

RECEIVED_IN_FULL = ("fully_received", "over_received", "arrived_with_refusals", "shortfall_covered")


def _amount(cell):
    if not isinstance(cell, dict) or cell.get("amount") in (None, ""):
        return None
    try:
        return float(cell["amount"])
    except (TypeError, ValueError):
        return None


def _day(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    # A non-breaking space, so a stage note never wraps between the day and its month.
    return f"{value.day}\u00a0{value.strftime('%b')}"


def _money(cell) -> str:
    if not isinstance(cell, dict) or cell.get("amount") in (None, ""):
        return "—"
    return f"{cell.get('currency') or ''} {money_digits(cell['amount'])}".strip()


def _dispatched(shipment) -> bool:
    return bool(shipment.get("dispatched_on")) or shipment.get("status") in (*IN_TRANSIT_STATUSES, "delivered")


def _stage_states(index: int, count: int = 6) -> list[str]:
    return ["done" if i < index else "now" if i == index else "todo" for i in range(count)]


def order_status(
    contract,
    *,
    award=None,
    shipments=(),
    receipts=(),
    invoices=(),
    match=None,
    invoice_above=None,
    contract_late=None,
    held_on_us=(),
    header_status=None,
    held_since=None,
    today=None,
) -> dict:
    """{"chip", "stages", "tiles"} for one order (dicts as the operations return them)."""
    match = match or {}
    priced = contract.get("consideration", "priced") == "priced"
    received_any = bool(receipts) or (_amount(match.get("received")) or 0) > 0
    received_full = match.get("status") in RECEIVED_IN_FULL or contract.get("status") in ("received", "closed")
    live_invoices = [i for i in invoices if i.get("status") != "rejected"]
    paid_all = bool(live_invoices) and all(i.get("status") == "paid" for i in live_invoices)
    if not priced:
        paid_all = received_full  # nothing is billed on a donated or bundled order

    moving = latest_moving_shipment([s for s in shipments if s.get("status") != "delivered"])
    transit_name = "In transit"
    if moving is not None and moving.get("status") in ("at_customs", "cleared", "lost"):
        transit_name = str(moving["status"]).replace("_", " ").capitalize()
    dispatched = [s for s in shipments if _dispatched(s)]
    first_dispatch = min((str(s.get("dispatched_on")) for s in dispatched if s.get("dispatched_on")), default="")
    last_receipt = max((str(r.get("received_on")) for r in receipts if r.get("received_on")), default="")

    done = [
        True,  # an order exists, so whatever decided it is behind it
        contract.get("status") not in ("draft", None, ""),
        bool(dispatched) or received_any,
        received_any and (moving is None or received_full),
        received_full,
        paid_all,
    ]
    index = next((i for i, d in enumerate(done) if not d), len(done))

    notes = [
        (
            (award.get("supplier_name") or "awarded")
            + (f", {_day(award.get('decided_on'))}" if award.get("decided_on") else "")
            if award
            else "direct order"
        ),
        f"PO {contract['reference']}" if contract.get("reference") else _day(contract.get("signed_on")) or "—",
        _day(first_dispatch) or "—",
        (
            ("held, waiting on us" if held_on_us else _day(moving.get("dispatched_on")) or "under way")
            if moving is not None and not received_full
            else "—"
        ),
        (
            f"{_day(last_receipt)}" + ("" if received_full else ", part")
            if last_receipt
            else ("in full" if received_full else "—")
        ),
        _paid_note(match, priced, paid_all, invoice_above, contract),
    ]
    names = ["Awarded", "Ordered", "Dispatched", transit_name, "Received", "Paid"]
    stages = [{"name": n, "note": note, "state": s} for n, note, s in zip(names, notes, _stage_states(index))]

    # Tiles.
    ordered, received = match.get("ordered") or {}, match.get("received") or {}
    unit = ordered.get("unit") or contract.get("quantity_unit")
    if _amount(ordered) is not None and _amount(received) is not None and received.get("unit") == ordered.get("unit"):
        received_value = f"{quantity_digits(received['amount'])} / {quantity_digits(ordered['amount'])}"
    elif _amount(ordered) is not None:
        received_value = f"— / {quantity_digits(ordered['amount'])}"
    else:
        received_value = "—"
    refused = _amount(match.get("refused")) or 0
    received_sub = " · ".join(
        p for p in (unit_noun(unit, 2) if unit else "", f"{quantity_digits(refused)} refused" if refused else "") if p
    )

    above = (invoice_above or {}).get("facts") or {}
    total = next((line for line in above.get("above") or [] if line.get("field") == "total"), None)
    currency = above.get("currency") or match.get("currency") or ""
    if not priced:
        billed_tile = {"label": "Billed vs agreed", "value": "—", "sub": "not billed on its own", "tone": ""}
    elif total is not None:
        billed_tile = {
            "label": "Billed vs agreed",
            "value": f"{currency} {money_digits(total['billed'])}".strip(),
            "sub": f"+{currency} {money_digits(total['difference'])} above {money_digits(total['agreed'])} agreed",
            # Neutral ground: whose move it is rides on the chip, so the tile does not read as an On us item.
            "tone": "",
            "chips": [{"label": "invoice check", "tone": OURS}],
        }
    else:
        billed = _amount(match.get("billed_amount")) or 0
        billed_tile = {
            "label": "Billed vs agreed",
            "value": _money(match.get("billed_amount")) if billed else "—",
            "sub": "within agreed" if billed else "nothing billed",
            "tone": "",
        }

    if not priced:
        paid_tile = {
            "label": "Paid",
            "value": "—",
            "sub": "donated" if contract.get("consideration") == "in_kind" else "in a setup fee",
            "tone": "",
        }
    else:
        paid_pct = _paid_percent(match, invoice_above, contract)
        paid_tile = {
            "label": "Paid",
            "value": _money(match.get("paid_amount")) if _amount(match.get("paid_amount")) else "—",
            "sub": (
                f"safe to pay now {_money(match.get('payable_now'))}"
                if _amount(match.get("payable_now"))
                else "settled" if paid_all else f"{paid_pct}% of agreed" if paid_pct is not None else ""
            ),
            "tone": "",
            # The invoice check, as a chip on the money it holds back -- not on the stage bar.
            "chips": (
                [{"label": "balance withheld: invoice above agreed", "tone": OURS}]
                if invoice_above and not paid_all
                else []
            ),
        }

    late = (contract_late or {}).get("facts") or {}
    if late:
        due = date.fromisoformat(late["expected_on"]) if late.get("expected_on") else None
        # Whose days they are: the supplier's, to its dispatch (all of them while nothing
        # is dispatched); a hold on us, since the goods sit waiting on our documents.
        dispatch_days = [
            date.fromisoformat(str(s["dispatched_on"])[:10]) for s in dispatched if s.get("dispatched_on")
        ]
        chips, lines = [], []
        if due is not None:
            if dispatch_days:
                supplier_days = max(0, (min(dispatch_days) - due).days)
            else:
                supplier_days = int(late.get("days_late") or 0)
            if held_on_us and held_since is not None and today is not None and held_since >= due:
                # Both dates recorded: the supplier's days run from due to the day the goods
                # reached customs; the days since then are the hold on us.
                lines = [
                    {
                        "label": "supplier",
                        "words": f"due {_day(due)} → at customs {_day(held_since)}",
                        "days": (held_since - due).days,
                        "tone": THEIRS,
                    },
                    {
                        "label": "on us",
                        "words": f"held since {_day(held_since)}",
                        "days": max(0, (today - held_since).days),
                        "tone": OURS,
                    },
                ]
            elif held_on_us:
                # No recorded day the hold began: the recorded facts only, no split.
                chips.append({"label": "held on us", "tone": OURS})
            else:
                chips.append({"label": f"supplier {supplier_days} d", "tone": THEIRS})
        late_tile = {
            "label": "Days late",
            "value": str(late.get("days_late", "")),
            "sub": "" if lines else f"due {day_text(due)}" if due else "",
            "tone": "",
            "chips": chips,
            "lines": lines,
        }
    else:
        late_tile = {
            "label": "Days late",
            "value": "0",
            "sub": "on time" if not received_full else "arrived",
            "tone": "",
        }

    tiles = [
        {"label": "Received / ordered", "value": received_value, "sub": received_sub, "tone": ""},
        billed_tile,
        paid_tile,
        late_tile,
    ]

    for tile in tiles:
        tile.setdefault("chips", [])
        tile.setdefault("lines", [])

    chip = None
    if header_status:
        tone = header_status.get("tone")
        chip = {
            "label": header_status["label"],
            "tone": (
                OURS
                if held_on_us and tone == "warning"
                else ERROR if tone == "warning" else PRIMARY if tone == "done" else NEUTRAL
            ),
        }
    return {"chip": chip, "stages": stages, "tiles": tiles}


def _agreed_total(match, invoice_above, contract):
    above = (invoice_above or {}).get("facts") or {}
    total = next((line for line in above.get("above") or [] if line.get("field") == "total"), None)
    if total is not None and total.get("agreed") not in (None, ""):
        return float(total["agreed"])
    try:
        return float(contract.get("unit_price")) * float(contract.get("quantity"))
    except (TypeError, ValueError):
        return None


def _paid_percent(match, invoice_above, contract):
    paid, agreed = _amount((match or {}).get("paid_amount")), _agreed_total(match, invoice_above, contract)
    if not paid or not agreed:
        return None
    return round(100 * paid / agreed)


def _paid_note(match, priced, paid_all, invoice_above, contract) -> str:
    """The Paid step's facts: payments only ("advance USD 53,400 · 50%"); the invoice check is a chip on the tiles."""
    if not priced:
        return "nothing to pay"
    if paid_all:
        return "paid"
    paid = _amount((match or {}).get("paid_amount"))
    if not paid:
        return "—"
    pct = _paid_percent(match, invoice_above, contract)
    words = "advance " if (match or {}).get("advance_state") else ""
    return f"{words}{_money(match.get('paid_amount'))}" + (f" · {pct}%" if pct is not None else "")


def held_since(shipment) -> date | None:
    """The day a shipment's move to customs was recorded (its history), or None when no change says so."""
    from django.contrib.contenttypes.models import ContentType

    from connect_labs.supply_chain.history.models import Revision
    from connect_labs.supply_chain.models import Shipment

    if not shipment or shipment.get("status") != "at_customs" or not shipment.get("id"):
        return None
    for revision in Revision.objects.filter(
        content_type=ContentType.objects.get_for_model(Shipment),
        object_id=str(shipment["id"]),
        changes__has_key="status",
    ).order_by("-recorded_at"):
        pair = revision.changes.get("status") or [None, None]
        if isinstance(pair, list) and len(pair) == 2 and pair[1] == "at_customs":
            return revision.recorded_at.date()
    return None
