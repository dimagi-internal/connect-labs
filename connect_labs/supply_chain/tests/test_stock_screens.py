"""The physical chain, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every supplier, batch and figure here is invented.

Three rules in this tier are load-bearing enough that a screen breaking one
would be worse than the screen not existing, so they are what these tests are
mostly about:

  * a receipt is the only event that brings stock into existence;
  * the ledger is append-only, so a correction is another movement and there is
    deliberately no edit screen;
  * only an adjustment may be negative — every other kind's direction comes
    from what the kind means, in one place, so a movement counted the wrong way
    round cannot silently double or zero a balance.
"""

from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import (
    Commodity,
    Contract,
    Item,
    Movement,
    Receipt,
    Shipment,
    StockCount,
    Supplier,
    SupplyPoint,
)

pytestmark = pytest.mark.django_db

PROGRAM = 10505
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="lovelace", password="x", email="lovelace@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import (  # noqa: F401  -- bind before patching
        form_views,
        fulfilment_views,
        stock_views,
        views,
    )
    from connect_labs.supply_chain.api_views import _access as real_access

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    for module in ("form_views", "views", "fulfilment_views", "stock_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")


@pytest.fixture
def plumpy(rutf):
    return Item.objects.create(scope_key=SCOPE, sku="NUT-92", name="A 92 g sachet", commodity=rutf)


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="central-store", name="Central store", kind="central_store", source="we_recorded"
    )


@pytest.fixture
def other_store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="kano", name="Kano store", kind="regional_store", source="we_recorded"
    )


@pytest.fixture
def contract(rutf):
    return Contract.objects.create(
        program_id=PROGRAM,
        supplier=Supplier.objects.create(scope_key=SCOPE, name="Northwind Foods"),
        commodity=rutf,
        buyer_of_record="programme_org",
        buyer_org=LabsOrg.objects.create(slug="us", name="Us"),
        reference="PO-1",
        source="we_recorded",
    )


def lines(**overrides):
    """One batch row, as a formset posts it."""
    payload = {
        "lines-TOTAL_FORMS": "1",
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "1",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": "",
        "lines-0-batch": "L-2026-04",
        "lines-0-expiry": "2028-04-01",
        "lines-0-quantity": "500",
        "lines-0-quantity_unit": "carton",
        "lines-0-quantity_rejected": "",
        "lines-0-rejection_reason": "",
    }
    payload.update(overrides)
    return payload


def movement_post(rutf, **overrides):
    payload = {
        "kind": "transfer",
        "occurred_on": "2026-05-01",
        "from_supply_point": "",
        "to_supply_point": "",
        "commodity": rutf.pk,
        "item": "",
        "batch": "",
        "expiry": "",
        "quantity": "50",
        "quantity_unit": "carton",
        "reference": "",
        "source": "we_recorded",
    }
    payload.update(overrides)
    return payload


def count_post(rutf, store, **overrides):
    payload = {
        "supply_point": store.pk,
        "commodity": rutf.pk,
        "item": "",
        "batch": "",
        "kind": "physical_count",
        "counted_on": "2026-05-01",
        "quantity": "480",
        "quantity_unit": "carton",
        "reason": "",
        "source": "we_recorded",
    }
    payload.update(overrides)
    return payload


class TestRecordingAReceipt:
    def test_stock_comes_into_existence(self, scoped, contract, store, plumpy):
        response = scoped.post(
            reverse("supply_chain:receipt_record", args=[contract.pk]),
            {
                "supply_point": store.pk,
                "reference": "GRN-1",
                "received_on": "2026-05-01",
                "source": "we_recorded",
                **lines(**{"lines-0-item": plumpy.pk}),
            },
        )
        assert response.status_code == 302

        made = Receipt.objects.get(reference="GRN-1")
        assert made.contract_id == contract.pk
        assert made.supply_point_id == store.pk
        line = made.lines.get()
        assert line.quantity_accepted == Decimal("500")
        assert line.batch == "L-2026-04"
        assert line.expiry == date(2028, 4, 1)
        assert line.item_id == plumpy.pk

    def test_zero_accepted_is_a_real_receipt(self, scoped, contract, store):
        """The consignment arrived and was refused in full. Recording it is
        what stops the order reading as outstanding forever."""
        response = scoped.post(
            reverse("supply_chain:receipt_record", args=[contract.pk]),
            {
                "supply_point": store.pk,
                "reference": "GRN-0",
                "received_on": "2026-05-01",
                "source": "we_recorded",
                **lines(
                    **{
                        "lines-0-quantity": "0",
                        "lines-0-quantity_rejected": "500",
                        "lines-0-rejection_reason": "seals broken",
                    }
                ),
            },
        )
        assert response.status_code == 302
        line = Receipt.objects.get(reference="GRN-0").lines.get()
        assert line.quantity_accepted == Decimal("0")
        assert line.quantity_rejected == Decimal("500")

    def test_a_refusal_needs_a_reason(self, scoped, contract, store):
        response = scoped.post(
            reverse("supply_chain:receipt_record", args=[contract.pk]),
            {
                "supply_point": store.pk,
                "reference": "GRN-2",
                "received_on": "2026-05-01",
                "source": "we_recorded",
                **lines(**{"lines-0-quantity_rejected": "100", "lines-0-rejection_reason": ""}),
            },
        )
        assert response.status_code == 200
        assert not Receipt.objects.filter(reference="GRN-2").exists()

    def test_a_receipt_with_no_lines_is_refused(self, scoped, contract, store):
        response = scoped.post(
            reverse("supply_chain:receipt_record", args=[contract.pk]),
            {
                "supply_point": store.pk,
                "reference": "GRN-3",
                "received_on": "2026-05-01",
                "source": "we_recorded",
                **lines(**{"lines-0-quantity": "", "lines-0-quantity_unit": ""}),
            },
        )
        assert response.status_code == 200
        assert not Receipt.objects.filter(reference="GRN-3").exists()

    def test_the_supply_point_picker_offers_only_this_programmes_points(self, scoped, contract, store):
        SupplyPoint.objects.create(
            program_id=99999,
            slug="theirs",
            name="A store in another programme",
            kind="central_store",
            source="we_recorded",
        )
        body = scoped.get(reverse("supply_chain:receipt_record", args=[contract.pk])).content.decode()
        assert "Central store" in body
        assert "A store in another programme" not in body


class TestRecordingADispatch:
    def test_a_dispatch_is_recorded_with_its_batches(self, scoped, contract, plumpy):
        response = scoped.post(
            reverse("supply_chain:shipment_record", args=[contract.pk]),
            {
                "reference": "DN-1",
                "sscc": "",
                "status": "dispatched",
                "dispatched_on": "2026-04-20",
                "expected_on": "2026-05-01",
                "carrier": "A carrier",
                "source": "supplier_reported",
                **lines(**{"lines-0-item": plumpy.pk}),
            },
        )
        assert response.status_code == 302

        made = Shipment.objects.get(reference="DN-1")
        assert made.contract_id == contract.pk
        assert made.is_in_transit, "dispatched and not yet received is never stock on hand"
        assert made.lines.get().quantity == Decimal("500")

    def test_a_dispatch_is_not_asked_what_was_refused(self, scoped, contract):
        """Nothing has arrived yet. Those fields are removed, not shown and ignored."""
        body = scoped.get(reverse("supply_chain:shipment_record", args=[contract.pk])).content.decode()
        assert "lines-0-quantity_unit" in body
        assert "lines-0-quantity_rejected" not in body

    def test_moving_a_consignment_along_keeps_the_rest_of_it(self, scoped, contract):
        """Asking for the whole dispatch again is how a carrier gets wiped by
        somebody recording that it cleared customs."""
        shipment = Shipment.objects.create(
            contract=contract, reference="DN-1", carrier="A carrier", status="dispatched", source="we_recorded"
        )
        response = scoped.post(
            reverse("supply_chain:shipment_status", args=[shipment.pk]),
            {"status": "at_customs", "expected_on": "2026-05-10", "source": "partner_reported"},
        )
        assert response.status_code == 302
        shipment.refresh_from_db()
        assert shipment.status == "at_customs"
        assert shipment.carrier == "A carrier", "a status change must not blank what it did not ask about"

    def test_a_shipment_from_another_programme_is_not_found(self, scoped, rutf):
        theirs = Contract.objects.create(
            program_id=99999,
            supplier=Supplier.objects.create(scope_key="prog:99999", name="Theirs"),
            commodity=rutf,
            buyer_of_record="programme_org",
            source="we_recorded",
        )
        their_shipment = Shipment.objects.create(contract=theirs, reference="X", source="we_recorded")
        assert scoped.get(reverse("supply_chain:shipment_status", args=[their_shipment.pk])).status_code == 404


class TestTheLedger:
    def test_a_transfer_is_posted(self, scoped, rutf, store, other_store):
        response = scoped.post(
            reverse("supply_chain:movement_record"),
            movement_post(rutf, from_supply_point=store.pk, to_supply_point=other_store.pk),
        )
        assert response.status_code == 302
        made = Movement.objects.get(program_id=PROGRAM)
        assert made.kind == "transfer"
        assert made.from_supply_point_id == store.pk
        assert made.to_supply_point_id == other_store.pk

    def test_a_transfer_needs_both_ends(self, scoped, rutf, store):
        response = scoped.post(
            reverse("supply_chain:movement_record"),
            movement_post(rutf, from_supply_point=store.pk),
        )
        assert response.status_code == 200
        assert not Movement.objects.filter(program_id=PROGRAM).exists()

    def test_only_an_adjustment_may_be_negative(self, scoped, rutf, store, other_store):
        """Every other kind's direction comes from what the kind means, in one
        place — so a movement counted the wrong way round cannot silently
        double or zero a balance."""
        response = scoped.post(
            reverse("supply_chain:movement_record"),
            movement_post(rutf, quantity="-50", from_supply_point=store.pk, to_supply_point=other_store.pk),
        )
        assert response.status_code == 200
        # On the field, in words. The database constraint catches this as well,
        # as `__all__` and by its own name, which is not an error message.
        assert "quantity" in response.context["form"].errors
        assert not Movement.objects.filter(program_id=PROGRAM).exists()

    def test_an_adjustment_may_be_negative(self, scoped, rutf, store):
        response = scoped.post(
            reverse("supply_chain:movement_record"),
            movement_post(
                rutf, kind="adjustment", quantity="-20", from_supply_point=store.pk, reference="count variance"
            ),
        )
        assert response.status_code == 302
        assert Movement.objects.get(program_id=PROGRAM).quantity == Decimal("-20")

    def test_a_movement_of_nothing_is_refused_in_words(self, scoped, rutf, store, other_store):
        """The database refuses this too, via the `movement_positive_unless_adjustment`
        constraint — so asserting only "no row was written" proves nothing about
        the form. What the form adds is a sentence: a constraint name is not an
        error message for a person, and it arrives as a banner rather than on
        the field that is wrong.
        """
        response = scoped.post(
            reverse("supply_chain:movement_record"),
            movement_post(rutf, quantity="0", from_supply_point=store.pk, to_supply_point=other_store.pk),
        )
        assert response.status_code == 200
        assert "quantity" in response.context["form"].errors
        assert "not a movement" in str(response.context["form"].errors["quantity"])
        assert not Movement.objects.filter(program_id=PROGRAM).exists()

    def test_there_is_no_way_to_edit_a_movement(self, scoped, rutf, store):
        """Not an omission. An edit screen would be offering to make a past
        balance irreproducible."""
        from django.urls import NoReverseMatch

        with pytest.raises(NoReverseMatch):
            reverse("supply_chain:movement_edit", args=[1])


class TestRecordingACount:
    def test_a_count_is_an_observation_and_moves_nothing(self, scoped, rutf, store):
        response = scoped.post(reverse("supply_chain:stock_count_record"), count_post(rutf, store))
        assert response.status_code == 302

        made = StockCount.objects.get(program_id=PROGRAM)
        assert made.quantity == Decimal("480")
        assert made.kind == "physical_count"
        assert made.adjustment_movement_id is None, "only an override writes a compensating adjustment"

    def test_a_count_of_zero_is_a_stockout_not_a_missing_answer(self, scoped, rutf, store):
        """`to_payload` drops None and "" and nothing else, so zero survives.
        Dropping it would report the point as never having been counted."""
        response = scoped.post(reverse("supply_chain:stock_count_record"), count_post(rutf, store, quantity="0"))
        assert response.status_code == 302
        assert StockCount.objects.get(program_id=PROGRAM).quantity == Decimal("0")

    def test_an_override_has_to_say_why(self, scoped, rutf, store):
        """It asserts a figure over both the ledger and the last count."""
        response = scoped.post(
            reverse("supply_chain:stock_count_record"),
            count_post(rutf, store, kind="override", reason=""),
        )
        assert response.status_code == 200
        assert not StockCount.objects.filter(program_id=PROGRAM).exists()

    def test_an_override_with_a_reason_writes_its_compensating_adjustment(self, scoped, rutf, store):
        response = scoped.post(
            reverse("supply_chain:stock_count_record"),
            count_post(rutf, store, kind="override", reason="recount after the audit"),
        )
        assert response.status_code == 302
        assert StockCount.objects.get(program_id=PROGRAM).adjustment_movement_id is not None


class TestThePayloadBoundary:
    def _form(self, cls, data):
        from connect_labs.labs.access.scopes import SYSTEM
        from connect_labs.supply_chain.data_access import SupplyDataAccess

        access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        form = cls(data, access=access)
        assert form.is_valid(), form.errors
        return form

    def test_a_count_of_zero_survives_to_payload(self, rutf, store):
        """Invisible to a round-trip that only ever posts a non-zero count,
        and the difference between a stockout and an unrecorded point."""
        from connect_labs.supply_chain.stock_forms import StockCountForm

        payload = self._form(StockCountForm, count_post(rutf, store, quantity="0")).payload()
        assert payload["quantity"] == "0"

    def test_a_receipt_line_carries_its_quantity_as_a_string(self, rtf_unused=None):
        """Invisible to a round-trip.

        The receipt-line schema accepts a number as well as a string, and the
        quantities a test would naturally use (500, 0) are exactly
        representable as floats — so a `float(...)` here round-trips through
        the database unchanged and every browser-level test stays green while
        the precision guarantee is gone.
        """
        from connect_labs.supply_chain.stock_views import ReceiptRecordView

        row = {
            "quantity": Decimal("1234567890123.4567"),
            "quantity_unit": "carton",
            "item": None,
            "batch": "",
            "expiry": None,
            "quantity_rejected": None,
            "rejection_reason": "",
        }
        line = ReceiptRecordView().line_payload(row)
        assert line["quantity_accepted"] == "1234567890123.4567"
        assert not isinstance(line["quantity_accepted"], float)

    def test_a_dispatch_line_does_the_same(self):
        from connect_labs.supply_chain.stock_views import ShipmentRecordView

        row = {
            "quantity": Decimal("1234567890123.4567"),
            "quantity_unit": "carton",
            "item": None,
            "batch": "",
            "expiry": None,
        }
        line = ShipmentRecordView().line_payload(row)
        assert line["quantity"] == "1234567890123.4567"

    def test_quantities_cross_as_exact_strings(self, rutf, store, other_store):
        from connect_labs.supply_chain.stock_forms import MovementForm

        data = movement_post(rutf, quantity="12.3456", from_supply_point=store.pk, to_supply_point=other_store.pk)
        payload = self._form(MovementForm, data).payload()
        assert payload["quantity"] == "12.3456"
        assert not isinstance(payload["quantity"], float)

    def test_the_product_is_named_by_slug(self, rutf, store):
        from connect_labs.supply_chain.stock_forms import StockCountForm

        payload = self._form(StockCountForm, count_post(rutf, store)).payload()
        assert payload["commodity_slug"] == "rutf"
        assert "commodity_id" not in payload


class TestTheScreensAreReachable:
    def test_an_order_offers_a_dispatch_and_a_receipt(self, scoped, contract):
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract.pk])).content.decode()
        assert reverse("supply_chain:shipment_record", args=[contract.pk]) in body
        assert reverse("supply_chain:receipt_record", args=[contract.pk]) in body

    def test_a_shipment_row_offers_moving_it_along(self, scoped, contract, plumpy):
        shipment = Shipment.objects.create(contract=contract, reference="DN-1", source="we_recorded")
        shipment.lines.create(item=plumpy, quantity=Decimal("500"), quantity_unit="carton")
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract.pk])).content.decode()
        assert reverse("supply_chain:shipment_status", args=[shipment.pk]) in body

    def test_the_stock_page_offers_a_movement_and_a_count(self, scoped):
        body = scoped.get(reverse("supply_chain:stock")).content.decode()
        assert reverse("supply_chain:movement_record") in body
        assert reverse("supply_chain:stock_count_record") in body


class TestTheScreensRefuseWithoutAProgramme:
    def test_the_ledger_screens_ask_for_a_programme(self, client, user):
        for name in ("supply_chain:movement_record", "supply_chain:stock_count_record"):
            response = client.get(reverse(name))
            assert response.status_code == 200, name
            assert "No programme selected" in response.content.decode(), name
