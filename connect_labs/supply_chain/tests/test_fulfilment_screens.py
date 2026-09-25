"""The money chain, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every supplier, reference and figure here is
invented.

The thing most worth testing in this tier is the one the tier exists for: a
figure with an invisible assumption inside it. So the tests that matter here
are about `buyer_of_record` being asked rather than defaulted, money crossing
the wire as an exact string, and a claimed duty relief not silently becoming a
relief.
"""

import base64
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import Commodity, Contract, Document, Invoice, Item, Payment, Supplier

pytestmark = pytest.mark.django_db

PROGRAM = 10504
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def user(client, django_user_model):
    """Signed in and attributable — this whole tier records provenance."""
    account = django_user_model.objects.create_user(username="hopper", password="x", email="hopper@dimagi.com")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    for module in ("form_views", "views", "fulfilment_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(scope_key=SCOPE, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton")


@pytest.fixture
def supplier():
    return Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods", type="manufacturer")


@pytest.fixture
def buyer():
    return LabsOrg.objects.create(slug="a-partner", name="A Partner Organisation")


@pytest.fixture
def contract(rutf, supplier, buyer):
    return Contract.objects.create(
        program_id=PROGRAM,
        supplier=supplier,
        commodity=rutf,
        buyer_of_record="partner_org",
        buyer_org=buyer,
        reference="PO-1",
        currency="USD",
        source="we_recorded",
    )


@pytest.fixture
def invoice(contract):
    return Invoice.objects.create(
        contract=contract, reference="INV-1", currency="USD", amount=Decimal("26210.00"), source="we_recorded"
    )


def contract_post(rutf, supplier, buyer, **overrides):
    payload = {
        "supplier": supplier.pk,
        "commodity": rutf.pk,
        "item": "",
        "buyer_of_record": "partner_org",
        "buyer_org": buyer.pk,
        "reference": "PO-2",
        "signed_on": "2026-04-01",
        "status": "placed",
        "currency": "usd",
        "quantity": "500",
        "quantity_unit": "carton",
        "unit_price": "52.42",
        "unit_price_unit": "per_pack",
        "freight_basis": "not_specified",
        "freight_amount": "",
        "duties_basis": "not_specified",
        "duties_amount": "",
        "vat_basis": "not_specified",
        "vat_amount": "",
        "incoterm": "CIF",
        "delivery_supply_point": "",
        "promised_lead_time_days": "",
        "source": "we_recorded",
    }
    payload.update(overrides)
    return payload


class TestRecordingAnOrder:
    def test_an_order_is_created(self, scoped, rutf, supplier, buyer):
        response = scoped.post(reverse("supply_chain:contract_create"), contract_post(rutf, supplier, buyer))
        assert response.status_code == 302

        made = Contract.objects.get(reference="PO-2")
        assert made.program_id == PROGRAM
        assert made.buyer_of_record == "partner_org"
        assert made.buyer_org_id == buyer.pk
        assert made.currency == "USD", "stored upper case"

    def test_the_price_arrives_exact(self, scoped, rutf, supplier, buyer):
        """Money crosses as a string so a float can never tender it.

        52.42 has no exact float representation; if this ever comes back as
        52.419999… the string boundary has been lost somewhere.
        """
        scoped.post(reverse("supply_chain:contract_create"), contract_post(rutf, supplier, buyer))
        assert Contract.objects.get(reference="PO-2").unit_price == Decimal("52.42")

    def test_who_is_buying_cannot_be_left_out(self, scoped, rutf, supplier, buyer):
        """No default, deliberately: duty and VAT fall on whoever imports."""
        response = scoped.post(
            reverse("supply_chain:contract_create"),
            contract_post(rutf, supplier, buyer, buyer_of_record="", buyer_org=""),
        )
        assert response.status_code == 200
        assert "buyer_of_record" in response.context["form"].errors
        assert "buyer_org" in response.context["form"].errors
        assert not Contract.objects.filter(reference="PO-2").exists()

    def test_a_price_has_to_say_what_it_is_per(self, scoped, rutf, supplier, buyer):
        """A price with no basis cannot be compared with anything, and the
        comparison is what this whole tier is for."""
        response = scoped.post(
            reverse("supply_chain:contract_create"),
            contract_post(rutf, supplier, buyer, unit_price_unit=""),
        )
        assert response.status_code == 200
        assert "unit_price_unit" in response.context["form"].errors

    def test_a_trade_item_that_is_not_a_version_of_the_product_is_refused(self, scoped, rutf, supplier, buyer):
        other = Commodity.objects.create(scope_key=SCOPE, slug="rusf", name="RUSF")
        wrong = Item.objects.create(scope_key=SCOPE, sku="X", name="Some RUSF", commodity=other)

        response = scoped.post(
            reverse("supply_chain:contract_create"),
            contract_post(rutf, supplier, buyer, item=wrong.pk),
        )
        assert response.status_code == 200
        assert "item" in response.context["form"].errors

    def test_the_supplier_picker_offers_only_this_programs_suppliers(self, scoped, supplier):
        # Asked of the supplier field, not the page: a supplier's company is a
        # labs-wide organisation, and the buyer picker lists organisations
        # labs-wide on purpose. What must not cross is the other program's
        # SUPPLIER -- its relationship with the company.
        elsewhere = Supplier.objects.enrol(scope_key="prog:99999", name="A supplier in another program")
        response = scoped.get(reverse("supply_chain:contract_create"))
        offered = set(response.context["form"].fields["supplier"].queryset.values_list("pk", flat=True))
        assert supplier.pk in offered
        assert elsewhere.pk not in offered

    def test_a_claimed_duty_relief_reaches_the_row(self, scoped, rutf, supplier, buyer):
        scoped.post(
            reverse("supply_chain:contract_create"),
            contract_post(rutf, supplier, buyer, duty_relief_claimed="on"),
        )
        made = Contract.objects.get(reference="PO-2")
        assert made.duty_relief_claimed
        assert not made.duty_relief_evidenced, "claimed is not evidenced — that is the whole distinction"

    def test_unticking_the_relief_clears_it_rather_than_being_an_omission(self, scoped, contract):
        """A cleared checkbox posts nothing at all, so an update that merges
        only what it was sent would leave the claim standing."""
        contract.duty_relief_claimed = True
        contract.save()

        scoped.post(
            reverse("supply_chain:contract_edit", args=[contract.pk]),
            contract_post(contract.commodity, contract.supplier, contract.buyer_org, reference="PO-1"),
        )
        contract.refresh_from_db()
        assert not contract.duty_relief_claimed

    def test_an_order_from_another_programme_is_not_found(self, scoped, rutf, supplier, buyer):
        theirs = Contract.objects.create(
            program_id=99999,
            supplier=supplier,
            commodity=rutf,
            buyer_of_record="partner_org",
            buyer_org=buyer,
            source="we_recorded",
        )
        assert scoped.get(reverse("supply_chain:contract_edit", args=[theirs.pk])).status_code == 404


class TestInvoicesAndPayments:
    def test_an_invoice_is_recorded_against_the_order_in_the_url(self, scoped, contract):
        response = scoped.post(
            reverse("supply_chain:invoice_record", args=[contract.pk]),
            {
                "reference": "INV-2",
                "issued_on": "2026-04-20",
                "status": "received",
                "currency": "USD",
                "amount": "26210.00",
                "quantity_billed": "500",
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        assert response.status_code == 302

        made = Invoice.objects.get(reference="INV-2")
        assert made.contract_id == contract.pk, "the contract comes from the URL, not from a picker"
        assert made.amount == Decimal("26210.00")
        assert made.source == "supplier_reported"

    def test_a_payment_opens_on_what_is_outstanding(self, scoped, invoice):
        """A part payment is the exception; a full one is the common case."""
        body = scoped.get(reverse("supply_chain:payment_record", args=[invoice.pk])).content.decode()
        assert "26210.00" in body

    def test_a_payment_is_recorded_against_its_invoice(self, scoped, invoice):
        response = scoped.post(
            reverse("supply_chain:payment_record", args=[invoice.pk]),
            {
                "paid_on": "2026-05-02",
                "amount": "26210.00",
                "currency": "USD",
                "method": "bank transfer",
                "reference": "TT-9",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302
        assert Payment.objects.get(reference="TT-9").invoice_id == invoice.pk

    def test_an_invoice_can_be_queried(self, scoped, invoice):
        response = scoped.post(
            reverse("supply_chain:invoice_edit", args=[invoice.pk]),
            {
                "reference": "INV-1",
                "issued_on": "",
                "status": "queried",
                "currency": "USD",
                "amount": "26210.00",
                "quantity_billed": "",
                "quantity_unit": "",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302
        invoice.refresh_from_db()
        assert invoice.status == "queried"

    def test_an_invoice_from_another_programme_is_not_found(self, scoped, rutf, supplier, buyer):
        """An invoice has no programme of its own — it is reached through its
        contract, and that relation is what has to be filtered."""
        theirs = Contract.objects.create(
            program_id=99999,
            supplier=supplier,
            commodity=rutf,
            buyer_of_record="partner_org",
            buyer_org=buyer,
            source="we_recorded",
        )
        their_invoice = Invoice.objects.create(contract=theirs, reference="THEIRS", source="we_recorded")
        assert scoped.get(reverse("supply_chain:invoice_edit", args=[their_invoice.pk])).status_code == 404
        assert scoped.get(reverse("supply_chain:payment_record", args=[their_invoice.pk])).status_code == 404


class TestAttachingEvidence:
    def test_a_file_is_stored_against_the_order(self, scoped, contract):
        upload = SimpleUploadedFile("exemption.pdf", b"not really a pdf", content_type="application/pdf")
        response = scoped.post(
            reverse("supply_chain:document_attach", args=[contract.pk]),
            {
                "kind": "duty_exemption",
                "title": "Duty exemption",
                "external_url": "",
                "upload": upload,
                "source": "document",
            },
        )
        assert response.status_code == 302
        made = Document.objects.get(title="Duty exemption")
        assert made.contract_id == contract.pk

    def test_a_link_is_accepted_instead_of_a_file(self, scoped, contract):
        response = scoped.post(
            reverse("supply_chain:document_attach", args=[contract.pk]),
            {
                "kind": "purchase_order",
                "title": "PO",
                "external_url": "https://example.invalid/po",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302
        assert Document.objects.get(title="PO").external_url == "https://example.invalid/po"

    def test_neither_a_file_nor_a_link_is_refused(self, scoped, contract):
        response = scoped.post(
            reverse("supply_chain:document_attach", args=[contract.pk]),
            {"kind": "purchase_order", "title": "PO", "external_url": "", "source": "we_recorded"},
        )
        assert response.status_code == 200
        assert not Document.objects.filter(title="PO").exists()

    def test_both_a_file_and_a_link_is_refused(self, scoped, contract):
        """Two copies are two documents that can later disagree."""
        upload = SimpleUploadedFile("po.pdf", b"x", content_type="application/pdf")
        response = scoped.post(
            reverse("supply_chain:document_attach", args=[contract.pk]),
            {
                "kind": "purchase_order",
                "title": "PO",
                "external_url": "https://example.invalid/po",
                "upload": upload,
                "source": "we_recorded",
            },
        )
        assert response.status_code == 200
        assert not Document.objects.filter(title="PO").exists()

    def test_the_form_declares_multipart_or_the_upload_never_arrives(self, scoped, contract):
        """A FileField on a form the template posts as urlencoded gives an
        empty request.FILES, which Django then reports as "this field is
        required" on the upload — a message describing the symptom."""
        body = scoped.get(reverse("supply_chain:document_attach", args=[contract.pk])).content.decode()
        assert 'enctype="multipart/form-data"' in body


class TestThePayloadBoundary:
    """What crosses the wire, asserted directly.

    A database tender-trip cannot see the difference between a Decimal and a
    float that happens to tender back, nor between a file sent as bytes and one
    Django coerced to a string. These can.
    """

    def _form(self, cls, data, files=None, **kwargs):
        from connect_labs.labs.access.scopes import SYSTEM
        from connect_labs.supply_chain.data_access import SupplyDataAccess

        access = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)
        form = cls(data, files or {}, access=access, **kwargs)
        assert form.is_valid(), form.errors
        return form

    def test_money_crosses_as_an_exact_string_not_a_float(self, rutf, supplier, buyer):
        from connect_labs.supply_chain.fulfilment_forms import ContractForm

        payload = self._form(ContractForm, contract_post(rutf, supplier, buyer)).payload()
        assert payload["unit_price"] == "52.42"
        assert not isinstance(payload["unit_price"], float)

    def test_the_product_is_named_by_slug_not_by_row_id(self, rutf, supplier, buyer):
        from connect_labs.supply_chain.fulfilment_forms import ContractForm

        payload = self._form(ContractForm, contract_post(rutf, supplier, buyer)).payload()
        assert payload["commodity_slug"] == "rutf"
        assert "commodity_id" not in payload

    def test_a_cleared_checkbox_crosses_as_false_not_as_an_omission(self, rutf, supplier, buyer):
        """`update_contract` sets only the keys it is sent.

        So un-claiming a duty relief depends on False SURVIVING `to_payload`,
        which drops None and "" and nothing else. A `to_payload` rewritten as
        `if not value: continue` would leave every un-tick silently ignored,
        and no browser-level test would notice — the row would simply keep the
        claim it already had.
        """
        from connect_labs.supply_chain.fulfilment_forms import ContractForm

        payload = self._form(ContractForm, contract_post(rutf, supplier, buyer)).payload()
        assert payload["duty_relief_claimed"] is False

    def test_an_uploaded_file_crosses_as_base64_not_as_a_django_file(self):
        from connect_labs.supply_chain.fulfilment_forms import DocumentForm

        upload = SimpleUploadedFile("e.pdf", b"hello", content_type="application/pdf")
        form = self._form(
            DocumentForm,
            {"kind": "duty_exemption", "title": "E", "external_url": "", "source": "document"},
            files={"upload": upload},
        )
        payload = form.payload()
        assert base64.b64decode(payload["content_base64"]) == b"hello"
        assert payload["filename"] == "e.pdf"
        assert "upload" not in payload, "the operation takes bytes; only a browser has an UploadedFile"


class TestTheScreensAreReachable:
    def test_the_orders_board_offers_a_new_order(self, scoped):
        body = scoped.get(reverse("supply_chain:orders")).content.decode()
        assert reverse("supply_chain:contract_create") in body

    def test_an_order_offers_editing_invoicing_and_evidence(self, scoped, contract):
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract.pk])).content.decode()
        assert reverse("supply_chain:contract_edit", args=[contract.pk]) in body
        assert reverse("supply_chain:invoice_record", args=[contract.pk]) in body
        assert reverse("supply_chain:document_attach", args=[contract.pk]) in body

    def test_an_order_shows_the_invoices_it_fetches(self, scoped, invoice):
        """OrderDetailView has always fetched these and rendered none of them,
        so the three-way match could show an invoiced total with nothing behind
        it."""
        body = scoped.get(reverse("supply_chain:order_detail", args=[invoice.contract_id])).content.decode()
        assert "INV-1" in body
        assert reverse("supply_chain:payment_record", args=[invoice.pk]) in body
        assert reverse("supply_chain:invoice_edit", args=[invoice.pk]) in body

    def test_an_invoice_with_no_billed_quantity_says_so(self, scoped, invoice):
        """Without it the three-way match cannot be run at all, which is a
        different thing from a match that ran and found nothing wrong."""
        body = scoped.get(reverse("supply_chain:order_detail", args=[invoice.contract_id])).content.decode()
        assert "not given" in body


class TestTheScreensRefuseWithoutAProgramme:
    def test_the_money_screens_ask_for_a_programme(self, client, user):
        response = client.get(reverse("supply_chain:contract_create"))
        assert response.status_code == 200
        assert "No programme selected" in response.content.decode()
