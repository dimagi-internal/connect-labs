"""The IPTSc shortfall world: an order of 700 treatment packets from a distributor, who dispatches
450 through its own update link; the partner receives them through ITS link, buys the missing
250 locally out of its setup fee, and receives them.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.

Not a test module: test_shortfall_screens imports its fixtures from here.
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Contract, Shipment, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.models import UpdateLink

PROGRAM = 10641


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="ngozi", password="x", email="ngozi@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


def _issue(da, **data):
    issued = op(da, "update_link_issue", data=data)
    return issued, UpdateLink.objects.get(pk=issued["id"])


@pytest.fixture
def world(da):
    us = op(da, "org_upsert", data={"slug": "programme", "name": "The programme"})
    distributor_org = op(da, "org_upsert", data={"slug": "harmattan", "name": "Harmattan Health Supplies"})
    partner = op(da, "org_upsert", data={"slug": "schi", "name": "Sahel Community Health Initiative"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "iptsc-packet",
            "name": "IPTSc three-day treatment packet",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
        },
    )
    op(da, "commodity_upsert", data={"slug": "dp-tablet", "name": "DP tablet", "base_unit": "tablet"})
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "iptsc-3day-packet",
            "name": "IPTSc three-day packet (standard)",
            "commodity_slug": "iptsc-packet",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
            "components": [{"commodity_slug": "dp-tablet", "quantity": "9", "base_unit": "tablet"}],
            "one_course_is": "base_unit",
        },
    )
    harmattan = op(da, "supplier_create", data={"name": "Harmattan Health Supplies", "type": "distributor"})
    tamarind = op(da, "supplier_create", data={"name": "Tamarind Pharmacy Wholesale", "type": "trader"})
    store = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "schi-store",
            "name": "SCHI district store",
            "kind": "regional_store",
            "managed_by_org_id": partner["id"],
            "source": "we_recorded",
        },
    )
    order = op(
        da,
        "contract_create",
        data={
            "supplier_id": harmattan["id"],
            "commodity_slug": "iptsc-packet",
            "item_id": item["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "reference": "IPTSC-PO-0715",
            "signed_on": "2026-07-15",
            "status": "confirmed",
            "quantity": "700",
            "quantity_unit": "packet",
            "unit_price": "1.80",
            "unit_price_unit": "per_base_unit",
            "currency": "USD",
            "freight_basis": "included",
            "duties_basis": "included",
            "vat_basis": "included",
            "delivery_supply_point_id": store["id"],
            "promised_lead_time_days": 45,
            "source": "we_recorded",
        },
    )
    distributor_issued, distributor_link = _issue(
        da, org_id=distributor_org["id"], contract_ids=[order["id"]], label="Harmattan"
    )
    partner_issued, partner_link = _issue(
        da, org_id=partner["id"], contract_ids=[order["id"]], supply_point_ids=[store["id"]], label="SCHI"
    )
    return {
        "us": us,
        "partner": partner,
        "item": item,
        "harmattan": harmattan,
        "tamarind": tamarind,
        "store": store,
        "order": order,
        "distributor_token": distributor_issued["token"],
        "partner_token": partner_issued["token"],
        "distributor_link": distributor_link,
        "partner_link": partner_link,
    }


def _dispatch(world):
    return service.submit(
        world["distributor_link"],
        "record_shipment",
        {
            "contract": Contract.objects.get(pk=world["order"]["id"]),
            "reference": "HHS-WB-3310",
            "status": "dispatched",
            "carrier": "Kano Freight",
            "dispatched_on": date(2026, 9, 19),
            "expected_on": date(2026, 9, 22),
            "quantity": "450",
            "unit_basis": "base",
            "batch": "DP-2608-A",
        },
    )


def _receive(world, shipment):
    return service.submit(
        world["partner_link"],
        "record_receipt",
        {
            "contract": Contract.objects.get(pk=world["order"]["id"]),
            "supply_point": SupplyPoint.objects.get(pk=world["store"]["id"]),
            "shipment": Shipment.objects.get(pk=shipment["id"]),
            "received_on": date(2026, 9, 24),
            "reference": "SCHI-GRN-0922",
            "quantity_accepted": "450",
            "unit_basis": "base",
            "batch": "DP-2608-A",
        },
    )


def _cover(da, world, received=True):
    cover = op(
        da,
        "contract_create",
        data={
            "supplier_id": world["tamarind"]["id"],
            "commodity_slug": "iptsc-packet",
            "item_id": world["item"]["id"],
            "buyer_of_record": "partner_org",
            "buyer_org_id": world["partner"]["id"],
            "reference": "SCHI-LP-0921",
            "status": "placed",
            "consideration": "bundled",
            "quantity": "250",
            "quantity_unit": "packet",
            "delivery_supply_point_id": world["store"]["id"],
            "covers_shortfall_of_id": world["order"]["id"],
            "source": "partner_reported",
        },
    )
    if received:
        op(
            da,
            "receipt_record",
            data={
                "contract_id": cover["id"],
                "supply_point_id": world["store"]["id"],
                "reference": "SCHI-GRN-0923",
                "received_on": "2026-09-22",
                "source": "we_recorded",
                "lines": [{"item_id": world["item"]["id"], "quantity_accepted": "250", "quantity_unit": "packet"}],
            },
        )
    return cover


@pytest.fixture
def played(da, world):
    """The whole story: dispatched, received short, covered, cover received."""
    shipment = _dispatch(world)
    _receive(world, shipment)
    world["cover"] = _cover(da, world)
    world["shipment"] = shipment
    return world


def _visible(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _page(client, name, *args, **query):
    url = reverse(f"supply_chain:{name}", args=args)
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    response = client.get(url)
    assert response.status_code == 200, response.content.decode()[:3000]
    return response.content.decode()


def _public(client, token, done=""):
    url = reverse("supply_chain:update_link_public", args=[token])
    return client.get(url + (f"?done={done}" if done else "")).content.decode()
