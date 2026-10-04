"""Batch 5: landed cost under buyer-import terms, freight that follows the Incoterm,
"Mark sent" reminders that stop at the deadline, and the comparable-count chip.

Every supplier, person and price here is invented.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Quote, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.comparison import compare_tender
from connect_labs.supply_chain.procurement.services.questions import audience_for_reason
from connect_labs.supply_chain.procurement.status import comparable_chip, comparison_grid, comparisons, gap_owner
from connect_labs.supply_chain.procurement.views import next_reminder_due
from connect_labs.supply_chain.values import Money, Unconfirmed

pytestmark = pytest.mark.django_db

PROGRAM = 10736
TODAY = date(2026, 10, 3)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Kano tender",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_open", tender_id=tender["id"])
    op(da, "tender_set_duty_terms", tender_id=tender["id"], duty_terms="buyer_waiver")
    suppliers = {
        key: op(da, "supplier_create", data={"name": name})
        for key, name in (
            ("cpt", "Harmattan Therapeutics"),
            ("exw", "Sahel Nutrition Industries"),
            ("ddp", "Kanem Foods Ltd"),
        )
    }
    return {"tender": tender, "suppliers": suppliers}


def _quote(da, world, key, incoterm, **extra):
    data = dict(
        tender_id=world["tender"]["id"],
        supplier_id=world["suppliers"][key]["id"],
        commodity_slug="rutf",
        as_quoted_amount="50.00",
        as_quoted_unit="per_pack",
        as_quoted_currency="USD",
        quantity_basis=2000,
        quantity_basis_unit="carton",
        pack_spec_source="stated_on_quote",
        base_per_pack_stated=150,
        incoterm=incoterm,
        received_on=TODAY.isoformat(),
    )
    data.update(extra)
    data = {k: v for k, v in data.items() if v is not None}
    return op(da, "quote_record", data=data)


def _rows(world):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    quotes = list(Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item"))
    compared = compare_tender(
        tender, Commodity.objects.get(slug="rutf"), quotes, {q.supplier_id: q.supplier for q in quotes}
    )
    by_name = {row.supplier_name.split(" ")[0]: row for row in compared.all_rows}
    return tender, quotes, compared, by_name


# ---- 1. clearing & forwarding ------------------------------------------------


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
    assert "1 fact on us" in chips and "Comparable" in chips


# ---- 2. freight follows the Incoterm ------------------------------------------


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


# ---- 3. Mark sent, never past the deadline -------------------------------------


def test_next_reminder_is_never_after_the_deadline():
    sent = date(2026, 10, 2)
    assert next_reminder_due(sent, 7, date(2026, 10, 20)) == date(2026, 10, 9)
    assert next_reminder_due(sent, 7, date(2026, 10, 6)) is None
    assert next_reminder_due(sent, 7, None) == date(2026, 10, 9)


def test_reminder_draft_says_mark_sent_and_row_says_reminder_sent():
    from django.template.loader import get_template

    source = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert 'data-testid="mark-sent"' in source and ">Mark sent</button>" in source
    assert "Record chase</button>" not in source
    # The changed cell keeps every row's "<date> · <nth> reminder" shape; the sent state rides the tag.
    assert 'data-testid="supplier-chased">{{ r.chased|default:"—" }}{% if o.next_due %}' in source
    assert 'label="Reminder sent"' in source
    assert "no further reminder · deadline" in source


# ---- 4. the comparable chip -----------------------------------------------------


def test_comparable_chip_counts_from_the_comparison(da, world):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    assert comparable_chip(comparisons(tender, [])) == ""
    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "exw", "EXW Niamey")
    _quote(da, world, "ddp", "DDP Kano", pack_spec_source="not_stated", base_per_pack_stated=None)
    quotes = list(Quote.objects.filter(tender=tender).select_related("supplier__org", "commodity", "item"))
    assert comparable_chip(comparisons(tender, quotes)) == "1 of 3 comparable · Harmattan"


def test_overview_row_carries_the_chip_and_stays_collecting(da, world):
    from connect_labs.supply_chain.standing import _tender_rows

    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    rows = _tender_rows(PROGRAM, TODAY, None)
    row = next(r for r in rows if r.tender_id == world["tender"]["id"])
    assert row.stage_name == "Collecting quotes"
    assert row.comparable_chip == "1 of 1 comparable · Harmattan" and row.comparable_count == 1


def test_tender_status_header_has_the_chip(da, world):
    from connect_labs.supply_chain.procurement.status import tender_status

    _quote(da, world, "cpt", "CPT Kano", freight_basis="included")
    _quote(da, world, "exw", "EXW Niamey")
    status = tender_status(Tender.objects.get(pk=world["tender"]["id"]), TODAY, program_id=PROGRAM)
    assert status["comparable_chip"] == "1 of 2 comparable · Harmattan"
    assert status["stages"][1]["state"] == "now"
