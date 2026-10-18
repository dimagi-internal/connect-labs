"""marketplace_round_responses -- a round's answers, to its owner only. All data invented.

Two properties matter: an outsider never reads what organisations told Dimagi,
and the contact person's details stay out of a transcript unless asked for.
"""

from __future__ import annotations

import pytest

from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import marketplace as tools
from connect_labs.solicitations.local_models import SOURCE_DIRECTORY, Solicitation, SolicitationResponse
from connect_labs.solicitations.tests.factories import make_org, make_user
from connect_labs.supply_chain.runner import dimagi_org

pytestmark = pytest.mark.django_db

QUESTIONS = [
    {"id": "timestamp", "text": "Timestamp", "column": 0},
    {"id": "organization_name", "text": "Organization name", "column": 1},
    {"id": "primary_contact", "text": "Primary contact", "column": 2},
    {"id": "email_id", "text": "Email ID (you can provide more than one)", "column": 3},
    {"id": "phone", "text": "Phone", "column": 4},
    {"id": "how_would_you_find_households", "text": "How would you find households?", "column": 5},
    {"id": "name_role_and_date", "text": "Name, Role, and Date", "column": 6},
]


@pytest.fixture
def round_():
    applicant = make_org("fenwick-trust", name="Fenwick Trust")
    call = Solicitation.objects.create(
        slug="ulab-demo",
        source=SOURCE_DIRECTORY,
        owner_org=dimagi_org(),
        title="Battery scoping demo",
        solicitation_type="eoi",
        status="closed",
        questions=QUESTIONS,
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        source_row=3,
        org_name="Unmatched Group",
        responses={"organization_name": "Unmatched Group", "how_would_you_find_households": "Door to door."},
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        source_row=2,
        llo_entity=applicant,
        org_name="Fenwick Trust",
        match_state="email",
        submitted_by_name="Ada Example",
        submitted_by_email="ada@fenwick.invalid",
        responses={
            "timestamp": "10/2/2026 5:33:33",
            "organization_name": "Fenwick Trust",
            "primary_contact": "Ada Example",
            "email_id": "ada@fenwick.invalid",
            "phone": "+000 000 0000",
            "how_would_you_find_households": "Through solar vendors' customer lists.",
            "name_role_and_date": "Ada Example, Director, 7 Oct",
        },
    )
    return call


def _call(user, **kw):
    return tools.marketplace_round_responses(user, slug="ulab-demo", **kw)


@pytest.mark.parametrize("email", ["d@dimagi.com", "a@dimagi-associate.com"])
def test_dimagi_reads_every_answer_in_row_order(round_, email):
    out = _call(make_user("reader", email=email))
    assert out["total"] == 2 and not out["truncated"]
    first, second = out["responses"]
    assert (first["source_row"], first["org_slug"]) == (2, "fenwick-trust")
    assert second["org_slug"] is None, "an unmatched submission is not given a near name"
    assert {
        "question_id": "how_would_you_find_households",
        "question": "How would you find households?",
        "answer": "Through solar vendors' customer lists.",
    } in first["answers"]
    assert out["round"]["owner"] == "Dimagi"


def test_contact_details_are_withheld_unless_asked_for(round_):
    reader = make_user("reader", email="d@dimagi.com")
    withheld = _call(reader)
    text = repr(withheld)
    for pii in ("Ada Example", "ada@fenwick.invalid", "+000 000 0000"):
        assert pii not in text, pii
    assert withheld["round"]["questions_withheld"] == 4

    shown = _call(reader, include_contacts=True)
    assert shown["responses"][0]["contact"] == {"name": "Ada Example", "emails": "ada@fenwick.invalid"}
    assert shown["round"]["questions_withheld"] == 0


def test_an_outsider_is_refused(round_):
    with pytest.raises(MCPToolError) as err:
        _call(make_user("outsider"))
    assert err.value.code == "PERMISSION_DENIED"


def test_platform_support_reads_it(round_):
    assert _call(make_user("support", staff=True))["total"] == 2


def test_an_owned_call_is_not_a_round(round_):
    Solicitation.objects.create(slug="owned-call", source="labs", owner_org=dimagi_org(), title="Owned")
    with pytest.raises(MCPToolError) as err:
        tools.marketplace_round_responses(make_user("d", email="d@dimagi.com"), slug="owned-call")
    assert err.value.code == "NOT_FOUND"
