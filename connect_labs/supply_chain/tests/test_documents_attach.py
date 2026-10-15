"""Attaching a document from the browser: to a quote, an approval, a checklist line.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import (
    _ask,
    _registration,
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


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
