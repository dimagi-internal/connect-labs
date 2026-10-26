"""Standing on a call: owner, operator, respondent, everyone else."""

import pytest
from django.core.exceptions import PermissionDenied

from connect_labs.solicitations import access
from connect_labs.solicitations.tests.factories import grant, make_call, make_org, make_response, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    owner_org = make_org("owner-org")
    firm_org = make_org("firm-org")
    owner = make_user("owner")
    grant(owner, owner_org)
    outsider = make_user("outsider")
    staff = make_user("support", staff=True)
    applicant = make_user("applicant")
    grant(applicant, firm_org)
    call = make_call(owner_org)
    response = make_response(call, applicant, llo_entity=firm_org)
    return {
        "owner_org": owner_org,
        "firm_org": firm_org,
        "owner": owner,
        "outsider": outsider,
        "staff": staff,
        "applicant": applicant,
        "call": call,
        "response": response,
    }


def test_standing_owner_operator_public(world):
    call = world["call"]
    assert access.standing(world["owner"], call) == access.OWNER
    assert access.standing(world["staff"], call) == access.OPERATOR
    assert access.standing(world["outsider"], call) == access.PUBLIC
    assert access.standing(None, call) == access.PUBLIC


def test_staff_member_of_owner_is_owner(world):
    grant(world["staff"], world["owner_org"])
    assert access.standing(world["staff"], world["call"]) == access.OWNER


def test_visibility_is_not_membership(world):
    """A user whose session lists the org (e.g. a labs-only org they can see) is not a member."""
    labs_only = make_org("labs-synthetic-x", labs_only=True)
    call = make_call(labs_only)

    class FakeRequest:
        session = {}
        user = world["outsider"]

    caller = access.caller_for(FakeRequest())
    assert access.can_manage(caller, call) is False


def test_only_managers_read_responses(world):
    response = world["response"]
    assert access.can_read_response(world["owner"], response)
    assert access.can_read_response(world["staff"], response)
    assert access.can_read_response(world["applicant"], response)
    assert not access.can_read_response(world["outsider"], response)
    assert not access.can_read_response(None, response)


def test_owner_never_sees_someone_elses_draft(world):
    draft = make_response(world["call"], world["outsider"], status="draft")
    assert not access.can_read_response(world["owner"], draft)
    assert access.can_read_response(world["outsider"], draft)


def test_respondent_edits_only_their_draft(world):
    draft = make_response(world["call"], world["outsider"], status="draft")
    assert access.can_edit_response(world["outsider"], draft)
    assert not access.can_edit_response(world["owner"], draft)
    assert not access.can_edit_response(world["applicant"], world["response"])  # submitted


def test_respondent_cannot_review_or_award_own_response(world):
    owner_org = world["owner_org"]
    applicant = world["applicant"]
    grant(applicant, owner_org)  # even as a member of the owning organisation
    response = world["response"]
    assert access.can_manage(applicant, world["call"])
    assert not access.can_review(applicant, response)
    assert not access.can_award(applicant, response)


def test_member_of_responding_org_cannot_review(world):
    colleague = make_user("colleague")
    grant(colleague, world["firm_org"])
    grant(colleague, world["owner_org"])
    assert not access.can_review(colleague, world["response"])


def test_owner_and_staff_review_and_award(world):
    assert access.can_review(world["owner"], world["response"])
    assert access.can_award(world["staff"], world["response"])
    assert not access.can_award(world["outsider"], world["response"])


def test_unowned_historical_round_is_staff_only(world):
    call = make_call(None)
    dimagi_person = make_user("dimagi-person", email="someone@dimagi.com")
    assert access.standing(world["staff"], call) == access.OPERATOR
    assert access.standing(world["owner"], call) == access.PUBLIC
    assert access.standing(dimagi_person, call) == access.PUBLIC


def test_unlisted_call_visible_to_owner_and_respondents_only(world):
    call = make_call(world["owner_org"], listed=False)
    assert not access.can_read(world["outsider"], call)
    assert access.can_read(world["owner"], call)
    make_response(call, world["outsider"], status="draft")
    assert access.can_read(world["outsider"], call)


def test_listed_draft_is_not_public(world):
    call = make_call(world["owner_org"], status="draft")
    assert not access.can_read(None, call)
    assert access.can_read(world["owner"], call)


def test_manageable_orgs(world):
    assert access.manageable_orgs(world["owner"]) == [world["owner_org"]]
    assert access.manageable_orgs(world["outsider"]) == []
    assert access.manageable_orgs(None) == []
    staff_orgs = access.manageable_orgs(world["staff"])
    assert world["owner_org"] in staff_orgs


def test_merged_membership_is_not_a_grant(world):
    other = make_org("other-org")
    membership = grant(world["outsider"], other)
    membership.org = world["owner_org"]  # what a merge does underneath it
    membership.save()
    assert access.manageable_orgs(world["outsider"]) == []
    assert not access.can_manage(world["outsider"], world["call"])


def test_dimagi_address_manages_dimagi_owned_calls():
    from connect_labs.supply_chain.runner import dimagi_org

    call = make_call(dimagi_org())
    person = make_user("dimagi-person", email="someone@dimagi.com")
    assert access.standing(person, call) == access.OWNER
    assert dimagi_org() in access.manageable_orgs(person)


def test_require_raises():
    access.require(True, "fine")
    with pytest.raises(PermissionDenied, match="nope"):
        access.require(False, "nope")


def test_resolve_org_by_keys():
    org = make_org("acme-health", connect_organization_id=4242, connect_organization_slug="acme", aliases=["acme-ng"])
    assert access.resolve_org("acme-health") == org
    assert access.resolve_org("4242") == org
    assert access.resolve_org("acme") == org
    assert access.resolve_org("acme-ng") == org
    assert access.resolve_org("nobody") is None


def test_manageable_orgs_are_membership_by_grant_only(world):
    """A membership row on Dimagi is not Dimagi membership: only a Dimagi address is."""
    from connect_labs.solicitations.data_access import SolicitationsDataAccess
    from connect_labs.supply_chain.runner import dimagi_org

    dimagi = dimagi_org()
    grant(world["outsider"], dimagi)
    hidden = make_call(dimagi, listed=False, title="Dimagi's unlisted call")
    assert dimagi not in access.manageable_orgs(world["outsider"])
    assert hidden not in SolicitationsDataAccess(user=world["outsider"]).get_solicitations()
    person = make_user("dimagi-person", email="someone@dimagi.com")
    assert dimagi in access.manageable_orgs(person)
    assert hidden in SolicitationsDataAccess(user=person).get_solicitations()


def test_owning_org_member_responding_under_a_typed_name_is_own_side(world):
    colleague = make_user("colleague")
    grant(colleague, world["owner_org"])
    response = make_response(world["call"], colleague, org_name="A Name Nobody Registered")
    assert response.llo_entity_id is None
    assert not access.can_review(world["owner"], response)
    assert not access.can_award(world["owner"], response)
    # Platform support is not on the owner's side.
    assert access.can_review(world["staff"], response)


def test_a_dimagi_associate_belongs_to_dimagi_and_is_staff():
    from connect_labs.supply_chain.runner import dimagi_org
    from connect_labs.utils.dimagi_user import is_dimagi_staff, is_dimagi_user

    associate = make_user("associate", email="someone@dimagi-associate.com")
    call = make_call(dimagi_org())
    assert access.standing(associate, call) == access.OWNER
    assert dimagi_org() in access.manageable_orgs(associate)
    assert is_dimagi_user(associate) and is_dimagi_staff(associate)


def test_a_lookalike_domain_is_not_dimagi():
    from connect_labs.supply_chain.runner import dimagi_org

    for email in ("someone@notdimagi.com", "someone@dimagi.com.example", "someone@dimagi-associates.com"):
        assert access.standing(make_user(email.split("@")[1], email=email), make_call(dimagi_org())) == access.PUBLIC


def test_dimagi_reads_its_directory_rounds_responses_and_nobody_manages_them():
    from connect_labs.solicitations.local_models import SOURCE_DIRECTORY
    from connect_labs.supply_chain.runner import dimagi_org

    round_ = make_call(dimagi_org(), source=SOURCE_DIRECTORY, slug="old-round")
    dimagi, associate = make_user("d", email="d@dimagi.com"), make_user("a", email="a@dimagi-associate.com")
    outsider, staff = make_user("outsider"), make_user("support", staff=True)

    assert access.can_read_round_responses(dimagi, round_)
    assert access.can_read_round_responses(associate, round_)
    assert access.can_read_round_responses(staff, round_)
    assert not access.can_read_round_responses(outsider, round_)
    assert not access.can_read_round_responses(None, round_)
    # Owned, but still a record kept in the sheet: nobody edits, reviews or awards it.
    for person in (dimagi, associate, staff):
        assert access.standing(person, round_) == access.PUBLIC
        assert not access.can_manage(person, round_)


def test_round_responses_rule_never_covers_an_owned_call():
    from connect_labs.supply_chain.runner import dimagi_org

    assert not access.can_read_round_responses(make_user("d", email="d@dimagi.com"), make_call(dimagi_org()))


# --- demonstration awards: an owner's own synthetic response -----------------------


@pytest.fixture
def demo(world):
    """ACE's labs_e2e shape: one person manages the call AND belongs to the
    organisation its seeded winner answers as, and entered that answer itself."""
    owner = world["owner"]
    winner_org = make_org("winner-org")
    grant(owner, winner_org)
    call = make_call(world["owner_org"], listed=False)
    response = make_response(call, None, llo_entity=winner_org, synthetic=True)
    return {**world, "winner_org": winner_org, "demo_call": call, "synthetic": response}


def test_own_side_synthetic_response_is_refused_without_the_flag(demo):
    owner, response = demo["owner"], demo["synthetic"]
    assert not access.can_review(owner, response)
    assert not access.can_award(owner, response)


def test_demonstration_review_and_award_allowed_when_every_condition_holds(demo):
    owner, response = demo["owner"], demo["synthetic"]
    assert access.can_demonstrate(owner, response)
    assert access.can_review(owner, response, demonstration=True)
    assert access.can_award(owner, response, demonstration=True)
    # Platform support may run one too.
    assert access.can_award(demo["staff"], response, demonstration=True)


def test_demonstration_refused_on_a_listed_call(demo):
    response = demo["synthetic"]
    response.solicitation.listed = True
    response.solicitation.save()
    assert not access.can_review(demo["owner"], response, demonstration=True)
    assert not access.can_award(demo["owner"], response, demonstration=True)


def test_demonstration_refused_for_a_real_response(demo):
    """A response not marked synthetic at creation -- the owner's own side stays refused."""
    real = make_response(demo["demo_call"], None, llo_entity=demo["winner_org"])
    assert not access.can_review(demo["owner"], real, demonstration=True)
    assert not access.can_award(demo["owner"], real, demonstration=True)
    assert not access.can_award(demo["owner"], real)


def test_demonstration_refused_for_a_response_with_a_respondent(demo):
    """Synthetic is an owner-entered answer; one a signed-in person submitted never qualifies."""
    submitted = make_response(demo["demo_call"], demo["owner"], llo_entity=demo["winner_org"], synthetic=True)
    assert not access.can_award(demo["owner"], submitted, demonstration=True)


def test_demonstration_refused_to_a_caller_who_cannot_manage_the_call(demo):
    member_only = make_user("member-only")
    grant(member_only, demo["winner_org"])
    assert not access.can_review(member_only, demo["synthetic"], demonstration=True)
    assert not access.can_award(member_only, demo["synthetic"], demonstration=True)
    assert not access.can_award(demo["outsider"], demo["synthetic"], demonstration=True)


def test_demonstration_refused_on_a_directory_round(demo):
    from connect_labs.solicitations.local_models import SOURCE_DIRECTORY

    call = demo["synthetic"].solicitation
    call.source = SOURCE_DIRECTORY
    call.save()
    assert not access.can_award(demo["owner"], demo["synthetic"], demonstration=True)
    assert not access.can_review(demo["owner"], demo["synthetic"], demonstration=True)


def test_a_synthetic_response_is_awarded_only_as_a_demonstration(demo):
    """Even by someone not on its side: an invented answer never becomes a real award."""
    staff = demo["staff"]
    assert not access.can_award(staff, demo["synthetic"])
    assert access.can_award(staff, demo["synthetic"], demonstration=True)
    # Reviewing it normally is unchanged for someone not on its side.
    assert access.can_review(staff, demo["synthetic"])


def test_real_behaviour_unchanged_by_the_demonstration_path(world):
    """Owner and staff still review and award a real response as before."""
    assert access.can_review(world["owner"], world["response"])
    assert access.can_award(world["owner"], world["response"])
    assert not access.can_award(world["owner"], world["response"], demonstration=True)
