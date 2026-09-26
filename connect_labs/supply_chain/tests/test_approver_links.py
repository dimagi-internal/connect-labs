"""An approver answers for itself, through a link of its own.

THIS REPOSITORY IS PUBLIC. Every organisation, product and number is invented.

A technical partner confirms a test kit before the programme buys it. Until
now the procurement lead recorded that answer after it arrived by email -- so
the gate an award waits on was cleared by the person it gates. An update link
can now be issued to the APPROVER, scoped to the approvals asked of it, and the
approver records its own answer. What must hold (the page authenticates nobody):

  - it answers only the approvals the link names -- not another approval of
    the same award, not one asked of someone else, not another programme's;
  - the answer goes through `approval_decide`, attributed to the approver
    (`partner_reported`, `decision_recorded_by_org` = the approver), and a
    signed-in caller cannot claim that attribution through the API;
  - the award page says the answer came through the approver's own link.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import AwardApproval
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission

pytestmark = pytest.mark.django_db

PROGRAM = 10618
OTHER_PROGRAM = 10619


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def _world(da, suffix=""):
    op(da, "commodity_upsert", data={"slug": "test-kit", "name": "Free chlorine test kit", "base_unit": "test"})
    item = op(
        da, "item_upsert", data={"sku": f"lumen{suffix}", "name": "Lumen FC-50 kit", "commodity_slug": "test-kit"}
    )
    supplier = op(da, "supplier_create", data={"name": "Harmattan Health Supplies"})
    aqualytic = op(da, "org_upsert", data={"slug": f"aqualytic{suffix}", "name": "Aqualytic"})
    funder = op(da, "org_upsert", data={"slug": f"northstar{suffix}", "name": "Northstar Fund"})
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
            "item_id": item["id"],
            "as_quoted_amount": "38.00",
            "as_quoted_unit": "per_pack",
        },
    )
    award = op(da, "award_create", tender_id=tender["id"], quote_id=quote["id"], rationale="Reads the dose")

    def ask(org, role):
        return op(da, "approval_request", data={"award_id": award["id"], "approver_org_id": org["id"], "role": role})

    return {
        "award": award,
        "aqualytic": aqualytic,
        "funder": funder,
        "technical": ask(aqualytic, "technical"),
        "funding": ask(funder, "funder"),
    }


@pytest.fixture
def world(da):
    return _world(da)


@pytest.fixture
def issued(da, world):
    return op(
        da,
        "update_link_issue",
        data={"org_id": world["aqualytic"]["id"], "approval_ids": [world["technical"]["id"]], "label": "Aqualytic"},
    )


def _link(issued):
    return UpdateLink.objects.get(pk=issued["id"])


def _url(token):
    return reverse("supply_chain:update_link_public", args=[token])


class TestIssuingALinkToAnApprover:
    def test_it_covers_the_named_approval(self, issued, world):
        assert issued["approval_ids"] == [world["technical"]["id"]]
        assert issued["contract_ids"] == [] and issued["supply_point_ids"] == []

    def test_an_approval_asked_of_someone_else_is_refused(self, da, world):
        with pytest.raises(ValueError, match="asked of Northstar Fund"):
            op(
                da,
                "update_link_issue",
                data={"org_id": world["aqualytic"]["id"], "approval_ids": [world["funding"]["id"]]},
            )

    def test_another_programmes_approval_is_refused(self, da, world):
        other = SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)
        theirs = _world(other, suffix="-x")
        with pytest.raises(ValueError, match="does not exist in this programme"):
            op(
                da,
                "update_link_issue",
                data={"org_id": world["aqualytic"]["id"], "approval_ids": [theirs["technical"]["id"]]},
            )


class TestTheApproverAnswers:
    def test_it_records_the_answer_as_the_approvers_own_word(self, issued, world):
        link = _link(issued)
        service.submit(
            link,
            "record_answer",
            {
                "approval": AwardApproval.objects.get(pk=world["technical"]["id"]),
                "status": "approved",
                "note": "Lumen FC-50 meets AQ-TK-1.",
            },
        )
        approval = AwardApproval.objects.get(pk=world["technical"]["id"])
        assert approval.status == "approved"
        assert approval.decision_note == "Lumen FC-50 meets AQ-TK-1."
        assert approval.decision_source == "partner_reported"
        assert approval.decision_recorded_by_org_id == world["aqualytic"]["id"]
        submission = UpdateLinkSubmission.objects.get(link=link)
        assert (submission.operation, submission.result_id) == ("approval_decide", approval.pk)
        # Read back at the moment of the write and kept, like every other action.
        assert submission.summary.startswith("approved Lumen FC-50 kit, awarded to Harmattan Health Supplies")
        assert "meets AQ-TK-1" in submission.summary

    def test_a_linked_letter_is_attached_to_the_approval(self, issued, world):
        service.submit(
            _link(issued),
            "record_answer",
            {
                "approval": AwardApproval.objects.get(pk=world["technical"]["id"]),
                "status": "approved",
                "document_url": "https://files.example/aqualytic-aq-tk-1-confirmation.pdf",
            },
        )
        approval = AwardApproval.objects.get(pk=world["technical"]["id"])
        document = approval.documents.get()
        assert document.external_url.endswith("confirmation.pdf")
        assert document.recorded_by_org_id == world["aqualytic"]["id"]

    def test_a_failed_attachment_undoes_the_answer(self, issued, world):
        """The answer, its letter, the submission and the audit event land
        together or not at all: a decided approval with no record of who
        decided it, or how, is the one outcome this page must never leave."""
        with pytest.raises(ValueError):
            service.submit(
                _link(issued),
                "record_answer",
                {
                    "approval": AwardApproval.objects.get(pk=world["technical"]["id"]),
                    "status": "approved",
                    "document_url": "ftp://files.example/confirmation.pdf",
                },
            )
        assert AwardApproval.objects.get(pk=world["technical"]["id"]).status == "requested"
        assert not UpdateLinkSubmission.objects.exists()

    @pytest.mark.parametrize(
        "url", ["ftp://files.example/confirmation.pdf", "https://files.example/" + "a" * 1100 + ".pdf"]
    )
    def test_the_form_refuses_a_link_the_document_cannot_hold(self, url):
        from django.core.exceptions import ValidationError

        from connect_labs.supply_chain.update_links.forms import RecordAnswerForm

        with pytest.raises(ValidationError):
            RecordAnswerForm.base_fields["document_url"].clean(url)

    def test_another_approval_on_the_same_award_is_out_of_scope(self, issued, world):
        with pytest.raises(service.OutOfScope):
            service.submit(
                _link(issued),
                "record_answer",
                {"approval": AwardApproval.objects.get(pk=world["funding"]["id"]), "status": "approved"},
            )
        assert AwardApproval.objects.get(pk=world["funding"]["id"]).status == "requested"

    def test_another_programmes_approval_is_out_of_scope(self, issued):
        other = SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)
        theirs = _world(other, suffix="-y")
        with pytest.raises(service.OutOfScope):
            service.submit(
                _link(issued),
                "record_answer",
                {"approval": AwardApproval.objects.get(pk=theirs["technical"]["id"]), "status": "declined"},
            )
        assert AwardApproval.objects.get(pk=theirs["technical"]["id"]).status == "requested"

    def test_a_supplier_link_cannot_answer_an_approval(self, da, world):
        supplier_link = op(
            da,
            "update_link_issue",
            data={"org_id": world["aqualytic"]["id"], "approval_ids": [world["technical"]["id"]]},
        )
        UpdateLink.objects.get(pk=supplier_link["id"]).approvals.clear()
        with pytest.raises(service.OutOfScope):
            service.submit(
                UpdateLink.objects.get(pk=supplier_link["id"]),
                "record_answer",
                {"approval": AwardApproval.objects.get(pk=world["technical"]["id"]), "status": "approved"},
            )

    def test_an_answered_approval_is_not_offered_again(self, issued, world):
        from connect_labs.supply_chain.update_links.forms import RecordAnswerForm

        link = _link(issued)
        assert RecordAnswerForm(scope=service.scope_for(link)).is_available()
        service.submit(
            link,
            "record_answer",
            {"approval": AwardApproval.objects.get(pk=world["technical"]["id"]), "status": "approved"},
        )
        assert not RecordAnswerForm(scope=service.scope_for(link)).is_available()

    def test_a_signed_in_caller_cannot_claim_the_approvers_word(self, world, django_user_model):
        from connect_labs.labs.access.scopes import Caller

        user = django_user_model.objects.create_user(username="lead", password="x", email="lead@dimagi.com")
        member = SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM, user=user)
        with pytest.raises(ValueError, match="only through the approver's own link"):
            op(
                member,
                "approval_decide",
                approval_id=world["technical"]["id"],
                status="approved",
                via_update_link_id=1,
            )
        assert Caller  # imported to document the SYSTEM-only rule


class TestThePublicPage:
    def test_it_shows_what_was_asked_and_offers_the_answer(self, client, issued, world):
        body = client.get(_url(issued["token"])).content.decode()
        assert "Updates for Aqualytic" in body
        assert "Lumen FC-50 kit" in body
        assert "Record your answer" in body
        # A supplier's actions mean nothing to an approver and are not listed.
        assert "Confirm an order" not in body
        assert "Record a dispatch" not in body
        assert "Northstar" not in body

    def test_posting_an_answer_decides_the_approval(self, client, issued, world):
        page = client.get(_url(issued["token"])).content.decode()
        assert re.search(r'name="record_answer-approval"', page)
        response = client.post(
            _url(issued["token"]),
            {
                "action": "record_answer",
                "record_answer-approval": world["technical"]["id"],
                "record_answer-status": "approved",
                "record_answer-note": "Confirmed against AQ-TK-1.",
            },
        )
        assert response.status_code == 302
        assert AwardApproval.objects.get(pk=world["technical"]["id"]).status == "approved"

    def test_posting_an_approval_outside_the_link_is_refused(self, client, issued, world):
        response = client.post(
            _url(issued["token"]),
            {
                "action": "record_answer",
                "record_answer-approval": world["funding"]["id"],
                "record_answer-status": "approved",
            },
        )
        assert response.status_code == 200
        assert AwardApproval.objects.get(pk=world["funding"]["id"]).status == "requested"


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.fulfilment import views as fulfilment_views  # noqa: F401
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    account = django_user_model.objects.create_user(username="appr2", password="x", email="appr2@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment.views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheAwardPage:
    def test_it_says_the_answer_came_through_the_approvers_own_link(self, scoped, issued, world):
        service.submit(
            _link(issued),
            "record_answer",
            {"approval": AwardApproval.objects.get(pk=world["technical"]["id"]), "status": "approved"},
        )
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert "through Aqualytic's own link" in body

    def test_the_issue_form_offers_pending_approvals(self, scoped, world):
        body = scoped.get(reverse("supply_chain:update_link_issue")).content.decode()
        assert 'name="approvals"' in body
        assert "Aqualytic (technical)" in body


class TestTheIssuedLinkSaysWhatItCovers:
    """The issued page listed the link's orders and supply points and never its
    approvals, so an approver's link read as covering nothing
    (supply-test-kits walkthrough, 2026-09-24)."""

    def test_it_names_the_approval_the_product_and_the_supplier(self, issued):
        from django.template.loader import render_to_string

        assert issued["approvals"][0]["product"] == "Lumen FC-50 kit"
        body = render_to_string("supply_chain/update_link_issued.html", {"link": issued})
        assert "Their technical approval of the Lumen FC-50 kit awarded to Harmattan Health Supplies" in body
        assert "no labs account" in body


class TestReceiptUnitsOnALink:
    def test_counted_in_uses_the_products_own_units(self, da):
        from types import SimpleNamespace

        from connect_labs.supply_chain.update_links.forms import UNIT_BASIS, _unit_choices

        kit = SimpleNamespace(pack_unit="kit", base_unit="test", base_per_pack=50)
        assert _unit_choices(SimpleNamespace(items=[kit])) == [("pack", "Kits (50 tests each)"), ("base", "Tests")]
        other = SimpleNamespace(pack_unit="carton", base_unit="sachet", base_per_pack=150)
        assert _unit_choices(SimpleNamespace(items=[kit, other])) == UNIT_BASIS
        assert _unit_choices(None) == UNIT_BASIS
