"""Late deliveries.

THIS REPOSITORY IS PUBLIC. Every organisation and figure here is invented.

`Shipment.expected_on` and `Contract.promised_lead_time_days` were both stored
and nothing read them, so a consignment ninety days late looked exactly like
one due tomorrow (field use cases, G3). Both checks report how late, against
which date, from which supplier -- and nothing else. They do not say what to
do about it (design doc section 22).
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.checks import KIND_CATEGORIES
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10613
TODAY = date.today()


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


def _found(da, kind):
    return [c for c in op(da, "checks_list")["checks"] if c["kind"] == kind]


@pytest.fixture
def contract(da):
    op(da, "commodity_upsert", data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L"})
    donor = op(da, "supplier_create", data={"name": "A donor"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    return op(
        da,
        "contract_create",
        data={
            "commodity_slug": "chlorine",
            "supplier_id": donor["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "consideration": "in_kind",
            "source": "we_recorded",
            "reference": "DON-1",
            "status": "placed",
            "quantity": "600",
            "quantity_unit": "jerry_can",
            "signed_on": (TODAY - timedelta(days=120)).isoformat(),
            "promised_lead_time_days": 30,
        },
    )


def _shipment(da, contract, expected_on, status="in_transit"):
    return op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "SHIP-1",
            "status": status,
            "expected_on": expected_on.isoformat(),
            "source": "supplier_reported",
        },
    )


class TestTheyAreThresholdsOnDatesInTheData:
    def test_both_kinds_are_declared(self):
        assert KIND_CATEGORIES["shipment_overdue"] == "threshold"
        assert KIND_CATEGORIES["contract_delivery_overdue"] == "threshold"


class TestShipmentOverdue:
    def test_a_shipment_past_its_expected_date_is_overdue_by_the_days_since(self, da, contract):
        shipment = _shipment(da, contract, TODAY - timedelta(days=90))
        found = _found(da, "shipment_overdue")
        assert len(found) == 1
        check = found[0]
        assert check["subject"]["id"] == shipment["id"]
        assert check["facts"]["days_late"] == 90
        assert check["facts"]["expected_on"] == (TODAY - timedelta(days=90)).isoformat()
        assert check["facts"]["supplier"] == {"id": contract["supplier_id"], "name": "A donor"}
        assert check["days_open"] == 90

    def test_a_late_shipment_on_the_road_is_the_suppliers_to_answer(self, da, contract):
        _shipment(da, contract, TODAY - timedelta(days=9))
        assert _found(da, "shipment_overdue")[0]["audience"] == "supplier"

    def test_a_late_shipment_held_at_customs_is_ours_to_chase(self, da, contract):
        # At customs it is the clearing that is outstanding, which the
        # programme chases -- "only the supplier can answer" was wrong there.
        _shipment(da, contract, TODAY - timedelta(days=9), status="at_customs")
        assert _found(da, "shipment_overdue")[0]["audience"] == "internal"

    def test_one_not_yet_due_is_not(self, da, contract):
        _shipment(da, contract, TODAY + timedelta(days=3))
        assert _found(da, "shipment_overdue") == []

    def test_a_delivered_shipment_is_not_overdue_however_late_it_was(self, da, contract):
        _shipment(da, contract, TODAY - timedelta(days=90), status="delivered")
        assert _found(da, "shipment_overdue") == []

    def test_a_received_shipment_is_not_overdue_even_if_nobody_moved_its_status(self, da, contract):
        shipment = _shipment(da, contract, TODAY - timedelta(days=10))
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded"},
        )
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "shipment_id": shipment["id"],
                "supply_point_id": store["id"],
                "received_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [{"quantity_accepted": "600", "quantity_unit": "jerry_can"}],
            },
        )
        assert _found(da, "shipment_overdue") == []

    def test_a_lost_shipment_is_lost_not_late(self, da, contract):
        _shipment(da, contract, TODAY - timedelta(days=10), status="lost")
        assert _found(da, "shipment_overdue") == []

    def test_a_shipment_with_no_expected_date_cannot_be_late(self, da, contract):
        op(da, "shipment_record", data={"contract_id": contract["id"], "source": "we_recorded"})
        assert _found(da, "shipment_overdue") == []


class TestContractDeliveryOverdue:
    def test_a_contract_past_its_promised_lead_time_and_not_received_is_overdue(self, da, contract):
        found = _found(da, "contract_delivery_overdue")
        assert len(found) == 1
        facts = found[0]["facts"]
        # Signed 120 days ago with 30 days promised: due 90 days ago.
        assert facts["days_late"] == 90
        assert facts["expected_on"] == (TODAY - timedelta(days=90)).isoformat()
        assert facts["promised_lead_time_days"] == 30
        assert facts["supplier"]["name"] == "A donor"
        assert found[0]["days_open"] == 90

    def test_it_says_nothing_about_what_to_do(self, da, contract):
        (check,) = _found(da, "contract_delivery_overdue")
        assert not {"priority", "severity", "recommendation", "message", "action"} & set(check)
        assert not {"priority", "severity", "recommendation", "action"} & set(check["facts"])

    def test_a_contract_with_no_promised_lead_time_cannot_be_late(self, da, contract):
        from connect_labs.supply_chain.models import Contract

        Contract.objects.filter(pk=contract["id"]).update(promised_lead_time_days=None)
        assert _found(da, "contract_delivery_overdue") == []

    def test_a_contract_fully_received_is_not_late(self, da, contract):
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "store", "name": "Store", "kind": "central_store", "source": "we_recorded"},
        )
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": store["id"],
                "received_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [{"quantity_accepted": "600", "quantity_unit": "jerry_can"}],
            },
        )
        assert _found(da, "contract_delivery_overdue") == []

    def test_a_cancelled_contract_is_not_late(self, da, contract):
        op(da, "contract_update", contract_id=contract["id"], data={"status": "cancelled"})
        assert _found(da, "contract_delivery_overdue") == []


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="late", password="x", email="late@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    monkeypatch.setattr("connect_labs.supply_chain.views._access", _scoped)
    monkeypatch.setattr("connect_labs.supply_chain.views.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_checks_page_lists_each_late_delivery_with_its_facts(self, scoped, da, contract):
        from django.urls import reverse

        _shipment(da, contract, TODAY - timedelta(days=90))
        body = scoped.get(reverse("supply_chain:checks")).content.decode()
        assert "Shipment past its expected date" in body
        assert "Delivery past the promised lead time" in body
        # How late is the header's "90 days past the expected date"; the facts
        # beneath it read as labelled words, not as the raw keys that repeated it.
        assert "90 days past the expected date" in body
        assert "days late" not in body
        assert "Promised lead time:" in body and "30 days" in body
        assert "A donor" in body
        # Links back to the order it is about.
        assert reverse("supply_chain:order_detail", args=[contract["id"]]) in body

    def test_the_page_names_itself_in_plain_words(self, scoped, da, contract):
        from django.urls import reverse

        body = scoped.get(reverse("supply_chain:checks")).content.decode()
        assert "Checks: what is missing, in conflict or overdue" in body
        assert "cannot answer, or disagrees with itself" not in body

    def test_with_nothing_to_report_it_says_what_has_arrived(self, scoped, da, contract, monkeypatch):
        from django.urls import reverse

        from connect_labs.supply_chain import views

        _shipment(da, contract, TODAY - timedelta(days=9), status="delivered")
        real_op = views.ChecksView.op

        def op_with_nothing_to_report(self, name, **kwargs):
            if name == "checks_list":
                return {"checks": [], "kinds": {}, "count": 0}
            return real_op(self, name, **kwargs)

        monkeypatch.setattr(views.ChecksView, "op", op_with_nothing_to_report)
        body = scoped.get(reverse("supply_chain:checks")).content.decode()
        assert "Nothing to report" in body
        assert "Arrived" in body and "SHIP-1" in body

    def test_the_checks_page_filters_by_kind(self, scoped, da, contract):
        from django.urls import reverse

        body = scoped.get(reverse("supply_chain:checks") + "?kind=shipment_overdue").content.decode()
        assert "Delivery past the promised lead time" not in body

    def test_the_order_page_says_how_late(self, scoped, da, contract):
        from django.urls import reverse

        _shipment(da, contract, TODAY - timedelta(days=90))
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "90 days past the promised lead time" in body
