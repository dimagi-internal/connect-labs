"""Approvals before an award becomes an order.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

A technical partner confirms a test kit before we buy it; a funder approves
using a stop-gap product (field use cases, G5). The approver is a third party
who is not the person deciding the award, and a contract may not rest on an
award whose approval is pending or was declined -- a statement of fact, the
way a duty relief may not be claimed without evidence.
"""

from datetime import date, timedelta

import jsonschema
import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.checks import KIND_CATEGORIES
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import AwardApproval
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10615
TODAY = date.today()


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L", "pack_unit": "jerry_can"},
    )
    supplier = op(da, "supplier_create", data={"name": "A distributor"})
    funder = op(da, "org_upsert", data={"slug": "funder", "name": "A funder"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
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
    return {"supplier": supplier, "funder": funder, "us": us, "round": round_, "award": award}


def _request(da, world, **extra):
    return op(
        da,
        "approval_request",
        data={
            "award_id": world["award"]["id"],
            "approver_org_id": world["funder"]["id"],
            "role": "funder",
            "requested_on": (TODAY - timedelta(days=6)).isoformat(),
            **extra,
        },
    )


def _order(da, world):
    return op(
        da,
        "contract_create",
        data={
            "award_id": world["award"]["id"],
            "round_id": world["round"]["id"],
            "commodity_slug": "chlorine",
            "supplier_id": world["supplier"]["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": world["us"]["id"],
            "source": "we_recorded",
        },
    )


class TestAnApprovalIsRequestedAndDecided:
    def test_a_request_starts_pending(self, da, world):
        approval = _request(da, world)
        assert approval["status"] == "requested"
        assert approval["role"] == "funder"
        assert approval["approver_org_id"] == world["funder"]["id"]
        assert approval["decided_on"] is None

    def test_the_role_is_one_of_three(self, da, world):
        with pytest.raises(jsonschema.ValidationError):
            _request(da, world, role="vibes")

    def test_deciding_records_the_outcome_and_the_date(self, da, world):
        approval = _request(da, world)
        decided = op(
            da, "approval_decide", approval_id=approval["id"], status="approved", note="use until the donor arrives"
        )
        assert decided["status"] == "approved"
        assert decided["decided_on"] == TODAY.isoformat()
        assert decided["decision_note"] == "use until the donor arrives"

    def test_the_answer_does_not_overwrite_what_was_asked(self, da, world):
        # The request's note is the record of what the approver was asked to
        # agree to. Writing the answer over it lost the question.
        approval = _request(da, world, note="90 jerry cans of stop-gap chlorine")
        decided = op(da, "approval_decide", approval_id=approval["id"], status="approved", note="approved by email")
        assert decided["note"] == "90 jerry cans of stop-gap chlorine"
        assert decided["decision_note"] == "approved by email"

    def test_a_decision_is_approved_or_declined_not_requested_again(self, da, world):
        approval = _request(da, world)
        with pytest.raises(jsonschema.ValidationError):
            op(da, "approval_decide", approval_id=approval["id"], status="requested")

    def test_a_decided_approval_is_not_decided_again(self, da, world):
        """A reversal is a new request, so the first decision stays on the
        record rather than being overwritten."""
        approval = _request(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="declined")
        with pytest.raises(ValueError, match="already"):
            op(da, "approval_decide", approval_id=approval["id"], status="approved")

    def test_an_approval_on_another_programmes_award_is_refused(self, da, world):
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        with pytest.raises(ValueError, match="award"):
            _request(elsewhere, world)

    def test_they_are_listed_per_award(self, da, world):
        approval = _request(da, world)
        assert [a["id"] for a in op(da, "approval_list", award_id=world["award"]["id"])] == [approval["id"]]
        assert op(da, "approval_list", status="approved") == []


class TestAwaitingApprovalIsACheck:
    def test_it_is_a_missing_fact(self):
        assert KIND_CATEGORIES["award_awaiting_approval"] == "missing"

    def test_each_pending_approval_says_who_and_how_long(self, da, world):
        approval = _request(da, world)
        (check,) = (c for c in op(da, "checks_list")["checks"] if c["kind"] == "award_awaiting_approval")
        assert check["subject"]["type"] == "award"
        assert check["subject"]["id"] == world["award"]["id"]
        assert check["facts"]["approval_id"] == approval["id"]
        assert check["facts"]["approver"] == {"id": world["funder"]["id"], "name": "A funder"}
        assert check["facts"]["role"] == "funder"
        assert check["days_open"] == 6

    def test_a_decided_approval_is_not_awaiting(self, da, world):
        approval = _request(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        assert not [c for c in op(da, "checks_list")["checks"] if c["kind"] == "award_awaiting_approval"]


class TestAnOrderMayNotRestOnAnUnapprovedAward:
    def test_a_pending_approval_refuses_the_contract_and_names_it(self, da, world):
        approval = _request(da, world)
        with pytest.raises(ValueError) as refused:
            _order(da, world)
        message = str(refused.value)
        # Named in words: whose approval, which award. Row ids meant nothing
        # to the person refused.
        assert "awaiting A funder's funder approval" in message
        assert f"approval {approval['id']}" not in message
        assert "asked" in message

    def test_a_declined_approval_refuses_it_too(self, da, world):
        approval = _request(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="declined")
        with pytest.raises(ValueError, match="declined"):
            _order(da, world)

    def test_once_approved_the_order_can_be_placed(self, da, world):
        approval = _request(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        assert _order(da, world)["award_id"] == world["award"]["id"]

    def test_a_refusal_later_reversed_by_a_new_approval_no_longer_blocks(self, da, world):
        first = _request(da, world)
        op(da, "approval_decide", approval_id=first["id"], status="declined")
        second = _request(da, world, requested_on=TODAY.isoformat())
        with pytest.raises(ValueError, match=f"asked {TODAY.isoformat()}"):
            _order(da, world)
        op(da, "approval_decide", approval_id=second["id"], status="approved")
        assert _order(da, world)["award_id"] == world["award"]["id"]

    def test_a_refusal_from_a_different_approver_still_blocks(self, da, world):
        other = op(da, "org_upsert", data={"slug": "regulator", "name": "A regulator"})
        declined = _request(da, world, approver_org_id=other["id"], role="regulatory")
        op(da, "approval_decide", approval_id=declined["id"], status="declined")
        approved = _request(da, world, requested_on=TODAY.isoformat())
        op(da, "approval_decide", approval_id=approved["id"], status="approved")
        with pytest.raises(ValueError, match="declined"):
            _order(da, world)

    def test_an_award_that_needed_no_approval_is_unaffected(self, da, world):
        assert _order(da, world)["award_id"] == world["award"]["id"]

    def test_an_award_from_another_programme_cannot_be_named(self, da, world):
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        op(elsewhere, "commodity_upsert", data={"slug": "chlorine", "name": "Chlorine"})
        supplier = op(elsewhere, "supplier_create", data={"name": "Someone"})
        with pytest.raises(ValueError, match="award"):
            op(
                elsewhere,
                "contract_create",
                data={
                    "award_id": world["award"]["id"],
                    "commodity_slug": "chlorine",
                    "supplier_id": supplier["id"],
                    "buyer_of_record": "programme_org",
                    "buyer_org_id": world["us"]["id"],
                    "source": "we_recorded",
                },
            )


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    account = django_user_model.objects.create_user(username="appr", password="x", email="appr@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheAwardPage:
    def test_it_lists_approvals_and_offers_requesting_and_deciding(self, scoped, da, world):
        approval = _request(da, world)
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert "Approvals" in body
        assert "A funder" in body
        assert "Funder" in body
        assert "requested" in body
        assert reverse("supply_chain:approval_request", args=[world["award"]["id"]]) in body
        assert reverse("supply_chain:approval_decide", args=[approval["id"]]) in body

    def test_it_shows_what_was_asked_and_what_they_answered(self, scoped, da, world):
        approval = _request(da, world, note="90 jerry cans of stop-gap chlorine")
        op(da, "approval_decide", approval_id=approval["id"], status="approved", note="approved by email")
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert "90 jerry cans of stop-gap chlorine" in body
        assert "approved by email" in body

    def test_it_says_an_order_cannot_be_placed_yet(self, scoped, da, world):
        _request(da, world)
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert "cannot be ordered" in body

    def test_once_approved_it_offers_placing_the_order(self, scoped, da, world):
        approval = _request(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert reverse("supply_chain:contract_create") + f"?award={world['award']['id']}" in body

    def test_a_reversed_refusal_offers_placing_the_order(self, scoped, da, world):
        first = _request(da, world)
        op(da, "approval_decide", approval_id=first["id"], status="declined")
        second = _request(da, world, requested_on=TODAY.isoformat())
        op(da, "approval_decide", approval_id=second["id"], status="approved")
        body = scoped.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content.decode()
        assert "cannot be ordered" not in body
        assert reverse("supply_chain:contract_create") + f"?award={world['award']['id']}" in body

    def test_the_comparison_links_to_its_awards(self, scoped, da, world):
        body = scoped.get(
            reverse("supply_chain:procurement_comparison", args=[world["round"]["id"]]) + "?commodity=chlorine"
        ).content.decode()
        assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) in body


class TestTheApprovalScreens:
    def test_requesting_an_approval(self, scoped, da, world):
        response = scoped.post(
            reverse("supply_chain:approval_request", args=[world["award"]["id"]]),
            {
                "approver_org": world["funder"]["id"],
                "role": "funder",
                "requested_on": TODAY.isoformat(),
                "note": "stop-gap product",
            },
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert AwardApproval.objects.get(award_id=world["award"]["id"]).status == "requested"

    def test_recording_the_decision(self, scoped, da, world):
        approval = _request(da, world)
        response = scoped.post(
            reverse("supply_chain:approval_decide", args=[approval["id"]]),
            {"status": "approved", "decided_on": TODAY.isoformat(), "note": "approved by email"},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        assert AwardApproval.objects.get(pk=approval["id"]).status == "approved"

    def test_the_order_form_from_an_award_refuses_while_it_waits(self, scoped, da, world):
        _request(da, world)
        page = scoped.get(reverse("supply_chain:contract_create") + f"?award={world['award']['id']}")
        assert page.status_code == 200
        from connect_labs.supply_chain.models import Commodity

        commodity = Commodity.objects.get(scope_key=f"prog:{PROGRAM}", slug="chlorine")
        response = scoped.post(
            reverse("supply_chain:contract_create") + f"?award={world['award']['id']}",
            {
                "supplier": world["supplier"]["id"],
                "commodity": commodity.pk,
                "buyer_of_record": "programme_org",
                "buyer_org": world["us"]["id"],
                "status": "placed",
                "currency": "USD",
                "freight_basis": "not_specified",
                "duties_basis": "not_specified",
                "vat_basis": "not_specified",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 200
        body = response.content.decode()
        assert "awaiting A funder&#x27;s funder approval" in body
        # Nothing on the form is wrong: the refusal is about the award, so
        # telling her to "fix what is marked" sends her looking for a field.
        assert "fix what is marked" not in body
