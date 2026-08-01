"""How a supplier has actually performed, fed back into the next award.

This module closes the one loop the rest of the app leaves open.

Procurement decides on price and a technical score somebody typed. Execution
then records, for every consignment that supplier moved, whether it arrived when
it said it would and whether the quantity reconciled. Those two halves have
never met: a supplier who bid three percent cheaper and then delivered eleven
days late across four contracts, twice short, ranked identically to one who
delivered clean. The award view could not see it, so the next award repeated it.

**Only MEASURED lateness counts here.** ``Milestone.delta_basis`` distinguishes a
leg that actually arrived late from one merely *forecast* to arrive late, and a
supplier's record must not be marked against a forecast that has not happened —
that would let a pessimistic ETA damage a supplier who then arrives on time. A
consignment still in transit contributes nothing to the record until it lands.

Everything here is derived from the append-only event log and the milestone
rail, so a performance figure is reconstructable from the same rows the rest of
the product argues from. Nothing is stored.
"""
from datetime import date

from ..models import Discrepancy, Milestone

# A supplier with fewer arrivals than this has a record, but not yet a rate:
# one late delivery out of one is 100% late, which is true and useless. Below
# the floor the figures are reported as counts and the rate is withheld, because
# a rate a reader cannot rely on is worse than no rate.
MIN_ARRIVALS_FOR_RATE = 3

# Within this many days of plan counts as on time. Humanitarian corridors do not
# run to the hour, and a threshold of zero would score a truck that arrived the
# following morning as late.
ON_TIME_GRACE_DAYS = 1.0


def _arrival_milestones(org_ids=None):
    """Arrival legs that have actually happened, by supplier.

    ``actual_at`` is the filter that matters: a leg with only an estimate has
    not arrived, so it has nothing to say about how this supplier performs.
    """
    qs = (
        Milestone.objects.filter(kind=Milestone.Kind.ARRIVE, actual_at__isnull=False, planned_at__isnull=False)
        .select_related("shipment__contract__org", "node")
        .order_by("actual_at")
    )
    if org_ids is not None:
        qs = qs.filter(shipment__contract__org_id__in=list(org_ids))
    return qs


def supplier_performance(org, as_of=None):
    """One supplier's delivered record, or None when they have never delivered.

    Returns counts always and rates only above ``MIN_ARRIVALS_FOR_RATE``, so a
    first-time bidder is reported as a first-time bidder rather than as a
    supplier with a perfect record.
    """
    as_of = as_of or date.today()
    arrivals = list(_arrival_milestones([org.id]))
    if not arrivals:
        return {
            "org_id": org.id,
            "org_name": org.legal_name,
            "arrivals": 0,
            "on_time": 0,
            "late": 0,
            "on_time_rate": None,
            "mean_days_late": None,
            "worst_days_late": None,
            "short_receipts": 0,
            "cartons_short": 0.0,
            "short_receipt_rate": None,
            "has_record": False,
            "basis": "No delivery on record with OES. Price and technical score are the only evidence available.",
        }

    deltas = [m.delta_days for m in arrivals if m.delta_days is not None]
    late = [d for d in deltas if d > ON_TIME_GRACE_DAYS]
    on_time = len(deltas) - len(late)

    # Short receipts are counted against the CONSIGNMENTS this supplier moved,
    # which is the denominator a reader expects — not against the number of
    # discrepancies, which would make a supplier with one bad consignment look
    # the same as one with twenty.
    shipment_ids = {m.shipment_id for m in arrivals}
    discrepancies = list(Discrepancy.objects.filter(shipment_id__in=shipment_ids))
    cartons_short = float(sum(float(d.shortfall or 0) for d in discrepancies))

    enough = len(deltas) >= MIN_ARRIVALS_FOR_RATE
    return {
        "org_id": org.id,
        "org_name": org.legal_name,
        "arrivals": len(deltas),
        "on_time": on_time,
        "late": len(late),
        "on_time_rate": round((on_time / len(deltas)) * 100, 1) if enough else None,
        "mean_days_late": round(sum(late) / len(late), 1) if late else 0.0,
        "worst_days_late": round(max(late), 1) if late else 0.0,
        "short_receipts": len(discrepancies),
        "cartons_short": cartons_short,
        "short_receipt_rate": (round((len(discrepancies) / len(arrivals)) * 100, 1) if enough else None),
        "has_record": True,
        "basis": (
            f"{len(deltas)} arrival{'' if len(deltas) == 1 else 's'} measured against the plan each "
            f"consignment was awarded under. Only legs that have actually arrived count — a consignment "
            f"still in transit is not held against a supplier. On time allows "
            f"{ON_TIME_GRACE_DAYS:g} day of grace. Short receipts are counts that did not reconcile "
            f"against their despatch advice at the delivery place."
        ),
    }


def performance_by_org(org_ids, as_of=None):
    """``supplier_performance`` for several suppliers, in one pass.

    The bid comparison needs every bidder's record at once and would otherwise
    issue a query per row per lot.
    """
    from ..models import SupplierOrg

    org_ids = list(org_ids)
    if not org_ids:
        return {}
    orgs = {o.id: o for o in SupplierOrg.objects.filter(id__in=org_ids)}

    arrivals_by_org = {}
    for m in _arrival_milestones(org_ids):
        arrivals_by_org.setdefault(m.shipment.contract.org_id, []).append(m)

    shipment_ids = [m.shipment_id for rows in arrivals_by_org.values() for m in rows]
    discrepancies_by_shipment = {}
    for d in Discrepancy.objects.filter(shipment_id__in=shipment_ids):
        discrepancies_by_shipment.setdefault(d.shipment_id, []).append(d)

    out = {}
    for org_id, org in orgs.items():
        rows = arrivals_by_org.get(org_id) or []
        if not rows:
            out[org_id] = supplier_performance(org, as_of=as_of)
            continue
        deltas = [m.delta_days for m in rows if m.delta_days is not None]
        late = [d for d in deltas if d > ON_TIME_GRACE_DAYS]
        discs = [d for m in rows for d in discrepancies_by_shipment.get(m.shipment_id, [])]
        enough = len(deltas) >= MIN_ARRIVALS_FOR_RATE
        out[org_id] = {
            "org_id": org_id,
            "org_name": org.legal_name,
            "arrivals": len(deltas),
            "on_time": len(deltas) - len(late),
            "late": len(late),
            "on_time_rate": (round(((len(deltas) - len(late)) / len(deltas)) * 100, 1) if enough else None),
            "mean_days_late": round(sum(late) / len(late), 1) if late else 0.0,
            "worst_days_late": round(max(late), 1) if late else 0.0,
            "short_receipts": len(discs),
            "cartons_short": float(sum(float(d.shortfall or 0) for d in discs)),
            "short_receipt_rate": (round((len(discs) / len(rows)) * 100, 1) if enough else None),
            "has_record": True,
            "basis": (
                f"{len(deltas)} arrival{'' if len(deltas) == 1 else 's'} measured against the plan each "
                f"consignment was awarded under. Only legs that have actually arrived count — a "
                f"consignment still in transit is not held against a supplier. On time allows "
                f"{ON_TIME_GRACE_DAYS:g} day of grace. Short receipts are counts that did not reconcile "
                f"against their despatch advice at the delivery place."
            ),
        }
    return out


def delivery_history(org, limit=12):
    """The consignments behind a supplier's figures, newest first.

    A rate nobody can drill into is a rate nobody will act against, and this is
    the evidence a procurement officer would be asked for if they declined a
    cheaper bid on delivery grounds.
    """
    rows = []
    for m in reversed(list(_arrival_milestones([org.id])[: limit * 3])):
        shipment = m.shipment
        rows.append(
            {
                "shipment_id": shipment.id,
                "reference": shipment.reference,
                "destination": m.node.name,
                "planned_at": m.planned_at.date().isoformat() if m.planned_at else None,
                "actual_at": m.actual_at.date().isoformat() if m.actual_at else None,
                "days_late": m.delta_days,
                "on_time": (m.delta_days or 0) <= ON_TIME_GRACE_DAYS,
                "contract_reference": shipment.contract.reference if shipment.contract_id else None,
            }
        )
        if len(rows) >= limit:
            break
    return rows


__all__ = [
    "MIN_ARRIVALS_FOR_RATE",
    "ON_TIME_GRACE_DAYS",
    "delivery_history",
    "performance_by_org",
    "supplier_performance",
]
