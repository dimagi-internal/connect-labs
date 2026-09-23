"""Three smaller gaps from the field (use cases, G8).

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

  - the supplier confirming that a payment arrived;
  - a shortfall on one order covered by another -- a partner buying locally
    what the main supplier could not deliver;
  - durable equipment, which moves through the ledger but is never consumed,
    so a consumption rate or months of stock for it would be a made-up number.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.checks import KIND_CATEGORIES, PAYMENT_CONFIRMATION_GRACE_DAYS
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Payment
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

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


def _unconfirmed(da):
    return [c for c in op(da, "checks_list")["checks"] if c["kind"] == "payment_unconfirmed"]


class TestThePayeeConfirmsAPayment:
    def test_it_is_a_missing_fact(self):
        assert KIND_CATEGORIES["payment_unconfirmed"] == "missing"

    def test_a_payment_starts_unconfirmed(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=1)
        assert payment["confirmed_by_payee_on"] is None

    def test_an_old_payment_with_no_confirmation_is_a_check_aged_from_payment(self, da, world):
        contract = _contract(da, world, world["main"], "700")
        payment = _paid(da, contract, days_ago=PAYMENT_CONFIRMATION_GRACE_DAYS + 6)
        (check,) = _unconfirmed(da)
        assert check["subject"] == {"type": "payment", "id": payment["id"], "label": check["subject"]["label"]}
        assert check["audience"] == "supplier"
        assert check["days_open"] == PAYMENT_CONFIRMATION_GRACE_DAYS + 6
        assert check["facts"]["contract_id"] == contract["id"]
        assert check["facts"]["amount"] == "900"

    def test_a_recent_one_is_not_yet(self, da, world):
        _paid(da, _contract(da, world, world["main"], "700"), days_ago=2)
        assert _unconfirmed(da) == []

    def test_confirming_it_clears_it(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=30)
        confirmed = op(da, "payment_confirm", payment_id=payment["id"], confirmed_on=TODAY.isoformat())
        assert confirmed["confirmed_by_payee_on"] == TODAY.isoformat()
        assert _unconfirmed(da) == []

    def test_confirmation_cannot_predate_the_payment(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=3)
        with pytest.raises(ValueError, match="before"):
            op(da, "payment_confirm", payment_id=payment["id"], confirmed_on=(TODAY - timedelta(days=10)).isoformat())


class TestAShortfallCoveredByAnotherOrder:
    def test_an_uncovered_shortfall_is_left_open(self, da, world):
        short = _contract(da, world, world["main"], "700")
        _receive(da, world, short, "450")
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "part_received"
        assert match["covered_by"] == []

    def test_a_covering_order_closes_it_by_name(self, da, world):
        short = _contract(da, world, world["main"], "700")
        _receive(da, world, short, "450")
        cover = _contract(
            da,
            world,
            world["local"],
            "250",
            covers_shortfall_of_id=short["id"],
            buyer_of_record="partner_org",
            buyer_org_id=world["partner"]["id"],
            consideration="bundled",
            unit_price=None,
            unit_price_unit=None,
        )
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "shortfall_covered"
        assert match["covered_by"] == [
            {
                "contract_id": cover["id"],
                "reference": cover["reference"],
                "supplier": {"id": world["local"]["id"], "name": "Local vendor"},
                "quantity": "250",
                "quantity_unit": "packet",
            }
        ]
        # The outstanding quantity is still stated: covered is not the same as
        # received, and the arithmetic stays visible.
        assert match["outstanding"]["amount"] == "250"

    def test_a_covered_order_is_not_reported_late(self, da, world):
        short = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, short, "450")
        _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" not in kinds

    def test_a_cancelled_covering_order_does_not_cover_it(self, da, world):
        short = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, short, "450")
        cover = _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
        op(da, "contract_update", contract_id=cover["id"], data={"status": "cancelled"})
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "part_received"
        assert match["covered_by"] == []
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" in kinds

    def test_a_fully_received_order_invoiced_beyond_receipt_is_not_late(self, da, world):
        full = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, full, "700")
        op(
            da,
            "invoice_record",
            data={
                "contract_id": full["id"],
                "amount": "1600.00",
                "quantity_billed": "800",
                "quantity_unit": "packet",
                "source": "supplier_reported",
            },
        )
        assert op(da, "contract_match", contract_id=full["id"])["status"] == "over_invoiced"
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" not in kinds

    def test_an_order_cannot_cover_itself(self, da, world):
        short = _contract(da, world, world["main"], "700")
        with pytest.raises(ValueError, match="its own shortfall"):
            op(da, "contract_update", contract_id=short["id"], data={"covers_shortfall_of_id": short["id"]})

    def test_it_must_be_an_order_in_this_programme(self, da, world):
        with pytest.raises(ValueError, match="contract"):
            _contract(da, world, world["local"], "250", covers_shortfall_of_id=999999)


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


class TestDurableEquipmentIsNotForecast:
    def test_an_item_is_consumable_unless_it_says_otherwise(self, da, world):
        op(da, "commodity_upsert", data={"slug": "muac", "name": "MUAC tape"})
        assert op(da, "item_upsert", data={"sku": "M", "commodity_slug": "muac"})["stock_class"] == "consumable"

    def test_it_still_has_a_balance(self, da, world, dispensers):
        item, site = dispensers
        on_hand = op(da, "stock_on_hand", supply_point_id=site["id"], item_id=item["id"])
        assert Decimal(on_hand["ledger"]["amount"]) == Decimal("4")

    def test_it_has_no_consumption_rate_months_of_stock_or_resupply(self, da, world, dispensers):
        item, site = dispensers
        plan = op(da, "resupply_plan", supply_point_id=site["id"], item_id=item["id"])
        assert plan["status"] == "durable"
        for key in ("amc", "months_of_stock", "days_to_stockout", "resupply_quantity"):
            assert "not_forecast" in plan[key], key
            assert "durable" in plan[key]["not_forecast"]
        assert Decimal(plan["on_hand"]["amount"]) == Decimal("4")

    def test_it_raises_no_stockout_or_below_minimum(self, da, world, dispensers):
        kinds = {c["kind"] for c in op(da, "checks_list")["checks"]}
        assert not {"stock_stockout", "stock_below_minimum"} & kinds


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="small", password="x", email="small@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views", "reference_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_order_page_shows_each_payment_and_whether_the_payee_confirmed_it(self, scoped, da, world):
        contract = _contract(da, world, world["main"], "700")
        payment = _paid(da, contract, days_ago=30)
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "not confirmed by the payee" in body
        assert reverse("supply_chain:payment_confirm", args=[payment["id"]]) in body

    def test_recording_the_confirmation(self, scoped, da, world):
        contract = _contract(da, world, world["main"], "700")
        payment = _paid(da, contract, days_ago=30)
        response = scoped.post(
            reverse("supply_chain:payment_confirm", args=[payment["id"]]), {"confirmed_by_payee_on": TODAY.isoformat()}
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert Payment.objects.get(pk=payment["id"]).confirmed_by_payee_on == TODAY
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert f"confirmed by the payee {TODAY.isoformat()}" in body

    def test_the_short_order_names_the_order_that_covers_it(self, scoped, da, world):
        short = _contract(da, world, world["main"], "700")
        _receive(da, world, short, "450")
        cover = _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
        body = scoped.get(reverse("supply_chain:order_detail", args=[short["id"]])).content.decode()
        assert "Shortfall covered by" in body
        assert reverse("supply_chain:order_detail", args=[cover["id"]]) in body
        covering = scoped.get(reverse("supply_chain:order_detail", args=[cover["id"]])).content.decode()
        assert "Covers the shortfall on" in covering
        assert reverse("supply_chain:order_detail", args=[short["id"]]) in covering

    def test_the_order_form_offers_the_order_it_covers(self, scoped, da, world):
        _contract(da, world, world["main"], "700")
        body = scoped.get(reverse("supply_chain:contract_create")).content.decode()
        assert 'name="covers_shortfall_of"' in body

    def test_the_item_page_and_form_carry_the_stock_class(self, scoped, da, world, dispensers):
        item, _site = dispensers
        page = scoped.get(reverse("supply_chain:item_detail", args=[item["id"]])).content.decode()
        assert "Durable" in page
        form = scoped.get(reverse("supply_chain:item_edit", args=[item["id"]])).content.decode()
        assert 'name="stock_class"' in form

    def test_the_stock_page_says_durable_not_forecast(self, scoped, da, world, dispensers):
        body = scoped.get(reverse("supply_chain:stock")).content.decode()
        assert "durable — not forecast" in body
