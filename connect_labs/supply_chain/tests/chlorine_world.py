"""The chlorine stop-gap world: a tender for 600 jerry cans, one quote, awarded; a regulator to approve.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.

Not a test module: the award, approval, document, alert and check screen tests import its
fixtures from here (as do test_chlorine_iteration_five and test_sheets_b3_compare).
"""

import sys
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

PROGRAM = 10632


TODAY = date.today()


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def account(django_user_model):
    return django_user_model.objects.create_user(
        username="amina", password="x", email="amina@dimagi.com", name="Amina Bello"
    )


@pytest.fixture
def client_in_programme(client, account, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.distribution import views as distribution_views  # noqa: F401
    from connect_labs.supply_chain.fulfilment import views as fulfilment_views  # noqa: F401
    from connect_labs.supply_chain.network import views as network_views  # noqa: F401
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401
    from connect_labs.supply_chain.reference import views as reference_views  # noqa: F401
    from connect_labs.supply_chain.stock import views as stock_views  # noqa: F401
    from connect_labs.supply_chain.update_links import views as link_views  # noqa: F401

    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for name, module in list(sys.modules.items()):
        if not name.startswith("connect_labs.supply_chain") or name.endswith("api_views"):
            continue
        if hasattr(module, "_access"):
            monkeypatch.setattr(module, "_access", _scoped)
        if hasattr(module, "has_program_context"):
            monkeypatch.setattr(module, "has_program_context", lambda request: True)
    return client


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can", "base_per_pack": 20},
    )
    supplier = op(da, "supplier_create", data={"name": "Sahel Chemicals", "type": "distributor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    regulator = op(da, "org_upsert", data={"slug": "regulator", "name": "The regulator"})
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Stop-gap chlorine",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "chlorine", "quantity": "600", "quantity_unit": "jerry_can"}],
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "commodity_slug": "chlorine",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "4.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
        },
    )
    award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"], rationale="registered locally")
    return {"supplier": supplier, "us": us, "regulator": regulator, "tender": tender, "quote": quote, "award": award}


def _registration(da, world):
    return op(
        da,
        "document_attach",
        data={
            "kind": "product_registration",
            "supplier_id": world["supplier"]["id"],
            "title": "Registration NG-0001",
            "external_url": "https://example.org/registration.pdf",
            "source": "document",
        },
    )


def _ask(da, world, **extra):
    return op(
        da,
        "approval_request",
        data={
            "award_id": world["award"]["id"],
            "approver_org_id": world["regulator"]["id"],
            "role": "regulatory",
            "requested_on": "2026-09-01",
            **extra,
        },
    )


def _comparable_pair(da, world):
    """Two comparable chlorine quotes on the world's tender, neither awarded yet."""
    op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
    for name, price in (("Second chemicals", "4.20"), ("Third chemicals", "4.40")):
        supplier = op(da, "supplier_create", data={"name": name})
        op(
            da,
            "quote_record",
            data={
                "tender_id": world["tender"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": price,
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 20,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )


def _compare_url(world):
    return reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=chlorine"
