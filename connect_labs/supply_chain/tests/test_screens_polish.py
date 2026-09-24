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
from connect_labs.supply_chain.templatetags.supply_chain_extras import buyer_comparison

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


class TestThePerBuyerPanelSaysWhatIsKnown:
    """CodeRabbit on #1975: the panel claimed different amounts from totals that
    were equal, and "none are payable" from totals that were unconfirmed."""

    def test_equal_confirmed_totals_are_said_to_be_the_same_and_nothing_more(self):
        cell = {"amount": "2400", "currency": "USD"}
        assert buyer_comparison({"programme_org": cell, "partner_org": dict(cell), "agency": dict(cell)}) == {
            "state": "same",
            "unconfirmed": [],
        }

    def test_the_same_amount_written_two_ways_is_the_same(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "agency": {"amount": "2400.00", "currency": "USD"},
            }
        )
        assert compared["state"] == "same"

    def test_any_unconfirmed_total_means_it_cannot_be_said(self):
        cell = {"amount": "2400", "currency": "USD"}
        compared = buyer_comparison(
            {"programme_org": cell, "partner_org": {"unconfirmed": ["duty rate not known"]}, "agency": cell}
        )
        assert compared == {"state": "unknown", "unconfirmed": ["partner_org"]}

    def test_confirmed_totals_that_differ_are_said_to_differ(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "partner_org": {"amount": "2760", "currency": "USD"},
                "agency": {"unconfirmed": ["x"]},
            }
        )
        assert compared == {"state": "differ", "unconfirmed": ["agency"]}

    def test_the_order_page_never_infers_none_payable_from_unconfirmed_totals(self, client_in_programme, da, world):
        order = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "partner_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-9",
                "quantity": "600",
                "quantity_unit": "jerry_can",
                "unit_price": "4.00",
                "unit_price_unit": "per_pack",
                "currency": "USD",
                "freight_basis": "included",
                "duties_basis": "not_specified",
                "vat_basis": "not_specified",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        panel = body.split("The same order, per buyer", 1)[1][:4000]
        assert "none are payable" not in panel
        assert "costing different amounts" not in panel
        assert "cannot be said yet" in panel
