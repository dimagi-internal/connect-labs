"""Setting a round's import duty terms drafts a clarification to every invited supplier.

An answer to one supplier ("we import, under the program's duty waiver")
changes how every quote on the round is costed, so the round drafts the same
clarification to every supplier it invited -- not only the one who asked.

Every supplier, person and price here is invented.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.views import _drafts_breakdown

pytestmark = pytest.mark.django_db

PROGRAM = 10735
TODAY = date(2026, 10, 3)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def round_(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Kano round",
            "delivery_point": {"name": "Central store", "city": "Kano", "country_name": "Nigeria"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_open", tender_id=tender["id"])
    asker = op(
        da,
        "supplier_create",
        data={"name": "Northwind Commodities", "contacts": [{"name": "A", "email": "a@northwind.example.invalid"}]},
    )
    quiet = op(da, "supplier_create", data={"name": "Lagoon Nutrition"})
    quoted = op(da, "supplier_create", data={"name": "Sahel Provisions"})
    for s in (asker, quiet, quoted):
        op(
            da,
            "outreach_log",
            data={
                "tender_id": tender["id"],
                "supplier_id": s["id"],
                "sent_on": (TODAY - timedelta(days=2)).isoformat(),
            },
        )
    return {"tender": tender, "suppliers": (asker, quiet, quoted)}


def _clarifications(da, tender_id):
    drafts = op(da, "tender_drafts_render", tender_id=tender_id, today=TODAY.isoformat())["drafts"]
    return [d for d in drafts if d["kind"] == "clarification"]


def test_not_settled_drafts_no_clarification(da, round_):
    assert _clarifications(da, round_["tender"]["id"]) == []


@pytest.mark.parametrize(
    "terms,expected",
    [
        (
            "buyer_waiver",
            "For this tender we import, under the program's duty waiver; please quote excluding import duty "
            "and state freight to Central store, Kano, Nigeria.",
        ),
        (
            "buyer_pays",
            "For this tender we import and pay the import duty ourselves; please quote excluding import duty "
            "and state freight to Central store, Kano, Nigeria.",
        ),
        (
            "supplier_ddp",
            "please quote delivered duty paid to Central store, Kano, Nigeria, with the import duty included.",
        ),
    ],
)
def test_setting_terms_drafts_a_clarification_to_every_invited_supplier(da, round_, terms, expected):
    tender_id = round_["tender"]["id"]
    op(da, "tender_set_duty_terms", tender_id=tender_id, duty_terms=terms, duty_estimate_percent="5")
    drafts = _clarifications(da, tender_id)
    assert sorted(d["supplier_name"] for d in drafts) == sorted(s["name"] for s in round_["suppliers"])
    for d in drafts:
        assert expected in d["text"]
        assert d["subject"] == "Kano round — clarification: import duty terms"
        assert "every invited supplier" in d["why"]
        # On the page, why is a labelled row (sheets b5): when the terms were set, who it goes to.
        facts = {f["label"]: f["value"] for f in d["facts"]}
        assert set(facts) == {"Duty terms set", "Goes to"}
        assert facts["Goes to"] == "every invited supplier"
    asker = next(d for d in drafts if d["supplier_name"] == "Northwind Commodities")
    assert asker["to"] == "a@northwind.example.invalid"


def test_an_answer_that_sets_terms_drafts_to_everyone_not_only_the_asker(da, round_):
    tender_id = round_["tender"]["id"]
    asker = round_["suppliers"][0]
    question = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": asker["id"],
            "tender_id": tender_id,
            "text": "Who will be importer of record?",
            "raised_on": (TODAY - timedelta(days=1)).isoformat(),
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "commitment_resolve",
        commitment_id=question["id"],
        resolution="We import, under the program's duty waiver.",
        resolved_on=TODAY.isoformat(),
        duty_terms="buyer_waiver",
    )
    drafts = _clarifications(da, tender_id)
    # Everyone else is told; the asker is owed a reply already, and the terms go into it instead.
    assert len(drafts) == 2
    assert asker["id"] not in {d["supplier_id"] for d in drafts}
    assert "from your answer to Northwind Commodities" in drafts[0]["why"]
    assert {"label": "From your answer to", "value": "Northwind Commodities"} in drafts[0]["facts"]
    everything = op(da, "tender_drafts_render", tender_id=tender_id, today=TODAY.isoformat())["drafts"]
    (reply,) = (d for d in everything if d["kind"] == "reply")
    assert "\n\nFor this tender we import, under the program's duty waiver" in reply["text"]
    assert reply["text"].count("For this tender") == 1


def test_closed_round_drafts_no_clarification(da, round_):
    tender_id = round_["tender"]["id"]
    op(da, "tender_set_duty_terms", tender_id=tender_id, duty_terms="buyer_waiver")
    op(da, "tender_close", tender_id=tender_id)
    assert _clarifications(da, tender_id) == []


def test_breakdown_counts_clarifications_so_the_parts_add_up():
    drafts = [
        {"kind": "reminder", "supplier_id": 1},
        {"kind": "clarification", "supplier_id": 1},
        {"kind": "clarification", "supplier_id": 2},
    ]
    assert _drafts_breakdown(drafts) == ("1 reminder · 2 clarifications of the duty terms")


# ---- one email to a supplier, never two (DDD sheets batch 3) ---------------------


def _ask_and_answer(da, tender_id, supplier, *, on, duty_terms=None):
    question = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": supplier["id"],
            "tender_id": tender_id,
            "text": "Who will be importer of record?",
            "raised_on": (on - timedelta(days=1)).isoformat(),
            "source": "supplier_reported",
        },
    )
    resolve = {"duty_terms": duty_terms} if duty_terms else {}
    op(
        da,
        "commitment_resolve",
        commitment_id=question["id"],
        resolution="We import, under the program's duty waiver.",
        resolved_on=on.isoformat(),
        **resolve,
    )
    return question


def test_terms_ride_in_the_email_already_due_and_a_sent_reply_is_not_followed_by_another(da, round_):
    tender_id = round_["tender"]["id"]
    asker, quiet, quoted = round_["suppliers"]
    # Lagoon has waited ten days: a reminder is due to it.
    row = next(r for r in da.list_outreach(tender_id=tender_id) if r.supplier_id == quiet["id"])
    op(da, "outreach_update", outreach_id=row.pk, data={"sent_on": (TODAY - timedelta(days=10)).isoformat()})
    # Northwind asked; the answer set the terms and the reply carrying it was marked sent that day.
    question = _ask_and_answer(da, tender_id, asker, on=TODAY, duty_terms="buyer_waiver")
    op(da, "commitment_reply_sent", commitment_ids=[question["id"]], sent_on=TODAY.isoformat())

    drafts = op(da, "tender_drafts_render", tender_id=tender_id, today=TODAY.isoformat())["drafts"]
    by_supplier = {}
    for d in drafts:
        by_supplier.setdefault(d["supplier_name"], []).append(d)

    # The reply went with the terms in it: nothing more to Northwind.
    assert "Northwind Commodities" not in by_supplier
    # Lagoon's reminder carries the terms, before its closing; no second email.
    (reminder,) = by_supplier["Lagoon Nutrition"]
    assert reminder["kind"] == "reminder"
    assert reminder["duty_terms"] == "buyer_waiver"
    body, _, closing = reminder["text"].rpartition("\n\nWith thanks,")
    assert body.endswith("please quote excluding import duty and state freight to Central store, Kano, Nigeria.")
    assert closing
    # Sahel has nothing else pending: the clarification stands alone.
    (alone,) = by_supplier["Sahel Provisions"]
    assert alone["kind"] == "clarification"


def test_a_reply_sent_before_the_terms_were_set_did_not_carry_them(da, round_):
    tender_id = round_["tender"]["id"]
    asker = round_["suppliers"][0]
    earlier = TODAY - timedelta(days=5)
    question = _ask_and_answer(da, tender_id, asker, on=earlier)
    op(da, "commitment_reply_sent", commitment_ids=[question["id"]], sent_on=earlier.isoformat())
    op(da, "tender_set_duty_terms", tender_id=tender_id, duty_terms="buyer_waiver")

    told = {d["supplier_id"] for d in _clarifications(da, tender_id)}
    assert asker["id"] in told
