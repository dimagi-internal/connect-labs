"""The "how a purchase works" guide: the steps in order, and which one the programme is on.

Written for a programme manager who has never seen the screens (Sophie's feedback,
"Guide the steps"). It reads what the pages already hold -- tenders, outreach,
quotes, awards, orders, stock points, receipts -- and decides nothing of its own:
the next step is the first one with no evidence yet.

A step with nothing of its own to count ("check your partners", "work out
quantities") counts as done once any later step has happened: the programme
cannot have asked for quotes without having decided what to ask for.
"""

from dataclasses import dataclass

# (key, title, one-line what-you-do, view name of its page)
STEPS = (
    (
        "partners",
        "Check partners",
        "See which organisations will buy or hold stock for you.",
        "supply_chain:organisations",
    ),
    ("suppliers", "Review suppliers", "Add the manufacturers you could buy from.", "supply_chain:suppliers"),
    ("quantities", "Work out quantities", "Decide how much each partner needs.", "supply_chain:workers"),
    (
        "tender",
        "Create the tender",
        "Write down what you want, how much, and where.",
        "supply_chain:procurement_tender_board",
    ),
    (
        "ask",
        "Ask for quotes",
        "Email suppliers, or give them marketplace access.",
        "supply_chain:procurement_tender_board",
    ),
    ("replies", "Record replies", "Enter each quote as suppliers answer.", "supply_chain:procurement_tender_board"),
    ("compare", "Compare", "Line the quotes up on one basis.", "supply_chain:procurement_tender_board"),
    ("award", "Award", "Choose the supplier.", "supply_chain:procurement_tender_board"),
    ("order", "Place the orders", "One order per buyer, each recording who bought it.", "supply_chain:orders"),
    ("warehouses", "Set up warehouses", "Add each partner's stores to the network.", "supply_chain:network"),
    ("deliveries", "Record each delivery", "Receive stock with its batch and expiry date.", "supply_chain:stock"),
)

# What each step's evidence is, from the `facts` the guide is given. None: nothing
# of its own to count, so it is implied by a later step.
_EVIDENCE = {
    "partners": None,
    "suppliers": lambda f: f.get("suppliers", 0) > 0,
    # Decided on the tender: it states how much to buy. A programme-wide read (no tender) has no
    # such fact, so there it is implied by a later step as before.
    "quantities": lambda f: bool(f.get("quantities_set")),
    "tender": lambda f: f.get("tenders", 0) > 0,
    "ask": lambda f: f.get("outreach", 0) > 0 or f.get("tenders_open_or_later", 0) > 0,
    "replies": lambda f: f.get("quotes", 0) > 0,
    "compare": lambda f: f.get("tenders_closed_or_later", 0) > 0 or f.get("awards", 0) > 0,
    "award": lambda f: f.get("awards", 0) > 0,
    "order": lambda f: f.get("orders", 0) > 0,
    "warehouses": lambda f: f.get("stock_points", 0) > 0,
    "deliveries": lambda f: f.get("receipts", 0) > 0,
}


@dataclass
class Step:
    key: str
    number: int
    title: str
    what: str
    view: str
    done: bool
    next: bool


def steps_for(facts: dict, *, strict: bool = False) -> list[Step]:
    """The guide's steps with `done` and `next` set from `facts` (counts of what exists).

    Walks backwards so an unevidenced step is done when anything after it is. `strict` is the
    per-tender read: a step with evidence of its own is ticked only when this tender did it, never
    because a later step happened ("Work out quantities" ticked though nothing was sized).
    """
    done = [False] * len(STEPS)
    later_done = False
    for i in range(len(STEPS) - 1, -1, -1):
        check = _EVIDENCE[STEPS[i][0]]
        if check is None:
            done[i] = later_done
        else:
            done[i] = bool(check(facts)) or (later_done and not strict)
        later_done = later_done or done[i]
    first_open = next((i for i, d in enumerate(done) if not d), None)
    return [
        Step(key, i + 1, title, what, view, done[i], i == first_open)
        for i, (key, title, what, view) in enumerate(STEPS)
    ]


def facts_for(program_id) -> dict:
    """Counts of what the programme has, from the records (a rewound past day gives the past)."""
    from connect_labs.supply_chain.models import (
        Award,
        Contract,
        Outreach,
        Quote,
        Receipt,
        Supplier,
        SupplyPoint,
        Tender,
        scope_key,
    )

    tenders = Tender.objects.filter(program_id=program_id)
    return {
        "suppliers": Supplier.objects.filter(scope_key=scope_key(program_id=program_id)).count(),
        "tenders": tenders.count(),
        "tenders_open_or_later": tenders.exclude(status="draft").count(),
        "tenders_closed_or_later": tenders.filter(status__in=("closed", "awarded")).count(),
        "outreach": Outreach.objects.filter(tender__program_id=program_id).count(),
        "quotes": Quote.objects.filter(tender__program_id=program_id).count(),
        "awards": Award.objects.filter(tender__program_id=program_id).count(),
        "orders": Contract.objects.filter(program_id=program_id).count(),
        "stock_points": SupplyPoint.objects.filter(program_id=program_id).count(),
        "receipts": Receipt.objects.filter(supply_point__program_id=program_id).count(),
    }


def tender_facts_for(program_id) -> list[tuple]:
    """[(tender, facts)] newest first: the same counts as `facts_for`, but for one tender each.

    A split tender's "orders" and "receipts" count only once every partner has ordered, and once
    every order has been received: the guide ticks a step only when that tender did it, not when its
    first partner did.
    """
    from connect_labs.supply_chain.models import Award, Contract, Outreach, Quote, Receipt, Tender

    base = facts_for(program_id)
    tenders = list(Tender.objects.filter(program_id=program_id).order_by("-created_at", "-pk"))
    ids = [t.pk for t in tenders]
    outreach, quotes, awards = {}, {}, {}
    for tender_id in Outreach.objects.filter(tender_id__in=ids).values_list("tender_id", flat=True):
        outreach[tender_id] = outreach.get(tender_id, 0) + 1
    for tender_id in Quote.objects.filter(tender_id__in=ids, voided=False).values_list("tender_id", flat=True):
        quotes[tender_id] = quotes.get(tender_id, 0) + 1
    for tender_id in Award.objects.filter(tender_id__in=ids).values_list("tender_id", flat=True):
        awards[tender_id] = awards.get(tender_id, 0) + 1
    contracts = {}
    for c in Contract.objects.filter(tender_id__in=ids).exclude(status="cancelled"):
        contracts.setdefault(c.tender_id, []).append(c)
    received = set(Receipt.objects.filter(contract__tender_id__in=ids).values_list("contract_id", flat=True))
    found = []
    for tender in tenders:
        mine = contracts.get(tender.pk, [])
        partners = {s.get("org_id") for s in tender.partner_splits or [] if isinstance(s, dict)}
        ordered_for = {c.partner_org_id or c.buyer_org_id for c in mine}
        ordered = bool(mine) and (not partners or partners <= ordered_for)
        delivered = ordered and all(c.pk in received for c in mine)
        lines = [ln for ln in tender.lines or [] if isinstance(ln, dict)]
        found.append(
            (
                tender,
                {
                    **base,
                    "quantities_set": any(_positive(ln.get("quantity")) for ln in lines),
                    "tenders": 1,
                    "tenders_open_or_later": int(tender.status != "draft"),
                    "tenders_closed_or_later": int(tender.status in ("closed", "awarded")),
                    "outreach": outreach.get(tender.pk, 0),
                    "quotes": quotes.get(tender.pk, 0),
                    "awards": awards.get(tender.pk, 0),
                    "orders": int(ordered),
                    "receipts": int(delivered),
                },
            )
        )
    return found


def _positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def guide_tenders(program_id, chosen=None) -> tuple:
    """(focus, listing): the tender the guide reads for, and a chip for each tender with its next step.

    The focus is the one asked for, else the newest tender with a step still to do, else the newest.
    """
    found = tender_facts_for(program_id)
    if not found:
        return None, []
    steps = {t.pk: steps_for(f, strict=True) for t, f in found}
    focus = next((t for t, _ in found if str(t.pk) == str(chosen)), None)
    if focus is None:
        focus = next((t for t, _ in found if any(s.next for s in steps[t.pk])), found[0][0])
    listing = [
        {
            "id": t.pk,
            "label": t.label,
            "current": t.pk == focus.pk,
            "next": next((s.title for s in steps[t.pk] if s.next), ""),
        }
        for t, _ in found
    ]
    return (focus, steps[focus.pk]), listing


def guide_context(request, program_id) -> dict:
    """What `_purchase_guide.html` takes: the steps for one tender, and the chooser between tenders.

    With no tender yet, the steps are the programme's, as before.
    """
    focus, listing = guide_tenders(program_id, request.GET.get("guide"))
    if focus is None:
        return {"purchase_guide": purchase_guide(request, program_id), "guide_tender": None, "guide_tenders": []}
    tender, steps = focus
    here = request.path + (f"?program_id={program_id}&guide=" if program_id else "?guide=")
    for chip in listing:
        chip["url"] = f"{here}{chip['id']}"
    return {
        "purchase_guide": _as_rows(request, program_id, steps),
        "guide_tender": {"id": tender.pk, "label": tender.label},
        "guide_tenders": listing,
    }


def _as_rows(request, program_id, steps) -> list[dict]:
    from connect_labs.supply_chain.navigation import scoped_url

    return [
        {
            "number": s.number,
            "title": s.title,
            "what": s.what,
            "url": scoped_url(request, s.view, program_id=program_id),
            "done": s.done,
            "next": s.next,
        }
        for s in steps
    ]


def purchase_guide(request, program_id) -> list[dict]:
    """The steps as the template draws them: title, what to do, link, and done / next."""
    return _as_rows(request, program_id, steps_for(facts_for(program_id)))
