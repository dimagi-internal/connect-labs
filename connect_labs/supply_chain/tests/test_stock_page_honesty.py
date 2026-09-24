"""What the stock page says about one chlorine store, and the order behind it.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

Found by walking a chlorine stop-gap purchase on the live page: a store whose
donor's in-kind consignment is ninety days late read "1 supply points", asked
for 184.88 jerry cans, credited its consumption to Connect visits that a
partner had keyed in, and said nothing at all about the 400 jerry cans it was
still waiting on. The donation's own page offered to record an invoice for
goods nobody will ever bill.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10613


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can"},
    )
    donor = op(da, "supplier_create", data={"name": "A water donor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    store = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "kano-store",
            "name": "A chlorine store",
            "kind": "central_store",
            "min_months_of_stock": "2",
            "max_months_of_stock": "4",
            "source": "we_recorded",
        },
    )

    def contract(**extra):
        data = {
            "commodity_slug": "chlorine",
            "supplier_id": donor["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "source": "partner_reported",
            "quantity_unit": "jerry_can",
            "consideration": "in_kind",
            "delivery_supply_point_id": store["id"],
            **extra,
        }
        return op(da, "contract_create", data=data)

    # The donation that arrived, in full.
    prior = contract(reference="DON-1", status="placed", quantity="400", signed_on=days_ago(160))
    op(
        da,
        "receipt_record",
        data={
            "contract_id": prior["id"],
            "supply_point_id": store["id"],
            "received_on": days_ago(100),
            "source": "partner_reported",
            "lines": [{"quantity_accepted": "400", "quantity_unit": "jerry_can"}],
        },
    )
    # Dispensed since, as the partner reported it -- not a Connect visit.
    for ago, cans in ((85, "84"), (55, "84"), (25, "83")):
        op(
            da,
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": days_ago(ago),
                "from_supply_point_id": store["id"],
                "commodity_slug": "chlorine",
                "quantity": cans,
                "quantity_unit": "jerry_can",
                "source": "partner_reported",
            },
        )
    return {"donor": donor, "us": us, "store": store, "contract": contract}


def _late_donation(world):
    """Signed 150 days ago on a 60-day lead time: due 90 days ago, nothing arrived."""
    return world["contract"](
        reference="DON-2",
        status="confirmed",
        quantity="400",
        signed_on=days_ago(150),
        promised_lead_time_days=60,
    )


def _row(da, world):
    rows = op(da, "network_stock")["points"]
    return next(r for r in rows if r["supply_point_id"] == world["store"]["id"])


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="stock", password="x", email="stock@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


def _stock_page(scoped):
    return scoped.get(reverse("supply_chain:stock")).content.decode()


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


class TestTheHeadlineCountsItsPoints:
    def test_one_point_is_a_supply_point(self, scoped, world):
        text = _text(_stock_page(scoped))
        assert "1 supply point ·" in text
        assert "1 supply points" not in text
        assert "1 has no count yet" in text


class TestWholePacksAreSentWhole:
    def test_the_send_figure_is_rounded_up_to_a_whole_jerry_can(self, scoped, da, world):
        row = _row(da, world)
        exact = Decimal(row["resupply_quantity"]["amount"])
        assert exact != exact.to_integral_value(), "the scenario needs a fractional resupply to mean anything"
        whole = int(exact.quantize(Decimal(1), rounding=ROUND_CEILING))

        text = _text(_stock_page(scoped))
        assert f"{whole} jerry cans" in text
        # The exact figure is not what the page asks for.
        assert f"{exact.quantize(Decimal('0.01'))} jerry cans" not in text

    def test_the_computation_itself_stays_exact(self, da, world):
        """Presentation rounds; the operation does not."""
        exact = Decimal(_row(da, world)["resupply_quantity"]["amount"])
        assert exact != exact.to_integral_value()

    def test_demand_per_month_claims_no_false_precision(self, scoped, da, world):
        """A three-month average of counted cans reads in whole cans ("84", not "83.72")."""
        amc = Decimal(_row(da, world)["amc"]["amount"])
        text = _text(_stock_page(scoped))
        shown = re.search(r"([\d,]+(?:\.\d+)?) jerry cans dispensed a month", text)
        assert shown is not None, text
        digits = shown.group(1).replace(",", "")
        assert "." not in digits
        assert abs(Decimal(digits) - amc) <= Decimal("0.5")


class TestDemandReadsAsTheDataAllows:
    """One demand rule: whole counted units, one place for a measure, never rounded to nothing."""

    def test_counted_units_are_whole(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import demand_text

        assert demand_text({"amount": "83.7209", "unit": "jerry_can"}) == "84 jerry cans"
        assert demand_text({"amount": "3033.33", "unit": "co-pack"}) == "3,033 co-packs"

    def test_a_measure_keeps_one_place(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import demand_text

        assert demand_text({"amount": "83.72", "unit": "L"}) == "83.7 L"

    def test_under_one_whole_unit_is_not_zero(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import demand_text

        assert demand_text({"amount": "0.4", "unit": "carton"}) == "0.4 cartons"


class TestTheFootnoteSaysWhereConsumptionComesFrom:
    def test_it_does_not_credit_connect_visits_with_a_partners_report(self, scoped, world):
        text = _text(_stock_page(scoped))
        assert "derived from Connect visits" not in text
        assert "not keyed by a clerk" not in text
        # What holds for every source: the ledger's consumption movements.
        assert "consumption movements" in text


class TestStockOnItsWayIsShownButNotCounted:
    def test_a_late_donation_is_listed_under_its_store(self, scoped, da, world):
        late = _late_donation(world)
        row = _row(da, world)
        assert len(row["expected_inbound"]) == 1
        expected = row["expected_inbound"][0]
        assert expected["contract_id"] == late["id"]
        assert expected["outstanding"] == {"amount": "400", "unit": "jerry_can"}
        assert expected["supplier"]["name"] == "A water donor"
        assert expected["expected_on"] == days_ago(90)
        assert expected["overdue"] is True

        text = _text(_stock_page(scoped))
        assert f"expected: 400 jerry cans from A water donor — overdue since {days_ago(90)}" in text
        assert "not counted as cover" in text
        assert reverse("supply_chain:order_detail", args=[late["id"]]) in _stock_page(scoped)

    def test_it_changes_neither_cover_nor_the_band(self, da, world):
        before = _row(da, world)
        _late_donation(world)
        after = _row(da, world)
        for key in ("on_hand", "months_of_stock", "resupply_quantity", "status"):
            assert after[key] == before[key], key
        assert after["status"] == "below_min"

    def test_a_fully_received_order_is_not_expected(self, da, world):
        assert _row(da, world)["expected_inbound"] == []

    def test_a_cancelled_order_is_not_expected(self, da, world):
        world["contract"](reference="DON-3", status="cancelled", quantity="50", signed_on=days_ago(20))
        assert _row(da, world)["expected_inbound"] == []

    def test_a_part_received_order_expects_only_the_rest(self, da, world):
        late = _late_donation(world)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": late["id"],
                "supply_point_id": world["store"]["id"],
                "received_on": days_ago(1),
                "source": "partner_reported",
                "lines": [{"quantity_accepted": "150", "quantity_unit": "jerry_can"}],
            },
        )
        (expected,) = _row(da, world)["expected_inbound"]
        assert expected["outstanding"] == {"amount": "250", "unit": "jerry_can"}

    def test_an_order_paid_in_advance_expects_what_is_awaited_not_what_was_refused(self, da, world):
        """Refused goods on an order paid ahead are owed back, not on their way --
        the order page's "Still outstanding" there is what is awaited."""
        advance = world["contract"](
            reference="ADV-1",
            status="confirmed",
            consideration="priced",
            payment_terms="advance",
            unit_price="2",
            currency="USD",
            quantity="100",
            signed_on=days_ago(20),
            promised_lead_time_days=60,
        )
        op(
            da,
            "receipt_record",
            data={
                "contract_id": advance["id"],
                "supply_point_id": world["store"]["id"],
                "received_on": days_ago(2),
                "source": "partner_reported",
                "lines": [
                    {
                        "quantity_accepted": "60",
                        "quantity_rejected": "10",
                        "rejection_reason": "leaking",
                        "quantity_unit": "jerry_can",
                    }
                ],
            },
        )
        match = op(da, "contract_match", contract_id=advance["id"])
        (expected,) = _row(da, world)["expected_inbound"]
        assert expected["outstanding"] == match["awaiting_delivery"] == {"amount": "30", "unit": "jerry_can"}

    def test_an_order_not_yet_due_is_expected_but_not_overdue(self, scoped, da, world):
        world["contract"](
            reference="DON-4",
            status="confirmed",
            quantity="60",
            signed_on=days_ago(5),
            promised_lead_time_days=60,
        )
        (expected,) = _row(da, world)["expected_inbound"]
        assert expected["overdue"] is False
        text = _text(_stock_page(scoped))
        assert f"expected: 60 jerry cans from A water donor — due {days_ago(-55)}" in text


class TestTheOrderPageOffersBillingOnlyForABoughtOrder:
    def _page(self, scoped, contract):
        return scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()

    def test_a_donation_offers_no_invoice(self, scoped, world):
        donated = _late_donation(world)
        body = self._page(scoped, donated)
        assert reverse("supply_chain:invoice_record", args=[donated["id"]]) not in body
        assert "Record an invoice" not in body

    def test_goods_paid_out_of_a_setup_fee_offer_no_invoice_either(self, scoped, world):
        """The page already treats billing as a bought order's business -- a
        purchase paid out of a setup fee has no invoice to match -- so the
        button follows the panel."""
        bundled = world["contract"](reference="SET-1", consideration="bundled", quantity="10")
        body = self._page(scoped, bundled)
        assert reverse("supply_chain:invoice_record", args=[bundled["id"]]) not in body

    def test_a_priced_order_still_offers_one(self, scoped, world):
        bought = world["contract"](reference="PO-1", consideration="priced", quantity="90")
        body = self._page(scoped, bought)
        assert reverse("supply_chain:invoice_record", args=[bought["id"]]) in body
        assert "Record an invoice" in body
