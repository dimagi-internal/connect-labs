"""Removing an invitation recorded in error.

Jonathan's reason for the capability, which is also why it is a hard delete
rather than a soft one: "if we make a mistake saying we did outreach, we
should be able to delete it." A row claiming we contacted somebody we never
contacted is not history -- it is a false assertion, and a voided-but-readable
version would keep making it. That is the opposite of a quote, which stays
readable when voided because it is a supplier's stated fact and the record of
having received it matters even once superseded.

It exists because a re-import doubled programme 10063's outreach log and there
was no way to take the phantom invitations back out.
"""

import jsonschema
import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10509


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def rutf(da):
    return op(
        da,
        "commodity_upsert",
        data={
            "slug": "rutf",
            "name": "RUTF",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )


@pytest.fixture
def invited(da, rutf):
    supplier = op(da, "supplier_create", data={"name": "Northwind"})
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Tender 1",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
        },
    )
    outreach = op(
        da,
        "outreach_log",
        data={"tender_id": tender["id"], "supplier_id": supplier["id"], "sent_on": "2026-05-01"},
    )
    return {"tender": tender, "supplier": supplier, "outreach": outreach}


def test_an_invitation_recorded_in_error_can_be_removed(da, invited):
    op(da, "outreach_delete", outreach_id=invited["outreach"]["id"], reason="never actually sent")
    assert op(da, "outreach_list") == []


def test_it_stops_being_counted_as_an_invitation(da, invited):
    """The count is what the mistake distorts. "Who has not replied" is acted
    on, so a phantom invitation is a phantom follow-up."""
    before = op(da, "chain_summary", commodity_slug="rutf")["source"]["rfq_issued"]
    assert before["invitations"] == 1
    assert before["awaiting_reply"] == 1

    op(da, "outreach_delete", outreach_id=invited["outreach"]["id"], reason="never actually sent")

    after = op(da, "chain_summary", commodity_slug="rutf")["source"]["rfq_issued"]
    assert after["invitations"] == 0
    assert after["awaiting_reply"] == 0


def test_a_reason_is_required(da, invited):
    """Recorded in the write log, which is where the trace lives once the row
    is gone -- every write operation is argument-logged to MCPAuditLog."""
    with pytest.raises(jsonschema.ValidationError):
        op(da, "outreach_delete", outreach_id=invited["outreach"]["id"])
    with pytest.raises(jsonschema.ValidationError):
        op(da, "outreach_delete", outreach_id=invited["outreach"]["id"], reason="")
    assert len(op(da, "outreach_list")) == 1


def test_an_unknown_row_is_refused_rather_than_silently_ignored(da, rutf):
    with pytest.raises(ValueError, match="not found"):
        op(da, "outreach_delete", outreach_id=999999, reason="typo")


def test_another_programmes_invitation_is_not_reachable(da, invited):
    """The scope check that stops a delete crossing programmes. `not found`
    rather than `forbidden` on purpose: a caller outside the scope learns
    nothing about what exists inside it."""
    other = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
    with pytest.raises(ValueError, match="not found"):
        op(other, "outreach_delete", outreach_id=invited["outreach"]["id"], reason="wrong programme")
    assert len(op(da, "outreach_list")) == 1


def test_the_quote_it_produced_is_left_alone(da, invited):
    """Deleting the invitation must not take the supplier's answer with it.
    Nothing references Outreach, so there is no cascade -- this pins that,
    because adding one later would make a delete destroy a quote."""
    quote = op(
        da,
        "quote_record",
        data={
            "tender_id": invited["tender"]["id"],
            "supplier_id": invited["supplier"]["id"],
            "commodity_slug": "rutf",
            "as_quoted_amount": "52.42",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "500",
            "quantity_basis_unit": "carton",
            "pack_spec_source": "not_stated",
        },
    )
    op(da, "outreach_delete", outreach_id=invited["outreach"]["id"], reason="logged twice")
    assert [q["id"] for q in op(da, "quote_list")] == [quote["id"]]
