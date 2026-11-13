"""The "Start here" page: what each group of tabs is for, the steps by moment, and where this programme is.

Written for programme staff who are not supply chain specialists (Sophie's round-2 feedback, "Start here").
It is a side-menu item, never a screen everyone must click through, and it decides nothing of its own: a step
is ticked when the records already show it was done, the same way the purchase guide (guide.py) ticks its steps.

Four moments, because the work comes at four speeds:

  Setting up (once)     partners, suppliers, the product
  Each purchase         size it, tender, quotes, compare, award, orders, deliveries
  Every week            Plan, Distribution, Issues
  Every month or so     the chase list, "Will we have enough?", when to buy next

and three entry points by role, which pages each of them uses (the access table in the feedback document).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.views.generic import TemplateView

# What each group of tabs is for, one line each (navigation.TAB_GROUPS names the groups).
GROUP_LINES = (
    ("", "Overview", "Where the program stands, and who to chase."),
    (
        "Buying",
        "Buying",
        "Ask suppliers for prices, compare, choose, order and track the delivery. A few times a year.",
    ),
    (
        "Distribution and stock",
        "Distribution and stock",
        "Where the stock is, what to hand out each week, and where it went. Weekly, and partly the local "
        "partners' (LLOs') work.",
    ),
    ("Setup", "Setup", "The product, the organisations, who is told what, and who can act. Mostly once."),
)

# (key, title, what you do, view name, evidence key in the facts or None for "no tick, a recurring check")
MOMENTS = (
    (
        "setup",
        "Setting up",
        "Once, when the program starts.",
        (
            (
                "partners",
                "Add the local partners (LLOs) and say how each one hands stock out",
                "partner_plans",
                "supply_chain:partners",
            ),
            ("suppliers", "Add the suppliers you could buy from", "suppliers", "supply_chain:suppliers"),
            ("product", "Add the product you buy", "product", "supply_chain:catalogue"),
        ),
    ),
    (
        "purchase",
        "Each purchase",
        "Every time you buy more.",
        (
            ("size", "Size it: how much each partner needs", "tender_sized", "supply_chain:purchases"),
            (
                "tender",
                "Create the tender (the request for quotes)",
                "tenders",
                "supply_chain:purchases",
            ),
            ("quotes", "Ask suppliers and record their quotes", "quotes", "supply_chain:purchases"),
            ("compare", "Compare the quotes", "compared", "supply_chain:purchases"),
            ("award", "Award the tender to one supplier", "awards", "supply_chain:purchases"),
            ("orders", "Place the orders", "orders", "supply_chain:purchases"),
            ("deliveries", "Record each delivery as it arrives", "receipts", "supply_chain:purchases"),
        ),
    ),
    (
        "week",
        "Every week",
        "While children are being treated.",
        (
            ("plan", "Plan: what to issue each field worker", "plans", "supply_chain:plan"),
            ("distribution", "Distribution: hand it out and confirm it", "handouts", "supply_chain:distribution"),
            ("issues", "Issues: follow up anything missing, taken or damaged", "issues", "supply_chain:issues"),
        ),
    ),
    (
        "month",
        "Every month or so",
        "To stay ahead of problems.",
        (
            (
                "chase",
                "Read the chase list on Overview and ask whoever is holding things up",
                None,
                "supply_chain:overview",
            ),
            ("enough", "Plan, running organisation view: will we have enough?", None, "supply_chain:plan_programme"),
            (
                "buy_next",
                "Decide when to start the next tender",
                None,
                "supply_chain:plan_programme",
            ),
        ),
    ),
)

# Who uses what (the access table in the round-2 document, "could partners see only their own pages?").
ROLES = (
    (
        "lead",
        "Program lead",
        "Runs the program for the funder: buying, partners and the whole picture.",
        (
            ("supply_chain:overview", "Overview"),
            ("supply_chain:purchases", "Purchases"),
            ("supply_chain:partners", "Partners"),
            ("supply_chain:plan_programme", "Plan, running organisation view"),
            ("supply_chain:flow", "Where it went"),
            ("supply_chain:alerts", "Alerts"),
        ),
    ),
    (
        "manager",
        "LLO manager",
        "Runs one local partner's stock: its stores and its supervisors.",
        (
            ("supply_chain:plan", "Plan, supervisor weekly view"),
            ("supply_chain:plan_stores", "Plan, store manager view"),
            ("supply_chain:distribution", "Distribution"),
            ("supply_chain:issues", "Issues"),
            ("supply_chain:workers", "Stock review"),
            ("supply_chain:flow", "Where it went"),
            ("supply_chain:network", "Network"),
        ),
    ),
    (
        "supervisor",
        "Supervisor",
        "Looks after a group of field workers (FLWs) for a local partner.",
        (
            ("supply_chain:plan", "Plan, supervisor weekly view"),
            ("supply_chain:distribution", "Distribution"),
            ("supply_chain:issues", "Issues"),
            ("supply_chain:workers", "Stock review"),
        ),
    ),
)


@dataclass
class StartStep:
    key: str
    title: str
    view: str
    done: bool | None  # None: a recurring check, nothing to tick
    note: str = ""


def facts_for(program_id) -> dict:
    """What the programme already has, counted from the records."""
    from connect_labs.supply_chain.models import (
        Award,
        Commodity,
        Contract,
        Distribution,
        Outreach,
        Quote,
        Receipt,
        StockIssue,
        Supplier,
        Tender,
        scope_key,
    )
    from connect_labs.supply_chain.partners.models import PartnerPlan
    from connect_labs.supply_chain.plan.models import StockPlan

    tenders = Tender.objects.filter(program_id=program_id)
    scope = scope_key(program_id=program_id)
    last_handout = (
        Distribution.objects.filter(program_id=program_id, confirmed_on__isnull=False)
        .order_by("-confirmed_on")
        .values_list("confirmed_on", flat=True)
        .first()
    )
    return {
        "partner_plans": PartnerPlan.objects.filter(program_id=program_id, ended_on__isnull=True)
        .values("org_id")
        .distinct()
        .count(),
        "suppliers": Supplier.objects.filter(scope_key=scope).count(),
        "product": Commodity.objects.filter(scope_key=scope).count(),
        "tenders": tenders.count(),
        "tender_sized": sum(
            1 for t in tenders if t.partner_splits or any((line or {}).get("quantity") for line in t.lines or [])
        ),
        "quotes": Quote.objects.filter(tender__program_id=program_id).count()
        + Outreach.objects.filter(tender__program_id=program_id).count(),
        "compared": tenders.filter(status__in=("closed", "awarded")).count(),
        "awards": Award.objects.filter(tender__program_id=program_id).count(),
        "orders": Contract.objects.filter(program_id=program_id).count(),
        "receipts": Receipt.objects.filter(supply_point__program_id=program_id).count(),
        "plans": StockPlan.objects.filter(program_id=program_id).count(),
        "handouts": Distribution.objects.filter(program_id=program_id, confirmed_on__isnull=False).count(),
        "last_handout": last_handout,
        "issues": StockIssue.objects.filter(program_id=program_id).count(),
    }


def _note(key: str, facts: dict) -> str:
    """A short fact beside a step, in plain words ("2 partners with a plan")."""
    from connect_labs.supply_chain.values import day_text

    def counted(fact, noun, tail=""):
        n = facts.get(fact, 0)
        return f"{n} {noun}{'' if n == 1 else 's'}{tail}" if n else ""

    notes = {
        "partners": lambda: counted("partner_plans", "partner", " with a plan"),
        "suppliers": lambda: counted("suppliers", "supplier", " on file"),
        "tender": lambda: counted("tenders", "tender"),
        "orders": lambda: counted("orders", "order"),
        "issues": lambda: counted("issues", "issue", " recorded"),
        "distribution": lambda: (
            f"last handout confirmed {day_text(facts['last_handout'])}" if facts.get("last_handout") else ""
        ),
    }
    return notes.get(key, lambda: "")()


def moments_for(facts: dict) -> list[dict]:
    """The moments with each step's tick, from `facts`; the first open step of each moment is its next one."""
    out = []
    for key, title, when, steps in MOMENTS:
        rows = []
        for step_key, text, evidence, view in steps:
            done = None if evidence is None else bool(facts.get(evidence))
            rows.append(StartStep(step_key, text, view, done, _note(step_key, facts)))
        first_open = next((r for r in rows if r.done is False), None)
        out.append({"key": key, "title": title, "when": when, "steps": rows, "next": first_open})
    return out


class StartHereView(TemplateView):
    template_name = "supply_chain/start_here.html"

    def get_context_data(self, **kwargs):
        from connect_labs.supply_chain import views as supply_views
        from connect_labs.supply_chain.api_views import has_program_context
        from connect_labs.supply_chain.banner import program_line
        from connect_labs.supply_chain.navigation import scoped_url, supply_tabs

        context = super().get_context_data(**kwargs)
        context["supply_tabs"] = supply_tabs(self.request)
        context["supply_program_line"] = program_line(self.request)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        program_id = supply_views._access(self.request).program_id

        def link(view):
            return scoped_url(self.request, view, program_id=program_id)

        moments = moments_for(facts_for(program_id))
        for moment in moments:
            for step in moment["steps"]:
                step.url = link(step.view)
        context["groups"] = [{"label": label, "lines": line} for _, label, line in GROUP_LINES]
        context["moments"] = moments
        context["roles"] = [
            {
                "key": key,
                "title": title,
                "what": what,
                "pages": [{"label": label, "url": link(view)} for view, label in pages],
            }
            for key, title, what, pages in ROLES
        ]
        return context
