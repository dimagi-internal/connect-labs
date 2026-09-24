"""Cross-cutting fixes from five filmed procurements (CHC co-packs, an IPTSc
shortfall, a chlorine stop-gap, a dispenser import, test kits).

Each class is one thing more than one walkthrough's judge found, fixed once for
every screen rather than once per walkthrough.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.
"""

import re
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


class TestAttachADocumentFromTheBrowser:
    def test_a_quote_page_offers_attach_and_the_screen_links_to_the_quote(self, client_in_programme, world):
        quote_id = world["quote"]["id"]
        page = client_in_programme.get(reverse("supply_chain:procurement_quote_detail", args=[quote_id])).content
        attach = reverse("supply_chain:quote_document_attach", args=[quote_id])
        assert attach in page.decode()
        form = client_in_programme.get(attach).content.decode()
        assert re.search(r'value="quotation"\s+selected', form)
        response = client_in_programme.post(
            attach,
            {
                "kind": "quotation",
                "title": "Quotation Q-7",
                "external_url": "https://example.org/q7.pdf",
                "source": "document",
            },
        )
        assert response.status_code == 302
        from connect_labs.supply_chain.models import Document

        assert Document.objects.get(title="Quotation Q-7").quote_id == quote_id
        page = client_in_programme.get(reverse("supply_chain:procurement_quote_detail", args=[quote_id])).content
        assert "Quotation Q-7" in page.decode()

    def test_an_approval_takes_its_evidence_and_the_award_page_shows_it(self, client_in_programme, da, world):
        approval = _ask(da, world)
        award_url = reverse("supply_chain:award_detail", args=[world["award"]["id"]])
        attach = reverse("supply_chain:approval_document_attach", args=[approval["id"]])
        assert attach in client_in_programme.get(award_url).content.decode()
        form = client_in_programme.get(attach).content.decode()
        assert "The regulator" in form
        assert re.search(r'value="product_registration"\s+selected', form)
        response = client_in_programme.post(
            attach,
            {
                "kind": "other",
                "title": "Regulator's letter",
                "external_url": "https://example.org/letter.pdf",
                "source": "document",
            },
        )
        assert response.status_code == 302
        body = client_in_programme.get(award_url).content.decode()
        assert "Regulator&#x27;s letter" in body or "Regulator's letter" in body

    def test_a_checklist_line_preselects_its_kind_and_says_which_line_it_fills(self, client_in_programme, da, world):
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-2",
                "quantity": "10",
                "quantity_unit": "jerry_can",
                "consideration": "in_kind",
                "source": "we_recorded",
            },
        )
        shipment = op(
            da,
            "shipment_record",
            data={
                "contract_id": contract["id"],
                "reference": "AWB-5",
                "status": "at_customs",
                "source": "we_recorded",
                "required_documents": [{"kind": "airway_bill", "owed_by_org_id": world["regulator"]["id"]}],
            },
        )
        page = client_in_programme.get(reverse("supply_chain:shipment_detail", args=[shipment["id"]])).content
        attach = reverse("supply_chain:shipment_document_attach", args=[shipment["id"]]) + "?kind=airway_bill"
        assert attach in page.decode()
        form = client_in_programme.get(attach).content.decode()
        assert re.search(r'value="airway_bill"\s+selected', form)
        assert "This fills the “Airway bill” line on the checklist for shipment AWB-5, owed by The regulator" in form

    def test_a_stored_document_opens_through_a_signed_redirect_and_is_scoped(self, client_in_programme, da, world):
        document = _registration(da, world)
        response = client_in_programme.get(reverse("supply_chain:document_open", args=[document["id"]]))
        assert response.status_code == 302
        assert response["Location"] == "https://example.org/registration.pdf"
        assert client_in_programme.get(reverse("supply_chain:document_open", args=[999999])).status_code == 404

    def test_a_refused_upload_is_a_message_not_a_server_error(self, da, world, monkeypatch):
        import base64

        from connect_labs.supply_chain.fulfilment import repository

        def refuse(*args, **kwargs):
            raise PermissionError("403 Forbidden")

        monkeypatch.setattr(repository.default_storage, "save", refuse)
        with pytest.raises(ValueError, match="refused this upload"):
            op(
                da,
                "document_attach",
                data={
                    "kind": "quotation",
                    "quote_id": world["quote"]["id"],
                    "filename": "q.pdf",
                    "content_base64": base64.b64encode(b"%PDF").decode(),
                    "source": "document",
                },
            )


class TestARegulatoryApprovalRestsOnARegistration:
    def test_it_references_the_registration_and_the_award_page_shows_it(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world, rests_on_document_id=registration["id"])
        assert approval["rests_on_document_id"] == registration["id"]
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert "Rests on" in body.decode() and "Registration NG-0001" in body.decode()

    def test_a_document_from_another_programme_is_refused(self, da, world):
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        op(elsewhere, "supplier_create", data={"name": "Elsewhere"})
        foreign = op(
            elsewhere,
            "document_attach",
            data={"kind": "product_registration", "external_url": "https://example.org/x.pdf", "source": "document"},
        )
        with pytest.raises(ValueError, match="does not exist in this programme"):
            _ask(da, world, rests_on_document_id=foreign["id"])

    def test_the_answer_can_name_it_too(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world)
        form = client_in_programme.get(reverse("supply_chain:approval_decide", args=[approval["id"]])).content
        assert "Registration NG-0001" in form.decode()
        decided = op(
            da,
            "approval_decide",
            approval_id=approval["id"],
            status="approved",
            rests_on_document_id=registration["id"],
        )
        assert decided["rests_on_document_id"] == registration["id"]


class TestPlaceOrderWhileAnApprovalIsPending:
    def test_the_button_is_shown_disabled_with_whose_answer_and_since_when(self, client_in_programme, da, world):
        _ask(da, world)
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        body = body.decode()
        assert "Place order" in body
        button = body.split("Place order</button>", 1)[0].rsplit("<button", 1)[1]
        assert "disabled" in button
        assert "regulatory approval from <b>The regulator</b>" in body
        assert "asked 2026-09-01 and not yet answered" in body
        assert reverse("supply_chain:contract_create") + "?award=" not in body

    def test_once_approved_it_is_a_link(self, client_in_programme, da, world):
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert reverse("supply_chain:contract_create") + f"?award={world['award']['id']}" in body.decode()


class TestAnAwardRecordsThePersonWhoDecided:
    def _compare(self, world):
        return reverse("supply_chain:procurement_comparison", args=[world["round"]["id"]]) + "?commodity=chlorine"

    def test_the_form_starts_with_the_signed_in_persons_name(self, client_in_programme, da, world):
        op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
        supplier = op(da, "supplier_create", data={"name": "Second chemicals"})
        op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "4.20",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 20,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        body = client_in_programme.get(self._compare(world)).content.decode()
        assert 'name="decided_by" value="Amina Bello"' in body

    def test_what_is_typed_is_what_is_recorded(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Third chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.90",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(
            self._compare(world),
            {"quote_id": quote["id"], "rationale": "cheaper", "decided_by": "Ngozi Eze"},
        )
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Ngozi Eze"

    def test_left_blank_it_is_the_signed_in_persons_name_not_their_login(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Fourth chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": world["round"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.80",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(self._compare(world), {"quote_id": quote["id"], "rationale": "cheaper"})
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Amina Bello"
