"""What the CHC co-pack render showed at iteration 3, fixed before iteration 4.

THIS REPOSITORY IS PUBLIC. Every organisation, person and number is invented.

- A distributor catching up recorded an order confirmation, a payment received
  and 600 inspected cartons in one sitting, and the order page listed all three
  at "24 Sep 2026, 12:08". A link update now says when it HAPPENED.
- One link page printed "2026-09-24", "24 Sep 2026" and "24 September 2026";
  the payment picker read "18000 USD paid 2026-09-24"; the product picker cut
  "Kaduna Pharma Works ORS/zinc co-pack (kpw-orszinc-copack)" mid-word.
- The count form never showed the ledger balance a count is kept beside or,
  as an override, replaces.
- An alert to an address named the address, never the person.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Contract, Invoice, Payment, SupplyPoint
from connect_labs.supply_chain.stock_views import ledger_balances
from connect_labs.supply_chain.tests.test_update_links import _world, op
from connect_labs.supply_chain.update_links import forms, service
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission

pytestmark = pytest.mark.django_db

PROGRAM = 10511


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(da):
    return _world(da)


@pytest.fixture
def link(da, world):
    issued = op(
        da,
        "update_link_issue",
        data={
            "org_id": world["eha"]["id"],
            "contract_ids": [world["contract"]["id"]],
            "supply_point_ids": [world["warehouse"]["id"], world["llo"]["id"]],
        },
    )
    return UpdateLink.objects.get(pk=issued["id"]), issued["token"]


def _contract(world):
    return Contract.objects.get(pk=world["contract"]["id"])


def _payment(world):
    contract = _contract(world)
    invoice = Invoice.objects.create(
        contract=contract,
        reference="INV-1",
        quantity_billed=Decimal("100"),
        quantity_unit="carton",
        amount=Decimal("18000"),
        currency="USD",
    )
    return Payment.objects.create(invoice=invoice, amount=Decimal("18000"), currency="USD", paid_on=date(2026, 9, 18))


class TestALinkUpdateSaysWhenItHappened:
    def test_the_date_they_give_is_kept_and_the_typing_day_beside_it(self, link, world):
        link_row, _ = link
        today = timezone.localdate()
        service.submit(link_row, "confirm_order", {"contract": _contract(world), "confirmed_on": today - timedelta(6)})
        service.submit(
            link_row,
            "record_receipt",
            {
                "contract": _contract(world),
                "supply_point": SupplyPoint.objects.get(pk=world["warehouse"]["id"]),
                "received_on": today - timedelta(1),
                "quantity_accepted": "96",
                "unit_basis": "pack",
            },
        )
        submissions = UpdateLinkSubmission.objects.order_by("pk")
        assert [s.happened_on for s in submissions] == [today - timedelta(6), today - timedelta(1)]

        updates = service.updates_for_contract(_contract(world))
        assert [u["on"] for u in updates] == [today - timedelta(1), today - timedelta(6)]
        assert all(u["caught_up"] for u in updates), "typed today about earlier days"

    def test_the_order_page_lists_them_in_the_order_they_happened(self, link, world):
        """Typed newest-first, the receipt landed above; typed in a different
        order, the order page still reads the events in their own order."""
        link_row, _ = link
        today = timezone.localdate()
        service.submit(
            link_row,
            "record_receipt",
            {
                "contract": _contract(world),
                "supply_point": SupplyPoint.objects.get(pk=world["warehouse"]["id"]),
                "received_on": today - timedelta(1),
                "quantity_accepted": "96",
                "unit_basis": "pack",
            },
        )
        service.submit(link_row, "confirm_payment", {"payment": _payment(world), "received_on": today - timedelta(6)})
        titles = [u["title"] for u in service.updates_for_contract(_contract(world))]
        assert titles == ["Goods received", "Payment confirmed"]

    def test_an_action_without_a_date_happened_when_it_was_recorded(self, link, world):
        link_row, _ = link
        service.submit(link_row, "confirm_order", {"contract": _contract(world)})
        (update,) = service.updates_for_contract(_contract(world))
        assert update["on"] == timezone.localdate()
        assert not update["caught_up"]

    def test_the_confirm_form_asks_the_day_and_starts_at_today(self, link):
        link_row, _ = link
        form = forms.ConfirmOrderForm(scope=service.scope_for(link_row))
        assert "confirmed_on" in form.fields
        assert form.initial["confirmed_on"] == timezone.localdate()


class TestTheLinkPageReadsOneWay:
    def test_the_payment_picker_reads_money_and_dates_as_every_screen_does(self, world):
        label = forms._PaymentChoice(queryset=Payment.objects.all()).label_from_instance(_payment(world))
        assert label == "USD 18,000.00 paid 18 Sep 2026 — PO-1"

    def test_the_product_picker_names_the_product_not_our_slug(self, world):
        from connect_labs.supply_chain.models import Item

        item = Item.objects.get(pk=world["item"]["id"])
        assert forms._ItemChoice(queryset=Item.objects.all()).label_from_instance(item) == "EHA co-pack"

    def test_the_order_picker_has_a_row_of_its_own(self, link):
        """Half a row cut the order's name to "Kaduna Pharma Works ORS/zi"."""
        link_row, _ = link
        scope = service.scope_for(link_row)
        for form_class in (forms.RecordReceiptForm, forms.RecordShipmentForm, forms.ConfirmOrderForm):
            assert "contract" in form_class(scope=scope).rows(), form_class.__name__

    def test_no_date_on_the_page_is_written_two_ways(self, client, link, world):
        link_row, token = link
        payment = _payment(world)
        service.submit(link_row, "confirm_payment", {"payment": payment, "received_on": date(2026, 9, 19)})
        body = client.get(reverse("supply_chain:update_link_public", args=[token])).content.decode()
        assert "received on 19 Sep 2026" in body
        assert "2026-09-" not in body.replace('value="2026-09-', ""), "an ISO date is printed as text"


class TestTheCountFormShowsTheLedger:
    def test_the_balance_of_each_point_and_item_is_handed_to_the_form(self, da, world, link):
        link_row, _ = link
        service.submit(
            link_row,
            "record_receipt",
            {
                "contract": _contract(world),
                "supply_point": SupplyPoint.objects.get(pk=world["warehouse"]["id"]),
                "received_on": timezone.localdate(),
                "quantity_accepted": "194",
                "unit_basis": "pack",
            },
        )
        balances = ledger_balances(PROGRAM)
        warehouse, item = world["warehouse"]["id"], world["item"]["id"]
        figure = balances[f"{warehouse}:{item}"]
        assert figure["known"] and figure["text"] == "194 cartons"
        assert (figure["unit"], figure["plural"]) == ("carton", "cartons")
        # A count without a trade item is compared against the point's only item.
        assert balances[f"{warehouse}:"]["text"] == "194 cartons"

    def test_the_page_carries_them(self, client, django_user_model, monkeypatch):
        from connect_labs.supply_chain import form_views, stock_views  # noqa: F401
        from connect_labs.supply_chain.api_views import _access as real_access

        client.force_login(django_user_model.objects.create_user(username="amara", password="x"))

        def _scoped(request):
            access = real_access(request)
            access.program_id = PROGRAM
            return access

        monkeypatch.setattr("connect_labs.supply_chain.form_views.has_program_context", lambda request: True)
        monkeypatch.setattr("connect_labs.supply_chain.form_views._access", _scoped)
        monkeypatch.setattr("connect_labs.supply_chain.stock_views._access", _scoped)
        body = client.get(reverse("supply_chain:stock_count_record")).content.decode()
        assert 'id="ledger-balances"' in body
        assert "The ledger holds" in body


class TestAnAlertNamesWhoItTells:
    def test_an_address_can_carry_a_name(self, da):
        created = op(
            da,
            "alert_subscription_create",
            data={
                "label": "Co-packs below a store's minimum",
                "check_kinds": ["stock_below_minimum"],
                "recipient_email": "amara.bello@chp.example",
                "recipient_name": "Amara Bello",
            },
        )
        assert created["recipient"] == "Amara Bello"
        assert created["recipient_email"] == "amara.bello@chp.example"

    def test_a_name_is_dropped_when_the_alert_moves_to_a_labs_user(self, da, django_user_model):
        user = django_user_model.objects.create_user(username="amara2", password="x", email="a2@chp.example")
        created = op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["stock_below_minimum"], "recipient_email": "x@chp.example", "recipient_name": "X"},
        )
        op(da, "alert_subscription_update", subscription_id=created["id"], data={"recipient_user_id": user.pk})
        assert AlertSubscription.objects.get(pk=created["id"]).recipient_name == ""

    def test_the_sent_log_says_who_by_name(self, client, django_user_model, monkeypatch):
        from connect_labs.supply_chain import views  # noqa: F401
        from connect_labs.supply_chain.alerts import views as alert_views  # noqa: F401
        from connect_labs.supply_chain.api_views import _access as real_access

        client.force_login(django_user_model.objects.create_user(username="amara3", password="x"))

        def _scoped(request):
            access = real_access(request)
            access.program_id = PROGRAM
            return access

        for module in ("views", "alerts.views"):
            monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
            monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
        sub = AlertSubscription.objects.create(
            program_id=PROGRAM,
            label="Co-packs low",
            check_kinds=["stock_below_minimum"],
            recipient_email="amara.bello@chp.example",
            recipient_name="Amara Bello",
        )
        AlertNotice.objects.create(
            subscription=sub,
            program_id=PROGRAM,
            kind="check",
            subject_kind="stock_below_minimum",
            subject={"type": "supply_point", "id": 1, "label": "Sahel CHI store"},
            detected_at=timezone.now(),
            delivery="queued",
            sent_at=timezone.now(),
            sent_to="amara.bello@chp.example",
        )
        body = client.get(reverse("supply_chain:alerts")).content.decode()
        assert "to Amara Bello · amara.bello@chp.example" in body
