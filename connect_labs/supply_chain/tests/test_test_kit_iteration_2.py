"""What the test-kit walkthrough asked for next: fewer codes, fewer URLs, one more answer.

THIS REPOSITORY IS PUBLIC. Every organisation, product and number is invented.

  - the requirement editor offers the figures the catalogue already knows, in
    words, instead of making the buyer type `range_max_mg_per_l`;
  - an approver's link takes the signed confirmation as an uploaded file, not
    only a URL to one;
  - the links screen is "Update links": approvers use it as well as suppliers;
  - the order header says what its Incoterm means;
  - the stock page, for one trade item, says which specification the kit meets
    and who approved it -- the balance is only worth planning on if that holds.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import AwardApproval, Commodity
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.compliance import figure_label, figure_unit
from connect_labs.supply_chain.templatetags.supply_chain_extras import incoterm_words
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

PROGRAM = 10621
SCOPE = f"prog:{PROGRAM}"
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, reference_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.fulfilment import views as fulfilment_views  # noqa: F401
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    account = django_user_model.objects.create_user(username="kits2", password="x", email="kits2@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment.views", "procurement.views", "reference_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "test-kit",
            "name": "Free chlorine test kit",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "spec_reference": "AQ-TK-1",
            "spec_requirements": [
                {"field": "tests_per_kit", "operator": ">=", "value": 50, "unit": "tests"},
                {"field": "range_max_mg_per_l", "operator": ">=", "value": 2.0, "unit": "mg/L"},
            ],
        },
    )
    lumen = op(
        da,
        "item_upsert",
        data={
            "sku": "lumen-fc50",
            "name": "Lumen FC-50 kit",
            "commodity_slug": "test-kit",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "spec_attributes": {"tests_per_kit": 50, "range_max_mg_per_l": 3.5, "shelf_life_months": 24},
        },
    )
    supplier = op(da, "supplier_create", data={"name": "Harmattan Health Supplies"})
    aqualytic = op(da, "org_upsert", data={"slug": "aqualytic", "name": "Aqualytic"})
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Test kits",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "test-kit", "quantity": "20", "quantity_unit": "kit"}],
        },
    )
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": tender["id"],
            "commodity_slug": "test-kit",
            "supplier_id": supplier["id"],
            "item_id": lumen["id"],
            "as_quoted_amount": "38.00",
            "as_quoted_unit": "per_pack",
            "incoterm": "DAP",
        },
    )
    award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"], rationale="Reads the dose")
    technical = op(
        da,
        "approval_request",
        data={"award_id": award["id"], "approver_org_id": aqualytic["id"], "role": "technical"},
    )
    return {"lumen": lumen, "supplier": supplier, "aqualytic": aqualytic, "award": award, "technical": technical}


# ---- the requirement editor ---------------------------------------------


class TestFigureNames:
    @pytest.mark.parametrize(
        "field, unit, label",
        [
            ("range_max_mg_per_l", "mg/L", "Range maximum (mg/L)"),
            ("shelf_life_months", "months", "Shelf life (months)"),
            ("tests_per_kit", "", "Tests per kit"),
            ("available_chlorine_pct", "%", "Available chlorine (%)"),
        ],
    )
    def test_a_figure_reads_as_words_with_its_unit(self, field, unit, label):
        assert figure_unit(field) == unit
        assert figure_label(field) == label


def _requirement_post(rows):
    data = {
        "slug": "test-kit",
        "name": "Free chlorine test kit",
        "base_unit": "test",
        "pack_unit": "kit",
        "base_per_pack": "50",
        "spec_reference": "AQ-TK-1",
        "requirements-TOTAL_FORMS": str(len(rows)),
        "requirements-INITIAL_FORMS": "0",
        "requirements-MIN_NUM_FORMS": "0",
        "requirements-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"requirements-{index}-{key}"] = value
    return data


class TestTheRequirementPicker:
    def test_it_offers_the_figures_the_items_state_in_words(self, scoped, world):
        body = scoped.get(reverse("supply_chain:product_edit", args=["test-kit"])).content.decode()
        assert '<option value="range_max_mg_per_l"' in body
        assert "Range maximum (mg/L)" in body
        # Stated on the item, not yet required: still offered, because it is checkable.
        assert "Shelf life (months)" in body
        # The rows already on the product arrive picked.
        assert '<option value="tests_per_kit" selected' in body

    def test_a_picked_figure_fills_its_own_unit(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            _requirement_post(
                [{"field": "range_max_mg_per_l", "operator": ">=", "value": "2.0", "rationale": "reads the dose"}]
            ),
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        saved = Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements
        assert saved == [
            {
                "field": "range_max_mg_per_l",
                "operator": ">=",
                "value": 2.0,
                "unit": "mg/L",
                "rationale": "reads the dose",
            }
        ]

    def test_a_new_figure_can_be_named(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            _requirement_post([{"new_field": "reagent_count", "operator": ">=", "value": "50"}]),
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        saved = Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements
        assert saved[0]["field"] == "reagent_count"

    def test_picking_and_naming_both_is_refused(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            _requirement_post(
                [{"field": "tests_per_kit", "new_field": "reagent_count", "operator": ">=", "value": "50"}]
            ),
        )
        assert response.status_code == 200
        assert "not both" in response.content.decode()

    def test_neither_is_refused(self, scoped, world):
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            _requirement_post([{"operator": ">=", "value": "50"}]),
        )
        assert response.status_code == 200
        assert "Choose the figure" in response.content.decode()


# ---- the approver's signed confirmation, uploaded ---------------------------


@pytest.fixture
def aqualytic_link(da, world):
    return op(
        da,
        "update_link_issue",
        data={"org_id": world["aqualytic"]["id"], "approval_ids": [world["technical"]["id"]], "label": "Aqualytic"},
    )


class TestTheApproverUploadsItsLetter:
    def test_an_uploaded_letter_is_stored_against_the_approval(
        self, client, settings, tmp_path, aqualytic_link, world
    ):
        settings.MEDIA_ROOT = str(tmp_path)
        url = reverse("supply_chain:update_link_public", args=[aqualytic_link["token"]])
        page = client.get(url).content.decode()
        assert 'enctype="multipart/form-data"' in page
        assert 'name="record_answer-document_file"' in page
        response = client.post(
            url,
            {
                "action": "record_answer",
                "record_answer-approval": world["technical"]["id"],
                "record_answer-status": "approved",
                "record_answer-note": "Meets AQ-TK-1.",
                "record_answer-document_file": SimpleUploadedFile(
                    "aqualytic-confirmation.pdf", PDF, content_type="application/pdf"
                ),
            },
        )
        assert response.status_code == 302, response.content.decode()[:3000]
        approval = AwardApproval.objects.get(pk=world["technical"]["id"])
        assert approval.status == "approved"
        document = approval.documents.get()
        assert document.storage_key and not document.external_url
        assert document.size_bytes == len(PDF)
        assert document.recorded_by_org_id == world["aqualytic"]["id"]
        assert document.title == "Aqualytic's signed confirmation"

    def test_a_file_that_is_not_a_document_is_refused(self, client, aqualytic_link, world):
        response = client.post(
            reverse("supply_chain:update_link_public", args=[aqualytic_link["token"]]),
            {
                "action": "record_answer",
                "record_answer-approval": world["technical"]["id"],
                "record_answer-status": "approved",
                "record_answer-document_file": SimpleUploadedFile("run.exe", b"MZ", content_type="application/x"),
            },
        )
        assert response.status_code == 200
        assert AwardApproval.objects.get(pk=world["technical"]["id"]).status == "requested"

    def test_a_file_and_a_link_together_are_refused(self, client, aqualytic_link, world):
        response = client.post(
            reverse("supply_chain:update_link_public", args=[aqualytic_link["token"]]),
            {
                "action": "record_answer",
                "record_answer-approval": world["technical"]["id"],
                "record_answer-status": "approved",
                "record_answer-document_url": "https://files.example/letter.pdf",
                "record_answer-document_file": SimpleUploadedFile("letter.pdf", PDF, content_type="application/pdf"),
            },
        )
        assert response.status_code == 200
        assert "not both" in response.content.decode()


# ---- names and words -------------------------------------------------------


class TestUpdateLinksByName:
    def test_the_tab_and_the_page_say_update_links(self, scoped):
        body = scoped.get(reverse("supply_chain:update_links")).content.decode()
        assert "Update links" in body
        assert "Supplier links" not in body


class TestIncotermInWords:
    @pytest.mark.parametrize(
        "term, words",
        [
            ("DAP", "DAP — delivered at place: the supplier pays freight to the named place"),
            ("dap Kano", "dap Kano — delivered at place: the supplier pays freight to the named place"),
            (
                "CIF",
                "CIF — cost, insurance and freight: the supplier pays sea freight and insurance to the named port",
            ),
            ("XYZ", "XYZ"),
            ("", ""),
        ],
    )
    def test_a_term_is_explained_once(self, term, words):
        assert incoterm_words(term) == words

    def test_the_order_header_explains_its_incoterm(self, scoped, da, world):
        op(
            da,
            "approval_decide",
            approval_id=world["technical"]["id"],
            status="approved",
        )
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
                "incoterm": "DAP",
                "status": "placed",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["aqualytic"]["id"],
                "source": "we_recorded",
            },
        )
        body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert "DAP — delivered at place: the supplier pays freight to the named place" in body


# ---- the stock page says which kit it is --------------------------------------


class TestTheStockSaysWhichKit:
    def test_it_names_the_specification_and_the_approval(self, scoped, aqualytic_link, world):
        service.submit(
            UpdateLink.objects.get(pk=aqualytic_link["id"]),
            "record_answer",
            {"approval": AwardApproval.objects.get(pk=world["technical"]["id"]), "status": "approved"},
        )
        body = scoped.get(reverse("supply_chain:stock") + f"?item_id={world['lumen']['id']}").content.decode()
        assert "data-standing" in body
        assert "Meets all 2 requirements of" in body and "AQ-TK-1." in body
        assert "Approved by <b>Aqualytic</b>" in body
        assert "through Aqualytic's own link" in body

    def test_an_unapproved_kit_claims_no_approval(self, scoped, world):
        body = scoped.get(reverse("supply_chain:stock") + f"?item_id={world['lumen']['id']}").content.decode()
        assert "data-standing" in body
        assert "Approved by" not in body

    def test_the_whole_network_carries_no_item_line(self, scoped, world):
        body = scoped.get(reverse("supply_chain:stock")).content.decode()
        assert "data-standing" not in body
