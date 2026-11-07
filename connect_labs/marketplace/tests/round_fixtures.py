"""The marketplace round the review tests share: one round, three applicants, its owner's member and ACE. All invented.

Loaded for every test in this package by `conftest.py`, so a test names the
fixtures it needs as arguments without importing them.
"""

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgProfile
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.supply_chain.runner import dimagi_org

ANSWER = "Through the customer lists of solar vendors."

CRITERIA = [
    {
        "id": "reach",
        "name": "Reach to households",
        "weight": 60,
        "rationale": "Half name no channel.",
    },
    {
        "id": "cost",
        "name": "Cost fit",
        "weight": 40,
        "rationale": "Quotes vary tenfold.",
    },
]


@pytest.fixture
def round_():
    applicant = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")
    OrgProfile.objects.create(org=applicant, countries=["Ghana"])
    # Applied, but not in the directory: only the application puts it on the network.
    LabsOrg.objects.create(slug="quietwater", name="Quietwater Collective")
    call = Solicitation.objects.create(
        owner_org=dimagi_org(),
        slug="battery-2026",
        title="Battery scoping",
        solicitation_type="eoi",
        delivery_type="chc",
        status="closed",
        response_spreadsheet_id="sheet-id-invalid",
        questions=[{"id": "how", "text": "How would you find households?", "column": 1}],
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        llo_entity=applicant,
        org_name="Fenwick Trust",
        source_row=2,
        match_state=SolicitationResponse.MATCH_EMAIL,
        responses={"how": ANSWER},
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        llo_entity=LabsOrg.objects.get(slug="quietwater"),
        org_name="Quietwater Collective",
        source_row=3,
        match_state=SolicitationResponse.MATCH_EMAIL,
    )
    SolicitationResponse.objects.create(
        solicitation=call,
        org_name="Unclaimed Group",
        source_row=4,
        match_state=SolicitationResponse.MATCH_UNMATCHED,
    )
    return call


@pytest.fixture
def member(django_user_model):
    return django_user_model.objects.create_user(username="member", password="x", email="m@dimagi-associate.com")


@pytest.fixture
def outsider(django_user_model):
    return django_user_model.objects.create_user(username="outsider", password="x", email="o@partner.invalid")


@pytest.fixture
def ace(django_user_model):
    return django_user_model.objects.create_user(username="ace", password="x", email="ace@dimagi-ai.com")


@pytest.fixture
def scored(round_, ace):
    """The round with criteria and an AI first pass on Fenwick."""
    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    SolicitationsDataAccess(user=ace).set_round_criteria("battery-2026", CRITERIA, "Read all three.")
    submission = round_.responses.get(source_row=2)
    ai = SolicitationsDataAccess(user=ace).create_review(
        submission.pk,
        {
            "kind": "ai",
            "criteria_scores": {"reach": 8, "cost": 5},
            "criteria_notes": {"reach": "Names solar vendors' lists."},
            "outcome": "pick",
            "strengths": "A real channel to households.",
            "provenance": {"model": "test", "rubric_version": "v1"},
        },
    )
    return submission, ai
