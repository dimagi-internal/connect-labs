"""Import clearance: the documents a consignment needs, and what it costs to land.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.

Donated dispensers held at customs need an airway bill, a packing list and a
product registration, each owed by somebody; and the fees to clear and move
them are paid to a clearing agent or to customs, not to the donor (field use
cases, G4). "Follow up with the donor when needed" is, as data, a list of
required documents naming who owes each one.
"""

from decimal import Decimal

import jsonschema
import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import records
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Shipment
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10614


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "dispenser", "name": "Chlorine dispenser", "category": "equipment"})
    donor_org = op(da, "org_upsert", data={"slug": "donor", "name": "A donor"})
    agent = op(da, "org_upsert", data={"slug": "agent", "name": "A clearing agent"})
    customs = op(da, "org_upsert", data={"slug": "customs", "name": "Customs service"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    supplier = op(da, "supplier_create", data={"name": "A donor"})
    contract = op(
        da,
        "contract_create",
        data={
            "commodity_slug": "dispenser",
            "supplier_id": supplier["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "source": "we_recorded",
            "reference": "DON-7",
            "quantity": "40",
            "quantity_unit": "dispenser",
            "unit_price": "100.00",
            "unit_price_unit": "per_pack",
            "freight_basis": "included",
            "duties_basis": "included",
            "vat_basis": "included",
        },
    )
    return {"donor": donor_org, "agent": agent, "customs": customs, "us": us, "contract": contract}


def _shipment(da, world, required=None):
    return op(
        da,
        "shipment_record",
        data={
            "contract_id": world["contract"]["id"],
            "reference": "AWB-1",
            "status": "at_customs",
            "source": "supplier_reported",
            "required_documents": (
                required
                if required is not None
                else [
                    {"kind": "airway_bill", "owed_by_org_id": world["donor"]["id"]},
                    {"kind": "packing_list", "owed_by_org_id": world["donor"]["id"]},
                    {"kind": "product_registration", "owed_by_org_id": world["agent"]["id"]},
                ]
            ),
        },
    )


def _outstanding(da):
    return [c for c in op(da, "checks_list")["checks"] if c["kind"] == "shipment_documents_outstanding"]


class TestTheChecksReadRightForAnImport:
    """What the checks list said about the dispenser import when it was filmed."""

    def test_documents_owed_by_several_parties_are_not_the_suppliers_alone_to_answer(self, da, world):
        # Two of the three are owed by the donor, one by the clearing agent;
        # "only the supplier can answer" was taken from the first line alone.
        from connect_labs.supply_chain.models import Supplier

        Supplier.objects.filter(pk=world["contract"]["supplier_id"]).update(org_id=world["donor"]["id"])
        _shipment(da, world)
        (check,) = _outstanding(da)
        assert check["audience"] != "supplier"

    def test_the_label_names_what_is_in_the_consignment(self, da, world):
        _shipment(da, world)
        (check,) = _outstanding(da)
        assert "Chlorine dispenser" in check["subject"]["label"]

    def test_a_consignment_with_its_own_document_list_is_not_also_asked_for_a_certificate(self, da, world):
        # Its list says what it needs to clear. A certificate check on top
        # kept a "missing document" on the checks list after the shipment's
        # own checklist read nothing outstanding.
        _shipment(da, world)
        kinds = {c["kind"] for c in op(da, "checks_list")["checks"]}
        assert "shipment_without_certificate" not in kinds

    def test_a_consignment_without_a_list_still_is(self, da, world):
        _shipment(da, world, required=[])
        kinds = {c["kind"] for c in op(da, "checks_list")["checks"]}
        assert "shipment_without_certificate" in kinds


class TestImportDocumentKinds:
    def test_the_clearance_documents_are_kinds_a_document_can_be(self):
        assert {
            "airway_bill",
            "bill_of_lading",
            "packing_list",
            "commercial_invoice",
            "import_permit",
            "customs_declaration",
            "product_registration",
        } <= set(records.DOCUMENT_KINDS)


class TestRequiredDocuments:
    def test_a_shipment_carries_what_it_needs_and_who_owes_it(self, da, world):
        shipment = _shipment(da, world)
        assert shipment["required_documents"][0] == {"kind": "airway_bill", "owed_by_org_id": world["donor"]["id"]}

    def test_a_required_document_must_be_a_document_kind(self, da, world):
        with pytest.raises(jsonschema.ValidationError):
            _shipment(da, world, required=[{"kind": "a_note_from_mum", "owed_by_org_id": world["donor"]["id"]}])

    def test_the_organisation_that_owes_it_must_exist(self, da, world):
        with pytest.raises(ValueError, match="organisation"):
            _shipment(da, world, required=[{"kind": "airway_bill", "owed_by_org_id": 999999}])

    def test_each_missing_document_is_named_with_who_owes_it(self, da, world):
        shipment = _shipment(da, world)
        (check,) = _outstanding(da)
        assert check["category"] == "missing"
        assert check["subject"] == {"type": "shipment", "id": shipment["id"], "label": check["subject"]["label"]}
        assert check["facts"]["outstanding"] == [
            {"kind": "airway_bill", "owed_by": {"id": world["donor"]["id"], "name": "A donor"}},
            {"kind": "packing_list", "owed_by": {"id": world["donor"]["id"], "name": "A donor"}},
            {"kind": "product_registration", "owed_by": {"id": world["agent"]["id"], "name": "A clearing agent"}},
        ]
        assert check["facts"]["required"] == 3

    def test_attaching_one_takes_it_off_the_list(self, da, world):
        shipment = _shipment(da, world)
        op(
            da,
            "document_attach",
            data={
                "kind": "airway_bill",
                "shipment_id": shipment["id"],
                "external_url": "https://example.org/awb.pdf",
                "source": "document",
            },
        )
        (check,) = _outstanding(da)
        assert [d["kind"] for d in check["facts"]["outstanding"]] == ["packing_list", "product_registration"]

    def test_a_shipment_with_everything_on_file_is_not_flagged(self, da, world):
        shipment = _shipment(da, world, required=[{"kind": "airway_bill", "owed_by_org_id": world["donor"]["id"]}])
        op(
            da,
            "document_attach",
            data={
                "kind": "airway_bill",
                "shipment_id": shipment["id"],
                "external_url": "https://example.org/awb.pdf",
                "source": "document",
            },
        )
        assert _outstanding(da) == []

    def test_a_shipment_that_requires_nothing_is_not_flagged(self, da, world):
        _shipment(da, world, required=[])
        assert _outstanding(da) == []


def _charge(da, shipment, payee, **extra):
    data = {
        "shipment_id": shipment["id"],
        "kind": "customs_fee",
        "payee_org_id": payee["id"],
        "amount": "250.00",
        "currency": "USD",
        "paid_on": "2026-09-10",
        "source": "we_recorded",
        **extra,
    }
    return op(da, "charge_record", data=data)


class TestCharges:
    def test_a_charge_is_paid_to_someone_other_than_the_supplier(self, da, world):
        shipment = _shipment(da, world)
        charge = _charge(da, shipment, world["customs"])
        assert charge["payee_org_id"] == world["customs"]["id"]
        assert charge["kind"] == "customs_fee"
        assert charge["amount"] == "250"
        listed = op(da, "charge_list", shipment_id=shipment["id"])
        assert [c["id"] for c in listed] == [charge["id"]]
        assert [c["id"] for c in op(da, "charge_list", contract_id=world["contract"]["id"])] == [charge["id"]]

    def test_the_kind_is_one_of_the_known_charges(self, da, world):
        shipment = _shipment(da, world)
        with pytest.raises(jsonschema.ValidationError):
            _charge(da, shipment, world["customs"], kind="bribe")

    def test_a_charge_needs_its_provenance(self, da, world):
        shipment = _shipment(da, world)
        with pytest.raises(jsonschema.ValidationError):
            op(
                da,
                "charge_record",
                data={"shipment_id": shipment["id"], "kind": "clearing", "payee_org_id": world["agent"]["id"]},
            )

    def test_a_charge_on_another_programmes_shipment_is_refused(self, da, world):
        shipment = _shipment(da, world)
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        with pytest.raises(ValueError, match="shipment"):
            _charge(elsewhere, shipment, world["customs"])

    def test_a_document_can_evidence_a_charge(self, da, world):
        shipment = _shipment(da, world)
        charge = _charge(da, shipment, world["customs"])
        document = op(
            da,
            "document_attach",
            data={
                "kind": "proof_of_payment",
                "charge_id": charge["id"],
                "external_url": "https://example.org/receipt.pdf",
                "source": "document",
            },
        )
        assert document["links"]["charge_id"] == charge["id"]
        assert op(da, "charge_list", shipment_id=shipment["id"])[0]["document_ids"] == [document["id"]]


class TestChargesAreInTheLandedCost:
    def test_the_landed_total_adds_every_charge_on_the_contracts_shipments_itemised(self, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], kind="customs_fee", amount="250.00")
        _charge(da, shipment, world["agent"], kind="clearing", amount="120.50")
        landed = op(da, "contract_landed_cost", contract_id=world["contract"]["id"])

        assert [(c["kind"], c["amount"]["amount"]) for c in landed["charges"]] == [
            ("customs_fee", "250"),
            ("clearing", "120.5"),
        ]
        assert landed["charges"][0]["payee"] == {"id": world["customs"]["id"], "name": "Customs service"}
        assert Decimal(landed["charges_total"]["amount"]) == Decimal("370.50")
        # 40 at 100.00, everything else included, plus the charges.
        assert Decimal(landed["landed_total"]["amount"]) == Decimal("4370.50")

    def test_a_charge_in_another_currency_needs_its_rate(self, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], currency="NGN", amount="380000")
        landed = op(da, "contract_landed_cost", contract_id=world["contract"]["id"])
        assert "unconfirmed" in landed["charges_total"]
        assert any("NGN" in reason for reason in landed["landed_total"]["unconfirmed"])

    def test_with_a_rate_it_is_converted(self, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], currency="NGN", amount="380000", fx_rate_to_usd="0.00065")
        landed = op(da, "contract_landed_cost", contract_id=world["contract"]["id"])
        assert Decimal(landed["charges_total"]["amount"]) == Decimal("247")

    def test_goods_given_in_kind_still_show_what_landing_them_cost(self, da, world):
        from connect_labs.supply_chain.models import Contract

        Contract.objects.filter(pk=world["contract"]["id"]).update(consideration="in_kind", unit_price=None)
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], amount="250.00")
        landed = op(da, "contract_landed_cost", contract_id=world["contract"]["id"])
        assert landed["goods"] == {"not_costed": "not purchased (in kind)"}
        assert Decimal(landed["charges_total"]["amount"]) == Decimal("250")


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, stock_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="clear", password="x", email="clear@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views", "stock_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheShipmentPage:
    def test_it_shows_the_checklist_with_who_owes_each_document(self, scoped, da, world):
        shipment = _shipment(da, world)
        op(
            da,
            "document_attach",
            data={
                "kind": "airway_bill",
                "shipment_id": shipment["id"],
                "external_url": "https://example.org/awb.pdf",
                "source": "document",
            },
        )
        body = scoped.get(reverse("supply_chain:shipment_detail", args=[shipment["id"]])).content.decode()
        assert "Documents it needs" in body
        assert "Airway bill" in body
        assert "on file" in body
        assert "Packing list" in body
        assert "outstanding" in body
        assert "A clearing agent" in body
        # Each outstanding one offers attaching exactly that kind.
        assert reverse("supply_chain:shipment_document_attach", args=[shipment["id"]]) + "?kind=packing_list" in body

    def test_it_shows_the_charges_and_offers_recording_one(self, scoped, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], amount="250.00")
        body = scoped.get(reverse("supply_chain:shipment_detail", args=[shipment["id"]])).content.decode()
        assert "Customs service" in body
        assert "250" in body
        assert reverse("supply_chain:charge_record", args=[shipment["id"]]) in body

    def test_the_order_page_links_to_each_shipment(self, scoped, da, world):
        shipment = _shipment(da, world)
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        assert reverse("supply_chain:shipment_detail", args=[shipment["id"]]) in body

    def test_the_checks_page_names_each_missing_document_and_who_owes_it(self, scoped, da, world):
        _shipment(da, world)
        body = scoped.get(reverse("supply_chain:checks")).content.decode()
        assert "Packing list — owed by A donor" in body
        assert "Product registration — owed by A clearing agent" in body
        assert "kind packing_list" not in body

    def test_an_airway_bill_is_not_a_certificate(self, scoped, da, world):
        # The order page's Certificate column read "on file" for any attached
        # document, while the checks list -- counting only certificates --
        # said "no certificate on file" about the same consignment.
        shipment = _shipment(da, world)
        op(
            da,
            "document_attach",
            data={
                "kind": "airway_bill",
                "shipment_id": shipment["id"],
                "external_url": "https://example.org/awb.pdf",
                "source": "document",
            },
        )
        assert op(da, "shipment_get", shipment_id=shipment["id"])["has_certificate"] is False
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        shipments = body.split(">Shipments<", 1)[1].split(">Received<", 1)[0]
        assert "on file" not in shipments
        op(
            da,
            "document_attach",
            data={
                "kind": "certificate_of_conformity",
                "shipment_id": shipment["id"],
                "external_url": "https://example.org/coc.pdf",
                "source": "document",
            },
        )
        assert op(da, "shipment_get", shipment_id=shipment["id"])["has_certificate"] is True
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        assert "on file" in body.split(">Shipments<", 1)[1].split(">Received<", 1)[0]

    def test_the_order_page_itemises_the_charges_in_landed_cost(self, scoped, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["agent"], kind="clearing", amount="120.50")
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        landed = body.split("Landed cost", 1)[1].split("Ordered", 1)[0]
        assert "Clearing" in landed
        assert "A clearing agent" in landed


class TestTheShipmentScreens:
    def test_requiring_a_document_adds_it_to_the_list(self, scoped, da, world):
        shipment = _shipment(da, world, required=[])
        response = scoped.post(
            reverse("supply_chain:shipment_require_document", args=[shipment["id"]]),
            {"kind": "import_permit", "owed_by_org": world["donor"]["id"], "source": "we_recorded"},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert Shipment.objects.get(pk=shipment["id"]).required_documents == [
            {"kind": "import_permit", "owed_by_org_id": world["donor"]["id"]}
        ]

    def test_requiring_a_document_keeps_who_reported_the_shipment(self, scoped, da, world):
        shipment = _shipment(da, world, required=[])
        page = scoped.get(reverse("supply_chain:shipment_require_document", args=[shipment["id"]])).content.decode()
        assert 'name="source"' not in page
        response = scoped.post(
            reverse("supply_chain:shipment_require_document", args=[shipment["id"]]),
            {"kind": "import_permit", "owed_by_org": world["donor"]["id"]},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert Shipment.objects.get(pk=shipment["id"]).source == "supplier_reported"

    def test_recording_a_charge(self, scoped, da, world):
        shipment = _shipment(da, world)
        response = scoped.post(
            reverse("supply_chain:charge_record", args=[shipment["id"]]),
            {
                "kind": "clearing",
                "payee_org": world["agent"]["id"],
                "amount": "120.50",
                "currency": "usd",
                "fx_rate_to_usd": "",
                "paid_on": "2026-09-10",
                "note": "",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        (charge,) = op(da, "charge_list", shipment_id=shipment["id"])
        assert charge["amount"] == "120.5"
        assert charge["currency"] == "USD"

    def test_attaching_a_document_to_the_shipment(self, scoped, da, world):
        shipment = _shipment(da, world)
        page = scoped.get(
            reverse("supply_chain:shipment_document_attach", args=[shipment["id"]]) + "?kind=packing_list"
        ).content.decode()
        import re

        assert re.search(r'value="packing_list"\s+selected', page)
        response = scoped.post(
            reverse("supply_chain:shipment_document_attach", args=[shipment["id"]]),
            {
                "kind": "packing_list",
                "title": "Packing list",
                "external_url": "https://example.org/pl.pdf",
                "source": "document",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        (check,) = _outstanding(da)
        assert "packing_list" not in [d["kind"] for d in check["facts"]["outstanding"]]


class TestTheScreensSpeakPlainly:
    """Database values that reached the dispenser-import walkthrough's screens."""

    def _receive(self, da, world):
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "wh", "name": "Distributor warehouse", "kind": "central_store", "source": "we_recorded"},
        )
        op(
            da,
            "receipt_record",
            data={
                "contract_id": world["contract"]["id"],
                "supply_point_id": store["id"],
                "received_on": "2026-09-20",
                "source": "partner_reported",
                "lines": [
                    {
                        "quantity_accepted": "38",
                        "quantity_rejected": "2",
                        "rejection_reason": "cracked",
                        "quantity_unit": "dispenser",
                    }
                ],
            },
        )
        return store

    def test_the_checks_page_says_where_a_shipment_is_in_words(self, scoped, da, world):
        _shipment(da, world)
        body = scoped.get(reverse("supply_chain:checks")).content.decode()
        assert "at_customs" not in body
        assert "at customs" in body

    def test_the_order_page_says_who_told_us_and_where_it_was_received(self, scoped, da, world):
        _shipment(da, world)
        self._receive(da, world)
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        received = body.split(">Received<", 1)[1].split(">Invoices<", 1)[0]
        assert "partner_reported" not in received
        assert "a partner told us" in received
        assert "Distributor warehouse" in received
        assert "2 dispenser" in received

    def test_a_donated_order_is_not_bought_and_its_landed_total_is_what_landing_it_cost(self, scoped, da, world):
        from connect_labs.supply_chain.models import Contract

        Contract.objects.filter(pk=world["contract"]["id"]).update(
            consideration="in_kind", unit_price=None, unit_price_unit=""
        )
        shipment = _shipment(da, world)
        _charge(da, shipment, world["agent"], kind="clearing", amount="120.50")
        body = scoped.get(reverse("supply_chain:order_detail", args=[world["contract"]["id"]])).content.decode()
        assert "Bought by" not in body
        assert "Donated to" in body
        landed = body.split("Landed cost", 1)[1].split("Ordered", 1)[0]
        total = landed.split("Landed total", 1)[1]
        assert "120.5" in total
        assert "goods donated" in total

    def test_the_shipment_page_says_how_we_know_and_formats_money(self, scoped, da, world):
        shipment = _shipment(da, world)
        _charge(da, shipment, world["customs"], amount="410000", currency="NGN", fx_rate_to_usd="0.00065")
        body = scoped.get(reverse("supply_chain:shipment_detail", args=[shipment["id"]])).content.decode()
        assert "told by supplier reported" not in body
        assert "410,000" in body
