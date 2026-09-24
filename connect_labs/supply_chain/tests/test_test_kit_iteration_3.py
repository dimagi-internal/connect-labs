"""What the test-kit walkthrough's second render asked for.

THIS REPOSITORY IS PUBLIC. Every organisation, product and number is invented.

  - an approver sees the letter it uploaded in its own read-back, and a
    fully answered approver link says so instead of listing "record your
    answer" as unavailable;
  - the order the approval released says the answer came through the
    approver's own link, and links the letter;
  - the stock page's labels read as words: "Record stock in or out",
    "Enough stock?", and the item's standing names its specification once.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from connect_labs.supply_chain.models import AwardApproval
from connect_labs.supply_chain.tests import test_test_kit_iteration_2 as base

pytestmark = pytest.mark.django_db

# The fixtures and helpers the iteration-2 file already builds the world with.
PDF = base.PDF
op = base.op
da = base.da
scoped = base.scoped
world = base.world
aqualytic_link = base.aqualytic_link


def _answer(client, link, world, note="Meets AQ-TK-1.", upload=True):
    data = {
        "action": "record_answer",
        "record_answer-approval": world["technical"]["id"],
        "record_answer-status": "approved",
        "record_answer-note": note,
    }
    if upload:
        data["record_answer-document_file"] = SimpleUploadedFile(
            "aqualytic-confirmation.pdf", PDF, content_type="application/pdf"
        )
    url = reverse("supply_chain:update_link_public", args=[link["token"]])
    response = client.post(url, data)
    assert response.status_code == 302, response.content.decode()[:2000]
    return client.get(response["Location"]).content.decode()


class TestTheApproverSeesItsAnswer:
    def test_the_read_back_names_the_letter_and_ends_once(self, client, settings, tmp_path, aqualytic_link, world):
        settings.MEDIA_ROOT = str(tmp_path)
        page = _answer(client, aqualytic_link, world)
        assert "with the signed confirmation aqualytic-confirmation.pdf" in page
        assert "AQ-TK-1.”." not in page and "AQ-TK-1.”" not in page

    def test_an_answered_link_says_nothing_more_is_needed(self, client, settings, tmp_path, aqualytic_link, world):
        settings.MEDIA_ROOT = str(tmp_path)
        page = _answer(client, aqualytic_link, world)
        assert "data-all-answered" in page
        assert "record your answer." not in page
        assert "Record an update" not in page


class TestTheOrderSaysWhoReleasedIt:
    def test_it_names_the_approvers_own_link_and_the_letter(
        self, client, scoped, settings, tmp_path, da, aqualytic_link, world
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        _answer(client, aqualytic_link, world)
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "test-kit",
                "item_id": world["lumen"]["id"],
                "award_id": world["award"]["id"],
                "quantity": "20",
                "quantity_unit": "kit",
                "status": "placed",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["aqualytic"]["id"],
                "source": "we_recorded",
            },
        )
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "through Aqualytic&#x27;s own link" in body or "through Aqualytic's own link" in body
        document = AwardApproval.objects.get(pk=world["technical"]["id"]).documents.get()
        assert reverse("supply_chain:document_open", args=[document.id]) in body


class TestTheStockReadsAsWords:
    def test_labels(self, scoped, world):
        body = scoped.get(reverse("supply_chain:stock")).content.decode()
        assert "Record stock in or out" in body and "Post a movement" not in body
        assert "Enough stock?" in body and "Against its band" not in body


class TestTheComparisonAndTheAward:
    def test_the_award_form_is_labelled_and_never_prefills_a_login(self, scoped, da, world):
        from connect_labs.supply_chain.models import Award

        Award.objects.all().delete()
        op(
            da,
            "quote_record",
            data={
                "round_id": world["award"]["round_id"],
                "commodity_slug": "test-kit",
                "supplier_id": world["supplier"]["id"],
                "item_id": world["lumen"]["id"],
                "as_quoted_amount": "38.00",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "20",
                "quantity_basis_unit": "kit",
                "pack_spec_source": "trade_item_confirmed",
                "base_per_pack_stated": 50,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        body = scoped.get(
            reverse("supply_chain:procurement_comparison", args=[world["award"]["round_id"]]) + "?commodity=test-kit"
        ).content.decode()
        assert "Why this offer" in body and "Decided by" in body and "<th>Award</th>" in body
        assert 'name="decided_by" value="kits2"' not in body

    def test_the_award_is_headed_by_what_was_chosen(self, scoped, world):
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert '<h2 class="text-lg font-semibold text-gray-900">Lumen FC-50 kit</h2>' in body

    def test_a_pending_approval_offers_a_link_prefilled_to_that_question(self, scoped, world):
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        href = (
            reverse("supply_chain:update_link_issue")
            + f"?org={world['aqualytic']['id']}&amp;approval={world['technical']['id']}"
        )
        assert href in body and "Send them a link to answer" in body
        form = scoped.get(href.replace("&amp;", "&")).content.decode()
        assert f'value="{world["technical"]["id"]}" checked' in form or (
            f'value="{world["technical"]["id"]}"' in form and "checked" in form
        )


class TestFreightIncludedIsSaid:
    def test_included_freight_reads_as_included(self, scoped, da, world):
        op(da, "approval_decide", approval_id=world["technical"]["id"], status="approved")
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "test-kit",
                "item_id": world["lumen"]["id"],
                "award_id": world["award"]["id"],
                "quantity": "20",
                "quantity_unit": "kit",
                "unit_price": "38.00",
                "freight_basis": "included",
                "status": "placed",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["aqualytic"]["id"],
                "source": "we_recorded",
            },
        )
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "included in the price" in body
