"""Cross-cutting fixes from five filmed procurements (CHC co-packs, an IPTSc
shortfall, a chlorine stop-gap, a dispenser import, test kits).

Each class is one thing more than one walkthrough's judge found, fixed once for
every screen rather than once per walkthrough.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.
"""

import sys
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

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
    from connect_labs.supply_chain import (  # noqa: F401  -- import every screen module before patching
        distribution_views,
        form_views,
        fulfilment_views,
        network_views,
        reference_views,
        stock_views,
        views,
    )
    from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401
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
    round_ = op(
        da,
        "round_create",
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
            "round_id": round_["id"],
            "commodity_slug": "chlorine",
            "supplier_id": supplier["id"],
            "as_quoted_amount": "4.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
        },
    )
    award = op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="registered locally")
    return {"supplier": supplier, "us": us, "regulator": regulator, "round": round_, "quote": quote, "award": award}


class TestDonorSuppliers:
    def test_a_supplier_can_be_a_donor_and_the_pages_say_so(self, client_in_programme, da):
        donor = op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        assert donor["type"] == "donor"
        body = client_in_programme.get(reverse("supply_chain:supplier_detail", args=[donor["id"]])).content.decode()
        assert "Donor" in body and "supplies in kind" in body
        listing = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
        assert "Donor — supplies in kind" in listing

    def test_the_supplier_form_offers_donor(self, client_in_programme):
        body = client_in_programme.get(reverse("supply_chain:supplier_create")).content.decode()
        assert 'value="donor"' in body

    def test_a_picker_names_a_donor_as_one(self, client_in_programme, da, world):
        op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        body = client_in_programme.get(reverse("supply_chain:contract_create")).content.decode()
        assert "Water For All (donor)" in body
