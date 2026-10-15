"""The solicitation agents act as their user, with that user's standing."""

import asyncio
from types import SimpleNamespace

import pytest

from connect_labs.ai.types import UserDependencies
from connect_labs.solicitations.tests.factories import grant, make_call, make_org, make_response, make_user

pytestmark = pytest.mark.django_db(transaction=True)


def _tool(agent, name):
    return agent._function_toolset.tools[name].function


def _ctx(deps):
    return SimpleNamespace(deps=deps)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world():
    org = make_org("owner-org", name="Owner Org")
    owner = make_user("owner")
    grant(owner, org)
    call = make_call(org, title="Survey call")
    make_response(call, make_user("applicant"), org_name="Firm Ltd", responses={"q1": "Ten years"})
    return {"org": org, "owner": owner, "call": call, "outsider": make_user("outsider")}


def test_review_agent_reads_responses_for_the_owner_only(world):
    from connect_labs.ai.agents.solicitation_review_agent import (
        SolicitationReviewAgentDeps,
        create_solicitation_review_agent_with_model,
    )

    agent = create_solicitation_review_agent_with_model("test")
    fetch = _tool(agent, "get_solicitation_data")

    owner_text = _run(
        fetch(_ctx(SolicitationReviewAgentDeps(UserDependencies(user=world["owner"]))), world["call"].pk)
    )
    assert "Firm Ltd" in owner_text
    assert "Ten years" in owner_text

    outsider_text = _run(
        fetch(_ctx(SolicitationReviewAgentDeps(UserDependencies(user=world["outsider"]))), world["call"].pk)
    )
    assert "Firm Ltd" not in outsider_text
    assert "Only the organisation" in outsider_text


def test_solicitation_agent_creates_for_named_org_and_refuses_others(world):
    from connect_labs.ai.agents.solicitation_agent import SolicitationAgentDeps, create_solicitation_agent_with_model

    agent = create_solicitation_agent_with_model("test")
    create = _tool(agent, "create_solicitation")

    made = _run(
        create(
            _ctx(SolicitationAgentDeps(UserDependencies(user=world["owner"]))),
            title="New call",
            description="Desc",
            org_slug="owner-org",
        )
    )
    assert "Created solicitation 'New call' for Owner Org" in made

    refused = _run(
        create(
            _ctx(SolicitationAgentDeps(UserDependencies(user=world["outsider"]))),
            title="New call",
            description="Desc",
            org_slug="owner-org",
        )
    )
    assert refused.startswith("Not allowed")

    unowned = _run(
        create(_ctx(SolicitationAgentDeps(UserDependencies(user=world["owner"]))), title="X", description="Y")
    )
    assert "org_slug" in unowned
