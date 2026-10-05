"""Unanswered round, 2026-10-04 batch 3 (procurement): one relief rule for the comparison and the
order, one verb per supplier move, Award only in the award fold, and gap tags that name who
supplies a fact without reading as a task.

THIS REPOSITORY IS PUBLIC. Every supplier, person and price here is invented.
"""

import pytest
from django.template.loader import get_template

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.procurement.services import pricing
from connect_labs.supply_chain.procurement.status import comparison_grid, tender_status
from connect_labs.supply_chain.templatetags.supply_chain_extras import supplies
from connect_labs.supply_chain.tests import test_unanswered_round_ddd005_batch5_estimates as estimates

pytestmark = pytest.mark.django_db

PROGRAM = estimates.PROGRAM
TODAY = estimates.TODAY
op = estimates.op
da = estimates.da
world = estimates.world
_quote = estimates._quote
_rows = estimates._rows

_ACCEPTANCE = dict(
    shelf_life_months_stated=24, lead_time_days=35, validity_until="2026-11-02", moq=500, moq_unit="carton"
)


def _harmattan(da, world):
    """USD per carton, carriage paid: comparable, costed at nil duty on the tender's waiver."""
    return _quote(da, world, "cpt", "CPT Kano", freight_basis="included", **_ACCEPTANCE)


def _attach_waiver(da, world):
    op(
        da,
        "document_attach",
        data={
            "kind": "duty_exemption",
            "title": "Duty waiver",
            "external_url": "https://files.example.org/waiver.pdf",
            "tender_id": world["tender"]["id"],
            "source": "document",
        },
    )


# ---- R. one relief rule ------------------------------------------------------


def test_a_quote_on_the_waiver_with_no_copy_on_file_ranks_provisionally(da, world):
    _harmattan(da, world)
    tender, quotes, compared, by_name = _rows(world)
    row = by_name["Harmattan"]
    assert pricing.quote_rests_on_relief(quotes[0], tender)
    # Still comparable, counted in "N of M comparable"; its figure is what is unconfirmed.
    assert row in compared.comparable and compared.comparable_count == 1
    assert row.relief_unevidenced is True
    assert compared.provisional is True
    snapshot = compared.to_snapshot()
    assert snapshot["provisional"] is True
    assert snapshot["comparable"][0]["relief_unevidenced"] is True


def test_the_waiver_on_file_clears_the_mark_and_the_provisional_ranking(da, world):
    _harmattan(da, world)
    _attach_waiver(da, world)
    tender, _, compared, by_name = _rows(world)
    assert pricing.duty_exemption_on_file(tender=tender)
    assert by_name["Harmattan"].relief_unevidenced is False
    assert compared.provisional is False


def test_a_quote_the_supplier_imports_does_not_rest_on_the_waiver(da, world):
    _quote(da, world, "ddp", "DDP Kano", freight_basis="included", duties_basis="included", **_ACCEPTANCE)
    tender, quotes, _, by_name = _rows(world)
    assert not pricing.quote_rests_on_relief(quotes[0], tender)
    assert by_name["Kanem"].relief_unevidenced is False


def test_the_grid_marks_the_landed_figure_unconfirmed_as_the_order_does(da, world):
    _harmattan(da, world)
    tender = Tender.objects.get(pk=world["tender"]["id"])
    _, quotes, compared, _ = _rows(world)
    grid = comparison_grid(tender, compared.to_snapshot(), {q.pk: q for q in quotes}, waiver_on_file=False)
    landed = next(r for r in grid["rows"] if r["key"] == "landed")
    (cell,) = landed["cells"]
    assert cell.get("unconfirmed") is True
    assert cell["v"].startswith("USD ")
    (column,) = grid["quotes"]
    assert column["chips"][0]["label"] == "Comparable"
    source = get_template("supply_chain/procurement/comparison.html").template.source
    assert 'data-testid="landed-unconfirmed"' in source


def test_the_order_reads_the_same_predicate(monkeypatch):
    """landed.relief_unevidenced is pricing.relief_unevidenced over the order's own relief."""
    from types import SimpleNamespace

    from connect_labs.supply_chain.fulfilment.services import landed

    seen = []
    monkeypatch.setattr(pricing, "relief_unevidenced", lambda rests, **kw: seen.append((rests, kw)) or "x")
    contract = SimpleNamespace(
        consideration="priced",
        buyer_of_record="programme_org",
        duties_basis="excluded",
        duties_amount=0,
        duty_relief_claimed=False,
    )
    assert landed.relief_unevidenced(contract) == "x"
    assert seen == [(True, {"contract": contract})]


# ---- A. Award is offered once, in the fold -------------------------------------


def test_facts_on_us_lists_open_facts_only_never_award(da, world):
    _harmattan(da, world)
    tender = Tender.objects.get(pk=world["tender"]["id"])
    _, quotes, compared, _ = _rows(world)
    grid = comparison_grid(tender, compared.to_snapshot(), {q.pk: q for q in quotes}, waiver_on_file=False)
    (column,) = grid["quotes"]
    labels = [a["label"] for a in column["actions"]]
    assert "Award" not in labels
    assert "Attach duty exemption" in labels and all(a["owner"] for a in column["actions"])
    source = get_template("supply_chain/procurement/comparison.html").template.source
    assert "a.award" not in source
    assert 'data-testid="award-start"' in source


# ---- V. one verb per supplier move ---------------------------------------------


def test_the_verb_function():
    assert rules.supplier_action(rules.ACTION_REMIND, 7) == {"label": "Remind", "anchor": "draft-supplier-7"}
    assert rules.supplier_action(rules.ACTION_REPLY, 7) == {"label": "Reply", "anchor": "draft-reply-7"}
    assert rules.supplier_action(rules.ACTION_ASK, 7)["label"] == "Ask"
    assert rules.supplier_action(rules.ACTION_RECORD_REPLY) == {"label": "Record a reply", "anchor": ""}


def test_a_drafted_reminder_reads_remind_in_the_table_and_the_rail(da, world):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    sid = world["suppliers"]["exw"]["id"]
    op(da, "outreach_log", data={"tender_id": tender.pk, "supplier_id": sid, "sent_on": "2026-09-20"})
    status = tender_status(tender, TODAY, program_id=PROGRAM, draft_anchors={f"draft-supplier-{sid}"})
    (row,) = (r for r in status["suppliers"] if r["supplier_id"] == sid)
    assert row["action"] == {"label": "Remind", "href": f"#draft-supplier-{sid}"}
    ours, theirs = rules.tender_moves(tender, TODAY)
    (move,) = (m for m in theirs if m.supplier_id == sid)
    assert move.cta == row["action"]["label"]
    assert move.href.endswith(row["action"]["href"])


def test_an_undrafted_silent_supplier_offers_record_a_reply(da, world):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    sid = world["suppliers"]["exw"]["id"]
    op(da, "outreach_log", data={"tender_id": tender.pk, "supplier_id": sid, "sent_on": "2026-09-20"})
    status = tender_status(tender, TODAY, program_id=PROGRAM, draft_anchors=set())
    (row,) = (r for r in status["suppliers"] if r["supplier_id"] == sid)
    assert row["action"]["label"] == "Record a reply"


def test_the_suppliers_card_counts_answered_as_the_stage_bar_does():
    source = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert "{{ outreach_asked }} answered" in source
    assert "{{ outreach_asked }} replied" not in source


# ---- T. who supplies a fact, not whose task it is ------------------------------


def test_gap_tags_name_who_supplies_the_fact():
    assert supplies(rules.US) == "to do"
    assert supplies(rules.SUPPLIERS) == "waiting"
    source = get_template("supply_chain/home.html").template.source
    assert '"supply_chain/_fact_chip.html"' in source
    assert "owner|supplies" in get_template("supply_chain/_fact_chip.html").template.source
    assert "ours{% else %}supplier's" not in source
