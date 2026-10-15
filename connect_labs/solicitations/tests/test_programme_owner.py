"""A call created by programme id alone: whose is it?

ACE creates calls by `program_id`. The owner is the programme's Supply runner
if one is declared; otherwise the programme's own Connect organisation, read
from the caller's org tree, provided the caller belongs to it (or is staff); a
labs-only programme is owned by its synthetic opportunity's labs organisation
on the same rule. Anything else is refused with a message naming `org_slug`.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.tests.factories import CRITERIA, QUESTIONS, grant, make_org, make_user

pytestmark = pytest.mark.django_db


def payload(**kw):
    data = {
        "title": "Survey firm wanted",
        "description": "A household survey.",
        "solicitation_type": "rfp",
        "status": "active",
        "questions": QUESTIONS,
        "evaluation_criteria": CRITERIA,
    }
    data.update(kw)
    return data


def tree(org_id=501, org_slug="river-health", program_id=88):
    return {
        "organizations": [{"id": org_id, "slug": org_slug, "name": "River Health"}],
        "programs": [{"id": program_id, "name": "Malaria 2026", "organization": org_slug}],
        "opportunities": [],
    }


def request_for(user, org_data):
    return SimpleNamespace(user=user, session={"labs_oauth": {"organization_data": org_data}}, labs_context={})


@pytest.fixture
def river():
    return make_org("river-health", name="River Health", connect_organization_id=501)


def test_programme_org_from_the_callers_tree(river):
    user = make_user("member")
    access = SolicitationsDataAccess(request=request_for(user, tree()))
    call = access.create_solicitation(payload(program_id=88))
    assert call.owner_org == river
    assert call.program_id == 88


def test_programme_org_found_by_connect_slug_when_unlinked():
    org = make_org("river-labs", name="River Health", connect_organization_slug="river-health")
    user = make_user("member")
    grant(user, org)
    access = SolicitationsDataAccess(request=request_for(user, tree()))
    assert access.create_solicitation(payload(program_id=88)).owner_org == org


def test_programme_org_needs_membership(river):
    """Seeing the programme in your tree without belonging to its organisation is not enough."""
    user = make_user("viewer")
    org_data = tree()
    org_data["organizations"] = []  # the programme is visible, the organisation is not theirs
    access = SolicitationsDataAccess(request=request_for(user, org_data))
    with pytest.raises(ValidationError, match="org_slug"):
        access.create_solicitation(payload(program_id=88))


def test_staff_may_use_the_programme_org(river):
    staff = make_user("support", staff=True)
    org_data = tree()
    org_data["organizations"] = []
    access = SolicitationsDataAccess(request=request_for(staff, org_data))
    assert access.create_solicitation(payload(program_id=88)).owner_org == river


def test_programme_not_in_tree_names_org_slug(river):
    user = make_user("member")
    access = SolicitationsDataAccess(request=request_for(user, tree(program_id=12)))
    with pytest.raises(ValidationError, match="org_slug"):
        access.create_solicitation(payload(program_id=88))


def test_runner_still_wins(river):
    from connect_labs.supply_chain.runner import declare

    runner_org = make_org("runner-org")
    declare(88, runner_org)
    user = make_user("member")
    grant(user, runner_org)
    access = SolicitationsDataAccess(request=request_for(user, tree()))
    assert access.create_solicitation(payload(program_id=88)).owner_org == runner_org


def test_runner_declared_but_caller_not_a_member_is_refused(river):
    from connect_labs.supply_chain.runner import declare

    declare(88, make_org("runner-org"))
    access = SolicitationsDataAccess(request=request_for(make_user("member"), tree()))
    with pytest.raises(PermissionDenied):
        access.create_solicitation(payload(program_id=88))


def test_mcp_caller_without_request_reads_tree_from_token(river):
    user = make_user("member")
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"),
        patch(
            "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data",
            return_value=tree(),
        ),
    ):
        call = SolicitationsDataAccess(user=user).create_solicitation(payload(program_id="88"))
    assert call.owner_org == river


def _synthetic(opp_id, labs_org, program_id=None):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    opp = SyntheticOpportunity(
        opportunity_id=opp_id, labs_only=True, gdrive_folder_id="x", org_name=labs_org.name, program_id=program_id
    )
    opp.labs_org = labs_org
    opp.save()
    return opp


def test_labs_only_programme_owned_by_its_synthetic_org():
    org = make_org("labs-demo-org", name="Demo Org", labs_only=True)
    _synthetic(10_123, org, program_id=10_200)
    user = make_user("demo")
    grant(user, org)
    call = SolicitationsDataAccess(user=user).create_solicitation(payload(program_id=10_200))
    assert call.owner_org == org


def test_labs_only_programme_defaults_to_opp_id():
    org = make_org("labs-demo-org", name="Demo Org", labs_only=True)
    _synthetic(10_124, org)
    user = make_user("demo")
    grant(user, org)
    call = SolicitationsDataAccess(user=user).create_solicitation(payload(program_id=10_124))
    assert call.owner_org == org


def test_labs_only_programme_needs_membership():
    org = make_org("labs-demo-org", name="Demo Org", labs_only=True)
    _synthetic(10_125, org)
    with pytest.raises(ValidationError, match="org_slug"):
        SolicitationsDataAccess(user=make_user("stranger")).create_solicitation(payload(program_id=10_125))
