"""Which stock figure is the official one for a field worker.

Sophie Feintuch, round 2 (programme 10172, 5 Nov 2026): until a partner's handovers are recorded in
Supply, the PHONE BALANCE (the figure the worker's own app reports after its last visit) is the
official figure, because without recorded handovers Supply's own figure (receipts and handovers
less what visits gave out) is only an estimate. Once a partner has recorded handovers, Supply's
records are official for that partner's field workers.

"Recorded handovers" means the partner has at least one CONFIRMED handout run in the ledger: a
`Distribution` run from one of its stores whose lines are all confirmed (`Distribution.status`).

Both figures are always shown, and the page says so when they differ. This is the one function
(`official_flw_stock`) the Plan, the Stock review, the Forecast and Where it went read; none works the
choice out for itself.
"""

from __future__ import annotations

from decimal import Decimal

from connect_labs.supply_chain.models import Distribution

PHONE = "phone"
RECORDS = "records"

# A difference smaller than this many units is rounding, not a disagreement.
TOLERANCE = Decimal("0.5")


def partners_with_handovers(program_id) -> set:
    """The partners (LabsOrg ids; None for places with no partner recorded) with a confirmed handout run.

    A run belongs to the partner of the store it went out from (`issues.partner_by_point`).
    """
    from connect_labs.supply_chain.stock.services import issues

    by_point = issues.partner_by_point(program_id)
    found = set()
    runs = Distribution.objects.filter(program_id=program_id).prefetch_related("lines")
    for run in runs:
        if run.status == "confirmed":
            found.add(by_point.get(run.supply_point_id))
    return found


def _amount(value):
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None:
        return None
    return Decimal(str(value))


def official_flw_stock(*, records, phone, partner_has_handovers: bool, phone_on=None) -> dict:
    """The official figure for one field worker, with both sides kept.

    `records` is Supply's figure (ledger), `phone` the app's reported balance; each a number,
    an {"amount", "unit"} dict or None. Returns:

      official   the figure to plan from (a Decimal, or None when neither exists)
      source     "phone" or "records": which one it is
      records, phone   both, as Decimals (None when unknown)
      differs    True when both are known and differ by more than rounding
      difference phone minus records (None unless both known)
      note       one plain sentence saying which is official and why, and the other when they differ
    """
    rec, ph = _amount(records), _amount(phone)
    wanted = RECORDS if partner_has_handovers else PHONE
    first, second = (rec, ph) if wanted == RECORDS else (ph, rec)
    # The wanted figure, else the other one when that is all there is.
    official = first if first is not None else second
    source = wanted if first is not None else (PHONE if wanted == RECORDS else RECORDS)
    if partner_has_handovers:
        why = "this partner's handovers are recorded in Supply, so Supply's records are official"
    else:
        why = "this partner's handovers are not recorded in Supply yet, so the phone balance is official"
    differs = rec is not None and ph is not None and abs(rec - ph) > TOLERANCE
    only = ""
    if official is None:
        only = "no figure is known from either the phone or Supply's records"
    elif source != wanted:
        only = f"only {'the phone balance' if source == PHONE else 'Supply’s record'} is known"
    note = only or why
    note = note[0].upper() + note[1:] + "."
    if differs:
        note += (
            f" The phone says {_fmt(ph)} and Supply's records say {_fmt(rec)}"
            f" ({'phone higher' if ph > rec else 'phone lower'} by {_fmt(abs(ph - rec))})."
        )
    return {
        "official": official,
        "source": source,
        "records": rec,
        "phone": ph,
        "phone_on": phone_on,
        "differs": bool(differs),
        "difference": (ph - rec) if (rec is not None and ph is not None) else None,
        "partner_has_handovers": partner_has_handovers,
        "note": note,
    }


def _fmt(value) -> str:
    return f"{value.quantize(Decimal(1)):,}"


def flw_totals(program_id, item, on_date=None, point_ids=None) -> dict:
    """What the field workers hold in all, by the official rule: the figure Where it went states.

    {official, records, phone, workers, differing, phone_workers}: `official` is each worker's official
    figure summed (a worker's phone balance until their partner's handovers are recorded, then Supply's
    records); `records` and `phone` are the two sides summed over the same workers (a worker with no phone
    balance adds their records to `phone`, so the two are comparable); `differing` counts workers whose two
    figures differ.
    """
    from connect_labs.supply_chain.stock.services import belief, issues

    handover_partners = partners_with_handovers(program_id)
    partner_of = issues.partner_by_point(program_id)
    official = records = phone = Decimal(0)
    workers = differing = phone_workers = 0
    for b in belief.worker_beliefs(program_id, item, on_date=on_date):
        if point_ids is not None and b.point.pk not in point_ids:
            continue
        got = official_flw_stock(
            records=b.on_hand.amount if hasattr(b.on_hand, "amount") else None,
            phone=b.reported.amount if b.reported is not None else None,
            partner_has_handovers=partner_of.get(b.point.pk) in handover_partners,
        )
        if got["official"] is None:
            continue
        workers += 1
        official += max(got["official"], Decimal(0))
        records += got["records"] if got["records"] is not None else got["official"]
        phone += got["phone"] if got["phone"] is not None else got["official"]
        phone_workers += got["phone"] is not None
        differing += got["differs"]
    return {
        "official": official,
        "records": records,
        "phone": phone,
        "workers": workers,
        "differing": differing,
        "phone_workers": phone_workers,
    }
