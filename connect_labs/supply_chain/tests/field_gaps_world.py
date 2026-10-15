"""Three gaps from the field: a payment the payee confirms, a shortfall another order covers,
durable equipment that is never consumed.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.

Not a test module: test_payment_confirmation, test_shortfall_cover and test_durable_items
import its fixtures from here.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 10616


TODAY = date.today()


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "iptsc", "name": "IPTSc packet", "base_unit": "packet"})
    main = op(da, "supplier_create", data={"name": "Main supplier"})
    local = op(da, "supplier_create", data={"name": "Local vendor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    partner = op(da, "org_upsert", data={"slug": "partner", "name": "A partner"})
    store = op(
        da,
        "supply_point_upsert",
        data={"slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded"},
    )
    return {"main": main, "local": local, "us": us, "partner": partner, "store": store}


def _contract(da, world, supplier, quantity, **extra):
    """An order; an override of None leaves that field out altogether."""
    data = {
        "commodity_slug": "iptsc",
        "supplier_id": supplier["id"],
        "buyer_of_record": "programme_org",
        "buyer_org_id": world["us"]["id"],
        "source": "we_recorded",
        "reference": f"PO-{supplier['id']}-{quantity}",
        "quantity": quantity,
        "quantity_unit": "packet",
        "unit_price": "2.00",
        "unit_price_unit": "per_base_unit",
        **extra,
    }
    return op(da, "contract_create", data={k: v for k, v in data.items() if v is not None})


def _receive(da, world, contract, quantity):
    op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "supply_point_id": world["store"]["id"],
            "received_on": TODAY.isoformat(),
            "source": "we_recorded",
            "lines": [{"quantity_accepted": quantity, "quantity_unit": "packet"}],
        },
    )


def _paid(da, contract, days_ago):
    invoice = op(
        da,
        "invoice_record",
        data={"contract_id": contract["id"], "amount": "900.00", "source": "supplier_reported"},
    )
    return op(
        da,
        "payment_record",
        data={
            "invoice_id": invoice["id"],
            "paid_on": (TODAY - timedelta(days=days_ago)).isoformat(),
            "amount": "900.00",
            "source": "we_recorded",
        },
    )


@pytest.fixture
def dispensers(da, world):
    op(da, "commodity_upsert", data={"slug": "dispenser", "name": "Dispenser", "category": "equipment"})
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "DISP-1",
            "name": "Chlorine dispenser",
            "commodity_slug": "dispenser",
            "base_unit": "dispenser",
            "stock_class": "durable",
        },
    )
    site = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "site",
            "name": "Water point",
            "kind": "facility",
            "source": "we_recorded",
            "min_months_of_stock": "1",
            "max_months_of_stock": "3",
        },
    )
    for days_ago in (60, 20):
        op(
            da,
            "movement_record",
            data={
                "kind": "receipt",
                "occurred_on": (TODAY - timedelta(days=days_ago)).isoformat(),
                "to_supply_point_id": site["id"],
                "item_id": item["id"],
                "commodity_slug": "dispenser",
                "quantity": "2",
                "quantity_unit": "dispenser",
                "source": "we_recorded",
            },
        )
    return item, site


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.fulfilment import views as fulfilment_views  # noqa: F401

    account = django_user_model.objects.create_user(username="small", password="x", email="small@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment.views", "reference.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


def _header(body):
    """The order page's header: from the breadcrumb to the action buttons."""
    return body.rsplit("Orders</a> ›", 1)[-1].split("Edit order", 1)[0]


def _visible(html):
    import re

    return re.sub(r"<[^>]+>", " ", html)


def _short_and_covered(da, world):
    short = _contract(
        da,
        world,
        world["main"],
        "700",
        reference="IPTSC-PO-0715",
        signed_on="2026-07-15",
        promised_lead_time_days=45,
    )
    _receive(da, world, short, "450")
    cover = _contract(
        da,
        world,
        world["local"],
        "250",
        reference="SCHI-LP-0921",
        covers_shortfall_of_id=short["id"],
        buyer_of_record="partner_org",
        buyer_org_id=world["partner"]["id"],
        consideration="bundled",
        unit_price=None,
        unit_price_unit=None,
    )
    _receive(da, world, cover, "250")
    return short, cover
