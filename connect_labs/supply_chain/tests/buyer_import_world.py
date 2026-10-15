"""A Kano tender on the buyer's duty waiver, quoted on three Incoterms (CPT, EXW, DDP).

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.

Not a test module: the comparison feature tests import its fixtures from here.
"""

from datetime import date

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Quote, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.comparison import compare_tender

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


_ACCEPTANCE = dict(
    shelf_life_months_stated=24, lead_time_days=35, validity_until="2026-11-02", moq=500, moq_unit="carton"
)


def _harmattan(da, world):
    """USD per carton, carriage paid: comparable, costed at nil duty on the tender's waiver."""
    return _quote(da, world, "cpt", "CPT Kano", freight_basis="included", **_ACCEPTANCE)
