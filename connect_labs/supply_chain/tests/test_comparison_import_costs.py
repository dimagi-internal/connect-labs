"""Landed cost under buyer-import terms: clearing and freight follow the Incoterm, one relief
rule for the comparison and the order, one verb per supplier move, the quote count as data.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on buyer_import_world.
"""

from decimal import Decimal

import pytest

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.models import Quote, Tender
from connect_labs.supply_chain.procurement.services import pricing
from connect_labs.supply_chain.procurement.services.questions import audience_for_reason
from connect_labs.supply_chain.procurement.status import (
    comparison_grid,
    comparisons,
    gap_owner,
    quote_count_words,
    tender_status,
)
from connect_labs.supply_chain.templatetags.supply_chain_extras import supplies
from connect_labs.supply_chain.tests import buyer_import_world
from connect_labs.supply_chain.tests.buyer_import_world import (
    _ACCEPTANCE,
    PROGRAM,
    TODAY,
    _harmattan,
    _quote,
    _rows,
    op,
)
from connect_labs.supply_chain.values import Money, Unconfirmed

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = buyer_import_world.da
world = buyer_import_world.world


def test_set_import_estimates_is_idempotent_and_attributed(da, world):
    tender_id = world["tender"]["id"]
    first = op(da, "tender_set_import_estimates", tender_id=tender_id, clearing_estimate_per_unit="1.20")
    again = op(da, "tender_set_import_estimates", tender_id=tender_id, clearing_estimate_per_unit="1.20")
    assert Decimal(first["clearing_estimate_per_unit"]) == Decimal("1.20")
    assert first["freight_estimate_per_unit"] is None
    assert first["import_estimates_set_on"] and again["import_estimates_set_on"] == first["import_estimates_set_on"]
    from connect_labs.supply_chain.history.models import Revision

    assert (
        Revision.objects.filter(object_id=str(tender_id), changes__has_key="clearing_estimate_per_unit")
        .exclude(action="create")
        .count()
        == 1
    )
    with pytest.raises(ValueError):
        op(da, "tender_set_import_estimates", tender_id=tender_id, clearing_estimate_per_unit="-1")
    cleared = op(da, "tender_set_import_estimates", tender_id=tender_id, clearing_estimate_per_unit="")
    assert cleared["clearing_estimate_per_unit"] is None


def test_clearing_estimate_lands_on_quotes_we_import_only(da, world):
    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "ddp", "DDP Kano", freight_basis="included", duties_basis="included")
    op(da, "tender_set_import_estimates", tender_id=world["tender"]["id"], clearing_estimate_per_unit="1.20")
    _, _, compared, rows = _rows(world)
    harmattan, kanem = rows["Harmattan"], rows["Kanem"]
    assert harmattan.clearing == "estimate" and kanem.clearing == ""
    assert harmattan.figures["landed_total_for_tender_quantity"].amount == Decimal("2000") * Decimal("51.20")
    assert kanem.figures["landed_total_for_tender_quantity"].amount == Decimal("2000") * Decimal("50.00")
    assert compared.like_for_like


def test_no_clearing_estimate_does_not_block_but_labels_the_figure(da, world):
    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "ddp", "DDP Kano", freight_basis="included", duties_basis="included")
    tender, quotes, compared, rows = _rows(world)
    harmattan = rows["Harmattan"]
    assert harmattan.is_comparable and harmattan.clearing == "open"
    assert harmattan.open_estimates == ["clearing estimate"] and "clearing estimate" not in harmattan.gaps
    assert gap_owner("clearing estimate") == "us"
    assert not compared.like_for_like
    grid = comparison_grid(tender, compared.to_snapshot(), {q.pk: q for q in quotes})
    assert grid["like_for_like"] is False
    landed = next(r for r in grid["rows"] if r["key"] == "landed")
    column = [c["name"] for c in grid["quotes"]].index("Harmattan Therapeutics")
    assert landed["cells"][column]["qualifier"] == "excl. clearing"
    assert landed["cells"][column]["v"] == "USD 50.00"
    clearing = next(r for r in grid["rows"] if r["key"] == "clearing")
    assert clearing["label"] == "Clearing & forwarding"
    assert clearing["cells"][column]["gap"] and clearing["cells"][column]["owner"] == "us"
    chips = [c["label"] for c in grid["quotes"][column]["chips"]]
    # The open estimate is the quote's one chip: a fact and whose it is, beside its shown figure.
    assert chips == ["clearing estimate · ours to fill"]


def test_exw_freight_is_ours_and_never_asked_of_the_supplier(da, world):
    _quote(da, world, "exw", "EXW Niamey")
    _, _, _, rows = _rows(world)
    sahel = rows["Sahel"]
    assert not sahel.is_comparable
    assert sahel.freight_ours == "open"
    assert "freight estimate" in sahel.gaps and "freight" not in sahel.gaps
    assert gap_owner("freight estimate") == "us"
    assert all(q.audience == "internal" for q in sahel.questions if "freight" in q.key)
    reason = next(r for r in sahel.figures["landed_total_as_quoted"].reasons if "freight" in r)
    assert audience_for_reason(reason) == "internal"


def test_exw_landed_includes_our_freight_and_clearing_estimates(da, world):
    _quote(da, world, "exw", "EXW Niamey")
    op(
        da,
        "tender_set_import_estimates",
        tender_id=world["tender"]["id"],
        clearing_estimate_per_unit="1.20",
        freight_estimate_per_unit="3.00",
    )
    _, _, _, rows = _rows(world)
    sahel = rows["Sahel"]
    assert sahel.is_comparable and sahel.freight_ours == "estimate"
    landed = sahel.figures["landed_total_for_tender_quantity"]
    assert isinstance(landed, Money) and landed.amount == Decimal("2000") * Decimal("54.20")


def test_cpt_freight_is_still_the_suppliers(da, world):
    _quote(da, world, "cpt", "CPT Kano")
    op(da, "tender_set_import_estimates", tender_id=world["tender"]["id"], freight_estimate_per_unit="3.00")
    _, _, _, rows = _rows(world)
    harmattan = rows["Harmattan"]
    assert harmattan.freight_ours == "" and "freight estimate" not in harmattan.gaps
    landed = harmattan.figures["landed_total_for_tender_quantity"]
    assert not isinstance(landed, Unconfirmed) and landed.amount == Decimal("2000") * Decimal("50.00")


def test_the_quote_count_counts_quotes_and_landed_prices(da, world):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    assert quote_count_words(comparisons(tender, [])) == ""
    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "exw", "EXW Niamey")
    _quote(da, world, "ddp", "DDP Kano", pack_spec_source="not_stated", base_per_pack_stated=None)
    quotes = list(Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item"))
    # Ex works leaves freight to our estimate (not recorded) and DDP states no pack: only the
    # carriage-paid quote has a landed price. The count says so, and names nobody as comparable.
    assert quote_count_words(comparisons(tender, quotes)) == "3 quotes · 1 with a landed price"


def test_overview_row_carries_the_count_and_stays_collecting(da, world):
    from connect_labs.supply_chain.standing import _tender_rows

    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    rows = _tender_rows(PROGRAM, TODAY, None)
    row = next(r for r in rows if r.tender_id == world["tender"]["id"])
    assert row.stage_name == "Collecting quotes"
    assert row.quotes_chip == "1 quote · 1 with a landed price"


def test_tender_status_counts_quotes_in_a_plain_tile(da, world):
    from connect_labs.supply_chain.procurement.status import tender_status

    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "exw", "EXW Niamey")
    status = tender_status(Tender.objects.get(pk=world["tender"]["id"]), TODAY, program_id=PROGRAM)
    assert "comparable_chip" not in status and "comparable" not in status
    assert (status["quoted"], status["priced"]) == (2, 1)
    tile = next(t for t in status["tiles"] if t["label"] == "Quotes")
    assert tile["value"] == "2" and tile["sub"].startswith("1 with a landed price")
    assert not any("omparable" in t["label"] for t in status["tiles"])
    assert status["stages"][1]["state"] == "now"


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
    # Its figure is shown and it can be awarded; no chip passes a verdict on it.
    assert column["awardable"] is True
    assert not any("omparable" in chip["label"] for chip in column["chips"])


def test_facts_on_us_lists_open_facts_only_never_award(da, world):
    _harmattan(da, world)
    tender = Tender.objects.get(pk=world["tender"]["id"])
    _, quotes, compared, _ = _rows(world)
    grid = comparison_grid(tender, compared.to_snapshot(), {q.pk: q for q in quotes}, waiver_on_file=False)
    (column,) = grid["quotes"]
    labels = [a["label"] for a in column["actions"]]
    assert "Award" not in labels
    # The exemption document is the tender's, attached once on the duty line: not a move per quote.
    assert "Attach duty exemption" not in labels and all(a["owner"] for a in column["actions"])


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


def test_gap_tags_name_who_supplies_the_fact():
    assert supplies(rules.US) == "ours to fill"
    assert supplies(rules.SUPPLIERS) == "waiting"
