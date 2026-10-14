"""A running organisation's team: who may change it, and what it takes (#2458).

THIS REPOSITORY IS PUBLIC. Every organisation and person here is invented.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command

from connect_labs.labs.access.scopes import SYSTEM, Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.labs.synthetic.provisioning import register_labs_only_opp
from connect_labs.marketplace.models import OrgMembership
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.runner import PROGRAMME, declare, dimagi_org, standing

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

PROGRAM = 10905


def _user(username, email, **flags):
    return get_user_model().objects.create(username=username, email=email, **flags)


def _access(user):
    with patch("connect_labs.supply_chain.data_access.may_use", return_value=None):
        return SupplyDataAccess(program_id=PROGRAM, user=user, caller=Caller(user=user))


@pytest.fixture(autouse=True)
def no_connect():
    with patch("connect_labs.supply_chain.identity._caller_organizations", return_value=[]):
        yield


@pytest.fixture
def oes():
    org = LabsOrg.objects.create(slug="labs-synthetic-oes-test", name="OES test", labs_only=True)
    register_labs_only_opp(
        opportunity_id=PROGRAM, label="OES test", program_id=PROGRAM, allowed_domains=["@oes.example", "@dimagi.com"]
    )
    declare(PROGRAM, org)
    return org


def test_support_adds_the_first_admin_who_then_acts_as_the_program(oes):
    founder = _user("founder", "founder@dimagi.com")
    call_command("supply_team_add", str(PROGRAM), "Founder@Dimagi.com", "--role", "admin")
    assert standing(_access(founder)).role == PROGRAMME


def test_an_admin_adds_and_removes_people(oes):
    admin = _user("admin", "admin@oes.example")
    OrgMembership.objects.create(org=oes, user=admin, role="admin")
    colleague = _user("colleague", "colleague@oes.example")
    team = call_operation("programme_team_add", _access(admin), {"email": "colleague@oes.example"})
    assert {(m["user_id"], m["role"]) for m in team["members"]} == {(admin.pk, "admin"), (colleague.pk, "member")}
    call_operation("programme_team_remove", _access(admin), {"user_id": colleague.pk})
    assert not OrgMembership.objects.filter(org=oes, user=colleague).exists()


def test_an_ordinary_member_cannot_change_the_team(oes):
    member = _user("member", "member@oes.example")
    OrgMembership.objects.create(org=oes, user=member, role="member")
    _user("other", "other@oes.example")
    with pytest.raises(PermissionDenied):
        call_operation("programme_team_add", _access(member), {"email": "other@oes.example", "role": "admin"})


def test_a_partner_who_can_see_the_program_cannot_join_it(oes):
    outsider = _user("outsider", "outsider@dimagi.com")
    with pytest.raises(PermissionDenied):
        call_operation("programme_team_add", _access(outsider), {"email": "outsider@dimagi.com", "role": "admin"})
    assert not OrgMembership.objects.filter(org=oes).exists()


def test_someone_who_could_not_open_the_program_is_refused(oes):
    _user("stranger", "someone@elsewhere.example")
    access = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    with pytest.raises(ValueError, match="allowed email domains"):
        call_operation("programme_team_add", access, {"email": "someone@elsewhere.example"})


def test_someone_who_never_signed_in_is_refused(oes):
    access = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    with pytest.raises(ValueError, match="signed in"):
        call_operation("programme_team_add", access, {"email": "nobody@oes.example"})


def test_the_last_admin_stays(oes):
    admin = _user("admin", "admin@oes.example")
    OrgMembership.objects.create(org=oes, user=admin, role="admin")
    with pytest.raises(ValueError, match="no admin left"):
        call_operation("programme_team_remove", _access(admin), {"user_id": admin.pk})


def test_dimagi_keeps_no_team_list():
    declare(PROGRAM, dimagi_org())
    access = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    with pytest.raises(ValueError, match="Dimagi address"):
        call_operation("programme_team_add", access, {"email": "x@dimagi.com"})
