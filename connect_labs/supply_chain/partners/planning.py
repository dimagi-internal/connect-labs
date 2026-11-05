"""Looking ahead: planning alerts and the size of a delivery to a shared facility.

Both read the forecast (`stock.services.forecast`), the one place the programme's expected need
per place is worked out, so an alert and a suggestion cannot disagree with the Forecast page.

**Planning checks are opt-in.** They run the forecast, which is not cheap, so `checks.run_checks` runs
them only when a caller names them in `kinds` (an alert subscription does); a page that lists every check
does not pay for them on every load (cf. #2825).
"""

from datetime import date, timedelta
from decimal import Decimal

from connect_labs.supply_chain.values import decimal_string

PLANNING_KINDS = ("delivery_after_stockout", "tender_start_by")

# How many days before a tender's start_by date the warning begins.
START_WARNING_DAYS = 14

# A shared facility is sent a few days of what its field workers use, not a month's: less on a shelf
# many people can reach is less to divert (Sophie: "keep stock at shared facilities low").
DEFAULT_COVER_DAYS = 7


def _rule_items(program_id):
    from connect_labs.supply_chain.models import DispensingRule

    seen, items = set(), []
    for rule in DispensingRule.objects.filter(program_id=program_id, status="active").select_related("item"):
        if rule.item_id not in seen:
            seen.add(rule.item_id)
            items.append(rule.item)
    return items


def _forecast(program_id, item, today):
    from connect_labs.supply_chain.stock.services import forecast

    return forecast.forecast(program_id, item, on_date=today, horizon_weeks=8)


def planning_checks(access, as_of=None, kinds=None) -> list[dict]:
    """The planning checks asked for in `kinds` (all of PLANNING_KINDS when None)."""
    from connect_labs.supply_chain.checks import _check

    wanted = set(kinds or PLANNING_KINDS) & set(PLANNING_KINDS)
    today = as_of or date.today()
    out = []
    if "delivery_after_stockout" in wanted:
        out += _deliveries_after_stockout(access, today, _check)
    if "tender_start_by" in wanted:
        out += _tenders_to_start(access, today, _check)
    return out


def _deliveries_after_stockout(access, today, _check) -> list[dict]:
    """A place the forecast runs dry, with an order for it that arrives after that day."""
    from connect_labs.supply_chain.models import SupplyPoint
    from connect_labs.supply_chain.stock.services.network import _expected_inbound

    out = []
    for item in _rule_items(access.program_id):
        try:
            result = _forecast(access.program_id, item, today)
        except Exception:  # noqa: BLE001 -- a forecast that cannot be worked out is no finding
            continue
        dry = {
            s["supply_point_id"]: s["runs_dry_on"]
            for s in result.get("stores", [])
            if s.get("runs_dry_on") is not None
        }
        if not dry:
            continue
        points = list(SupplyPoint.objects.filter(program_id=access.program_id, pk__in=dry))
        inbound = _expected_inbound(access.program_id, points, item=item, as_of=today)
        for point in points:
            runs_dry_on = date.fromisoformat(dry[point.pk])
            for order in inbound.get(point.pk, []):
                arrives = order.get("expected_on")
                if arrives is None or arrives <= runs_dry_on:
                    continue
                out.append(
                    _check(
                        "delivery_after_stockout",
                        subject_type="supply_point",
                        subject_id=point.pk,
                        label=f"{point.name} — order {order['reference'] or order['contract_id']}",
                        audience="internal",
                        facts={
                            "runs_dry_on": runs_dry_on.isoformat(),
                            "arrives_on": arrives.isoformat(),
                            "days_without_stock": (arrives - runs_dry_on).days,
                            "order": order["reference"] or str(order["contract_id"]),
                            "contract_id": order["contract_id"],
                            "supplier": order["supplier"]["name"],
                            "item": item.name,
                        },
                        since=runs_dry_on,
                        as_of=today,
                    )
                )
    return out


def _tenders_to_start(access, today, _check) -> list[dict]:
    """A tender still in draft whose start-by day (counted back from its needed-by date) is near or past.

    The day comes from `procurement.services.timing`, the same count the tender page shows; a tender with
    no needed-by date, or no lead time to count back with, has none and is not a finding.
    """
    from connect_labs.supply_chain.models import Tender
    from connect_labs.supply_chain.procurement.services.timing import timing

    out = []
    for tender in Tender.objects.filter(program_id=access.program_id, status="draft", needed_by__isnull=False):
        lead_times = list(
            tender.quotes.filter(voided=False, superseded_by__isnull=True).values_list("lead_time_days", flat=True)
        )
        counted = timing(tender, lead_times, today=today)
        start_by = counted["start_by"]
        if start_by is None or start_by > today + timedelta(days=START_WARNING_DAYS):
            continue
        out.append(
            _check(
                "tender_start_by",
                subject_type="tender",
                subject_id=tender.pk,
                label=tender.label,
                audience="internal",
                facts={
                    "needed_by": tender.needed_by.isoformat(),
                    "start_by": start_by.isoformat(),
                    "days_late": max((today - start_by).days, 0),
                    "note": counted["note"],
                },
                since=start_by - timedelta(days=START_WARNING_DAYS),
                as_of=today,
            )
        )
    return out


# ---- sizing a delivery to a shared facility ------------------------------------------------------------


def _stock_amount(value) -> Decimal:
    """A forecast stock figure as a number: it arrives as {"amount", "unit"}, or None when unknown."""
    if isinstance(value, dict):
        value = value.get("amount")
    return Decimal(0) if value in (None, "") else Decimal(str(value))


def delivery_suggestions(program_id, item, *, cover_days=DEFAULT_COVER_DAYS, today=None, org_id=None) -> dict:
    """Suggested delivery to each facility: `cover_days` of what the field workers it supplies use, less what it holds.

    A suggestion, not an order: nothing is recorded. The need is the forecast's expected need for the
    first week, per day; a facility already holding more than that cover is suggested nothing. Only
    facilities (the shared places) are sized -- a warehouse is sized by what it supplies, a field worker
    by the handout run. `org_id` narrows to one partner's facilities.
    """
    from connect_labs.supply_chain.stock.services.issues import partner_by_point

    today = today or date.today()
    result = _forecast(program_id, item, today)
    owner = partner_by_point(program_id) if org_id is not None else {}
    rows = []
    for store in result.get("stores", []):
        if store["kind"] != "facility" or not store["workers"]:
            continue
        if org_id is not None and owner.get(store["supply_point_id"]) != org_id:
            continue
        weekly = Decimal(store["need"][0]) if store["need"] and store["need"][0] is not None else Decimal(0)
        per_day = weekly / 7
        wanted = per_day * cover_days
        held = _stock_amount(store["own_on_hand"])
        suggested = max(Decimal(0), wanted - max(held, Decimal(0)))
        rows.append(
            {
                "supply_point_id": store["supply_point_id"],
                "name": store["name"],
                "workers": store["workers"],
                "uses_per_day": str(per_day.quantize(Decimal("0.1"))),
                "cover_days": cover_days,
                "holds": decimal_string(held),
                "suggested": str(suggested.quantize(Decimal("1"))),
                "runs_dry_on": store.get("runs_dry_on"),
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    return {
        "item_id": item.pk,
        "item_name": item.name,
        "unit": result.get("unit"),
        "cover_days": cover_days,
        "facilities": rows,
    }
