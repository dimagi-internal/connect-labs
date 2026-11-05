"""The Sourcing list: one row per tender, saying what the Overview's buying table says about it.

Sophie Feintuch's round-2 walk of the demo, "Sourcing list": the list had less than the Overview's
table. Each row now carries the tender's stage, the quotes in, the partners it is for, the day the
goods are needed, and the next move -- read from the same rows the Overview reads
(`standing._tender_rows`), so the two cannot disagree.
"""

from __future__ import annotations

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.procurement.services.award_orders import order_words, split_progress
from connect_labs.supply_chain.values import day_text


def marketplace_in_use(program_id) -> bool:
    """Whether any tender of the programme is offered through the supplier marketplace.

    A tender is restricted to the suppliers it invited; that word means nothing to a programme that
    never opens a tender to the marketplace, so the list says it only where the choice exists.
    """
    from connect_labs.supply_chain.models import Tender

    tenders = Tender.objects.filter(program_id=program_id)
    return (
        tenders.filter(visibility="public").exclude(status="draft").exists()
        or tenders.filter(invited_orgs__isnull=False).exists()
    )


def sourcing_rows(program_id, today) -> list[dict]:
    from connect_labs.supply_chain.models import Commodity, Contract, Tender
    from connect_labs.supply_chain.standing import _tender_rows

    tenders = list(Tender.objects.filter(program_id=program_id).order_by("-created_at", "-pk"))
    standing = {r.tender_id: r for r in _tender_rows(program_id, today, None, ordered=True)}
    names = dict(Commodity.objects.values_list("slug", "name"))
    contracts = {}
    for c in Contract.objects.filter(tender_id__in=[t.pk for t in tenders]).exclude(status="cancelled"):
        contracts.setdefault(c.tender_id, []).append(c)
    states = order_words(program_id, [c.pk for cs in contracts.values() for c in cs], today)
    out = []
    for tender in tenders:
        row = standing.get(tender.pk)
        progress = split_progress(tender, today)
        lines = [ln for ln in tender.lines or [] if isinstance(ln, dict) and ln.get("commodity_slug")]
        stage, detail = ("Draft", "not yet sent") if tender.status == "draft" else (tender.status.capitalize(), "")
        if row is not None:
            stage, detail = row.stage_name, row.stage_detail
        mine = contracts.get(tender.pk, [])
        if mine:
            stage = "Ordered"
            if progress:
                detail = progress["words"]
            else:
                detail = " · ".join(states[c.pk][0] for c in mine if c.pk in states)
        move, whose = (row.next_move, row.whose) if row is not None else (None, "")
        to_do = list(progress["to_do"]) if progress else []
        out.append(
            {
                "id": tender.pk,
                "label": tender.label,
                "url": reverse("supply_chain:procurement_tender_detail", args=[tender.pk]),
                "status": tender.status,
                "restricted": tender.visibility == "private",
                "products": [
                    {"slug": ln["commodity_slug"], "name": names.get(ln["commodity_slug"], ln["commodity_slug"])}
                    for ln in lines
                ],
                "stage": stage,
                "stage_detail": detail,
                "deadline": row.deadline_note if row is not None else "",
                "quotes": (row.quotes_chip if row is not None else "") or "none yet",
                "partners": [p["org_name"] for p in progress["partners"]] if progress else [],
                "needed_by": day_text(tender.needed_by),
                "next_move": move.text if move is not None else "",
                "next_whose": "Ours" if whose == rules.US else "Waiting on suppliers" if whose else "",
                "to_do": to_do,
            }
        )
    return out
