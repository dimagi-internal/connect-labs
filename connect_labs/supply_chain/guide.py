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
    ("award", "Award", "Choose the supplier, with a reason.", "supply_chain:procurement_tender_board"),
    ("order", "Place the orders", "One order per buyer, each recording who bought it.", "supply_chain:orders"),
    ("warehouses", "Set up warehouses", "Add each partner's stores to the network.", "supply_chain:network"),
    ("deliveries", "Record each delivery", "Receive stock with its batch and expiry date.", "supply_chain:stock"),
)

# What each step's evidence is, from the `facts` the guide is given. None: nothing
# of its own to count, so it is implied by a later step.
_EVIDENCE = {
    "partners": None,
    "suppliers": lambda f: f.get("suppliers", 0) > 0,
    "quantities": None,
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


def steps_for(facts: dict) -> list[Step]:
    """The guide's steps with `done` and `next` set from `facts` (counts of what exists).

    Walks backwards so an unevidenced step is done when anything after it is.
    """
    done = [False] * len(STEPS)
    later_done = False
    for i in range(len(STEPS) - 1, -1, -1):
        check = _EVIDENCE[STEPS[i][0]]
        done[i] = later_done if check is None else bool(check(facts)) or later_done
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


def purchase_guide(request, program_id) -> list[dict]:
    """The steps as the template draws them: title, what to do, link, and done / next."""
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
        for s in steps_for(facts_for(program_id))
    ]
