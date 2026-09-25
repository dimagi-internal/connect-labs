"""Did a supplier deliver when they said, and all of it?

The domain recorded what a supplier PROMISED -- `promised_lead_time_days` on
every contract -- and what actually happened -- a receipt, with a date and a
quantity -- and never compared the two. So the supplier directory was a list
of names and "which of these has ever let us down" could not be asked.

OTIF (on time, in full) is the sector's standard answer to that, and it is
computable from what is already stored. Four decisions keep it honest rather
than flattering, and each is a way this measure is commonly got wrong:

**What cannot be judged is not scored.** A contract carrying no promised lead
time is excluded from the rate and counted separately, because scoring it as
a pass would make "never commit to a date" the winning strategy. An order
still on its way is excluded too -- unfinished is not failed -- and counted,
so a supplier cannot look punctual by having everything outstanding.

**On time and in full are reported apart as well as together.** A supplier
who always delivers everything a fortnight late is a different problem from
one who is punctual and short, and a single OTIF figure cannot say which you
have.

**The denominator travels with the rate.** "2 of 2" and "200 of 200" are
different claims and a bare 100% hides which one you hold, so no rate is
returned without the count it came from, and a rate out of nothing is None
rather than zero or one.

**Refused goods were not delivered.** In full means accepted, not shipped.

Deliberately NOT here: a single blended supplier score. The inputs are in
different units -- punctuality, completeness, quality -- and the weighting
between them is a buyer's judgement about what matters on this programme,
not arithmetic. `DomainHomeView` refused to rank for the same reason.
"""

import statistics
from datetime import timedelta

from connect_labs.supply_chain.fulfilment.services.match import three_way_match
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.values import Quantity


def _quantity(figure):
    """A `Quantity`'s amount, or None when the domain could not state one."""
    return figure.amount if isinstance(figure, Quantity) else None


def _promised_on(contract):
    """The day this was due, or None when nobody said.

    Needs BOTH the signing date and the promised lead time: a lead time with
    no start is a duration from nowhere.
    """
    if not contract.signed_on or not contract.promised_lead_time_days:
        return None
    return contract.signed_on + timedelta(days=int(contract.promised_lead_time_days))


def _completed_on(contract):
    """The day the last of it arrived, or None while any is still to come.

    The LAST receipt, not the first: an order half-delivered on time and
    finished a month late was not delivered on time, and taking the earliest
    receipt would say it was.
    """
    dates = [receipt.received_on for receipt in contract.receipts.all() if receipt.received_on]
    return max(dates) if dates else None


def judge_contract(contract) -> dict:
    """One order's verdict, or why it cannot be judged."""
    promised = _promised_on(contract)
    completed = _completed_on(contract)
    match = three_way_match(contract)

    ordered = _quantity(match["ordered"])
    accepted = _quantity(match["received"])
    in_full = ordered is not None and accepted is not None and accepted >= ordered

    if promised is None:
        return {"verdict": "no_promise", "contract_id": contract.pk}
    # Judged on completion, not on today: an order still coming is counted
    # as not yet due rather than as a failure, because the quantity that will
    # eventually arrive is not yet known. Its lateness, if it is late, is
    # already reported by the overdue checks -- which is where lateness
    # belongs, because that is a fact about today and this is a record of how
    # orders finished.
    if completed is None:
        return {"verdict": "not_yet_due", "contract_id": contract.pk}

    on_time = completed <= promised
    return {
        "verdict": "judged",
        "contract_id": contract.pk,
        "on_time": on_time,
        "in_full": in_full,
        "promised_days": int(contract.promised_lead_time_days),
        "actual_days": (completed - contract.signed_on).days,
        "days_late": max(0, (completed - promised).days),
    }


def supplier_performance(access, supplier_id=None) -> list[dict]:
    """One row per supplier in this programme, with what their orders did.

    Scoped through `access` like every other read here. Ordered by name, not
    by score -- see the module docstring on why there is no single score.
    """
    contracts = (
        Contract.objects.filter(program_id=access.program_id)
        .exclude(status="cancelled")
        .select_related("supplier", "item", "commodity")
        .prefetch_related("receipts__lines", "invoices__payments")
    )
    if supplier_id is not None:
        contracts = contracts.filter(supplier_id=supplier_id)

    rows: dict[int, dict] = {}
    for contract in contracts:
        if contract.supplier_id is None:
            continue
        row = rows.setdefault(
            contract.supplier_id,
            {
                "supplier_id": contract.supplier_id,
                "supplier_name": contract.supplier.name if contract.supplier else "",
                "orders": 0,
                "measurable": 0,
                "on_time": 0,
                "in_full": 0,
                "otif": 0,
                "no_promise": 0,
                "not_yet_due": 0,
                "_promised": [],
                "_actual": [],
                "_late": [],
            },
        )
        row["orders"] += 1
        judged = judge_contract(contract)
        if judged["verdict"] == "no_promise":
            row["no_promise"] += 1
            continue
        if judged["verdict"] == "not_yet_due":
            row["not_yet_due"] += 1
            continue
        row["measurable"] += 1
        row["on_time"] += int(judged["on_time"])
        row["in_full"] += int(judged["in_full"])
        row["otif"] += int(judged["on_time"] and judged["in_full"])
        row["_promised"].append(judged["promised_days"])
        row["_actual"].append(judged["actual_days"])
        row["_late"].append(judged["days_late"])

    out = []
    for row in rows.values():
        promised, actual, late = row.pop("_promised"), row.pop("_actual"), row.pop("_late")
        n = row["measurable"]
        out.append(
            {
                **row,
                # None, never 0.0: a rate out of no measurable orders is not
                # a bad score, it is an absence of evidence, and the two read
                # identically once a zero reaches a page.
                "on_time_rate": (row["on_time"] / n) if n else None,
                "in_full_rate": (row["in_full"] / n) if n else None,
                "otif_rate": (row["otif"] / n) if n else None,
                # The median rather than the mean, because one catastrophic
                # order should not make a reliable supplier look unreliable
                # on a handful of contracts -- and the worst case is reported
                # beside it rather than averaged away.
                "promised_days_median": int(statistics.median(promised)) if promised else None,
                "actual_days_median": int(statistics.median(actual)) if actual else None,
                "days_late_worst": max(late) if late else None,
            }
        )
    return sorted(out, key=lambda r: r["supplier_name"].lower())
